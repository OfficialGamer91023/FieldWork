from openai import AsyncOpenAI
import os
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from urllib.parse import urlencode
from dotenv import load_dotenv
import json
import websockets
from datetime import datetime, timezone 
import asyncio
from fastapi import WebSocketDisconnect
import boto3
from boto3.dynamodb.conditions import Key
import uuid

app = FastAPI()
load_dotenv()
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
ASSEMBLY_API_KEY = os.environ["ASSEMBLY_API"]
FEATHERLESS_API_KEY = os.environ["FEATHERLESS_API"]

dynamodb = boto3.resource("dynamodb")
studies_table = dynamodb.Table(os.environ["STUDIES_TABLE"])
sessions_table = dynamodb.Table(os.environ["SESSIONS_TABLE"])
s3 = boto3.client("s3")
BUCKET = os.environ["TRANSCRIPTS_BUCKET"]
sqs = boto3.client("sqs")
EXTRACTION_QUEUE_URL = os.environ.get("EXTRACTION_QUEUE_URL")

CLIENT = AsyncOpenAI(
base_url="https://api.featherless.ai/v1",
api_key=FEATHERLESS_API_KEY
)

html = """
<!DOCTYPE html>
<html>
    <head>
        <title>Audio Chat</title>
    </head>
    <body>
        <h1>WebSocket Audio Chat</h1>
        <div>
            <button id="startBtn" onclick="startRecording()">Start Recording</button>
            <button id="stopBtn" onclick="stopRecording()" disabled>Stop Recording</button>
        </div>
        <ul id='messages'>
        </ul>
        <script>
            var ws = new WebSocket("ws://localhost:8000/ws");
            
            ws.onmessage = function(event) {
                var messages = document.getElementById('messages');
                var message = document.createElement('li');
                var content = document.createTextNode(event.data);
                message.appendChild(content);
                messages.appendChild(message);
                const utterance = new SpeechSynthesisUtterance(event.data);
                speechSynthesis.speak(utterance);
            };

            ws.onerror = function(error) {
                console.error("WebSocket Error: ", error);
            };

            ws.onclose = function(event) {
                console.log("WebSocket connection closed. Code:", event.code);
                // Ensure recording stops if the server disconnects us
                stopRecording(); 
            };

            // --- Raw PCM AudioWorklet Setup ---
            
            let audioContext;
            let workletNode;
            let micStream;

            async function startRecording() {
                try {
                    // Request microphone access
                    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    
                    // Set up the Audio API
                    audioContext = new AudioContext({ sampleRate: 16000 });
                    const source = audioContext.createMediaStreamSource(micStream);
                    
                    // Load our custom processor script from the static folder
                    await audioContext.audioWorklet.addModule('/static/processor.js');
                    workletNode = new AudioWorkletNode(audioContext, 'pcm-processor');
                    
                    console.log("Actual sample rate: ", audioContext.sampleRate);

                    // Listen for the buffered Int16 chunk arriving from the audio thread
                    workletNode.port.onmessage = (event) => {
                        const chunk = event.data; // This is the Int16Array buffer
                        if (ws.readyState === WebSocket.OPEN) {
                            ws.send(chunk);
                        }
                    };
                    
                    // Connect the microphone to the processor
                    source.connect(workletNode);
                    workletNode.connect(audioContext.destination);

                    document.getElementById('startBtn').disabled = true;
                    document.getElementById('stopBtn').disabled = false;
                } catch (err) {
                    console.error("Error accessing microphone:", err);
                    alert("Microphone access denied or not available.");
                }
            }

            function stopRecording() {
                // Shut down the audio context and microphone tracks
                if (audioContext && audioContext.state !== 'closed') {
                    audioContext.close();
                }
                if (micStream) {
                    micStream.getTracks().forEach(track => track.stop());
                }
                document.getElementById('startBtn').disabled = false;
                document.getElementById('stopBtn').disabled = true;
            }
        </script>
    </body>
</html>
"""

def connect_to_assemblyai():
    BASE = "wss://streaming.assemblyai.com/v3/ws"
    CONNECTION_PARAMS = {
        "sample_rate" : 16000,
        "speech_model" : "universal-3-6-pro",
        "mode": "balanced"
    }
    API_ENDPOINT = f"{BASE}?{urlencode(CONNECTION_PARAMS)}"
    return websockets.connect(API_ENDPOINT, additional_headers={"Authorization": ASSEMBLY_API_KEY})

