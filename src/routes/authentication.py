import uuid
from fastapi import APIRouter, Cookie, HTTPException, Response
from jose import jwt

import os

from fastapi import Depends
from sqlalchemy.orm import Session
from src import models, schema, auth, deps
from src.utils.credit_helper import ensure_credits_are_valid
import redis

router = APIRouter(prefix="/authentication", tags=["authentication"])

redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.Redis.from_url(redis_url)
frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")


@router.post("/signup", response_model=schema.UserOut)
def signup(user: schema.UserCreate, db: Session = Depends(deps.get_db)):
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed_pw = auth.hash_password(user.password)
    personal_info = user.personal_info if hasattr(user, 'personal_info') else None
    new_user = models.User(email=user.email, name=user.name, hashed_password=hashed_pw, personal_info=personal_info)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.post("/login", response_model=schema.Token)
def login(
    user: schema.UserLogin, response: Response, db: Session = Depends(deps.get_db)
):
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if not db_user or not auth.verify_password(user.password, db_user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    session_id = str(uuid.uuid4())

    # store session in redis
    redis_client.set(
        f"refresh:{session_id}",
        str(db_user.id),
        ex=60 * 60 * 24 * auth.REFRESH_TOKEN_EXPIRE_DAYS,
    )

    access_token = auth.create_access_token(str(db_user.id))
    refresh_token = auth.create_refresh_token(str(db_user.id), session_id)

    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60 * 24 * auth.REFRESH_TOKEN_EXPIRE_DAYS,
    )
    ensure_credits_are_valid(db_user, db)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": schema.UserOut.from_orm(db_user),
    }


@router.post("/refresh")
def refresh_access_token(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    try:
        payload = jwt.decode(
            refresh_token,
            auth.SECRET_KEY,
            algorithms=[auth.ALGORITHM],
        )
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401)

        user_id = payload.get("sub")
        session_id = payload.get("sid")

    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    # Verify session in Redis
    stored_user_id = redis_client.get(f"refresh:{session_id}")
    if not stored_user_id or stored_user_id.decode("utf-8") != user_id:
        raise HTTPException(status_code=401, detail="Session expired")

    # 🔁 ROTATE refresh token (VERY IMPORTANT)
    redis_client.delete(f"refresh:{session_id}")

    new_session_id = str(uuid.uuid4())
    redis_client.set(
        f"refresh:{new_session_id}",
        user_id,
        ex=60 * 60 * 24 * auth.REFRESH_TOKEN_EXPIRE_DAYS,
    )

    new_refresh_token = auth.create_refresh_token(user_id, new_session_id)
    new_access_token = auth.create_access_token(user_id)

    response.set_cookie(
        key="refresh_token",
        value=new_refresh_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60 * 24 * auth.REFRESH_TOKEN_EXPIRE_DAYS,
    )

    return {
        "access_token": new_access_token,
        "expires_in": auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@router.post("/logout")
def logout(refresh_token: str | None = Cookie(default=None)):
    if refresh_token:
        try:
            payload = jwt.decode(
                refresh_token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM]
            )
            redis_client.delete(f"refresh:{payload.get('sid')}")
        except Exception:
            pass
    return {"ok": True}


@router.get("/me", response_model=schema.UserOut)
def get_me(user: models.User = Depends(deps.get_current_user)):
    return user
