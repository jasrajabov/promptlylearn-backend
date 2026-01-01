from fastapi import APIRouter, HTTPException
from src.tasks.generate_chat_stream import generate_chat_stream_task


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/send")
async def send_message(payload: dict):
    """
    Enqueues a chat task. Returns task_id for frontend polling.
    """
    session_id = payload.get("session_id")
    message = payload.get("message")

    if not session_id or not message:
        raise HTTPException(status_code=400, detail="session_id and message required")

    task = generate_chat_stream_task.delay(session_id, message)
    return {"task_id": task.id}
