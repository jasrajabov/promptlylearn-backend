import datetime
import traceback
from openai import OpenAI
import os
from src.models import Status
from src.schema import (
    CourseSchema,
    ClarificationBlockSchema,
    ClarifyLessonRequest,
    LessonSchema,
    GenerateQuizRequest,
    GenerateQuizResponse,
)
from dotenv import load_dotenv
import json

from src.enums import Language

load_dotenv()  # only if using .env
SECRET = os.getenv("OPENAI_API_KEY")


client = OpenAI(api_key=SECRET)


def generate_course_outline(topic: str, level: str) -> CourseSchema:
    prompt = f"""
    Generate a detailed course outline about "{topic} for level {level}".
    All skills required to learn this topic should be covered.
    Add as many modules as needed,
    Provide only JSON in this format:
    {{
    
      "title": "Course Title",
      "description": "Course Description",
      "modules": [
        {{
          "title": "Module Title",
          "lessons": [
            {{"title": "Lesson Title"}}
          ]
        }}
      ],
      fullyGenerated: false
    }}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": prompt},
                {
                    "role": "system",
                    "content": "You are a course creator that generates course outlines",
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=10000,
        )

        course_outline = response.choices[0].message.content
        course_outline = json.loads(course_outline)

        course_outline["level"] = level
        return course_outline
    except Exception:
        traceback.print_exc()


def generate_lesson_details(lesson: LessonSchema) -> LessonSchema:
    prompt = f"""
Expand the following lesson into full detailed content.

Lesson Title: {lesson.title}

Requirements:
- Each lesson must have at least 5–7 explanatory paragraphs.
- Explanations must feel like real course content, not just summaries.
- Include theory, practical examples, best practices, and pitfalls.
- If it is a programming lesson:
  - Provide multiple code examples (at least 2–3).
  - Provide expected outputs for each code example.
  - Add explanations before/after each code block.
- If not a programming lesson:
  - Provide case studies, real-world scenarios, or detailed step-by-step guides.
- Paragraphs must be separate objects in the `content_blocks` array.
- If a field has no value (e.g., no code), explicitly set it to null.
- Respond ONLY with valid JSON (no markdown, no extra text).

