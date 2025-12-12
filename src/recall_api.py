"""
FastAPI Server - HTTP API for meeting management and Recall.ai webhooks.
"""
import asyncio
import logging
import os
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import json
from recall_livekit_bridge import RecallLiveKitBridge
from speech_trigger_agent import agent as speech_agent

logger = logging.getLogger("recall-api")
logger.setLevel(logging.INFO)


# ============================================================================
# Pydantic Models
# ============================================================================

class JoinMeetingRequest(BaseModel):
    """Request to join a meeting"""
    meeting_url: str = Field(..., description="Google Meet URL")
    bot_name: str = Field(default="Jarvis", description="Bot display name")
    enable_transcription: bool = Field(default=True)
    enable_audio_streaming: bool = Field(default=True)


class JoinMeetingResponse(BaseModel):
    """Response from joining a meeting"""
    bot_id: str
    meeting_url: str
    bot_name: str
    status: str
    message: str


class BotStatusResponse(BaseModel):
    """Bot status response"""
    bot_id: str
    status: str
    meeting_url: Optional[str] = None
    joined_at: Optional[str] = None
    metadata: Optional[dict] = None


class LeaveMeetingResponse(BaseModel):
    """Response from leaving a meeting"""
    bot_id: str
    success: bool
    message: str


class SendMessageRequest(BaseModel):
    """Request to send a chat message"""
    message: str


class WebhookEvent(BaseModel):
    """Recall.ai webhook event"""
    event: str
    data: dict


# ============================================================================
# Global State (will be initialized by session coordinator)
# ============================================================================

# These will be set by the session coordinator when the app starts
recall_manager = None
session_coordinator = None


def set_recall_manager(manager):
    """Set the Recall.ai bot manager instance"""
    global recall_manager
    recall_manager = manager


def set_session_coordinator(coordinator):
    """Set the session coordinator instance"""
    global session_coordinator
    session_coordinator = coordinator


# ============================================================================
# FastAPI Application
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler"""
    logger.info("Starting Recall.ai API server...")
    yield
    logger.info("Shutting down Recall.ai API server...")


app = FastAPI(
    title="Recall.ai Voice Agent API",
    description="API for managing voice AI bots in Google Meet meetings",
    version="1.0.0",
    lifespan=lifespan,
)


# ============================================================================
# Meeting Management Endpoints
# ============================================================================

@app.post("/meet/join", response_model=JoinMeetingResponse)
async def join_meeting(request: JoinMeetingRequest, background_tasks: BackgroundTasks):
    """
    Join a Google Meet meeting with a voice AI bot.
    
    The bot will:
    1. Join the meeting as a participant
    2. Listen to conversation via real-time transcription
    3. Process speech through the AI pipeline
    4. Respond via voice output
    """
    if not recall_manager:
        raise HTTPException(status_code=503, detail="Recall manager not initialized")
    
    try:
        # Create and join the bot
        bot_session = await recall_manager.create_bot(
            meeting_url=request.meeting_url,
            bot_name=request.bot_name,
            enable_transcription=request.enable_transcription,
            enable_audio_streaming=request.enable_audio_streaming,
        )
        
        # Register with session coordinator if available
        if session_coordinator:
            background_tasks.add_task(
                session_coordinator.register_session,
                bot_session.bot_id,
                request.meeting_url,
            )
        
        return JoinMeetingResponse(
            bot_id=bot_session.bot_id,
            meeting_url=request.meeting_url,
            bot_name=request.bot_name,
            status=bot_session.status.value,
            message="Bot is joining the meeting. It may take a few seconds to connect.",
        )
        
    except Exception as e:
        logger.error(f"Failed to join meeting: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/meet/{bot_id}", response_model=BotStatusResponse)
async def get_meeting_status(bot_id: str):
    """Get the status of a bot in a meeting"""
    if not recall_manager:
        raise HTTPException(status_code=503, detail="Recall manager not initialized")
    
    try:
        status_data = await recall_manager.get_bot_status(bot_id)
        session = recall_manager.get_session(bot_id)
        
        # Get the latest status
        status_changes = status_data.get("status_changes", [])
        current_status = status_changes[-1].get("code", "unknown") if status_changes else "unknown"
        
        return BotStatusResponse(
            bot_id=bot_id,
            status=current_status,
            meeting_url=session.meeting_url if session else None,
            joined_at=session.joined_at.isoformat() if session and session.joined_at else None,
            metadata=status_data,
        )
        
    except Exception as e:
        logger.error(f"Failed to get bot status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/meet/{bot_id}", response_model=LeaveMeetingResponse)
async def leave_meeting(bot_id: str):
    """Remove the bot from the meeting"""
    if not recall_manager:
        raise HTTPException(status_code=503, detail="Recall manager not initialized")
    
    try:
        success = await recall_manager.leave_meeting(bot_id)
        
        # Cleanup session coordinator
        if session_coordinator:
            await session_coordinator.cleanup_session(bot_id)
        
        return LeaveMeetingResponse(
            bot_id=bot_id,
            success=success,
            message="Bot has left the meeting" if success else "Failed to leave meeting",
        )
        
    except Exception as e:
        logger.error(f"Failed to leave meeting: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/meet/{bot_id}/chat")
async def send_chat_message(bot_id: str, request: SendMessageRequest):
    """Send a chat message in the meeting"""
    if not recall_manager:
        raise HTTPException(status_code=503, detail="Recall manager not initialized")
    
    try:
        success = await recall_manager.send_chat_message(bot_id, request.message)
        return {"success": success, "message": "Chat message sent" if success else "Failed to send"}
    except Exception as e:
        logger.error(f"Failed to send chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Webhook Endpoints (for Recall.ai lifecycle events)
# ============================================================================

@app.post("/webhooks/recall")
async def recall_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive webhook events from Recall.ai.
    
    Events include:
    - bot.status_change: Bot status updates (joining, in_call, done, etc.)
    - recording.done: Recording completed
    - transcript.done: Transcription completed
    """
    try:
        body = await request.json()
        event_type = body.get("event", "")
        event_data = body.get("data", {})
        
        logger.info(f"Received webhook: {event_type}")
        logger.debug(f"Webhook data: {body}")
        
        # Handle bot status changes
        if event_type == "bot.status_change":
            bot_id = event_data.get("bot", {}).get("id")
            status = event_data.get("status", {}).get("code")
            
            if bot_id and recall_manager:
                recall_manager.update_session_status(bot_id, status)
                
                # Notify session coordinator
                if session_coordinator:
                    background_tasks.add_task(
                        session_coordinator.handle_status_change,
                        bot_id,
                        status,
                    )
            
            logger.info(f"Bot {bot_id} status changed to: {status}")
        
        return {"received": True, "event": event_type}
        
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return JSONResponse(
            status_code=200,  # Always return 200 to prevent retries
            content={"received": True, "error": str(e)},
        )


