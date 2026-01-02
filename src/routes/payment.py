# routes/payment.py
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
import stripe
from src import deps
from src.models import User
from sqlalchemy.orm import Session
from fastapi import Request
import os


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


@router.post("/stripe-webhook")
async def stripe_webhook(request: Request, db: Session = Depends(deps.get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if event["type"] == "invoice.payment_succeeded":
        customer_id = event["data"]["object"]["customer"]
        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            print("Payment succeeded for user:", user.email)
            user.membership_active = True
            user.membership_plan = "premium"
            db.commit()

    elif event["type"] in ["customer.subscription.deleted", "invoice.payment_failed"]:
        customer_id = event["data"]["object"]["customer"]
        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            user.membership_active = False
            user.membership_plan = "free"
            db.commit()

    return JSONResponse({"status": "ok"})
