import os
import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from celery.result import AsyncResult
from src import deps

import redis

from src.models import Course, Roadmap, Status
from src.tasks.generate_quiz import generate_quiz_questions


redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.Redis.from_url(redis_url)
frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")


router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/status/{type}/{task_id}")
def task_status(type: str, task_id: str, db: Session = Depends(deps.get_db)):
    """
    TODO: Refactor to reduce duplication
    Check the status of a Celery task and update the corresponding Course or Roadmap status in DB.
    """
    if type == "course_outline":
        course = db.query(Course).filter(Course.task_id == task_id).first()
        if not course:
            return {"status": "UNKNOWN"}

        result = AsyncResult(task_id)
        if result.successful():
            course.status = Status.NOT_STARTED

            db.commit()
            db.refresh(course)
            return {"status": "SUCCESS", "course_id": course.id}

        if result.failed() and course.status != "FAILED":
            course.status = Status.FAILED
            db.commit()
            db.refresh(course)
            return {"status": "FAILURE"}

        # Task still running
        return {"status": course.status}
    if type == "roadmap_outline":
        roadmap = db.query(Roadmap).filter(Roadmap.task_id == task_id).first()
        if not roadmap:
            return {"status": "UNKNOWN"}

        result = AsyncResult(task_id)
        if result.successful():
            roadmap.status = Status.NOT_STARTED
            db.commit()
            db.refresh(roadmap)
            return {"status": "SUCCESS", "roadmap_id": roadmap.id}

        if result.failed() and roadmap.status != "FAILED":
            roadmap.status = Status.FAILED
            db.commit()
            db.refresh(roadmap)
            return {"status": "FAILURE"}

        # Task still running
        return {"status": roadmap.status}
    if type == "quiz_generation":
        data = redis_client.get(f"quiz:{task_id}")
        if data:
            return {"status": "done", "quiz": json.loads(data)}

        error = redis_client.get(f"quiz_error:{task_id}")
        if error:
            return {"status": "error", "detail": json.loads(error)}

        async_result = generate_quiz_questions.AsyncResult(task_id)
        return {"status": async_result.status.lower()}

    if type == "chat_stream":
        key = f"chat_result:{task_id}"
        print("Checking Redis key:", key)
        reply = redis_client.get(key)

        if reply is None:
            return {"status": "pending"}

        # Once frontend receives the answer -> delete cached result
        redis_client.delete(key)
        return {"status": "ready", "reply": reply}
