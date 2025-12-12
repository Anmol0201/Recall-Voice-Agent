# 🏗️ Recall.ai Voice Agent - Architecture Plan

## System Overview

This document details the complete architecture for integrating your LiveKit voice agent with Google Meet via Recall.ai.

---

## 🎯 Design Goals

✅ **Real-time voice interaction** in Google Meet  
✅ **Preserve existing LLM pipeline** (minimal changes)  
✅ **Low latency** (< 6 seconds response time)  
✅ **Scalable architecture** (multiple meetings support)  
✅ **Robust error handling** (reconnection, failures)

---

## 📊 Component Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         GOOGLE MEET CALL                             │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐   │
│  │ Participant│  │ Participant│  │ Participant│  │  Recall.ai │   │
│  │    User    │  │    Alice   │  │     Bob    │  │  Bot       │   │
│  │            │  │            │  │            │  │  "Jarvis"  │   │
│  └────────────┘  └────────────┘  └────────────┘  └────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                      ↑                    ↑
                                      │ Audio              │ Audio
                                      │ Capture            │ Output
                                      ↓                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│                       RECALL.AI CLOUD SERVICE                        │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Bot Instance (ID: abc123)                                   │  │
│  │  - Status: in_call_recording                                 │  │
│  │  - Audio capture (16kHz PCM)                                 │  │
│  │  - Real-time transcription (Recall AI Streaming)             │  │
│  │  - Audio output via MP3                                      │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                      ↑                                  ↑
                      │ WebSocket                        │ HTTP API
                      │ (Transcripts)                    │ (Control)
                      ↓                                  ↓
┌─────────────────────────────────────────────────────────────────────┐
│                      YOUR APPLICATION SERVER                         │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  RECALL WEBSOCKET SERVER (Port 8765)                       │    │
│  │  - Receives: transcript.data, participant_events           │    │
│  │  - Handles: Real-time event routing                        │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                ↓                                     │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  SESSION COORDINATOR                                       │    │
│  │  - Accumulates transcripts                                 │    │
│  │  - Detects silence (1.5s threshold)                        │    │
│  │  - Manages conversation context                            │    │
│  │  - Orchestrates AI pipeline                                │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                ↓                                     │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  LLM PROCESSING                                            │    │
│  │  - Your custom LLM API OR                                  │    │
│  │  - OpenRouter (Gemini/GPT/Claude) OR                       │    │
│  │  - Direct LangChain integration                            │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                ↓                                     │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  GOOGLE CLOUD TTS                                          │    │
│  │  - Text → MP3 audio                                        │    │
│  │  - Base64 encoding                                         │    │
│  │  - Voice: Female, en-IN                                    │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                ↓                                     │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  RECALL BOT MANAGER                                        │    │
│  │  - Sends audio via output_audio API                        │    │
│  │  - Manages bot lifecycle                                   │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  FASTAPI HTTP SERVER (Port 8000)                           │    │
│  │  - POST /meet/join - Join meeting                          │    │
│  │  - GET /meet/{bot_id} - Get status                         │    │
│  │  - DELETE /meet/{bot_id} - Leave meeting                   │    │
│  │  - POST /webhooks/recall - Receive status updates          │    │
│  └────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🔄 Data Flow Sequence

### Phase 1: Meeting Join (3-4 seconds)

```
1. User/API → POST /meet/join
   ↓
2. RecallBotManager → POST https://api.recall.ai/v1/bot/
   {
     "meeting_url": "https://meet.google.com/xxx",
     "bot_name": "Jarvis",
     "recording_config": {
       "transcript": {"provider": {"recallai_streaming": {}}},
       "realtime_endpoints": [{
         "type": "websocket",
         "url": "wss://your-server.com/ws"
       }]
     }
   }
   ↓
3. Recall.ai creates bot → Joins Google Meet
   ↓
4. Recall.ai → WebSocket connection to your server
   ↓
5. SessionCoordinator → Session state = ACTIVE
```

### Phase 2: Conversation Loop (continuous)

