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

        prompt = f"""
                You are an expert curriculum designer.

                **TOPIC:** {topic}
                **LEVEL:** {level.capitalize()}{custom_section}

                **INSTRUCTIONS:**
                1. STRICTLY DO NOT GENERATE COURSES ON ILLEGAL, HARMFUL, UNSAFE, OR UNETHICAL TOPICS.
                2. If the topic is illegal/harmful, respond ONLY with:
                {{
                    "error": "Topic not allowed"
                }}
                3. Design a complete, professional course for the given topic and level.
                4. Follow a logical progression (foundational → advanced).
                5. Include 5-12 modules, each with 3-8 lessons.
                6. Align difficulty with {level} level:
                - Beginner: no prior knowledge assumed, focus on fundamentals.
                - Intermediate: build on foundations, practical applications.
                - Advanced: sophisticated concepts, best practices, real-world scenarios.
                7. Ensure clear titles, logical flow, and comprehensive coverage.

                **OUTPUT:** Valid JSON ONLY (no markdown, no code blocks):
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
                """

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
        raise
