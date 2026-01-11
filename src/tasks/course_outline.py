import traceback
import os
import json
import redis
from openai import OpenAI
from celery_app import celery_app
from src.database import SessionLocal
from src.db_queries import save_course_outline_with_modules
from src.schema import CourseSchema

# OpenAI and Redis clients
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))

# Database session factory
db = SessionLocal()


@celery_app.task(
    name="src.tasks.course_outline.generate_course_outline_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def generate_course_outline_task(
    self,
    topic: str,
    level: str,
    user_id: str,
    roadmap_id: str | None = None,
    roadmap_node_id: str | None = None,
    course_id: str | None = None,
    custom_prompt: str | None = None,
) -> CourseSchema:
    try:
        session = SessionLocal()
        custom_section = (
            f"\n\n**CUSTOM REQUIREMENTS:**\n{custom_prompt}" if custom_prompt else ""
        )

        prompt = f"""Create a comprehensive course outline as an expert curriculum designer.
        **TOPIC:** {topic}
        **LEVEL:** {level.capitalize()}{custom_section}

        **REQUIREMENTS:**
        • Design complete learning path covering all essential skills
        • Structure with logical progression (foundational → advanced)
        • Include 5-12 modules with 3-8 lessons each
        • Align difficulty with {level} level
        • {level.capitalize()} means: {"no prior knowledge assumed, focus on fundamentals" if level == "beginner" else "build on foundations, practical applications" if level == "intermediate" else "sophisticated concepts, best practices, real-world scenarios"}

        **OUTPUT:** Valid JSON only (no markdown/code blocks):

        {{
        "title": "Specific course title",
        "description": "2-3 sentences: what students learn and achieve",
        "modules": [
            {{
            "title": "Module title",
            "lessons": [
                {{"title": "Specific lesson topic"}},
                {{"title": "Specific lesson topic"}}
            ]
            }}
        ]
        }}

        Ensure: clear titles, logical flow, comprehensive {level}-level coverage."""

        print("Generating course outline with prompt:", prompt)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": prompt},
                {
                    "role": "system",
                    "content": "You are a course creator generating course outlines.",
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=10000,
        )

        course_outline = json.loads(response.choices[0].message.content)

        save_course_outline_with_modules(course_id, session, user_id, course_outline)

        return {"course_id": course_id}

    except Exception:
        traceback.print_exc()
        raise  # Let Celery handle retry if enabled
