from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src import schema
from src.deps import get_current_user, get_db
from src.models import AdminAuditLog, User, UserRole
from src.utils.email_service import send_account_deletion_email
from src.utils.credit_helper import ensure_credits_are_valid
from fastapi import BackgroundTasks
import stripe
import os
import logging

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/user", tags=["user"])


@router.get("/me", response_model=schema.UserDetailResponse)
def get_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_credits_are_valid(user, db)
    return user


@router.patch("/me", response_model=schema.UserDetailResponse)
def update_me(
    user_update: schema.UserUpdate,
    user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    user.name = user_update.name
    user.personal_info = user_update.personal_info
    db.commit()
    return user


stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


@router.post("/me/delete")
async def delete_own_account(
    request: schema.DeleteAccountRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Allow user to delete their own account with confirmation"""

    # Verify password
    from passlib.context import CryptContext

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    if not pwd_context.verify(request.password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect password")

    # Verify confirmation text
    if request.confirm_text.upper() != "DELETE":
        raise HTTPException(
            status_code=400, detail="Please type 'DELETE' to confirm account deletion"
        )

    # Prevent admin self-deletion
    if current_user.role in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
        raise HTTPException(
            status_code=403,
            detail="Admin accounts cannot be self-deleted. Contact a super admin.",
        )

    # Save user info before deletion
    user_email = current_user.email
    user_name = current_user.name or "User"

    # Cancel active Stripe subscription if exists
    subscription_cancelled = False
    if current_user.stripe_customer_id:
        try:
            # Get customer's subscriptions
            subscriptions = stripe.Subscription.list(
                customer=current_user.stripe_customer_id, status="active", limit=10
            )

            # Cancel all active subscriptions
            for subscription in subscriptions.data:
                stripe.Subscription.cancel(subscription.id)
                subscription_cancelled = True
                logger.info(
                    f"Cancelled subscription {subscription.id} for customer {current_user.stripe_customer_id}"
                )

            # Optionally delete the Stripe customer
            # stripe.Customer.delete(current_user.stripe_customer_id)

        except stripe.error.StripeError as e:
            logger.error(f"Error cancelling Stripe subscription: {str(e)}")
            # Log the error but don't block account deletion
            # You might want to handle this differently based on your business logic

    # Clean up related data
    db.query(AdminAuditLog).filter(
        AdminAuditLog.target_user_id == current_user.id
    ).update({"target_user_id": None})

    db.query(AdminAuditLog).filter(
        AdminAuditLog.admin_user_id == current_user.id
    ).update({"admin_user_id": None})

    db.flush()

    # Delete user (this will cascade to courses, roadmaps, etc.)
    db.delete(current_user)
    db.commit()

    # Send confirmation email
    background_tasks.add_task(
        send_account_deletion_email,
        user_email,
        user_name,
        subscription_cancelled,  # Pass this info to the email
    )

    return {
        "success": True,
        "message": "Account deleted successfully",
        "subscription_cancelled": subscription_cancelled,
    }
