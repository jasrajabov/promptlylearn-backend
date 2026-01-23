import os
import json
import redis
from openai import OpenAI
from celery_app import celery_app

# OpenAI and Redis clients
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
QUIZ_TTL_SECONDS = 3600  # 1 hour


# Database session factory
@celery_app.task(name="src.tasks.generate_quiz.generate_quiz_questions", bind=True)
def generate_quiz_questions(self, lesson_name: str) -> dict:
    """
    Generate quiz questions for a given lesson.
    """
    task_id = self.request.id
    prompt = f"""
    You are an expert quiz creator. Create a set of 5 multiple-choice questions for the lesson titled "{lesson_name}".

    General Rules:
    - Each question should have 4 answer choices.
    - Only one answer should be correct.
    - Provide a brief explanation for the correct answer.

    Output Format:
    Return ONLY valid JSON following this schema exactly (no markdown, no explanations):

    {{
    "questions": [
        {{
        "question": "string",
        "options": list[string],
        "correct_option_index": 0,
        "explanation": "string"
        }}
    ]
    }}
    """

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        max_tokens=1500,
    )

    quiz_json = response.choices[0].message.content

    try:
        quiz_data = json.loads(quiz_json)
        redis_client.setex(f"quiz:{task_id}", QUIZ_TTL_SECONDS, json.dumps(quiz_data))
        return {"status": "success", "task_id": task_id}
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse quiz JSON: {e}")
