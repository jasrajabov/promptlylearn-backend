import os
import redis
from openai import OpenAI
from src.database import SessionLocal
from src.models import (
    Lesson as LessonORM,
    Module as ModuleORM,
    Course as CourseORM,
    Status,
)
from celery_app import celery_app

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))


@celery_app.task(
    name="src.tasks.lesson_stream.generate_lesson_markdown_stream_task", bind=True
)
def generate_lesson_markdown_stream_task(
    self, lesson_id, module_id, course_id, stream_id
):
    """Background Celery task that streams lesson markdown tokens to Redis."""
    db = SessionLocal()
    channel = f"lesson_stream:{stream_id}"

    try:
        lesson = db.query(LessonORM).filter_by(id=lesson_id).first()
        course = db.query(CourseORM).filter_by(id=course_id).first()
        module = db.query(ModuleORM).filter_by(id=module_id).first()

        if not lesson or not course:
            redis_client.publish(channel, "[[ERROR]] Lesson or Course not found")
            return

        markdown_buffer = ""

        stream = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": f"""
Expand this lesson into detailed content. Be as comprehensive as possible.
Include explanations, examples, and expected outputs.
Lesson Title: {lesson.title}. This lesson is part of the course: {course.title}.
Markdown only, no JSON.
Markdown should have headings, code blocks, and text.
If expected output is present, add it as a code block with the language "text".
""",
                }
            ],
            stream=True,
        )

        for event in stream:
            if event.choices:
                delta = event.choices[0].delta
                token = getattr(delta, "content", None)
                if token:
                    print("Streaming token:", token)
                    markdown_buffer += token
                    redis_client.publish(channel, token)

        # Save lesson content
        lesson.content = markdown_buffer
        lesson.status = Status.IN_PROGRESS
        if module and module.status == Status.NOT_GENERATED:
            module.status = Status.IN_PROGRESS
        if course and course.status == Status.NOT_GENERATED:
            course.status = Status.IN_PROGRESS

        db.add_all([lesson, module, course])
        db.commit()

        redis_client.publish(channel, "[[STREAM_END]]")

    except Exception as e:
        redis_client.publish(channel, f"[[ERROR]] {e}")
        db.rollback()
    finally:
        db.close()
