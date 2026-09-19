import json
import os
import urllib.request
from urllib.error import HTTPError, URLError
import boto3
from botocore.exceptions import ClientError
from datetime import datetime, timezone

dynamodb = boto3.resource("dynamodb")
sessions_table = dynamodb.Table(os.environ["SESSIONS_TABLE"])
s3 = boto3.client("s3")
BUCKET = os.environ["TRANSCRIPTS_BUCKET"]

FEATHERLESS_URL = "https://api.featherless.ai/v1/chat/completions"
TIMEOUT_SECONDS = 25


def _load_featherless_key():
    """Fetch the API key from Secrets Manager once per container (cold start),
    so the plaintext value never lives in tfstate or the Lambda env config."""
    secrets = boto3.client("secretsmanager")
    resp = secrets.get_secret_value(SecretId=os.environ["FIELDWORK_SECRET_ARN"])
    return json.loads(resp["SecretString"])["FEATHERLESS_API"]


# Resolved at import time → reused across warm invocations.
FEATHERLESS_API_KEY = _load_featherless_key()

SYSTEM_PROMPT = """You are an expert qualitative researcher.
Analyze the following interview transcript (a dialogue between an Interviewer
and a User) and extract:
1. "answers": the key takeaways, written in YOUR OWN words as concise summary
   points. Paraphrase — do not just copy the user's sentences.
2. "sentiment": the User's overall sentiment — exactly one of "positive",
   "negative", "neutral", or "mixed".
3. "quotes": 1-2 of the User's most revealing statements copied VERBATIM — the
   User's exact words, word-for-word, never paraphrased or summarized.

Only use content the User actually said. Never invent answers or quotes.

Output strictly as JSON with this schema:
{
    "answers": ["takeaway in your own words", "another takeaway"],
    "sentiment": "mixed",
    "quotes": ["the user's exact words"]
}"""

def _parse_insight(content):
    """Small models sometimes wrap JSON in ```fences``` or add a sentence of
    preamble. Try a clean parse first, then fall back to the first {...} block."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start:end + 1])
        raise


def handler(event, context):
    """
    Triggered by SQS batches.
    """
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        study_id = body.get("studyId")
        session_id = body.get("sessionId")
        
        if not study_id or not session_id:
            continue
            
        print(f"Processing session {session_id} for study {study_id}")
        
        # 1. Fetch from DynamoDB
        resp = sessions_table.get_item(
            Key={"studyId": study_id, "sessionId": session_id},
            ConsistentRead=True,  # producer writes the row then sends; beat the eventual-consistency window
        )
        item = resp.get("Item")
        if not item:
            print(f"Session not found in DB")
            continue
            
        transcript_key = item.get("transcriptKey")
        if not transcript_key:
            print(f"No transcript key found")
            continue
            
        # 2. Fetch transcript from S3
        s3_resp = s3.get_object(Bucket=BUCKET, Key=transcript_key)
        transcript_data = json.loads(s3_resp["Body"].read().decode('utf-8'))
        
        # Guard: an interview with no user turns has nothing to extract.
        # Skip the LLM call entirely so we never fabricate insight from an
        # empty transcript (garbage-in must not become confident-garbage-out).
        user_turns = sum(1 for m in transcript_data if m.get("role") == "user")
        if user_turns == 0:
            print(f"No user turns for {session_id}; marking empty, skipping extraction")
            try:
                sessions_table.update_item(
                    Key={"studyId": study_id, "sessionId": session_id},
                    UpdateExpression="SET #a = :a, #s = :s, #q = :q, #p = :p",
                    ConditionExpression="attribute_not_exists(#p)",
                    ExpressionAttributeNames={
                        "#a": "answers", "#s": "sentiment", "#q": "quotes", "#p": "processedAt",
                    },
                    ExpressionAttributeValues={
                        ":a": [], ":s": "n/a", ":q": [],
                        ":p": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
            continue

        # Format the transcript into a readable string for the LLM
        transcript_text = ""
        for msg in transcript_data:
            if msg["role"] == "system": continue
            role = "Interviewer" if msg["role"] == "assistant" else "User"
            transcript_text += f"{role}: {msg['content']}\n"

        # 3. Call Featherless via urllib
        req_body = json.dumps({
            "model": "Qwen/Qwen2.5-7B-Instruct",  # same model the orchestrator uses on Featherless
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": transcript_text}
            ],
            "response_format": {"type": "json_object"}
        }).encode('utf-8')
        
        req = urllib.request.Request(FEATHERLESS_URL, data=req_body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {FEATHERLESS_API_KEY}")
        # Featherless is behind Cloudflare, which 403s the default "Python-urllib"
        # User-Agent (error 1010). Any normal UA clears it — the SDK sent one for us.
        req.add_header("User-Agent", "fieldwork-extractor/1.0")
        
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                result = json.loads(response.read().decode('utf-8'))
                insight_str = result["choices"][0]["message"]["content"]
                insight = _parse_insight(insight_str)
        except HTTPError as e:
            print(f"Featherless HTTPError: {e.code} - {e.read().decode('utf-8')}")
            raise # Bubble up to fail the SQS message
        except URLError as e:
            print(f"Featherless URLError: {e.reason}")
            raise
        except Exception as e:
            print(f"Error processing LLM response: {e}")
            raise
            
        # 4. Update DynamoDB — alias every name so we never trip a reserved word,
        #    guarded so a duplicate SQS delivery can't double-write.
        try:
            sessions_table.update_item(
                Key={"studyId": study_id, "sessionId": session_id},
                UpdateExpression="SET #a = :a, #s = :s, #q = :q, #p = :p",
                ConditionExpression="attribute_not_exists(#p)",
                ExpressionAttributeNames={
                    "#a": "answers",
                    "#s": "sentiment",
                    "#q": "quotes",
                    "#p": "processedAt",
                },
                ExpressionAttributeValues={
                    ":a": insight.get("answers", []),
                    ":s": insight.get("sentiment", "neutral"),
                    ":q": insight.get("quotes", []),
                    ":p": datetime.now(timezone.utc).isoformat()
                }
            )
            print(f"Successfully processed {session_id}")
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                print(f"Session {session_id} was already processed (duplicate delivery)")
                # Do NOT raise here, we want SQS to consider this message successfully processed
                pass
            else:
                raise

    return {"statusCode": 200, "body": json.dumps("Success")}
