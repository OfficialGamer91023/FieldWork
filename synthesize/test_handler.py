import os
import json

os.environ["STUDIES_TABLE"] = "fieldwork-studies"
os.environ["SESSIONS_TABLE"] = "fieldwork-sessions"
os.environ["FIELDWORK_SECRET_ARN"] = "arn:aws:secretsmanager:us-east-1:135808958639:secret:fieldwork/apis-gkwn6G"
os.environ["MAX_RETRIES"] = "3"

from handler import handler

event = {
    "Records": [
        {
            "body": json.dumps({"studyId": "test_study_123"}),
            "attributes": {"ApproximateReceiveCount": "1"}
        }
    ]
}

handler(event, None)
