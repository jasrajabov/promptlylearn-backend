# routes/payment.py
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
import stripe
from src import deps
from src.models import MembershipStatus, User
from sqlalchemy.orm import Session
from fastapi import Request
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

router = APIRouter(prefix="/payment", tags=["payment"])

PRICE_ID = "price_1Shl43LBs0XeqslSCEv5hxHW"  # your Stripe price ID


@router.post("/create-subscription")
def create_subscription(
    user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    # Ensure customer exists (standard logic)
    if not user.stripe_customer_id:
        customer = stripe.Customer.create(email=user.email)
        user.stripe_customer_id = customer.id
        db.commit()
        db.refresh(user)

    # Create subscription
    subscription = stripe.Subscription.create(
        # trial_period_days=1,
        customer=user.stripe_customer_id,
        items=[{"price": PRICE_ID}],
        payment_behavior="default_incomplete",
        expand=["latest_invoice.confirmation_secret"],  # 👈 singular
    )
    confirmation_secret = subscription.latest_invoice.confirmation_secret
    client_secret = confirmation_secret.get("client_secret")

    if not client_secret:
        raise HTTPException(
            status_code=400, detail="No client secret found on the latest invoice."
        )
    print("Created subscription:", subscription.id)
    print("Client secret:", client_secret)
    print("Amount due:", subscription.latest_invoice.amount_due)

    return {
        "subscription_id": subscription.id,
        "client_secret": client_secret,
        "amount_due": subscription.latest_invoice.amount_due,
    }

@router.post("/cancel-subscription")
def cancel_subscription(
    user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    """
    Cancel the user's active subscription.
    The subscription will remain active until the end of the current billing period.
    """
    logger.info("Canceling subscription for user: %s", user.email)
    if not user.stripe_customer_id:
        raise HTTPException(
            status_code=400, 
            detail="No customer ID found. User has no subscription."
        )

    try:
        # Get all active subscriptions for this customer
        subscriptions = stripe.Subscription.list(
            customer=user.stripe_customer_id,
            status="active",
            limit=1
        )

        if not subscriptions.data:
            raise HTTPException(
                status_code=404, 
                detail="No active subscription found."
            )

        subscription = subscriptions.data[0]
        
        # Cancel the subscription at period end (user keeps access until billing period ends)
        canceled_subscription = stripe.Subscription.modify(
            subscription.id,
            cancel_at_period_end=True
        )
        
        # cancel_at is the timestamp when the subscription will actually cancel
        cancel_at = canceled_subscription.get('cancel_at')
        logger.info({
            "subscription_id": canceled_subscription.id,
            "cancel_at_period_end": canceled_subscription.get('cancel_at_period_end'),
            "cancel_at": cancel_at,
        })
        return {
            "status": True,
            "message": "Subscription will be canceled at the end of the billing period.",
            "subscription_id": canceled_subscription.id,
            "cancel_at_period_end": canceled_subscription.get('cancel_at_period_end'),
            "cancel_at": cancel_at,  # When it will actually cancel
        }

    except stripe.error.StripeError as e:
        logger.error("Stripe error: %s", str(e))
        raise HTTPException(
            status_code=400, 
            detail=f"Stripe error: {str(e)}"
        )
    except Exception as e:
        logger.error("Error canceling subscription: %s", str(e))
        raise HTTPException(
            status_code=500, 
            detail=f"Error canceling subscription: {str(e)}"
        )


@router.post("/stripe-webhook")
async def stripe_webhook(request: Request, db: Session = Depends(deps.get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info("Received event: %s", event)
    if event["type"] == "invoice.payment_succeeded":
        customer_id = event["data"]["object"]["customer"]
        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            logger.info("Payment succeeded for user: %s", user.email)
            user.membership_status = MembershipStatus.ACTIVE
            user.membership_plan = "premium"
            db.commit()
    
    
    elif event["type"] in ["customer.subscription.deleted", "invoice.payment_failed"]:
        customer_id = event["data"]["object"]["customer"]
        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            user.membership_status = MembershipStatus.INACTIVE
            user.membership_plan = "free"
            user.membership_active_until = datetime.now(timezone.utc)
            db.commit()

    elif "cancel_at" in event["data"]["object"] and "cancellation_details" in event["data"]["object"] and event["data"]["object"]["cancellation_details"]["reason"] == "cancellation_requested":
        customer_id = event["data"]["object"]["customer"]
        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            user.membership_status = MembershipStatus.CANCELED
            user.membership_active_until = datetime.fromtimestamp(
                event["data"]["object"]["cancel_at"], timezone.utc
            )
            logger.info("Subscription canceled for user: %s", user.email)
            db.commit()
    return JSONResponse({"status": "ok"})
