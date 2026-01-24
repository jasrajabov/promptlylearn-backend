import uuid
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Cookie,
    HTTPException,
    Response,
    Request,
    Depends,
)
from fastapi.responses import RedirectResponse
from jose import jwt
from authlib.integrations.starlette_client import OAuth
from starlette.config import Config
import httpx
import os
from sqlalchemy.orm import Session
from src import models, schema, auth, deps
from src.utils.credit_helper import ensure_credits_are_valid
from src.utils.email_service import send_welcome_email, send_password_reset_email
import redis
import logging
from datetime import datetime, timedelta
import secrets
from src.auth import hash_password

router = APIRouter(prefix="/authentication", tags=["authentication"])

redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.Redis.from_url(redis_url)
frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
backend_url = os.getenv("BACKEND_URL", "http://localhost:8000")

logger = logging.getLogger(__name__)

# ==================== OAUTH CONFIGURATION ====================

# Create OAuth config from environment
config = Config(environ=os.environ)
oauth = OAuth(config)

# Register Google OAuth
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# Register GitHub OAuth
oauth.register(
    name="github",
    client_id=os.getenv("GITHUB_CLIENT_ID"),
    client_secret=os.getenv("GITHUB_CLIENT_SECRET"),
    access_token_url="https://github.com/login/oauth/access_token",
    access_token_params=None,
    authorize_url="https://github.com/login/oauth/authorize",
    authorize_params=None,
    api_base_url="https://api.github.com/",
    client_kwargs={"scope": "user:email"},
)

# ==================== HELPER FUNCTIONS ====================


def create_session_and_tokens(user_id: str, response: Response):
    """Create session in Redis and generate access + refresh tokens"""
    session_id = str(uuid.uuid4())

    # Store session in Redis
    redis_client.set(
        f"refresh:{session_id}",
        str(user_id),
        ex=60 * 60 * 24 * auth.REFRESH_TOKEN_EXPIRE_DAYS,
    )

    # Generate tokens
    access_token = auth.create_access_token(str(user_id))
    refresh_token = auth.create_refresh_token(str(user_id), session_id)

    # Set refresh token cookie
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
        max_age=60 * 60 * 24 * auth.REFRESH_TOKEN_EXPIRE_DAYS,
    )

    return access_token, refresh_token


async def get_or_create_oauth_user(
    email: str, name: str, oauth_provider: str, oauth_provider_id: str, db: Session
):
    """Get existing user or create new one from OAuth"""
    # Check if user exists
    db_user = db.query(models.User).filter(models.User.email == email).first()

    if db_user:
        # User exists, update OAuth info if not set
        if not db_user.oauth_provider:
            db_user.oauth_provider = oauth_provider
            db_user.oauth_provider_id = oauth_provider_id
            db.commit()
            db.refresh(db_user)
        return db_user

    # Create new user with OAuth
    new_user = models.User(
        email=email,
        name=name,
        hashed_password=None,  # OAuth users don't have password
        oauth_provider=oauth_provider,
        oauth_provider_id=oauth_provider_id,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Send welcome email
    try:
        email_result = await send_welcome_email(email, name)
        if not email_result["success"]:
            logger.warning(f"Failed to send welcome email: {email_result['message']}")
    except Exception as e:
        logger.warning(f"Failed to send welcome email: {str(e)}")

    return new_user


# ==================== EXISTING AUTH ROUTES ====================


@router.post("/signup", response_model=schema.UserDetailResponse)
async def signup(
    user: schema.UserCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
):
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_pw = auth.hash_password(user.password)
    personal_info = user.personal_info if hasattr(user, "personal_info") else None

    new_user = models.User(
        email=user.email,
        name=user.name,
        hashed_password=hashed_pw,
        personal_info=personal_info,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    background_tasks.add_task(send_welcome_email, user.email, user.name)

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
        "user": schema.UserDetailResponse.from_orm(db_user),
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


@router.get("/me", response_model=schema.UserDetailResponse)
def get_me(user: models.User = Depends(deps.get_current_user)):
    return user


# ==================== OAUTH ROUTES ====================


@router.get("/google")
async def google_login(request: Request):
    """Initiate Google OAuth flow"""
    redirect_uri = f"{backend_url}/authentication/google/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback")
async def google_callback(
    request: Request, response: Response, db: Session = Depends(deps.get_db)
):
    """Handle Google OAuth callback"""
    try:
        # Get access token from Google
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get("userinfo")

        if not user_info:
            raise HTTPException(
                status_code=400, detail="Failed to get user info from Google"
            )

        email = user_info["email"]
        name = user_info.get("name", email.split("@")[0])
        provider_id = user_info["sub"]

        # Get or create user
        db_user = await get_or_create_oauth_user(
            email=email,
            name=name,
            oauth_provider="google",
            oauth_provider_id=provider_id,
            db=db,
        )

        # Ensure credits are valid
        ensure_credits_are_valid(db_user, db)

        # Create session and tokens
        access_token, refresh_token = create_session_and_tokens(
            str(db_user.id), response
        )

        # Redirect to frontend with access token in URL
        redirect_url = f"{frontend_url}/login?token={access_token}"
        return RedirectResponse(url=redirect_url)

    except Exception as e:
        logger.error(f"Google OAuth error: {str(e)}")
        error_url = f"{frontend_url}/login?error=Authentication failed"
        return RedirectResponse(url=error_url)


@router.get("/github")
async def github_login(request: Request):
    """Initiate GitHub OAuth flow"""
    redirect_uri = f"{backend_url}/authentication/github/callback"
    return await oauth.github.authorize_redirect(request, redirect_uri)


@router.get("/github/callback")
async def github_callback(
    request: Request, response: Response, db: Session = Depends(deps.get_db)
):
    """Handle GitHub OAuth callback"""
    try:
        # Get access token from GitHub
        token = await oauth.github.authorize_access_token(request)

        # Get user info from GitHub API
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"token {token['access_token']}"}

            # Get user profile
            user_response = await client.get(
                "https://api.github.com/user", headers=headers
            )
            user_data = user_response.json()

            # Get user email
            email = user_data.get("email")
            if not email:
                # Email might be private, fetch from emails endpoint
                email_response = await client.get(
                    "https://api.github.com/user/emails", headers=headers
                )
                emails = email_response.json()
                # Get primary email
                primary_email = next(
                    (e for e in emails if e["primary"] and e["verified"]), None
                )
                if not primary_email:
                    primary_email = next((e for e in emails if e["verified"]), None)

                email = primary_email["email"] if primary_email else None

            if not email:
                raise HTTPException(
                    status_code=400, detail="No verified email found in GitHub account"
                )

            name = user_data.get("name") or user_data.get("login")
            provider_id = str(user_data["id"])

            # Get or create user
            db_user = await get_or_create_oauth_user(
                email=email,
                name=name,
                oauth_provider="github",
                oauth_provider_id=provider_id,
                db=db,
            )

            # Ensure credits are valid
            ensure_credits_are_valid(db_user, db)

            # Create session and tokens
            access_token, refresh_token = create_session_and_tokens(
                str(db_user.id), response
            )

            # Redirect to frontend with access token in URL
            redirect_url = f"{frontend_url}/login?token={access_token}"
            return RedirectResponse(url=redirect_url)

    except Exception as e:
        logger.error(f"GitHub OAuth error: {str(e)}")
        error_url = f"{frontend_url}/login?error=Authentication failed"
        return RedirectResponse(url=error_url)


