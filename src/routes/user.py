from fastapi import APIRouter, Depends

from src import schema
from src.deps import get_current_user, get_db
from src.models import User


router = APIRouter(prefix="/user", tags=["user"])


@router.get("/me", response_model=schema.UserOut)
def get_me(user: User = Depends(get_current_user)):
    return user

@router.patch("/me", response_model=schema.UserOut)
def update_me(user_update: schema.UserUpdate, user: User = Depends(get_current_user), db=Depends(get_db)):
    user.name = user_update.name
    user.personal_info = user_update.personal_info
    db.commit()
    return user