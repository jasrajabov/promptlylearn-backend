from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from src.models import User
from src.exceptions import NotEnoughCreditsException

FREE_WEEKLY_CREDITS = 100


def ensure_credits_are_valid(user: User, db: Session):
    if user.membership_plan == "premium" and user.membership_active:
        return  # premium users don't need resets

    now = datetime.utcnow()

    if not user.credits_reset_at:
        user.credits = FREE_WEEKLY_CREDITS
        user.credits_reset_at = now + timedelta(days=7)
        db.commit()
        return

    if now >= user.credits_reset_at:
        user.credits = FREE_WEEKLY_CREDITS
        user.credits_reset_at = now + timedelta(days=7)
        db.commit()


def consume_credits(
    user: User,
    db: Session,
    cost: int,
):
    ensure_credits_are_valid(user, db)

    if user.membership_plan == "premium" and user.membership_active:
        return  # unlimited

    if user.credits < cost:
        raise NotEnoughCreditsException(
            message="Not enough credits. Upgrade to premium or wait for reset."
        )

    user.credits -= cost
    db.commit()
