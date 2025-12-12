# Recall.ai Google Meet Voice Agent Integration

This implementation allows your LiveKit voice agent to join Google Meet meetings via Recall.ai and interact with participants in real-time.

## 🎯 Architecture Overview

```
Google Meet Participant
    ↓ (speaks)
Recall.ai Bot (in meeting)
    ↓ (WebSocket - real-time transcription)
Your WebSocket Server
    ↓ (text)
Your LLM Pipeline
    ↓ (response text)
Google Cloud TTS
    ↓ (MP3 audio)
Recall.ai Bot Output Audio API
    ↓ (speaks in meeting)
Google Meet Participants hear response
```

## 📦 Components Created

### 1. **recall_manager.py** - Bot Lifecycle Management

- Creates Recall.ai bots programmatically
- Manages bot lifecycle (join, status, leave)
- Sends audio output to meetings
- Handles bot status tracking

### 2. **recall_websocket.py** - Real-Time Event Handler

- WebSocket server receiving events from Recall.ai
- Processes real-time transcripts (partial & final)
- Handles participant events (join, leave, speech detection)
- Raw audio streaming (optional)

### 3. **session_coordinator.py** - AI Pipeline Orchestrator

- Coordinates the complete voice AI flow
- Accumulates transcripts with silence detection
- Processes speech through your LLM
- Converts responses to audio via Google TTS
- Sends audio back to Recall.ai

### 4. **recall_api.py** - HTTP API Server

- FastAPI server for meeting management
- REST endpoints to join/leave meetings
- Webhook receiver for Recall.ai events
- Session status monitoring

### 5. **recall_main.py** - Main Entry Point

- Unified application startup
- Combines all components
- Command-line interface

## 🚀 Setup Instructions

### Step 1: Install Dependencies

```powershell
# Navigate to project directory
cd "d:\foodnest Testing\voice-agent\mochand-realtime"

# Install dependencies with uv
uv sync
```

### Step 2: Configure Environment

Copy the example config and fill in your credentials:

```powershell
cp .env.recall.example .env.local
```

Edit `.env.local`:

```bash
# Required: Get from https://www.recall.ai/
RECALL_API_KEY=your_actual_recall_api_key

# Required: Your bot's display name in meetings
BOT_NAME=Jarvis

# Required for local dev: Expose ports with ngrok
# Terminal 1: ngrok http 8765
PUBLIC_WEBSOCKET_URL=wss://your-ngrok-url.ngrok-free.app

# Terminal 2: ngrok http 8000
PUBLIC_WEBHOOK_URL=https://your-ngrok-url.ngrok-free.app/webhooks/recall

# Google Cloud TTS credentials (already have this)
GOOGLE_CREDENTIALS_FILE=google_credentials_file.json

# Optional: Your custom LLM API
AGENT_API_URL=http://localhost:8080/api/chat

# Fallback: OpenRouter API key
OPENROUTER_API_KEY=your_openrouter_key
```

### Step 3: Expose Local Servers (Development)

For local development, you need to expose your servers to the internet so Recall.ai can connect:

```powershell
# Terminal 1: Expose WebSocket server (port 8765)
ngrok http 8765

# Copy the URL (e.g., https://abc123.ngrok-free.app)
# Set PUBLIC_WEBSOCKET_URL=wss://abc123.ngrok-free.app

# Terminal 2: Expose HTTP API (port 8000)
ngrok http 8000

# Copy the URL (e.g., https://xyz789.ngrok-free.app)
# Set PUBLIC_WEBHOOK_URL=https://xyz789.ngrok-free.app/webhooks/recall
```

### Step 4: Run the Agent

```powershell
# Option 1: Auto-join a specific meeting
python -m src.recall_main --meeting-url "https://meet.google.com/bor-hhdy-gfv" --bot-name "Jarvis"

# Option 2: Just start servers (join via API later)
python -m src.recall_main --no-auto-join

# Option 3: Set meeting in .env.local and run
# TEST_MEETING_URL=https://meet.google.com/bor-hhdy-gfv
python -m src.recall_main
```

## 📡 API Usage

Once the server is running, you can control it via REST API:

### Join a Meeting

```bash
curl -X POST http://localhost:8000/meet/join \
  -H "Content-Type: application/json" \
  -d '{
    "meeting_url": "https://meet.google.com/bor-hhdy-gfv",
    "bot_name": "Jarvis"
  }'
```

Response:

```json
{
  "bot_id": "abc123-def456",
  "meeting_url": "https://meet.google.com/bor-hhdy-gfv",
  "bot_name": "Jarvis",
  "status": "joining_call",
  "message": "Bot is joining the meeting..."
}
```

### Check Bot Status

```bash
curl http://localhost:8000/meet/abc123-def456
```

### Leave Meeting

```bash
curl -X DELETE http://localhost:8000/meet/abc123-def456
```

### List All Sessions

```bash
curl http://localhost:8000/sessions
```

## 🎤 How It Works

### Real-Time Flow

1. **Bot Joins Meeting** (3-4 seconds)
   - Recall.ai bot appears as "Jarvis" in Google Meet
   - WebSocket connection established to your server
2. **Participant Speaks**
   - Recall.ai captures audio
   - Transcription streamed to your WebSocket server
   - Partial results arrive in real-time (~200ms latency)
3. **Silence Detection**
   - System detects 1.5 seconds of silence
   - Accumulated transcript sent to LLM
4. **LLM Processing** (2-3 seconds)
   - Your LLM generates response
   - Can use custom API or OpenRouter/Gemini
5. **TTS Generation** (~500ms)
   - Google Cloud TTS converts response to MP3
   - Audio encoded to base64