```
PARTICIPANT SPEAKS:
1. Google Meet → Audio to Recall.ai bot
   ↓
2. Recall.ai → Real-time transcription
   ↓
3. Recall.ai → WebSocket → Your server
   Event: transcript.partial_data (every ~200ms)
   ↓
4. RecallWebSocketServer → SessionCoordinator
   ↓
5. SessionCoordinator → Accumulates transcript
   ↓
6. [1.5 seconds of silence detected]
   ↓
7. SessionCoordinator → Process utterance
   ↓
8. SessionCoordinator → Your LLM API
   POST http://localhost:8080/api/chat
   {
     "user_query": "What's the weather?",
     "chat_history": [...]
   }
   ↓
9. LLM → Streaming response (2-3 sec)
   ↓
10. SessionCoordinator → Google Cloud TTS
    ↓
11. TTS → MP3 audio (Base64) (500ms)
    ↓
12. SessionCoordinator → RecallBotManager
    ↓
13. RecallBotManager → POST https://api.recall.ai/v1/bot/{id}/output_audio/
    {
      "kind": "mp3",
      "b64_data": "..."
    }
    ↓
14. Recall.ai bot → Speaks in Google Meet
    ↓
15. Participants hear response
```

### Phase 3: Meeting End

```
1. User → DELETE /meet/{bot_id}
   OR
   Bot auto-leaves (no participants)
   ↓
2. RecallBotManager → POST https://api.recall.ai/v1/bot/{id}/leave_call/
   ↓
3. Recall.ai → Webhook: bot.status_change (done)
   ↓
4. SessionCoordinator → Cleanup session
```

---

## 🗂️ Module Responsibilities

### 1. **recall_manager.py** - Recall.ai API Client

**Purpose:** Interface with Recall.ai REST API  
**Functions:**

- `create_bot(meeting_url, bot_name)` → Join meeting
- `get_bot_status(bot_id)` → Check bot status
- `output_audio(bot_id, mp3_b64)` → Send audio to meeting
- `leave_meeting(bot_id)` → Exit meeting
- `send_chat_message(bot_id, message)` → Send chat

**State Management:**

- Tracks bot sessions (bot_id → BotSession)
- Bot status (ready, joining, in_call, done)
- Meeting metadata

### 2. **recall_websocket.py** - Event Stream Handler

**Purpose:** Receive real-time events from Recall.ai  
**Events Handled:**

- `transcript.data` → Final transcript utterance
- `transcript.partial_data` → Partial (streaming) transcript
- `participant_events.join` → Someone joined
- `participant_events.leave` → Someone left
- `participant_events.speech_on` → Started speaking
- `participant_events.speech_off` → Stopped speaking
- `audio_mixed_raw.data` → Raw audio buffer (optional)

**Processing:**

- Parses JSON events
- Extracts participant info
- Routes to SessionCoordinator callbacks

### 3. **session_coordinator.py** - AI Pipeline Orchestrator

**Purpose:** Complete voice AI workflow  
**Responsibilities:**

- Accumulate partial transcripts
- Detect silence (1.5s threshold)
- Trigger LLM processing
- Convert response to audio
- Send back to Recall.ai

**Conversation Management:**

- Maintain chat history (last 10 messages)
- Track current speaker
- Prevent overlapping responses
- Session state machine

### 4. **recall_api.py** - HTTP API Server

**Purpose:** User-facing REST API  
**Endpoints:**

- `POST /meet/join` → Join meeting
- `GET /meet/{bot_id}` → Get status
- `DELETE /meet/{bot_id}` → Leave meeting
- `POST /meet/{bot_id}/chat` → Send chat
- `POST /webhooks/recall` → Receive webhooks
- `GET /sessions` → List all sessions

**Integration:**

- Uses RecallBotManager for bot control
- Uses SessionCoordinator for session management
- Handles webhook events from Recall.ai

### 5. **recall_main.py** - Application Entry Point

**Purpose:** Bootstrap and coordinate all services  
**Functions:**

- Initialize all components
- Start WebSocket server (port 8765)
- Start HTTP API server (port 8000)
- Handle graceful shutdown
- CLI interface

---

## 📡 API Integration Points

### Recall.ai REST API

```
Base URL: https://us-east-1.recall.ai/api/v1/

POST /bot/
  - Create bot and join meeting
  - Returns: bot_id, status

GET /bot/{id}/
  - Get bot details and status
  - Returns: full bot object

POST /bot/{id}/output_audio/
  - Send audio to speak in meeting
  - Body: {"kind": "mp3", "b64_data": "..."}

POST /bot/{id}/leave_call/
  - Remove bot from meeting

POST /bot/{id}/send_chat_message/
  - Send text chat in meeting
  - Body: {"message": "..."}
```

### Your LLM API (Custom)

```
POST http://localhost:8080/api/chat
{
  "userid": "recall_voice_agent",
  "chat_history": [
    {"role": "user", "content": "previous message"},
    {"role": "assistant", "content": "previous response"}
  ],
  "user_query": "current question",
  "mode": "brief",
  "source": "voice"
}

Response: Streaming text chunks
```

