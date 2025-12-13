from celery_app import celery_app
import json
import os
import redis
from openai import OpenAI
from typing import List, Dict, Any

@celery_app.task(name="src.tasks.generate_chat_stream.generate_chat_stream_task", bind=True)
def generate_chat_stream_task(self, session_id: str, user_message: str):
    """
    Celery task to generate a chat reply for a given session and message.
    Saves the final reply and updates session chat history in Redis.
    """
    redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # Load existing chat history
    chat_key = f"course_chat:{session_id}"
    history_raw = redis_client.get(chat_key)
    history = json.loads(history_raw) if history_raw else []

    # Append user message to history
    history.append({"role": "user", "content": user_message})

    # Call OpenAI (non-streaming)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=history,
        stream=False,
    )

    reply = response.choices[0].message.content

    # Append assistant reply to history
    history.append({"role": "assistant", "content": reply})

    # Save updated history back to Redis
    redis_client.set(chat_key, json.dumps(history))

    # Save final reply keyed by task_id for frontend polling
    result_key = f"chat_result:{self.request.id}"
    print("result_key:", result_key)
    redis_client.set(result_key, reply)

    return {"status": "completed", "reply": reply}