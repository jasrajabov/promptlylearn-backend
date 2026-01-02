import json
import os
import time
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import uuid

from src import deps
from src import models
from src import schema
from src.exceptions import NotEnoughCreditsException
from src.schema import CourseAllSchema, CourseSchema, LessonSchema, StatusUpdateSchema
from src.models import Course as CourseORM, Lesson as LessonORM, Status
from src.tasks.course_outline import generate_course_outline_task
from src.models import User
from src.utils.credit_helper import consume_credits
import redis

from sqlalchemy.orm import joinedload

from src.tasks.lesson_stream import generate_lesson_markdown_stream_task


router = APIRouter(prefix="/course", tags=["course"])

redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.Redis.from_url(redis_url)
frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")


@router.post("/generate-course-outline")
def generate_course_outline(
    payload: dict,
    db: Session = Depends(deps.get_db),
    user: User = Depends(deps.get_current_user),
):
    GENERATION_COST = 10
    consume_credits(user, db, GENERATION_COST)
    topic = payload["topic"]
    level = payload["level"]
    roadmap_node_id = payload.get("roadmap_node_id")
    roadmap_id = payload.get("roadmap_id")

    # 1. Create empty course entry
    course_id = str(uuid.uuid4())
    course = CourseORM(
        id=course_id,
        user_id=user.id,
        title=topic,
        description=None,
        level=level,
        status=Status.GENERATING,
        roadmap_node_id=roadmap_node_id,
        roadmap_id=roadmap_id,
    )

    db.add(course)
    db.commit()
    db.refresh(course)

    # 2. Launch Celery task
    task = generate_course_outline_task.delay(
        topic=topic,
        level=level,
        user_id=user.id,
        roadmap_node_id=roadmap_node_id,
        course_id=course_id,
    )

    # 3. Save task_id to DB
    course.task_id = task.id
    db.commit()

    # 4. Return immediately to UI
    return {"task_id": task.id, "course_id": course_id, "status": "GENERATING"}


@router.get("/get_all_courses", response_model=list[CourseAllSchema])
def get_all_courses(
    db: Session = Depends(deps.get_db), user: User = Depends(deps.get_current_user)
):
    print("Fetching courses for user ID:", user.id)
    courses = db.query(models.Course).filter(models.Course.user_id == user.id).all()
    res = []
    for course in courses:
        course_data = {
            "id": course.id,
            "title": course.title,
            "description": course.description,
            "level": course.level,
            "status": course.status,
            "created_at": course.created_at,
            "task_id": course.task_id,
            "modules": [
                {
                    "id": course_module.id,
                    "title": course_module.title,
                    "status": course_module.status,
                }
                for course_module in course.modules
            ],
        }
        print(f"Course ID: {course.id}, Title: {course.title}")
        res.append(course_data)
    return res


@router.get("/lessons/{lesson_id}", response_model=schema.LessonSchema)
async def get_lesson(
    lesson_id: str,
    db: Session = Depends(deps.get_db),
    user: User = Depends(deps.get_current_user),
):
    print("Fetching lesson for user ID:", user.id)
    lesson = (
        db.query(models.Lesson)
        .filter(models.Lesson.id == lesson_id, models.Lesson.user_id == user.id)
        .first()
    )
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    return LessonSchema.model_validate(lesson)


@router.get(
    "/{roadmap_id}/{roadmap_node_id}",
    response_model=list[CourseAllSchema],
)
def get_courses_by_roadmap(
    roadmap_id: str,
    roadmap_node_id: str,
    db: Session = Depends(deps.get_db),
    user: User = Depends(deps.get_current_user),
):
    courses = (
        db.query(models.Course)
        .filter(
            models.Course.user_id == user.id,
            models.Course.roadmap_id == roadmap_id,
            models.Course.roadmap_node_id == roadmap_node_id,
        )
        .all()
    )
    print("Roadmap node id:", roadmap_node_id)
    print("Course roadmap_node_ids:", [c.roadmap_node_id for c in courses])

    for course in courses:
        db.refresh(course)  # ← THIS FIXES STALE STATUS

    return [CourseAllSchema.model_validate(course) for course in courses]


@router.get("/{course_id}", response_model=CourseSchema)
async def get_course(
    course_id: str,
    db: Session = Depends(deps.get_db),
    user: User = Depends(deps.get_current_user),
):
    course = (
        db.query(models.Course)
        .options(joinedload(models.Course.modules).joinedload(models.Module.lessons))
        .filter(models.Course.id == course_id, models.Course.user_id == user.id)
        .first()
    )
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    return CourseSchema.model_validate(course)


