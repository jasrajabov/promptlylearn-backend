import os
from datetime import datetime
from dotenv import load_dotenv

from src import auth
from .database import SessionLocal
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from fastapi.security import OAuth2PasswordBearer
from jose import jwt
from src import models
import stripe


load_dotenv()


SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60))

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")  # your login endpoint


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    try:
        payload = jwt.decode(
            token,
            auth.SECRET_KEY,
            algorithms=[auth.ALGORITHM],
        )

        if payload.get("type") != "access":
            raise HTTPException(status_code=401)

        user_id = payload.get("sub")

    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(models.User).get(user_id)
    if not user:
        raise HTTPException(status_code=401)

    # Check if user account is suspended or deleted
    if hasattr(user, "status"):
        if user.status == models.UserStatus.DELETED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Account has been deleted"
            )

        if user.status == models.UserStatus.SUSPENDED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Account is suspended. Reason: {user.suspended_reason or 'No reason provided'}",
            )

    # Update last login timestamp
    if hasattr(user, "last_login_at"):
        user.last_login_at = datetime.utcnow()
        user.login_count = (user.login_count or 0) + 1
        db.commit()

    return user


def premium_required(user: models.User = Depends(get_current_user)) -> models.User:
    """
    Dependency to ensure the current user is authenticated and has an active premium membership.
    """
    if not user.membership_active:
        raise HTTPException(status_code=403, detail="Premium membership required")
    return user


# --- Admin Authentication Helpers ---


def require_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    """
    Require user to have ADMIN or SUPER_ADMIN role.
    Raises 403 if user doesn't have sufficient permissions.

    Usage:
        @router.get("/admin/users")
        async def list_users(current_user: User = Depends(require_admin)):
            ...
    """
    print("current_user", current_user)
    if not hasattr(current_user, "role"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required. User role system not configured.",
        )

    if current_user.role not in [models.UserRole.ADMIN, models.UserRole.SUPER_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required. You do not have sufficient permissions.",
        )

    return current_user


def require_super_admin(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    """
    Require user to have SUPER_ADMIN role.
    Raises 403 if user doesn't have super admin permissions.

    Usage:
        @router.delete("/admin/users/{user_id}")
        async def delete_user(
            user_id: str,
            current_user: User = Depends(require_super_admin)
        ):
            ...
    """
    if not hasattr(current_user, "role"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required. User role system not configured.",
        )

    if current_user.role != models.UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required. This action is restricted to super administrators.",
        )

    return current_user


# --- Permission Checkers ---


def check_permission(user: models.User, required_role: models.UserRole) -> bool:
    """
    Check if user has the required role or higher.
    Role hierarchy: SUPER_ADMIN > ADMIN > USER
    """
    if not hasattr(user, "role"):
        return False

    role_hierarchy = {
        models.UserRole.USER: 1,
        models.UserRole.ADMIN: 2,
        models.UserRole.SUPER_ADMIN: 3,
    }

    user_level = role_hierarchy.get(user.role, 0)
    required_level = role_hierarchy.get(required_role, 0)

    return user_level >= required_level


def can_modify_user(admin: models.User, target_user: models.User) -> bool:
    """
    Check if admin can modify target user.
    Rules:
    - Super admins can modify anyone
    - Regular admins can only modify regular users
    - Users cannot be modified by users with equal or lower roles
    """
    if not hasattr(admin, "role") or not hasattr(target_user, "role"):
        return False

    if admin.role == models.UserRole.SUPER_ADMIN:
        return True

    if admin.role == models.UserRole.ADMIN:
        return target_user.role == models.UserRole.USER

    return False


def require_permission_to_modify(
    target_user_id: str,
    current_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> tuple[models.User, models.User]:
    """
    Check if current user has permission to modify target user.
    Returns tuple of (current_user, target_user).

    Usage:
        @router.patch("/admin/users/{user_id}/suspend")
        async def suspend_user(
            user_id: str,
            admin_and_target = Depends(
                lambda user_id=user_id: require_permission_to_modify(user_id)
            )
        ):
            admin, target = admin_and_target
            ...
    """
    target_user = db.query(models.User).filter(models.User.id == target_user_id).first()

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found"
        )

    if not can_modify_user(current_user, target_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions to modify this user. "
            f"Target user role: {target_user.role.value}",
        )

    return current_user, target_user


# --- Helper Functions ---


def is_admin(user: models.User) -> bool:
    """Check if user is admin or super admin"""
    if not hasattr(user, "role"):
        return False
    return user.role in [models.UserRole.ADMIN, models.UserRole.SUPER_ADMIN]


def is_super_admin(user: models.User) -> bool:
    """Check if user is super admin"""
    if not hasattr(user, "role"):
        return False
    return user.role == models.UserRole.SUPER_ADMIN


# --- Stripe Functions (existing) ---


def create_customer(email: str) -> stripe.Customer:
    """Create a Stripe customer."""
    return stripe.Customer.create(email=email)


def create_subscription(customer_id: str, price_id: str) -> stripe.Subscription:
    return stripe.Subscription.create(
        customer=customer_id,
        items=[{"price": price_id}],
        payment_behavior="default_incomplete",
    )
