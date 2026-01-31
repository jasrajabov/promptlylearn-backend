from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from src.models import MembershipStatus, User
from src.exceptions import NotEnoughCreditsException

DAILY_CREDITS = 200


def ensure_credits_are_valid(user: User, db: Session):
    if (
        user.membership_plan == "premium"
        and user.membership_status == MembershipStatus.ACTIVE
    ):
        return  # premium users don't need resets

    now = datetime.utcnow()

    if not user.credits_reset_at:
        user.credits = DAILY_CREDITS
        user.credits_reset_at = now + timedelta(days=1)
        db.commit()
        return

    if now >= user.credits_reset_at and user.credits < DAILY_CREDITS:
        user.credits = DAILY_CREDITS
        user.credits_reset_at = now + timedelta(days=1)
        db.commit()


def consume_credits(
    user: User,
    db: Session,
    cost: int,
):
    ensure_credits_are_valid(user, db)

    if (
        user.membership_plan == "premium"
        and user.membership_status == MembershipStatus.ACTIVE
    ) or (
        user.membership_active_until
        and user.membership_active_until > datetime.utcnow()
    ):
        return  # unlimited
    elif (
        user.membership_active_until
        and user.membership_active_until < datetime.utcnow()
    ):
        raise NotEnoughCreditsException(
            message="Membership has expired. Please purchase a subscription."
        )
    if user.credits < cost:
        raise NotEnoughCreditsException(
            message="Not enough credits. Upgrade to premium or wait for reset."
        )

    user.credits -= cost
    db.commit()


def update_user_subscription_details(
    user: User,
    db: Session,
):
    if (
        user.membership_plan == "premium"
        and user.membership_status == MembershipStatus.ACTIVE
    ):
        return  # already premium
    if (
        user.membership_plan == "premium"
        and user.membership_active_until < datetime.utcnow()
    ):
        user.membership_status = MembershipStatus.INACTIVE
        user.membership_plan = "free"
        db.commit()
