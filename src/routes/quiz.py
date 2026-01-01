from fastapi import APIRouter, HTTPException

from src.schema import GenerateQuizRequest
from src.tasks.generate_quiz import generate_quiz_questions


router = APIRouter(prefix="/quiz", tags=["quiz"])


@router.post("/generate-quiz", response_model=dict)
async def generate_quiz(req: GenerateQuizRequest):
    print(f"Received request to generate quiz for lesson: {req.lesson_name}")
    try:
        task = generate_quiz_questions.delay(
            lesson_name=req.lesson_name,
        )
    except Exception as e:
        print(f"Error occurred: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return {"task_id": task.id}
