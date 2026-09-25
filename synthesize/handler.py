import json
import os
import urllib.request
from urllib.error import HTTPError, URLError
import boto3
from botocore.exceptions import ClientError
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
studies_table = dynamodb.Table(os.environ["STUDIES_TABLE"])
sessions_table = dynamodb.Table(os.environ["SESSIONS_TABLE"])

FEATHERLESS_URL = "https://api.featherless.ai/v1/chat/completions"
TIMEOUT_SECONDS = 35  # Synthesis might take a bit longer than extraction
# Overridable so a failure can be injected on purpose (terraform -var synthesis_model=...).
SYNTHESIS_MODEL = os.environ.get("SYNTHESIS_MODEL", "Qwen/Qwen2.5-7B-Instruct")

def _load_featherless_key():
    """Fetch the API key from Secrets Manager once per container (cold start),
    so the plaintext value never lives in tfstate or the Lambda env config."""
    secrets = boto3.client("secretsmanager")
    resp = secrets.get_secret_value(SecretId=os.environ["FIELDWORK_SECRET_ARN"])
    return json.loads(resp["SecretString"])["FEATHERLESS_API"]

FEATHERLESS_API_KEY = _load_featherless_key()

SYSTEM_PROMPT = """You are an expert qualitative researcher.
You will get the study's research goal and seed questions, then the extracted insights from multiple interview sessions of that study.
Synthesize the data into overarching themes that answer the research goal — group by what users did, needed, or got stuck on, not by how they felt in general. Rank the themes by how many sessions raised them, with the most prevalent theme first. For each theme, provide:
1. "title": A short, descriptive title for the theme.
2. "summary": A 2-3 sentence summary of the theme across all sessions.
3. "sessionCitations": An array of session IDs that contributed to this theme.
4. "quotes": An array of the exact quotes that support this theme, along with the session ID they came from.

Output strictly as JSON with this schema:
{
    "themes": [
        {
            "title": "Theme Title",
            "summary": "Summary of the theme.",
            "sessionCitations": ["session_123", "session_456"],
            "quotes": [
                {
                    "text": "The exact quote verbatim",
                    "sessionId": "session_123"
                }
            ]
        }
    ]
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

def _normalize(text):
    """Keep only letters, digits and single spaces so a quote the model retyped
    (curly quotes, dropped period, extra whitespace) still matches the stored one."""
    if not isinstance(text, str): return ""
    cleaned = "".join(ch for ch in text.lower() if ch.isalnum() or ch.isspace())
    return " ".join(cleaned.split())

def handler(event, context):
    """
    Triggered by SQS batches.
    """
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        study_id = body.get("studyId")
        
        if not study_id:
            continue
            
        print(f"Synthesizing study {study_id}")
        
        receive_count = int(record.get("attributes", {}).get("ApproximateReceiveCount", 1))

        # 1. Mark the run as started
        try:
            # ALL_NEW hands back the study row, so the goal comes along for free.
            study = studies_table.update_item(
                Key={"studyId": study_id},
                UpdateExpression="SET #status = :running, #startedAt = :now",
                ReturnValues="ALL_NEW",
                ConditionExpression="attribute_exists(studyId)",
                ExpressionAttributeNames={
                    "#status": "synthesisStatus",
                    "#startedAt": "synthesisStartedAt"
                },
                ExpressionAttributeValues={
                    ":running": "RUNNING",
                    ":now": datetime.now(timezone.utc).isoformat()
                }
            )["Attributes"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                print(f"Study {study_id} does not exist. Dropping message.")
                continue # return normally, SQS drops
            raise

        # 2. Collect the sessions
        try:
            sessions = []
            last_evaluated_key = None
            
            while True:
                query_kwargs = {
                    "KeyConditionExpression": Key("studyId").eq(study_id),
                    "ConsistentRead": True
                }
                if last_evaluated_key:
                    query_kwargs["ExclusiveStartKey"] = last_evaluated_key
                    
                resp = sessions_table.query(**query_kwargs)
                
                for item in resp.get("Items", []):
                    if "processedAt" not in item:
                        continue
                    if not item.get("answers") and not item.get("quotes"):
                        continue
                    sessions.append(item)
                    
                last_evaluated_key = resp.get("LastEvaluatedKey")
                if not last_evaluated_key:
                    break

            if not sessions:
                print(f"No processed sessions found for {study_id}. Marking as DONE.")
                studies_table.update_item(
                    Key={"studyId": study_id},
                    UpdateExpression="SET #status = :done, #themes = :themes, #synthAt = :now REMOVE synthesisError",
                    ExpressionAttributeNames={
                        "#status": "synthesisStatus",
                        "#themes": "themes",
                        "#synthAt": "synthesizedAt"
                    },
                    ExpressionAttributeValues={
                        ":done": "DONE",
                        ":themes": [],
                        ":now": datetime.now(timezone.utc).isoformat()
                    }
                )
                continue

            # 3. The LLM call
            # Send the extracted answers and quotes. Label each block with sessionId
            synthesis_input = ""
            if study.get("goal"):
                synthesis_input += f"Research goal: {study['goal']}\n"
            if study.get("seedQuestions"):
                synthesis_input += "Seed questions:\n" + "".join(f"- {q}\n" for q in study["seedQuestions"])
            if synthesis_input:
                synthesis_input += "\n"
            for s in sessions:
                s_id = s["sessionId"]
                answers = s.get("answers", [])
                quotes = s.get("quotes", [])
                
                synthesis_input += f"--- Session ID: {s_id} ---\n"
                if answers:
                    synthesis_input += "Key Takeaways:\n"
                    for a in answers:
                        synthesis_input += f"- {a}\n"
                if quotes:
                    synthesis_input += "Key Quotes:\n"
                    for q in quotes:
                        synthesis_input += f"- \"{q}\"\n"
                synthesis_input += "\n"

            req_body = json.dumps({
                "model": SYNTHESIS_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": synthesis_input}
                ],
                "response_format": {"type": "json_object"}
            }).encode('utf-8')
            
            req = urllib.request.Request(FEATHERLESS_URL, data=req_body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {FEATHERLESS_API_KEY}")
            req.add_header("User-Agent", "fieldwork-synthesizer/1.0")
            
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    insight_str = result["choices"][0]["message"]["content"]
                    insight = _parse_insight(insight_str)
                    raw_themes = insight.get("themes", [])
                    if not isinstance(raw_themes, list):
                        raw_themes = []

                    valid_session_ids = {s["sessionId"] for s in sessions}
                    valid_quotes_map = {(s["sessionId"], _normalize(q)): q for s in sessions for q in s.get("quotes", [])}
                    
                    themes = []
                    for t in raw_themes:
                        if not isinstance(t, dict):
                            continue
                        
                        citations = t.get("sessionCitations", [])
                        if not isinstance(citations, list):
                            citations = []
                        valid_citations = [c for c in citations if c in valid_session_ids]

                        raw_quotes = t.get("quotes", [])
                        if not isinstance(raw_quotes, list):
                            raw_quotes = []
                            
                        valid_qs = []
                        for q in raw_quotes:
                            if not isinstance(q, dict):
                                continue
                            q_text = q.get("text")
                            q_session = q.get("sessionId")
                            if q_session in valid_session_ids:
                                orig_quote = valid_quotes_map.get((q_session, _normalize(q_text)))
                                if orig_quote:
                                    if not any(vq["text"] == orig_quote for vq in valid_qs):
                                        valid_qs.append({"text": orig_quote, "sessionId": q_session})

                        themes.append({
                            "title": str(t.get("title", "Untitled")),
                            "summary": str(t.get("summary", "")),
                            "modelCitations": set(valid_citations),
                            "quotes": valid_qs
                        })

                    def _cited(th):
                        # A verified quote proves its session raised this theme, even if
                        # the model forgot to list it in sessionCitations.
                        return th["modelCitations"] | {q["sessionId"] for q in th["quotes"]}

                    # A quote may support only one theme: small models reuse a strong
                    # quote under a second, unrelated theme. Walk themes strongest-first
                    # and keep each quote only where it appears first.
                    themes.sort(key=lambda th: len(_cited(th)), reverse=True)
                    used_quotes = set()
                    for th in themes:
                        th["quotes"] = [q for q in th["quotes"] if (q["sessionId"], q["text"]) not in used_quotes]
                        used_quotes.update((q["sessionId"], q["text"]) for q in th["quotes"])
                        cited = _cited(th)
                        del th["modelCitations"]
                        th["sessionCitations"] = sorted(cited)
                        th["sessionCount"] = len(cited)

                    themes = [th for th in themes if th["sessionCount"] > 0]  # drop themes with no valid citations
                    themes = sorted(themes, key=lambda x: x["sessionCount"], reverse=True)[:5]
                    if not themes:
                        raise ValueError("LLM returned no valid themes")
            except HTTPError as e:
                print(f"Featherless HTTPError: {e.code} - {e.read().decode('utf-8')}")
                raise
            except URLError as e:
                print(f"Featherless URLError: {e.reason}")
                raise
            except Exception as e:
                print(f"Error processing LLM response: {e}")
                raise

            # 4. Record the result (Success)
            studies_table.update_item(
                Key={"studyId": study_id},
                UpdateExpression="SET #status = :done, #themes = :themes, #synthAt = :now REMOVE synthesisError",
                ExpressionAttributeNames={
                    "#status": "synthesisStatus",
                    "#themes": "themes",
                    "#synthAt": "synthesizedAt"
                },
                ExpressionAttributeValues={
                    ":done": "DONE",
                    ":themes": themes,
                    ":now": datetime.now(timezone.utc).isoformat()
                }
            )
            print(f"Successfully synthesized {study_id}")
            
        except Exception as e:
            print(f"Error processing synthesis for {study_id}: {e}")
            max_retries = int(os.environ.get("MAX_RETRIES", "3"))
            if receive_count < max_retries:
                # Re-raise to let SQS retry
                raise
            else:
                # Max retries reached, fail it and return normally to drop from queue
                print(f"Max retries reached ({receive_count}). Marking as FAILED.")
                try:
                    studies_table.update_item(
                        Key={"studyId": study_id},
                        UpdateExpression="SET #status = :failed, #error = :error",
                        ExpressionAttributeNames={
                            "#status": "synthesisStatus",
                            "#error": "synthesisError"
                        },
                        ExpressionAttributeValues={
                            ":failed": "FAILED",
                            ":error": str(e)
                        }
                    )
                except Exception as db_e:
                    print(f"Failed to record FAILED status: {db_e}")
                    raise # Let it go to DLQ instead of staying RUNNING forever
                
    return {"statusCode": 200, "body": json.dumps("Success")}
