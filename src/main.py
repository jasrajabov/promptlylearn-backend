from celery.result import AsyncResult
import uuid
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from openai import OpenAI
from src.schema import (
    CourseAllSchema,
    CourseSchema,
    GenerateQuizRequest,
    LessonSchema,
    RoadmapNodeCourseIdUpdate,
    RoadmapNodeResponse,
    StatusUpdateSchema,
    RoadmapRequest,
    RoadmapResponseSchema,
)
from src.models import Course, Roadmap, RoadmapNode, User
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from src.models import (
    Lesson as LessonORM,
    Course as CourseORM,
    Status,
)
import os
import time

from fastapi import Depends
from sqlalchemy.orm import Session, joinedload, selectinload
from src import models, schema, auth, deps
from src.database import engine, Base
import json
from src.tasks.lesson_stream import generate_lesson_markdown_stream_task
from src.tasks.course_outline import generate_course_outline_task
from src.tasks.generate_roadmap import generate_roadmap_outline
from src.tasks.generate_quiz import generate_quiz_questions
from src.tasks.generate_chat_stream import generate_chat_stream_task
import redis

Base.metadata.create_all(bind=engine)


frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")

app = FastAPI()

# Allow your frontend to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # specific frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

load_dotenv()  # only if using .env
SECRET = os.getenv("OPENAI_API_KEY")


client = OpenAI(api_key=SECRET)
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))


@app.post("/signup", response_model=schema.UserOut)
def signup(user: schema.UserCreate, db: Session = Depends(deps.get_db)):
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed_pw = auth.hash_password(user.password)
    new_user = models.User(email=user.email, name=user.name, hashed_password=hashed_pw)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@app.post("/login", response_model=schema.Token)
def login(user: schema.UserLogin, db: Session = Depends(deps.get_db)):
    print("user:", user)
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if not db_user or not auth.verify_password(user.password, db_user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = auth.create_access_token({"sub": str(db_user.id)})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": schema.UserOut.from_orm(db_user),
    }


@app.post("/generate-course-outline")
def generate_course_outline(
    payload: dict,
    db: Session = Depends(deps.get_db),
    user: User = Depends(deps.get_current_user),
):
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