6. **Bot Responds**
   - Audio sent to Recall.ai via Output Audio API
   - Bot speaks in Google Meet
   - Participants hear the response

**Total Latency: 4-6 seconds** (acceptable for conversational AI)

## 🔧 Configuration Options

### LLM Integration

You can use:

1. **Your Custom LLM API** (FastAPI backend)

   ```bash
   AGENT_API_URL=http://localhost:8080/api/chat
   ```

2. **OpenRouter** (Gemini/GPT/Claude)

   ```bash
   OPENROUTER_API_KEY=sk-or-...
   ```

3. **Direct Integration**
   - Modify `session_coordinator._generate_llm_response()`
   - Use OpenAI SDK, Anthropic SDK, etc.

### Transcription Provider

Currently using `recallai_streaming` (Recall's built-in). Can switch to:

- `assembly_ai_async_chunked`
- `aws_transcribe_streaming`
- `deepgram_streaming`

Modify in `recall_manager.py`:

```python
"transcript": {
    "provider": {
        "assembly_ai_async_chunked": {}  # or other provider
    }
}
```

### Silence Detection Tuning

In `session_coordinator.py`:

```python
self.silence_threshold = 1.5  # Seconds before processing
self.min_utterance_length = 3  # Minimum characters to process
```

## 📊 Event Types

### Transcript Events

```python
# Final transcript
{
  "event": "transcript.data",
  "data": {
    "data": {
      "words": [{"text": "hello", ...}],
      "participant": {"id": 1, "name": "John"}
    }
  }
}

# Partial transcript (real-time)
{
  "event": "transcript.partial_data",
  ...
}
```

### Participant Events

```python
# Someone joins
{
  "event": "participant_events.join",
  "data": {
    "data": {
      "participant": {"id": 2, "name": "Alice"}
    }
  }
}

# Speech detection
{
  "event": "participant_events.speech_on",
  ...
}
```

## 🐛 Debugging

### Check Logs

The system logs all events:

```
[INFO] recall-manager: Bot created successfully: abc123
[INFO] recall-websocket: Bot abc123 connected via WebSocket
[INFO] session-coordinator: 🎤 Processing utterance from User: hello
[INFO] session-coordinator: 🤖 Generated response: Hi there!
[INFO] session-coordinator: ✅ Audio response sent successfully
```

### Common Issues

1. **"Recall manager not initialized"**

   - Make sure `recall_main.py` is running
   - Check that RECALL_API_KEY is set

2. **WebSocket not connecting**

   - Verify PUBLIC_WEBSOCKET_URL is accessible
   - Check ngrok is running on port 8765
   - Test: `curl https://your-ngrok-url.ngrok-free.app`

3. **No audio output**

   - Check Google Cloud credentials are valid
   - Verify TTS is enabled in your GCP project
   - Check bot has `automatic_audio_output` configured

4. **Bot not joining meeting**
   - Check meeting URL format: `https://meet.google.com/xxx-xxxx-xxx`
   - For private meetings, may need authenticated Google Meet bot
   - Check Recall.ai dashboard for bot status

### Test WebSocket Locally

```python
import asyncio
import websockets
import json

async def test():
    uri = "ws://localhost:8765"
    async with websockets.connect(uri) as ws:
        # Send test event
        event = {
            "event": "transcript.data",
            "data": {
                "data": {
                    "words": [{"text": "hello"}],
                    "participant": {"id": 1, "name": "Test"}
                },
                "bot": {"id": "test-bot"}
            }
        }
        await ws.send(json.dumps(event))

asyncio.run(test())
```

## 📈 Production Deployment

For production:

1. **Deploy on Cloud Server** (AWS, GCP, Azure)

   ```bash
   # Use proper domain instead of ngrok
   PUBLIC_WEBSOCKET_URL=wss://voicebot.yourdomain.com
   PUBLIC_WEBHOOK_URL=https://api.yourdomain.com/webhooks/recall
   ```

2. **Use Process Manager**

   ```bash
   # Install pm2
   npm install -g pm2

   # Start service
   pm2 start "python -m src.recall_main" --name recall-agent
   pm2 startup
   pm2 save
   ```

3. **Reverse Proxy (Nginx)**

   ```nginx
   # WebSocket
   location /ws {
       proxy_pass http://localhost:8765;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
   }

   # HTTP API
   location / {
       proxy_pass http://localhost:8000;
   }
   ```

4. **SSL/TLS**
   - Use Let's Encrypt for SSL certificates
   - Recall.ai requires HTTPS for webhooks
   - WebSocket must use WSS (secure)

## 🔐 Security

- **Webhook Verification**: Add secret token to webhook URL

  ```bash
  PUBLIC_WEBHOOK_URL=https://api.yourdomain.com/webhooks/recall?token=your-secret
  ```

- **API Authentication**: Add middleware to FastAPI

  ```python
  from fastapi import Depends, HTTPException, Header

  async def verify_token(x_token: str = Header(...)):
      if x_token != "your-secret":
          raise HTTPException(status_code=403)

  @app.post("/meet/join", dependencies=[Depends(verify_token)])
  ```

## 📝 Next Steps

1. **Add Chat Commands**: Detect specific commands in transcript
2. **Multi-Language Support**: Use language detection in TTS
3. **Conversation Memory**: Store history in database
4. **Screen Sharing**: Use Recall's video output API
5. **Meeting Analytics**: Track participation, sentiment analysis
6. **Custom Prompts**: Per-meeting AI personalities

## 🆘 Support

- Recall.ai Docs: https://docs.recall.ai/
- Recall.ai Support: https://docs.recall.ai/docs/how-to-get-support
- Issues: Create issue in your repo

## 📄 License

Same as your existing project.
