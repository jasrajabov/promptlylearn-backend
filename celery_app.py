from celery import Celery
import os

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)

celery_app = Celery(
    "ai_course_builder", broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND,
    include=[
        "src.tasks.generate_quiz",
        "src.tasks.generate_roadmap",
        "src.tasks.lesson_stream",
        "src.tasks.course_outline",
        "src.tasks.generate_chat_stream",
    ],
)