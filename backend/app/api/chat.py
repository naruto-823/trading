from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.ai.chat import stream_chat
from app.db import get_db
from app.schemas.chat import ChatRequest

router = APIRouter()

@router.post("/chat")
async def chat(request: ChatRequest, db: Session = Depends(get_db)):
    messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]

    async def event_generator():
        async for event in stream_chat(messages, db):
            yield event

    return StreamingResponse(event_generator(), media_type="text/event-stream")