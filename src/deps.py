import os

from dotenv import load_dotenv

from src import auth
from .database import SessionLocal
from fastapi import Depends, HTTPException
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

    return user


def premium_required(user: models.User = Depends(get_current_user)) -> models.User:
    """
    Dependency to ensure the current user is authenticated and has an active premium membership.
    """
    if not user.membership_active:
        raise HTTPException(status_code=403, detail="Premium membership required")
    return user


def create_customer(email: str) -> stripe.Customer:
    """Create a Stripe customer."""
    return stripe.Customer.create(email=email)


def create_subscription(customer_id: str, price_id: str) -> stripe.Subscription:
    return stripe.Subscription.create(
        customer=customer_id,
        items=[{"price": price_id}],
        payment_behavior="default_incomplete",
    )