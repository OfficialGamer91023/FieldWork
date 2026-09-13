import os
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

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


@app.get("/")
async def get():
    return HTMLResponse(html)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    async for chunk in websocket.iter_bytes():
        await websocket.send_text(f"Recieved bytes of len : {len(chunk)}")
        print(f"Recieved bytes of len : {len(chunk)}")
    print("Connection closed")
    # await websocket.close()
    # while True:
    #     chunk = await websocket.receive_bytes()
    #     await websocket.send_text(f"Recieved bytes of len : {len(chunk)}")
    #     print(f"Recieved bytes of len : {len(chunk)}")