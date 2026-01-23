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
import logging

logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))



@celery_app.task(
    name="src.tasks.lesson_stream.generate_lesson_markdown_stream_task", bind=True
)
def generate_lesson_markdown_stream_task(
    self, model: str, lesson_id: str, module_id: str, course_id: str, stream_id: str, custom_prompt: str | None = None
):
    """Background Celery task that streams lesson markdown tokens to Redis."""
    db = SessionLocal()
    channel = f"lesson_stream:{stream_id}"
    logger.info(f"Using model {model} to generate content")
    try:
        lesson = db.query(LessonORM).filter_by(id=lesson_id).first()
        course = db.query(CourseORM).filter_by(id=course_id).first()
        module = db.query(ModuleORM).filter_by(id=module_id).first()

        if not lesson or not course:
            redis_client.publish(channel, "[[ERROR]] Lesson or Course not found")
            return

        markdown_buffer = ""

        stream = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": f"""
                            Expand this lesson into detailed content for the learner. 
                            Be as comprehensive as possible.
                            Do not give table of contents.
                            This is a professional course, so no need to add follow-up such as 'If you want, I can...'
                            Include explanations, examples, and expected outputs when relevant.
                            Lesson Title: {lesson.title}. This lesson is part of the course: {course.title}.
                            Markdown only, no JSON.
                            If expected output is present, add it as a code block with the language "text".
                            Additional instructions: {custom_prompt if custom_prompt else "None"}.
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
