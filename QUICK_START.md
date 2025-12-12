# 🚀 Quick Start Guide - Recall.ai Voice Agent

## ✅ Pre-Implementation Checklist

Before running, ensure you have:

- [ ] Recall.ai API key from https://www.recall.ai/
- [ ] Google Cloud credentials JSON file (for TTS)
- [ ] ngrok installed for local development (`choco install ngrok` or download)
- [ ] Meeting URL ready: `https://meet.google.com/bor-hhdy-gfv`

## 📋 Setup Steps (5 minutes)

### 1. Install Dependencies

```powershell
cd "d:\foodnest Testing\voice-agent\mochand-realtime"
uv sync
```

### 2. Configure Environment

```powershell
# Copy example config
cp .env.recall.example .env.local

# Edit .env.local and set:
# - RECALL_API_KEY=your_actual_key
# - BOT_NAME=Jarvis
# - GOOGLE_CREDENTIALS_FILE=google_credentials_file.json
```

### 3. Expose Servers (Development Only)

```powershell
# Terminal 1: WebSocket
ngrok http 8765
# Copy URL, e.g., https://abc123.ngrok-free.app
# Set in .env.local: PUBLIC_WEBSOCKET_URL=wss://abc123.ngrok-free.app

# Terminal 2: HTTP API
ngrok http 8000
# Copy URL, e.g., https://xyz789.ngrok-free.app
# Set in .env.local: PUBLIC_WEBHOOK_URL=https://xyz789.ngrok-free.app/webhooks/recall
```

### 4. Run the Agent

```powershell
python -m src.recall_main --meeting-url "https://meet.google.com/bor-hhdy-gfv"
```

## 🎯 What Happens Next

```
1. Bot joins as "Jarvis" in Google Meet [3-4 sec]
2. Participants see the bot in the call
3. When someone speaks:
   - Real-time transcript appears in logs
   - After 1.5s silence, LLM processes
   - Bot responds via voice [4-6 sec total]
4. Conversation continues...
```

## 🔍 Verify It's Working

### Check Logs

```
[INFO] RecallBotManager: Bot created successfully: abc123
[INFO] RecallWebSocketServer: Bot abc123 connected via WebSocket
[INFO] SessionCoordinator: Session abc123 is now ACTIVE
[INFO] SessionCoordinator: 🎤 Processing utterance from User: hello
[INFO] SessionCoordinator: 🤖 Generated response: Hi there! How can I help?
[INFO] SessionCoordinator: ✅ Audio response sent successfully
```

### Check Google Meet

- Look for "Jarvis" in participants list
- Speak and watch console logs
- Bot should respond after ~5 seconds

## 🎛️ Control via API

```bash
# Join meeting
curl -X POST http://localhost:8000/meet/join \
  -H "Content-Type: application/json" \
  -d '{"meeting_url": "https://meet.google.com/bor-hhdy-gfv"}'

# Check status
curl http://localhost:8000/meet/YOUR_BOT_ID

# Leave meeting
curl -X DELETE http://localhost:8000/meet/YOUR_BOT_ID

# List all sessions
curl http://localhost:8000/sessions
```

## 📊 File Structure

```
src/
├── recall_manager.py        # Bot creation & management
├── recall_websocket.py      # Real-time events receiver
├── recall_api.py            # HTTP REST API
├── session_coordinator.py   # AI pipeline orchestrator
└── recall_main.py           # Main entry point

.env.local                   # Your configuration (create this)
.env.recall.example          # Example configuration
RECALL_README.md             # Full documentation
QUICK_START.md              # This file
```

## 🐛 Troubleshooting

### Bot doesn't join

```
Problem: "Failed to create bot: 401"
Solution: Check RECALL_API_KEY is correct
```

### No WebSocket connection

```
Problem: "WebSocket connection failed"
Solution:
1. Check ngrok is running: ngrok http 8765
2. Verify PUBLIC_WEBSOCKET_URL uses wss:// (not ws://)
3. Test URL in browser: https://your-ngrok-url.ngrok-free.app
```

### No audio output

```
Problem: Bot doesn't speak
Solution:
1. Check GOOGLE_CREDENTIALS_FILE path is correct
2. Verify Google Cloud TTS API is enabled
3. Check logs for TTS errors
```

### Can't hear transcripts

```
Problem: No transcript events in logs
Solution:
1. Check bot status: curl http://localhost:8000/meet/BOT_ID
2. Should show status: "in_call_recording"
3. Check Recall.ai dashboard for bot status
```

## 💡 Tips

### Better Voice Quality

```python
# In session_coordinator.py, modify TTS settings:
voice = texttospeech.VoiceSelectionParams(
    language_code="en-US",  # Change to en-US for American accent
    name="en-US-Neural2-F",  # Specific voice
    ssml_gender=texttospeech.SsmlVoiceGender.FEMALE,
)

audio_config = texttospeech.AudioConfig(
    audio_encoding=texttospeech.AudioEncoding.MP3,
    speaking_rate=1.1,  # Faster speech
    pitch=2.0,          # Higher pitch
)
```

### Faster Responses

```python
# In session_coordinator.py:
self.silence_threshold = 1.0  # Reduce from 1.5 to 1.0 seconds
```

### Custom Greetings

```python
# In session_coordinator._handle_participant_event():
if event.event_type == "join":
    greeting = f"Welcome {event.participant_name} to the meeting!"
    await self._send_audio_response(bot_id, greeting)
```

## 📞 Test Conversation Example

```
You: "Hello Jarvis, what's the weather like?"
Bot: [5 seconds later] "I don't have access to real-time weather data,
     but I can help you with other tasks!"

You: "Tell me a joke"
Bot: [5 seconds later] "Why did the AI go to therapy?
     It had too many neural issues!"
```

## ⚡ Performance Benchmarks

| Operation               | Latency         |
| ----------------------- | --------------- |
| Bot join meeting        | 3-4 seconds     |
| Transcript (partial)    | ~200ms          |
| Transcript (final)      | ~500ms          |
| Silence detection       | 1.5 seconds     |
| LLM processing          | 2-3 seconds     |
| TTS generation          | 500ms-1s        |
| Audio transmission      | ~200ms          |
| **Total response time** | **4-6 seconds** |

## 🎓 Next Learning Steps

1. **Read** `RECALL_README.md` for full documentation
2. **Explore** Recall.ai docs: https://docs.recall.ai/
3. **Customize** prompts in `session_coordinator.py`
4. **Add features** like screen sharing, chat messages
5. **Deploy** to production with proper domain

## 🆘 Need Help?

1. Check logs for error messages
2. Read `RECALL_README.md` debugging section
3. Test each component individually
4. Check Recall.ai bot status in dashboard: https://www.recall.ai/

## ✅ Success Criteria

Your integration is working when:

- ✅ Bot appears in Google Meet as "Jarvis"
- ✅ Console shows real-time transcripts
- ✅ Bot responds to questions via voice
- ✅ Response time is under 6 seconds
- ✅ No errors in console logs

---

**Ready to go? Run:** `python -m src.recall_main --meeting-url "https://meet.google.com/bor-hhdy-gfv"`
