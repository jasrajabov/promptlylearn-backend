from fastapi import APIRouter, Depends, HTTPException

from src import deps
from src.schema import GenerateQuizRequest
from src.tasks.generate_quiz import generate_quiz_questions
from src.utils.credit_helper import consume_credits
from src.exceptions import NotEnoughCreditsException


router = APIRouter(prefix="/quiz", tags=["quiz"])


@router.post("/generate-quiz", response_model=dict)
async def generate_quiz(
    req: GenerateQuizRequest,
    user=Depends(deps.get_current_user),
    db=Depends(deps.get_db),
):
    GENERATION_COST = 5
    try:
        consume_credits(user, db, GENERATION_COST)
    except NotEnoughCreditsException as e:
        return {
            "error_message": e.message,
            "error_type": "NotEnoughCreditsException",
        }
    try:
        task = generate_quiz_questions.delay(
            lesson_name=req.lesson_name,
        )
    except Exception as e:
        print(f"Error occurred: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return {"task_id": task.id}