@router.get("/lessons/{lesson_id}", response_model=schema.LessonSchema)
async def get_lesson(
    lesson_id: str,
    db: Session = Depends(deps.get_db),
    user: User = Depends(deps.get_current_user),
):
    print("Fetching lesson for user ID:", user.id)
    lesson = (
        db.query(models.Lesson)
        .filter(models.Lesson.id == lesson_id, models.Lesson.user_id == user.id)
        .first()
    )
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    return LessonSchema.model_validate(lesson)


@router.patch("/lessons/{lesson_id}/status")
def update_lesson_status(
    lesson_id: str,
    payload: StatusUpdateSchema,
    db: Session = Depends(deps.get_db),
    user: User = Depends(deps.get_current_user),
):
    lesson = (
        db.query(models.Lesson)
        .join(models.Module)
        .join(models.Course)
        .filter(models.Lesson.id == lesson_id, models.Course.user_id == user.id)
        .first()
    )
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    lesson.status = payload.status.upper()
    db.commit()
    db.refresh(lesson)
    return {"id": lesson.id, "status": lesson.status}


@router.patch("/modules/{module_id}/status")
async def update_module_status(
    module_id: str,
    payload: StatusUpdateSchema,
    db: Session = Depends(deps.get_db),
    user: User = Depends(deps.get_current_user),
):
    module = (
        db.query(models.Module)
        .join(models.Course)
        .filter(models.Module.id == module_id, models.Course.user_id == user.id)
        .first()
    )
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")

    module.status = payload.status
    db.commit()
    db.refresh(module)
    return {"id": module.id, "status": module.status}


@router.patch("/{course_id}/status")
def update_course_status(
    course_id: str,
    payload: StatusUpdateSchema,
    db: Session = Depends(deps.get_db),
    user: User = Depends(deps.get_current_user),
):
    course = (
        db.query(models.Course)
        .filter(models.Course.id == course_id, models.Course.user_id == user.id)
        .first()
    )
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    course.status = payload.status
    db.commit()
    db.refresh(course)
    return {"id": course.id, "status": course.status}


@router.delete("/{course_id}")
def delete_course(
    course_id: str,
    db: Session = Depends(deps.get_db),
    user: User = Depends(deps.get_current_user),
):
    course = (
        db.query(models.Course)
        .filter(models.Course.id == course_id, models.Course.user_id == user.id)
        .first()
    )
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    db.delete(course)
    db.commit()
    return {"detail": "Course deleted successfully"}


@router.delete("/lessons/{lesson_id}")
def delete_lesson(
    lesson_id: str,
    db: Session = Depends(deps.get_db),
    user: User = Depends(deps.get_current_user),
):
    lesson = (
        db.query(models.Lesson)
        .join(models.Module)
        .join(models.Course)
        .filter(models.Lesson.id == lesson_id, models.Course.user_id == user.id)
        .first()
    )
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    # delete only content blocks belonging to the lesson
    deleted_count = (
        db.query(models.ContentBlock)
        .filter(models.ContentBlock.lesson_id == lesson_id)
        .delete(synchronize_session=False)
    )
    db.commit()

    # refresh lesson to reflect emptied relationship if needed
    db.refresh(lesson)
    return {"detail": "Content blocks deleted", "deleted_count": deleted_count}


@router.post("/generate-lesson-stream")
async def generate_lesson_markdown_stream(
    request: Request, db: Session = Depends(deps.get_db)
):
    body = await request.json()
    token = body.get("token")
    course_id = body.get("course_id")
    module_id = body.get("module_id")
    lesson_id = body.get("lesson_id")

    user = deps.get_current_user(token=token, db=db)
    if not user:
        raise HTTPException(
            status_code=401, detail="Invalid authentication credentials"
        )

    GENERATION_COST = 20
    try:
        consume_credits(user, db, GENERATION_COST)
    except NotEnoughCreditsException:
        raise HTTPException(
            status_code=402,
            detail="Not enough credits. Upgrade to premium or wait for reset.",
            headers={"X-Error-Type": "NotEnoughCreditsException"},
        )

    lesson = db.query(LessonORM).filter_by(id=lesson_id).first()
    course = db.query(CourseORM).filter_by(id=course_id).first()
    if not lesson or not course:
        raise HTTPException(status_code=404, detail="Lesson or course not found")

    # Create unique stream ID
    stream_id = str(uuid.uuid4())
    channel = f"lesson_stream:{stream_id}"

    # Launch background task
    generate_lesson_markdown_stream_task.delay(
        lesson_id, module_id, course_id, stream_id
    )

    # Redis subscriber generator
    def event_stream():
        r = redis.Redis.from_url(redis_url)
        pubsub = r.pubsub()
        pubsub.subscribe(channel)

        for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            data = msg["data"].decode()

            if data == "[[STREAM_END]]":
                yield "\n\n"
                break
            elif data.startswith("[[ERROR]]"):
                yield f"event: error\n{json.dumps({'error': data})}\n\n"
                break
            else:
                yield f"{data}"
            time.sleep(0.01)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": frontend_url,
        },
    )