### OpenRouter (Fallback)

```
POST https://openrouter.ai/api/v1/chat/completions
{
  "model": "google/gemini-2.0-flash-001",
  "messages": [
    {"role": "system", "content": "You are Jarvis..."},
    {"role": "user", "content": "question"}
  ]
}

Response: {"choices": [{"message": {"content": "answer"}}]}
```

### Google Cloud TTS

```python
from google.cloud import texttospeech

client = texttospeech.TextToSpeechClient()
response = client.synthesize_speech(
    input=SynthesisInput(text="Hello"),
    voice=VoiceSelectionParams(language_code="en-IN"),
    audio_config=AudioConfig(audio_encoding=AudioEncoding.MP3)
)

audio_b64 = base64.b64encode(response.audio_content)
```

---

## 🔐 Configuration Requirements

### Environment Variables

```bash
# Recall.ai
RECALL_API_KEY=sk_live_...           # Required
RECALL_REGION=us-east-1              # us-east-1, us-west-2, eu-west-1

# Public endpoints (for Recall.ai to reach you)
PUBLIC_WEBSOCKET_URL=wss://...       # Required - WebSocket endpoint
PUBLIC_WEBHOOK_URL=https://...       # Optional - Status webhooks

# Bot settings
BOT_NAME=Jarvis                      # Display name in meeting

# LLM
AGENT_API_URL=http://localhost:8080  # Your custom LLM (optional)
OPENROUTER_API_KEY=sk-or-...         # Fallback LLM (optional)

# Google Cloud
GOOGLE_CREDENTIALS_FILE=creds.json   # Required for TTS

# Server ports
WEBSOCKET_PORT=8765                  # Default: 8765
API_PORT=8000                        # Default: 8000
```

### Required Services

1. **Recall.ai Account** - https://www.recall.ai/
2. **Google Cloud Project** - Enable Text-to-Speech API
3. **ngrok** (dev) or **Public server** (prod) - Expose local ports

### Optional Services

1. **Your LLM API** - Custom backend
2. **OpenRouter** - Fallback LLM provider
3. **Redis** - Session state (for multi-instance)
4. **PostgreSQL** - Conversation history persistence

---

## ⚡ Performance Optimization

### Latency Breakdown

```
Bot join meeting:        3-4 sec  (one-time)
Transcript (partial):    ~200ms   (continuous)
Silence detection:       1.5 sec  (configurable)
LLM processing:          2-3 sec  (depends on model)
TTS generation:          500ms-1s (depends on length)
Audio transmission:      ~200ms   (network)
────────────────────────────────
Total response time:     4-6 sec  (acceptable)
```

### Optimization Strategies

1. **Reduce Silence Threshold**

   ```python
   self.silence_threshold = 1.0  # From 1.5 to 1.0 seconds
   ```

2. **Use Faster LLM**

   ```python
   # Gemini Flash instead of Pro
   model="google/gemini-2.0-flash-001"
   ```

3. **Preemptive Processing**

   ```python
   # Start LLM processing on partial transcripts
   # Cancel if user continues speaking
   ```

4. **Cache Common Responses**

   ```python
   # Cache TTS for common phrases
   tts_cache = {
       "Hello": "cached_mp3_b64_...",
       "How can I help?": "cached_mp3_b64_..."
   }
   ```

5. **Streaming TTS** (future)
   ```python
   # Stream audio chunks as they're generated
   # Start speaking before full response is ready
   ```

---

## 🛡️ Error Handling

### Connection Failures

```python
# Retry logic for Recall.ai API
max_retries = 3
retry_delay = 5  # seconds

for attempt in range(max_retries):
    try:
        response = await recall_manager.create_bot(...)
        break
    except aiohttp.ClientError as e:
        if attempt < max_retries - 1:
            await asyncio.sleep(retry_delay)
        else:
            raise
```

### WebSocket Disconnection

```python
# Automatic reconnection
# Recall.ai has built-in retry: 30 attempts, 3-second intervals
# If all fail, webhook notifies you: realtime_endpoint.failed
```

### LLM Timeout

```python
# Set timeout
async with aiohttp.ClientSession() as session:
    async with session.post(
        llm_url,
        json=payload,
        timeout=aiohttp.ClientTimeout(total=30)  # 30 seconds
    ) as response:
        ...
```

### TTS Failure

