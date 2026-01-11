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
from src.utils.email_service import (
    send_subscription_receipt_email,
    send_subscription_cancellation_email,
)
from fastapi import BackgroundTasks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

router = APIRouter(prefix="/payment", tags=["payment"])

PRICE_ID = "price_1Shl43LBs0XeqslSCEv5hxHW"  # your Stripe price ID


@router.post("/create-subscription")
async def create_subscription(
    user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    # Ensure customer exists
    if not user.stripe_customer_id:
        customer = stripe.Customer.create(email=user.email)
        user.stripe_customer_id = customer.id
        db.commit()
        db.refresh(user)

    # Create subscription
    subscription = stripe.Subscription.create(
        customer=user.stripe_customer_id,
        items=[{"price": PRICE_ID}],
        payment_behavior="default_incomplete",
        expand=["latest_invoice.confirmation_secret"],
    )

    confirmation_secret = subscription.latest_invoice.confirmation_secret
    client_secret = confirmation_secret.get("client_secret")

    if not client_secret:
        raise HTTPException(
            status_code=400, detail="No client secret found on the latest invoice."
        )

    return {
        "subscription_id": subscription.id,
        "client_secret": client_secret,
        "amount_due": subscription.latest_invoice.amount_due,
    }


@router.post("/cancel-subscription")
async def cancel_subscription(
    request: Request,
    user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    """
    Cancel the user's active subscription.
    The subscription will remain active until the end of the current billing period.
    """
    body = await request.json()
    cancellation_reasons = body.get("cancellation_reasons", [])
    feedback = body.get("feedback", "")
    logger.info("Canceling subscription for user: %s", user.email)
    if not user.stripe_customer_id:
        raise HTTPException(
            status_code=400, detail="No customer ID found. User has no subscription."
        )

    try:
        # Get all active subscriptions for this customer
        subscriptions = stripe.Subscription.list(
            customer=user.stripe_customer_id, status="active", limit=1
        )

        if not subscriptions.data:
            raise HTTPException(status_code=404, detail="No active subscription found.")

        subscription = subscriptions.data[0]

        # Cancel the subscription at period end (user keeps access until billing period ends)
        updated_subscription = stripe.Subscription.modify(
            subscription.id,
            cancel_at_period_end=True,
            cancellation_details={
                "comment": feedback if feedback else None,
                "feedback": "customer_service"
                if "customer service" in str(cancellation_reasons).lower()
                else "low_quality",
            }
            if cancellation_reasons
            else None,
        )

        # cancel_at is the timestamp when the subscription will actually cancel
        cancel_at = updated_subscription.get("cancel_at")
        logger.info(
            {
                "subscription_id": updated_subscription.id,
                "cancel_at_period_end": updated_subscription.get(
                    "cancel_at_period_end"
                ),
                "cancel_at": cancel_at,
            }
        )
        return {
            "status": True,
            "message": "Subscription will be canceled at the end of the billing period.",
            "subscription_id": updated_subscription.id,
            "cancel_at_period_end": updated_subscription.get("cancel_at_period_end"),
            "cancel_at": cancel_at,  # When it will actually cancel
        }

    except stripe.error.StripeError as e:
        logger.error("Stripe error: %s", str(e))
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")
    except Exception as e:
        logger.error("Error canceling subscription: %s", str(e))
        raise HTTPException(
            status_code=500, detail=f"Error canceling subscription: {str(e)}"
        )


@router.post("/stripe-webhook")
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info("Received event: %s", event["type"])
    print("Received event:", event)
    # Handle invoice.payment_succeeded (standard event)
    if event["type"] == "invoice.payment_succeeded":
        invoice = event["data"]["object"]
        customer_id = invoice["customer"]

        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()

        if user:
            logger.info("Payment succeeded for user: %s", user.email)
            user.membership_status = MembershipStatus.ACTIVE
            user.membership_plan = "premium"
            db.commit()

            # Send receipt email for subscription payments
            if invoice["billing_reason"] in [
                "subscription_create",
                "subscription_cycle",
            ]:
                try:
                    # Format amount
                    amount_formatted = f"${invoice['amount_paid'] / 100:.2f}"

                    # Get subscription ID from invoice
                    subscription_id = None
                    if invoice.get("subscription"):
                        subscription_id = invoice["subscription"]
                    elif invoice.get("parent") and invoice["parent"].get(
                        "subscription_details"
                    ):
                        subscription_id = invoice["parent"]["subscription_details"][
                            "subscription"
                        ]

                    if subscription_id:
                        # Get subscription for billing period - expand items to get the data
                        subscription = stripe.Subscription.retrieve(
                            subscription_id, expand=["items.data"]
                        )

                        # Access items directly (it's a dict-like object)
                        if subscription.get("items") and subscription["items"].get(
                            "data"
                        ):
                            first_item = subscription["items"]["data"][0]
                            period_start = datetime.fromtimestamp(
                                first_item["current_period_start"]
                            ).strftime("%b %d, %Y")
                            period_end = datetime.fromtimestamp(
                                first_item["current_period_end"]
                            ).strftime("%b %d, %Y")

                            # Get billing interval
                            interval = first_item["price"]["recurring"]["interval"]
                            billing_frequency = (
                                "Monthly" if interval == "month" else "Yearly"
                            )
                        else:
                            # Fallback to invoice period
                            period_start = datetime.fromtimestamp(
                                invoice["period_start"]
                            ).strftime("%b %d, %Y")
                            period_end = datetime.fromtimestamp(
                                invoice["period_end"]
                            ).strftime("%b %d, %Y")
                            billing_frequency = "Monthly"

                        billing_period = (
                            f"{billing_frequency} ({period_start} - {period_end})"
                        )
                    else:
                        # Fallback to invoice period if no subscription found
                        period_start = datetime.fromtimestamp(
                            invoice["period_start"]
                        ).strftime("%b %d, %Y")
                        period_end = datetime.fromtimestamp(
                            invoice["period_end"]
                        ).strftime("%b %d, %Y")
                        billing_period = f"Monthly ({period_start} - {period_end})"

                    # Get invoice URL
                    invoice_url = (
                        invoice.get("hosted_invoice_url")
                        or f"https://dashboard.stripe.com/invoices/{invoice['id']}"
                    )

                    # Send receipt email in background
                    background_tasks.add_task(
                        send_subscription_receipt_email,
                        to_email=user.email,
                        user_name=user.name,
                        invoice_number=invoice["number"],
                        amount=amount_formatted,
                        billing_period=billing_period,
                        invoice_url=invoice_url,
                        payment_date=datetime.fromtimestamp(
                            invoice["created"]
                        ).strftime("%B %d, %Y"),
                    )
                    logger.info("Receipt email queued for user: %s", user.email)
                except Exception as e:
                    logger.error(f"Error sending receipt email: {e}")
                    import traceback

                    logger.error(traceback.format_exc())

    # Handle subscription deletion or payment failure
    elif event["type"] in ["customer.subscription.deleted", "invoice.payment_failed"]:
        customer_id = event["data"]["object"]["customer"]
        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            user.membership_status = MembershipStatus.INACTIVE
            user.membership_plan = "free"
            user.membership_active_until = datetime.now(timezone.utc)
            db.commit()

    # Handle subscription cancellation (cancel_at_period_end = true)
    elif event["type"] == "customer.subscription.updated":
        subscription = event["data"]["object"]

        # Check if subscription was just cancelled (cancel_at_period_end changed to true)
        if (
            subscription.get("cancel_at_period_end")
            and subscription.get("cancellation_details", {}).get("reason")
            == "cancellation_requested"
        ):
            customer_id = subscription["customer"]
            user = db.query(User).filter(User.stripe_customer_id == customer_id).first()

            if user:
                # Update user status to CANCELED
                cancel_at = subscription.get("cancel_at") or subscription.get(
                    "current_period_end"
                )
                user.membership_status = MembershipStatus.CANCELED
                user.membership_active_until = datetime.fromtimestamp(
                    cancel_at, timezone.utc
                )
                db.commit()
                logger.info("Subscription canceled for user: %s", user.email)

                # Send cancellation email
                try:
                    # Format the access_until date
                    access_until_datetime = datetime.fromtimestamp(
                        cancel_at, timezone.utc
                    )
                    access_until_full = access_until_datetime.strftime(
                        "%B %d, %Y at %I:%M %p UTC"
                    )
                    access_until_short = access_until_datetime.strftime("%b %d, %Y")

                    # Send cancellation email in background
                    background_tasks.add_task(
                        send_subscription_cancellation_email,
                        to_email=user.email,
                        user_name=user.name,
                        access_until=access_until_full,
                        access_until_short=access_until_short,
                    )
                    logger.info("Cancellation email queued for user: %s", user.email)
                except Exception as e:
                    logger.error(f"Error sending cancellation email: {e}")
                    import traceback

                    logger.error(traceback.format_exc())
                    # Don't fail the webhook if email fails

    return JSONResponse({"status": "ok"})