@app.post("/generate-roadmap")
async def generate_roadmap(
    request: RoadmapRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    print(f"Received request to generate roadmap: {request.roadmap_name}")

    # 2. Save to DB
    roadmap = Roadmap(
        roadmap_name=request.roadmap_name,
        description=f"AI-generated roadmap for {request.roadmap_name}",
        user_id=current_user.id,
        nodes_json=None,
        edges_json=None,
        status=Status.GENERATING,
        task_id=None,
    )
    db.add(roadmap)
    db.commit()
    db.refresh(roadmap)

    task = generate_roadmap_outline.delay(
        roadmap_name=request.roadmap_name,
        roadmap_id=roadmap.id,
        user_id=current_user.id,
    )
    roadmap.task_id = task.id
    db.commit()
    return {"task_id": task.id, "roadmap_id": roadmap.id, "status": "GENERATING"}


@app.get("/generate-roadmap/{roadmap_id}", response_model=RoadmapResponseSchema)
async def get_generated_roadmap(
    roadmap_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    roadmap = (
        db.query(Roadmap)
        .filter(Roadmap.id == roadmap_id, Roadmap.user_id == current_user.id)
        .first()
    )
    if not roadmap:
        raise HTTPException(status_code=404, detail="Roadmap not found")
    return RoadmapResponseSchema(
        id=roadmap.id,
        roadmap_name=roadmap.roadmap_name,
        nodes_json=[RoadmapNodeResponse.model_validate(node) for node in roadmap.nodes],
        edges_json=roadmap.edges_json or [],
        created_at=roadmap.created_at,
        status=roadmap.status,
        description=roadmap.description,
    )


@app.delete("/delete_roadmap/{roadmap_id}")
async def delete_roadmap(
    roadmap_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    roadmap = (
        db.query(Roadmap)
        .filter(Roadmap.id == roadmap_id, Roadmap.user_id == current_user.id)
        .first()
    )
    if not roadmap:
        raise HTTPException(status_code=404, detail="Roadmap not found")
    db.delete(roadmap)
    db.commit()
    return {"detail": "Roadmap deleted successfully"}


@app.get("/get_all_roadmaps", response_model=list[RoadmapResponseSchema])
async def get_all_roadmaps(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    roadmaps = db.query(Roadmap).filter(Roadmap.user_id == current_user.id).all()
    return [
        RoadmapResponseSchema(
            id=roadmap.id,
            roadmap_name=roadmap.roadmap_name,
            nodes_json=[
                RoadmapNodeResponse.model_validate(node) for node in roadmap.nodes
            ],
            edges_json=roadmap.edges_json or [],
            status=roadmap.status,
            task_id=roadmap.task_id,
            created_at=roadmap.created_at,
            description=roadmap.description,
        )
        for roadmap in roadmaps
    ]


@app.patch("/roadmaps/{roadmap_id}/status", response_model=dict)
async def update_roadmap_status(
    roadmap_id: str,
    payload: StatusUpdateSchema,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    print("Current user ID:", current_user.id)
    status = payload.status
    roadmap = (
        db.query(Roadmap)
        .filter(Roadmap.id == roadmap_id, Roadmap.user_id == current_user.id)
        .first()
    )
    if not roadmap:
        raise HTTPException(status_code=404, detail="Roadmap not found")

    roadmap.status = status
    db.commit()
    db.refresh(roadmap)
    return {"roadmap_id": roadmap.id, "status": roadmap.status}


@app.patch("/roadmaps/{roadmap_id}/{roadmap_node_id}/status", response_model=dict)
async def update_roadmap_node(
    roadmap_id: str,
    roadmap_node_id: str,
    payload: StatusUpdateSchema,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    roadmap = (
        db.query(Roadmap)
        .options(selectinload(Roadmap.nodes))
        .filter(Roadmap.id == roadmap_id, Roadmap.user_id == current_user.id)
        .first()
    )
    if not roadmap:
        raise HTTPException(status_code=404, detail="Roadmap not found")

    node = next((n for n in roadmap.nodes if n.node_id == roadmap_node_id), None)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    node.status = payload.status
    db.commit()
    db.refresh(roadmap)

    statuses = [n.status for n in roadmap.nodes]

    if all(s == Status.NOT_STARTED for s in statuses):
        roadmap.status = Status.NOT_STARTED
    elif all(s == Status.COMPLETED for s in statuses):
        roadmap.status = Status.COMPLETED
    else:
        roadmap.status = Status.IN_PROGRESS

    db.commit()
    db.refresh(roadmap)

    return {
        "node_id": node.node_id,
        "node_status": node.status,
        "roadmap_status": roadmap.status,
    }


@app.post(
    "/update_roadmap_course_id/{roadmap_id}", response_model=RoadmapResponseSchema
)
async def update_roadmap(
    roadmap_id: str,
    request: RoadmapNodeCourseIdUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    roadmap = (
        db.query(Roadmap)
        .filter(Roadmap.id == roadmap_id, Roadmap.user_id == current_user.id)
        .first()
    )
    print("node_id:", request.node_id)
    print("request.node_id repr:", repr(request.node_id))
    print("roadmap node ids:", [n.node_id for n in roadmap.nodes])
    if not roadmap:
        print("Roadmap not found")
        raise HTTPException(status_code=404, detail="Roadmap not found")
    print(roadmap.nodes)
    node = (
        db.query(RoadmapNode)
        .filter(
            RoadmapNode.node_id == str(request.node_id),
            RoadmapNode.roadmap_id == roadmap.id,
        )
        .first()
    )
    if not node:
        print("Node not found")
        raise HTTPException(status_code=404, detail="Node not found")
    node.course_id = request.course_id
    db.commit()
    db.refresh(roadmap)
    return RoadmapResponseSchema.model_validate(roadmap)


@app.post("/generate-quiz", response_model=dict)
async def generate_quiz(req: GenerateQuizRequest):
    print(f"Received request to generate quiz for lesson: {req.lesson_name}")
    try:
        task = generate_quiz_questions.delay(
            lesson_name=req.lesson_name,
        )
    except Exception as e:
        print(f"Error occurred: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return {"task_id": task.id}


@app.get("/get_all_courses", response_model=list[CourseAllSchema])
def get_all_courses(
    db: Session = Depends(deps.get_db), user: User = Depends(deps.get_current_user)
):
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


@app.get(
    "/get_all_courses/{roadmap_id}/{roadmap_node_id}",
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


@app.get("/get_course/{course_id}", response_model=CourseSchema)
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


@app.get("/lessons/{lesson_id}", response_model=schema.LessonSchema)
async def get_lesson(
    lesson_id: str,
    db: Session = Depends(deps.get_db),
    user: User = Depends(deps.get_current_user),
):
    lesson = (
        db.query(models.Lesson)
        .filter(models.Lesson.id == lesson_id, models.Lesson.user_id == user.id)
        .first()
    )
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    return LessonSchema.model_validate(lesson)


@app.patch("/lessons/{lesson_id}/status")
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


@app.patch("/modules/{module_id}/status")
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


@app.patch("/courses/{course_id}/status")
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


@app.delete("/courses/{course_id}")
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


@app.delete("/lessons/{lesson_id}")
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


@app.post("/generate-lesson-markdown-stream")
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


@app.get("/task-status/{type}/{task_id}")
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
        return {
            "status": "ready",
            "reply": reply
        }

@app.post("/api/chat/send")
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