Example JSON output:
{{
  "id": "lesson-1",
  "title": "Getting Started with Python",
  "content_blocks": [
    {{
      "title": "Introduction to Python",
      "content": "Python is a versatile programming language known for its readability and simplicity...",
      "code": null,
      "expected_output": null,
      "code_language": null,
      "output_language": null
    }},
    {{
      "title": "Installing Python",
      "content": "Before writing code, you need to install Python...",
      "code": "python --version",
      "expected_output": "Python 3.11.0",
      "code_language": "bash",
      "output_language": "text"
    }}
  ],
  "is_programming_lesson": true
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )

        # Step 1: get raw content
        detailed_lesson_raw = response.choices[0].message.content
        print("Detailed Lesson (raw):", detailed_lesson_raw)

        # Step 2: parse JSON string into dict
        if isinstance(detailed_lesson_raw, str):
            detailed_lesson = json.loads(detailed_lesson_raw)
        else:
            detailed_lesson = detailed_lesson_raw  # already dict

        # --- ENFORCE IDs ---
        detailed_lesson["id"] = lesson.id

        # --- Fix potential list issues ---
        for block in detailed_lesson.get("content_blocks", []):
            if isinstance(block.get("expected_output"), list):
                block["expected_output"] = "\n".join(map(str, block["expected_output"]))
            if isinstance(block.get("code_language"), list):
                block["code_language"] = "\n".join(map(str, block["code_language"]))

        # --- Add DB-related fields ---
        detailed_lesson["status"] = Status.IN_PROGRESS
        detailed_lesson["created_at"] = datetime.datetime.now()
        detailed_lesson["updated_at"] = datetime.datetime.now()

        print("Returning lesson:", detailed_lesson)
        return LessonSchema(**detailed_lesson)

    except Exception:
        traceback.print_exc()
        return lesson


def generate_quiz_content(request: GenerateQuizRequest) -> GenerateQuizResponse:
    content = json.dumps(request.content)
    lesson_name = json.dumps(request.lesson_name)

    prompt = f"""You are an expert course creator. Generate a quiz strictly in JSON format based on the lesson below.

Lesson Name: {lesson_name}
Content: {content}
Number of Questions: 10
Question Type: "multiple-choice" | "true-false" | "short-answer" (choose one)

Requirements:
- Respond ONLY with a valid JSON object.
- No explanations, Markdown, or trailing commas.
- Each question must have:
  - "question": string
  - "options": list of strings
  - "correctOptionIndex": integer (0-based index into options array)

Schema:
{{
  "questions": [
    {{
      "question": "string",
      "options": list[string],
      "correct_option_index": 0
    }}
  ]
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )

        # In your SDK, content is still a string
        quiz_str = response.choices[0].message.content
        print("Generated Quiz (raw):", quiz_str)

        quiz_dict = json.loads(quiz_str)  # ✅ safely load string to dict
        print("Generated Quiz Dict:", quiz_dict)

        return GenerateQuizResponse(**quiz_dict)

    except json.JSONDecodeError:
        print("Failed to parse JSON. Returning fallback response.")
        traceback.print_exc()
        return GenerateQuizResponse(questions=[])
    except Exception:
        traceback.print_exc()
        return GenerateQuizResponse(questions=[])


def clarify_lesson_details(request: ClarifyLessonRequest) -> ClarificationBlockSchema:
    content = json.dumps(request.content)
    question = json.dumps(request.question)

    prompt = f"""You are an expert course creator. Answer, clarify, or expand the following lesson content based on the user's request.

Content Block:
{content}

User's Question: {question}

Requirements:
- Include examples if applicable
- Make the content engaging and informative
- Answers must be in paragraphs for better clarity
- If the question is not related to the content, respond with "Request is not related to content"

Respond ONLY in valid JSON format like this (no extra text):
{{
  "question": {question},
  "answers": [
    {{
      "text": "Your detailed answer here or null",
      "code": "code example if applicable or null",
      "code_language": "programming language if applicable must of one of {Language} or null"
      "output": "expected output if applicable or null"
    }},
    {{
      "text": "Another part of the answer if needed or null",
      "code": "code example if applicable or null",
      "code_language": "programming language if applicable must of one of {Language} or null"
      "output": "expected output if applicable or null"
    }}
  ]
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        clarification = response.choices[0].message.content
        print("Raw Clarification:", clarification)

        # Safely parse JSON
        clarification_dict = json.loads(clarification)
        print("Generated Clarification:", clarification_dict)

        if clarification_dict.get("output"):
            clarification_dict["output"] = str(clarification_dict["output"])

        return ClarificationBlockSchema(**clarification_dict)
    except json.JSONDecodeError:
        print("Failed to parse JSON. Returning fallback response.")
        traceback.print_exc()
        return ClarificationBlockSchema(
            question=request.question,
            answer="Sorry, I couldn't generate a proper clarification.",
        )
    except Exception:
        traceback.print_exc()
        return ClarificationBlockSchema(
            question=request.question, answer="An unexpected error occurred."
        )


# def generate_roadmap_outline(roadmap_name: str) -> RoadmapResponseSchema:
#     """
#     Generate a structured learning roadmap for a career or skill path.
#     Supports optional branching paths (e.g., Frontend vs Backend).
#     """
#     prompt = f"""
#     You are an expert curriculum architect.
#     Create a detailed career roadmap for the path "{roadmap_name}".

#     General Rules:
#     - Each node represents a MAJOR course or learning milestone.
#     - The roadmap should flow logically from beginner → intermediate → advanced.
#     - Include 6–12 nodes total.
#     - You may create branching paths (for example: Frontend vs Backend, Core vs Advanced, etc.)
#     - Each node must include:
#         - id (string)
#         - label (course or milestone name)
#         - description (2–3 sentences)
#         - type:  "core" | "optional" | "project" | "prerequisite" | "certification"| "tooling" |"soft-skill" | "portfolio" | "specialization" | "capstone"
#         - order_index (integer starting from 1)
#         - optional field: "branch" (string, if the node belongs to a branch such as "Frontend" or "Backend")

#     Edges:
#     - Connect nodes in a logical learning order using source → target IDs.
#     - Branches should still connect to a shared foundation (e.g., all start from “Introduction”).
#     - When branches merge, create edges that reconnect to shared advanced nodes.

#     Output Format:
#     Return ONLY valid JSON following this schema exactly (no markdown, no explanations):

#     {{
#         "nodes": [
#             {{
#                 "node_id": "1",
#                 "label": "Introduction to {roadmap_name}",
#                 "description": "Overview of the career path, tools, and goals.",
#                 "type": "core",
#                 "order_index": 1
#             }},
#             {{
#                 "node_id": "2",
#                 "label": "Core Fundamentals",
#                 "description": "Learn the core principles and key technologies used in {roadmap_name}.",
#                 "type": "core",
#                 "order_index": 2
#             }}
#             ...
#         ],
#         "edges": [
#             {{ "source": "1", "target": "2" }},
#             ...
#         ]
#     }}

#     Ensure:
#     - All IDs are strings.
#     - JSON is syntactically valid and parsable.
#     - Include meaningful branching when relevant.
#     - DO NOT include any text outside the JSON.
#     """

#     response = client.chat.completions.create(
#         model="gpt-4o-mini",
#         messages=[{"role": "user", "content": prompt}],
#         temperature=0.7,
#     )

#     raw_output = response.choices[0].message.content.strip()
#     print("Raw Roadmap Output:", raw_output)

#     # --- parse and validate ---
#     json_data = json.loads(raw_output)

#     return RoadmapResponseSchema(
#         id="",  # will be assigned when saved to DB
#         roadmap_name=roadmap_name,
#         nodes_json=json_data.get("nodes", []),
#         edges_json=json_data.get("edges", []),
#     )
