import boto3
import json

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('fieldwork-sessions')

# Scan for any session that has processedAt
response = table.scan(
    FilterExpression="attribute_exists(processedAt)",
    Limit=10
)

items = response.get('Items', [])
if not items:
    print("No processed sessions found.")
    exit(1)

study_id = items[0]['studyId']
print(f"Found studyId: {study_id} with {len([i for i in items if i['studyId'] == study_id])} processed sessions in scan.")

lambda_client = boto3.client('lambda', region_name='us-east-1')

event = {
    "Records": [
        {
            "body": json.dumps({"studyId": study_id}),
            "attributes": {"ApproximateReceiveCount": "1"}
        }
    ]
}

print("Invoking Lambda...")
resp = lambda_client.invoke(
    FunctionName='fieldwork-synthesizer',
    InvocationType='RequestResponse',
    Payload=json.dumps(event)
)

print(f"Lambda response status: {resp['StatusCode']}")
response_payload = resp['Payload'].read().decode('utf-8')
print(f"Lambda response payload: {response_payload}")

# Now fetch the study to see if it worked
studies_table = dynamodb.Table('fieldwork-studies')
study = studies_table.get_item(Key={'studyId': study_id}).get('Item', {})
print("Study row:")
print(json.dumps(study, indent=2, default=str))
