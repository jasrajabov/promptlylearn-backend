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
) -> CourseSchema:
    try:
        session = SessionLocal()

        # 3️⃣ Build prompt for OpenAI
        prompt = f"""
        Generate a detailed course outline about "{topic} for level {level}".
        Include all skills needed and as many modules as required.
        Provide only JSON in this format:
        {{
          "title": "Course Title",
          "description": "Course Description",
          "modules": [
            {{
              "title": "Module Title",
              "lessons": [{{"title": "Lesson Title"}}]
            }}
          ],
          "fullyGenerated": false
        }}
        """
        print("Generating course outline with prompt:", prompt)

        # 4️⃣ Call OpenAI
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
