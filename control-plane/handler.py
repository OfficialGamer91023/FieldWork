import json
import os
import uuid
import secrets
import boto3
import decimal
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super(DecimalEncoder, self).default(obj)

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["STUDIES_TABLE"])

def handler(event, context):
    """
    The main entry point. API Gateway routes all requests here.
    This acts as a tiny internal router.
    """
    route = event["routeKey"]
    
    if route == "POST /studies":
        return create_study(event)
    if route == "GET /studies":
        return list_studies(event)
    if route == "GET /studies/{id}":
        return get_study(event)
    if route == "PATCH /studies/{id}":
        return publish_study(event)
    if route == "GET /invite/{token}":
        return resolve_invite(event)

    return {
        "statusCode": 404,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "Route not found"})
    }


def create_study(event):
    """
    Handles POST /studies
    """
    body_str = event.get("body")
    if not body_str:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Request body is empty"})
        }

    try:
        payload = json.loads(body_str)
    except json.JSONDecodeError:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Invalid JSON format"})
        }

    if not isinstance(payload, dict):
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "JSON body must be an object"})
        }

    title = payload.get("title")
    if not title:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Missing required field: title"})
        }

    try:
        study_id = uuid.uuid4().hex
        invite_token = secrets.token_urlsafe(32)
        created_at = datetime.now(timezone.utc).isoformat()
        founder_id = "founder-demo" 
        
        item = {
            "studyId": study_id,
            "founderId": founder_id,
            "createdAt": created_at,
            "inviteToken": invite_token,
            "title": title,
            "goal": payload.get("goal", ""),
            "seedQuestions": payload.get("seedQuestions", []),
            "status": "draft"
        }
        
        table.put_item(Item=item)
        
        return {
            "statusCode": 201,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "studyId": study_id,
                "inviteToken": invite_token
            })
        }
    except Exception as e:
        print(f"Error in create_study: {str(e)}") 
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal Server Error"})
        }


def list_studies(event):
    founder_id = "founder-demo"
    resp = table.query(
        IndexName = "byFounder",
        KeyConditionExpression=Key("founderId").eq(founder_id))
    items = resp.get("Items", [])
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"studies": items}, cls=DecimalEncoder) 
    }


def get_study(event):
    study_id = event.get("pathParameters", {}).get("id")
    resp = table.get_item(Key = {"studyId" : study_id})
    item = resp.get("Item")
    if not item:
        return {
        "statusCode": 404,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "study not found"})
        }

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(item, cls=DecimalEncoder)
    }

def publish_study(event):
    """
    Handles PATCH /studies/{id} — flips a study from draft to live.
    The ConditionExpression makes this a no-op-safe 404 if the study
    doesn't exist, instead of silently creating a bare item.
    """
    study_id = event.get("pathParameters", {}).get("id")
    try:
        table.update_item(
            Key={"studyId": study_id},
            UpdateExpression="SET #s = :live",
            ConditionExpression="attribute_exists(studyId)",
            ExpressionAttributeNames={"#s": "status"},  # status is a reserved word
            ExpressionAttributeValues={":live": "live"},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {
                "statusCode": 404,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "study not found"})
            }
        raise

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"studyId": study_id, "status": "live"})
    }


def resolve_invite(event):
    token_id = event.get("pathParameters", {}).get("token")
    resp = table.query(
        IndexName="byInviteToken",
        KeyConditionExpression=Key("inviteToken").eq(token_id)
    )
    items = resp.get("Items", [])
    
    if not items:
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "token not found"})
        }
    study = items[0]
    
    if study.get("status") != "live":
        return {
            "statusCode": 403,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "This study is not currently active"})
        }
        
    safe_study = {
        "studyId": study.get("studyId"),
        "title": study.get("title", ""),
        "goal": study.get("goal", ""),
        "seedQuestions": study.get("seedQuestions", [])
    }
    
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(safe_study, cls=DecimalEncoder)
    }