# ============================================================================
# Health & Status Endpoints
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "recall_manager": recall_manager is not None,
        "session_coordinator": session_coordinator is not None,
    }


@app.get("/sessions")
async def list_sessions():
    """List all active bot sessions"""
    if not recall_manager:
        return {"sessions": []}
    
    sessions = []
    for bot_id, session in recall_manager.sessions.items():
        sessions.append({
            "bot_id": bot_id,
            "meeting_url": session.meeting_url,
            "bot_name": session.bot_name,
            "status": session.status.value,
            "created_at": session.created_at.isoformat(),
            "joined_at": session.joined_at.isoformat() if session.joined_at else None,
        })
    
    return {"sessions": sessions}


# ============================================================================
# WebSocket Endpoint for Recall.ai Real-time Events
# ============================================================================

@app.websocket("/")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for Recall.ai real-time events.
    Receives transcript, audio, and participant events.
    """
    await websocket.accept()
    logger.info(f"WebSocket connection accepted from {websocket.client}")
    
    bot_id = None
    
    try:
        while True:
            # Receive message from Recall.ai
            message = await websocket.receive_text()
            
            try:
                data = json.loads(message)
                event_type = data.get("event", "")
                event_data = data.get("data", {})
                
                # DEBUG: Log ALL events
                logger.info(f"📥 WebSocket Event: {event_type}")
                
                # Log data size for audio events
                if event_type == "audio_mixed_raw.data":
                    audio_data = event_data.get("audio", "")
                    logger.info(f"🔊 AUDIO EVENT - Size: {len(audio_data)} bytes")
                
                logger.debug(f"Event data: {json.dumps(event_data, indent=2)[:500]}")
                
                # Extract bot_id from the event
                if not bot_id:
                    bot_info = event_data.get("bot", {})
                    bot_id = bot_info.get("id")
                    if bot_id:
                        logger.info(f"Bot {bot_id} connected via WebSocket")
                
                # Forward to session coordinator
                if session_coordinator and bot_id:
                    if event_type == "transcript.data":
                        logger.info(f"🎤 TRANSCRIPT EVENT (final): {event_data}")
                        await session_coordinator._handle_transcript(bot_id, event_data, is_partial=False)
                    elif event_type == "transcript.partial_data":
                        logger.info(f"🎤 TRANSCRIPT EVENT (partial): {event_data}")
                        await session_coordinator._handle_transcript(bot_id, event_data, is_partial=True)
                    elif event_type == "audio_mixed_raw.data":
                        # NEW: Handle raw audio stream for LiveKit processing
                        logger.debug(f"🔊 AUDIO EVENT: Received raw audio chunk")
                        await session_coordinator._handle_audio_chunk(bot_id, event_data)
                    elif event_type.startswith("participant_events."):
                        event_name = event_type.replace("participant_events.", "")
                        logger.info(f"Participant event: {event_name} for bot {bot_id}")
                        
                        # NEW: Trigger speech agent on speech_on events
                        if event_name == "speech_on" and bot_id:
                            logger.info(f"🎤 Triggering speech response for bot {bot_id}")
                            # Run in background to avoid blocking WebSocket
                            asyncio.create_task(speech_agent.handle_speech_event(bot_id, is_speech_on=True))
                    else:
                        logger.debug(f"Received event: {event_type}")
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse WebSocket message: {e}")
            except Exception as e:
                logger.error(f"Error processing WebSocket event: {e}", exc_info=True)
                
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for bot {bot_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        if bot_id:
            logger.info(f"Cleaning up WebSocket for bot {bot_id}")


# ============================================================================
# Run Server (for standalone usage)
# ============================================================================

def run_api_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the FastAPI server"""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_api_server()