async def get_llm_reply(message_list):
    response = await CLIENT.chat.completions.create(
        model='Qwen/Qwen2.5-7B-Instruct',
        messages=message_list,
    )
    return response.choices[0].message.content


@app.get("/")
async def get():
    return HTMLResponse(html)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return

    resp = await asyncio.to_thread(
        studies_table.query, 
        IndexName="byInviteToken", 
        KeyConditionExpression=Key("inviteToken").eq(token)
    )
    items = resp.get("Items", [])
    if not items or items[0].get("status") != "live":
        await websocket.close(code=1008)
        return

    study = items[0]
    study_id = study.get("studyId")
    session_id = uuid.uuid4().hex

    await websocket.accept()
    
    goal = study.get("goal", "")
    seed_questions = "\n".join(f"- {q}" for q in study.get("seedQuestions", []))
    
    system_prompt = f"""You are an expert user researcher conducting a live voice interview.

Your research goal: {goal}

Seed questions to cover:
{seed_questions}

Rules:
- Ask exactly ONE open-ended question per turn. Never stack multiple questions.
- Dig into specifics and emotions. When they mention a frustration, ask them to walk you through the last time it happened, or why it mattered.
- Keep it short and conversational — you are being spoken aloud by a text-to-speech voice. Limit responses to 1-2 sentences.
- Do not give advice, opinions, or answer factual/trivia questions. If they go off-topic, gently steer back to the research goal.
- Start by warmly introducing yourself in one sentence and asking your first question."""

    messages = [{"role": "system", "content": system_prompt}]
    
    ended_cleanly = False

    async with connect_to_assemblyai() as aai_ws:
        async def browser_to_aai():
            while True:
                chunk = await websocket.receive_bytes()
                await aai_ws.send(chunk)

        async def aai_to_browser():
            async for message in aai_ws:
                try:
                    data = json.loads(message)
                    msg_type = data.get('type')

                    if msg_type == "Begin":
                        aai_session_id = data.get('id')
                        expires_at = data.get('expires_at')
                        print(f"\nSession began: ID={aai_session_id}, ExpiresAt={datetime.fromtimestamp(expires_at)}")
                    elif msg_type == "Turn":
                        transcript = data.get('transcript', '')
                        end_turn = data.get('end_of_turn', False)

                        if end_turn:
                            messages.append({"role": "user", "content": transcript})
                            print('\r' + ' ' * 80 + '\r', end='')
                            print(f"User: {transcript}")
                            reply = await get_llm_reply(messages)
                            messages.append({"role": "assistant", "content": reply})
                            print(f"Interviewer: {reply}")
                            await websocket.send_text(reply)
                        else:
                            print(f"\r{transcript}", end='')
                    elif msg_type == "Termination":
                        audio_duration = data.get('audio_duration_seconds', 0)
                        session_duration = data.get('session_duration_seconds', 0)
                        print(f"\nSession Terminated: Audio Duration={audio_duration}s, Session Duration={session_duration}s")
                except json.JSONDecodeError as e:
                    print(f"Error decoding message: {e}")
                except Exception as e:
                    print(f"Error handling message: {e}")
                
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(browser_to_aai())
                tg.create_task(aai_to_browser())
        except* WebSocketDisconnect:
            ended_cleanly = True
            print("Client disconnected")
        finally:
            try:
                key = f"transcripts/{study_id}/{session_id}.json"
                
                await asyncio.to_thread(
                    s3.put_object, 
                    Bucket=BUCKET, 
                    Key=key,
                    Body=json.dumps(messages).encode()
                )
                
                await asyncio.to_thread(
                    sessions_table.put_item, 
                    Item={
                        "studyId": study_id, 
                        "sessionId": session_id,
                        "endedAt": datetime.now(timezone.utc).isoformat(), 
                        "turnCount": (len(messages) - 1) // 2, 
                        "transcriptKey": key,
                        "status": "completed" if ended_cleanly else "aborted"
                    }
                )
                
                if EXTRACTION_QUEUE_URL:
                    payload = {
                        "studyId": study_id,
                        "sessionId": session_id
                    }
                    await asyncio.to_thread(
                        sqs.send_message,
                        QueueUrl=EXTRACTION_QUEUE_URL,
                        MessageBody=json.dumps(payload)
                    )
            except Exception as e:
                print(f"Error persisting session data: {e}")