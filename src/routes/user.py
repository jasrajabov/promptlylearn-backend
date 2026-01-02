from fastapi import APIRouter, Depends

from src import schema
from src.deps import get_current_user
from src.models import User


router = APIRouter(prefix="/user", tags=["user"])


@router.get("/me", response_model=schema.UserOut)
def get_me(user: User = Depends(get_current_user)):
    return user