```python
# Fallback to simple response
if not audio_b64:
    await recall_manager.send_chat_message(
        bot_id,
        "I'm sorry, I had trouble generating audio. Here's my text response: ..."
    )
```

---

## 📈 Scalability Considerations

### Single Instance Limits

- **WebSocket connections:** ~100 concurrent (per server)
- **HTTP requests:** ~1000 req/sec (with uvicorn)
- **Memory:** ~100MB per active session
- **CPU:** ~10% per active session (during LLM processing)

### Multi-Instance Architecture (Future)

```
                ┌─── Instance 1 (8765, 8000)
Load Balancer ──┼─── Instance 2 (8766, 8001)
                └─── Instance 3 (8767, 8002)
                          ↓
                   Redis (shared state)
                          ↓
                   PostgreSQL (history)
```

### State Management

```python
# Use Redis for distributed sessions
import redis.asyncio as redis

session_store = await redis.from_url("redis://localhost")
await session_store.set(f"session:{bot_id}", json.dumps(session_data))
```

---

## 🧪 Testing Strategy

### Unit Tests

```python
# Test individual components
pytest tests/test_recall_manager.py
pytest tests/test_websocket_server.py
pytest tests/test_session_coordinator.py
```

### Integration Tests

```python
# Test full pipeline with mock Recall.ai
pytest tests/test_integration.py
```

### End-to-End Tests

```python
# Join real Google Meet, verify bot behavior
pytest tests/test_e2e.py --meeting-url "https://meet.google.com/test"
```

---

## 🚀 Deployment Options

### Option 1: Single Server (Simple)

```
Ubuntu/Debian server
- Install Python 3.9+
- Install dependencies
- Run with PM2/systemd
- Use Nginx reverse proxy
- SSL with Let's Encrypt
```

### Option 2: Docker (Portable)

```dockerfile
FROM python:3.11-slim
COPY . /app
WORKDIR /app
RUN pip install -e .
CMD ["python", "-m", "src.recall_main"]
```

### Option 3: Cloud Run (Serverless)

```
Google Cloud Run
- Auto-scaling
- Pay per use
- Managed SSL
- Built-in load balancing
```

### Option 4: Kubernetes (Enterprise)

```
Kubernetes cluster
- High availability
- Auto-scaling
- Multi-region
- Advanced monitoring
```

---

## 📊 Monitoring & Logging

### Metrics to Track

```python
# Response times
avg_llm_latency = sum(llm_times) / len(llm_times)
avg_tts_latency = sum(tts_times) / len(tts_times)
avg_total_latency = sum(total_times) / len(total_times)

# Session stats
active_sessions = len(coordinator.sessions)
total_utterances_processed = counter
total_responses_sent = counter

# Errors
llm_failures = counter
tts_failures = counter
audio_send_failures = counter
```

### Logging Structure

```python
import logging

# Structured logging
logger.info(
    "LLM processing completed",
    extra={
        "bot_id": bot_id,
        "utterance_length": len(text),
        "response_length": len(response),
        "latency_ms": latency,
    }
)
```

---

## ✅ Success Metrics

Integration is successful when:

1. ✅ Bot joins meeting within 5 seconds
2. ✅ Real-time transcripts appear in logs (<500ms latency)
3. ✅ Bot responds to questions accurately
4. ✅ Total response time < 6 seconds (P95)
5. ✅ No connection drops during 30-minute meetings
6. ✅ Audio quality is clear and natural
7. ✅ Handles 3+ simultaneous speakers
8. ✅ Conversation context maintained across turns
9. ✅ Graceful error handling (fallbacks work)
10. ✅ API endpoints respond correctly

---

## 🎓 Implementation Timeline

**Phase 1: Core Setup** (COMPLETED ✅)

- [x] RecallBotManager
- [x] RecallWebSocketServer
- [x] SessionCoordinator
- [x] FastAPI server
- [x] Dependencies updated

**Phase 2: Testing** (NEXT)

- [ ] Install dependencies: `uv sync`
- [ ] Configure environment
- [ ] Test bot creation
- [ ] Test WebSocket connection
- [ ] Test full conversation flow

**Phase 3: Optimization** (FUTURE)

- [ ] Reduce latency (silence threshold, faster LLM)
- [ ] Add caching (TTS, common responses)
- [ ] Improve error handling
- [ ] Add conversation memory

**Phase 4: Production** (FUTURE)

- [ ] Deploy to cloud server
- [ ] Set up monitoring
- [ ] Configure SSL/domains
- [ ] Load testing
- [ ] Documentation

---

**This completes the architectural plan. All code is ready for testing!**
