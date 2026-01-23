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
    self,
    roadmap_name: str,
    roadmap_id: str,
    user_id: str,
    custom_prompt: str | None = None,
) -> RoadmapResponseSchema:
    """
    Generate a structured learning roadmap for a career or skill path.
    Supports optional branching paths (e.g., Frontend vs Backend).
    """
    custom_section = (
        f"\n**CUSTOM REQUIREMENTS:** {custom_prompt}" if custom_prompt else ""
    )

    prompt = f"""Design a comprehensive career learning roadmap as an expert curriculum architect.

**ROADMAP:** {roadmap_name}{custom_section}

**SPECIFICATIONS:**
• 8-15 nodes: prerequisites (1-2) → core skills (4-6) → specializations (2-4) → capstone/portfolio (1-2)
• Node types: prerequisite, core, tooling, specialization, project, portfolio, certification, soft-skill, capstone
• Include meaningful branching for career specializations when relevant
• Each node: unique string ID, specific label, 2-3 sentence description (what + why), type, order_index
• Optional: branch field for specialization paths (e.g., "Frontend", "Backend")

**EDGES:**
• Connect in logical learning order: source → target (string IDs)
• All branches start from shared foundation
• Branches can reconverge to advanced shared nodes
• Every node except first needs incoming edge(s)

**OUTPUT:** Valid JSON only (no markdown/explanations):

{{
  "roadmap_name": "{roadmap_name}",
  "description": "3-4 sentences: what's taught, skills gained, career outcomes",
  "nodes": [
    {{
      "node_id": "1",
      "label": "Specific title",
      "description": "What's covered and why it matters. Key concepts/technologies.",
      "type": "prerequisite",
      "order_index": 1
    }},
    {{
      "node_id": "2",
      "label": "Next milestone",
      "description": "Learning content and real-world relevance.",
      "type": "core",
      "order_index": 2
    }}
  ],
  "edges": [
    {{"source": "1", "target": "2"}}
  ]
}}

**ENSURE:**
✓ Compelling roadmap description
✓ Specific labels (not generic)
✓ Meaningful branching structure
✓ Valid, parsable JSON
✓ All edges properly connected"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )

    raw_output = response.choices[0].message.content.strip()
    json_data = json.loads(raw_output)
    saved_roadmap = save_roadmap(roadmap_id, json_data, db, user_id)

    return saved_roadmap
