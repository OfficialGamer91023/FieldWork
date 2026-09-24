import boto3
from datetime import datetime, timezone

dynamodb = boto3.resource('dynamodb')
sessions_table = dynamodb.Table('fieldwork-sessions')

study_id = 'demo_study_001'

sessions = [
    {
        'studyId': study_id,
        'sessionId': 'session_1',
        'processedAt': datetime.now(timezone.utc).isoformat(),
        'answers': ['Users find the onboarding too long', 'The new color scheme is great'],
        'quotes': ['I really like the new colors.', 'Onboarding takes forever!']
    },
    {
        'studyId': study_id,
        'sessionId': 'session_2',
        'processedAt': datetime.now(timezone.utc).isoformat(),
        'answers': ['Onboarding is confusing', 'Customer support was very helpful'],
        'quotes': ['I got stuck during onboarding.', 'Support answered immediately.']
    },
    {
        'studyId': study_id,
        'sessionId': 'session_3',
        'processedAt': datetime.now(timezone.utc).isoformat(),
        'answers': ['The UI is too cluttered', 'The new color scheme is great'],
        'quotes': ['There are too many buttons on the screen.', 'The new colors pop nicely.']
    }
]

for s in sessions:
    sessions_table.put_item(Item=s)

studies_table = dynamodb.Table('fieldwork-studies')
studies_table.put_item(Item={
    'studyId': study_id,
    'founderId': 'founder_1',
    'createdAt': datetime.now(timezone.utc).isoformat(),
    'inviteToken': 'demo_token'
})

print(f"Seeded 3 sessions for study {study_id}")