@router.post("/forgot-password")
async def forgot_password(
    request: schema.ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
):
    """
    Request a password reset email.
    Returns success even if email doesn't exist (security best practice).
    """
    logger.info(f"Password reset requested for email: {request.email}")

    # Find user by email
    user = db.query(models.User).filter(models.User.email == request.email).first()

    if not user:
        logger.warning(
            f"Password reset requested for non-existent email: {request.email}"
        )
        # Return success anyway to prevent email enumeration
        return {"message": "If that email exists, a password reset link has been sent"}

    # Generate secure random token
    reset_token = secrets.token_urlsafe(32)

    # Create reset token in database
    token_expires = datetime.utcnow() + timedelta(hours=1)  # Token valid for 1 hour

    # Delete any existing unused tokens for this user
    db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.user_id == user.id,
        models.PasswordResetToken.used == False,
    ).delete()

    # Create new token
    db_token = models.PasswordResetToken(
        user_id=user.id, token=reset_token, expires_at=token_expires, used=False
    )
    db.add(db_token)
    db.commit()

    logger.info(f"Password reset token created for user: {user.id}")

    # Send email in background
    background_tasks.add_task(
        send_password_reset_email,
        email=user.email,
        reset_token=reset_token,
        user_name=user.name,
    )

    logger.info(f"Password reset email queued for: {user.email}")

    return {"message": "If that email exists, a password reset link has been sent"}


@router.post("/reset-password", response_model=schema.ResetPasswordResponse)
async def reset_password(
    request: schema.ResetPasswordRequest,
    db: Session = Depends(deps.get_db),
):
    """
    Reset password using the token from email.
    """
    logger.info("Password reset attempt with token")

    # Validate token
    db_token = (
        db.query(models.PasswordResetToken)
        .filter(models.PasswordResetToken.token == request.token)
        .first()
    )

    if not db_token:
        logger.warning("Invalid password reset token used")
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    # Check if token is expired
    if db_token.expires_at < datetime.utcnow():
        logger.warning(
            f"Expired password reset token used for user: {db_token.user_id}"
        )
        raise HTTPException(status_code=400, detail="Reset token has expired")

    # Check if token was already used
    if db_token.used:
        logger.warning(
            f"Already-used password reset token attempted for user: {db_token.user_id}"
        )
        raise HTTPException(status_code=400, detail="Reset token has already been used")

    # Get user
    user = db.query(models.User).filter(models.User.id == db_token.user_id).first()
    if not user:
        logger.error(f"User not found for valid reset token: {db_token.user_id}")
        raise HTTPException(status_code=404, detail="User not found")

    # Validate new password
    if len(request.new_password) < 8:
        raise HTTPException(
            status_code=400, detail="Password must be at least 8 characters"
        )

    # Update password
    user.hashed_password = hash_password(request.new_password)

    # Mark token as used
    db_token.used = True

    db.commit()

    logger.info(f"Password successfully reset for user: {user.id}")

    return {"message": "Password has been reset successfully"}


@router.post("/verify-reset-token")
async def verify_reset_token(
    token: str,
    db: Session = Depends(deps.get_db),
):
    """
    Verify if a reset token is valid (for frontend to check before showing reset form).
    """
    logger.debug("Verifying reset token")

    db_token = (
        db.query(models.PasswordResetToken)
        .filter(
            models.PasswordResetToken.token == token,
            models.PasswordResetToken.used == False,
            models.PasswordResetToken.expires_at > datetime.utcnow(),
        )
        .first()
    )

    if not db_token:
        logger.warning("Invalid or expired token verification attempt")
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user = db.query(models.User).filter(models.User.id == db_token.user_id).first()

    return {"valid": True, "email": user.email if user else None}
