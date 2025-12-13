import os
import json
import redis
from openai import OpenAI
from celery_app import celery_app
from src.database import SessionLocal
from src.db_queries import save_roadmap
from src.schema import RoadmapResponseSchema

# OpenAI and Redis clients
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))

# Database session factory
db = SessionLocal()


@celery_app.task(name="src.tasks.generate_roadmap.generate_roadmap_outline", bind=True)
def generate_roadmap_outline(
    self, roadmap_name: str, roadmap_id: str, user_id: str
) -> RoadmapResponseSchema:
    """
    Generate a structured learning roadmap for a career or skill path.
    Supports optional branching paths (e.g., Frontend vs Backend).
    """
    prompt = f"""
    You are an expert curriculum architect. Think of it as designing a comprehensive learning roadmap for someone to follow for career growth.
    Create a detailed career roadmap for the path "{roadmap_name}".

    General Rules:
    - Each node represents a course or learning milestone.
    - Include 6–12 nodes total.
    - 2-3 sentences of detailed description
    - You may create branching paths (for example: Frontend vs Backend, Core vs Advanced, etc.)
    - You can get detailed, but keep it high-level (no individual lessons).
    - Each node must include:
        - id (string)
        - label (course or milestone name)
        - description (2–3 sentences)
        - type:  "core" | "optional" | "project" | "prerequisite" | "certification"| "tooling" |"soft-skill" | "portfolio" | "specialization" | "capstone"
        - order_index (integer starting from 1)
        - optional field: "branch" (string, if the node belongs to a branch such as "Frontend" or "Backend")

    Edges:
    - Connect nodes in a logical learning order using source → target IDs.
    - Branches should still connect to a shared foundation (e.g., all start from “Introduction”).
    - When branches merge, create edges that reconnect to shared advanced nodes.

    Output Format:
    Return ONLY valid JSON following this schema exactly (no markdown, no explanations):

    {{
        "roadmap_name": "{roadmap_name}",
        "description": "Sample description of the roadmap. This roadmap covers essential skills and knowledge for {roadmap_name}. Possible career paths and specializations are...",
        "nodes": [
            {{
                "node_id": "1",
                "label": "Introduction to {roadmap_name}",
                "description": "Overview of the career path, tools, and goals.",
                "type": "core",
                "order_index": 1
            }},
            {{
                "node_id": "2",
                "label": "Core Fundamentals",
                "description": "Learn the core principles and key technologies used in {roadmap_name}.",
                "type": "core",
                "order_index": 2
            }}
            ...
        ],
        "edges": [
            {{ "source": "1", "target": "2" }},
            ...
        ]
    }}

    Ensure:
    - All IDs are strings.
    - JSON is syntactically valid and parsable.
    - Include meaningful branching when relevant.
    - DO NOT include any text outside the JSON.
    """

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )

    raw_output = response.choices[0].message.content.strip()
    print("Raw Roadmap Output:", raw_output)

    # --- parse and validate ---
    json_data = json.loads(raw_output)
    print("Parsed Roadmap JSON:", json_data)
    saved_roadmap = save_roadmap(roadmap_id, json_data, db, user_id)

    return saved_roadmap
