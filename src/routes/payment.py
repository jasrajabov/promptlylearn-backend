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

# 🔥 Define subscription-compatible payment methods
SUBSCRIPTION_PAYMENT_METHODS = ["card"]


@router.post("/create-subscription")
async def create_subscription(
    request: Request,
    user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    """
    Create a new subscription for the user with subscription-compatible payment methods only.
    This excludes Cash App, Amazon Pay, Klarna, and other one-time payment methods.
    """
    try:
        # Get price_id from request body (optional, defaults to monthly)
        body = await request.json()
        price_id = body.get("price_id", PRICE_ID)  # Use default if not provided

        # Ensure customer exists
        if not user.stripe_customer_id:
            customer = stripe.Customer.create(
                email=user.email,
                name=user.name,
                metadata={
                    "user_id": str(user.id),
                    "user_email": user.email,
                },
            )
            user.stripe_customer_id = customer.id
            db.commit()
            db.refresh(user)
            logger.info(f"Created Stripe customer for user: {user.email}")

        # 🔥 NEW API: Use confirmation_secret instead of payment_intent
        # Create subscription with ONLY subscription-compatible payment methods
        subscription = stripe.Subscription.create(
            customer=user.stripe_customer_id,
            items=[{"price": price_id}],  # Use dynamic price_id
            payment_behavior="default_incomplete",
            expand=[
                "latest_invoice.confirmation_secret"
            ],  # 🔥 Use new confirmation_secret
            # 🔥 Limit to subscription-compatible methods
            payment_settings={
                "payment_method_types": SUBSCRIPTION_PAYMENT_METHODS,
                "save_default_payment_method": "on_subscription",
            },
            # Additional useful settings
            metadata={
                "user_id": str(user.id),
                "user_email": user.email,
                "price_id": price_id,
            },
            # Automatically attempt to collect payment
            collection_method="charge_automatically",
        )

        logger.info(
            f"Created subscription {subscription.id} for user: {user.email} with price: {price_id}"
        )

        # 🔥 NEW: Get client secret from confirmation_secret (new Stripe API)
        invoice = subscription.latest_invoice

        if not invoice:
            raise HTTPException(
                status_code=400,
                detail="No invoice found for subscription.",
            )

        # Get confirmation_secret object
        confirmation_secret = getattr(invoice, "confirmation_secret", None)

        if not confirmation_secret:
            raise HTTPException(
                status_code=400,
                detail="No confirmation secret found. Please try again.",
            )

        # Extract client_secret from the confirmation_secret object
        client_secret = (
            confirmation_secret.get("client_secret")
            if isinstance(confirmation_secret, dict)
            else getattr(confirmation_secret, "client_secret", None)
        )

        if not client_secret:
            raise HTTPException(
                status_code=400,
                detail="No client secret found in confirmation secret.",
            )

        logger.info(
            f"Successfully retrieved client secret for subscription {subscription.id}"
        )

        return {
            "subscription_id": subscription.id,
            "client_secret": client_secret,
            "amount_due": invoice.amount_due,
            "currency": invoice.currency,
            "status": subscription.status,
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating subscription: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Payment error: {str(e)}")
    except Exception as e:
        logger.error(f"Error creating subscription: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while creating your subscription. Please try again.",
        )


@router.post("/cancel-subscription")
async def cancel_subscription(
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    """
    Cancel the user's active subscription.
    The subscription will remain active until the end of the current billing period.
    """
    try:
        body = await request.json()
        cancellation_reasons = body.get("cancellation_reasons", [])
        feedback = body.get("feedback", "")

        logger.info(f"Canceling subscription for user: {user.email}")

        if not user.stripe_customer_id:
            raise HTTPException(
                status_code=400,
                detail="No customer ID found. User has no subscription.",
            )

        # Get all active subscriptions for this customer
        subscriptions = stripe.Subscription.list(
            customer=user.stripe_customer_id,
            status="active",
            limit=1,
        )

        if not subscriptions.data:
            raise HTTPException(
                status_code=404,
                detail="No active subscription found.",
            )

        subscription = subscriptions.data[0]

        # Prepare cancellation details
        cancellation_details = None
        if cancellation_reasons or feedback:
            # Map reasons to Stripe's expected feedback types
            feedback_type = "other"
            if any("service" in str(r).lower() for r in cancellation_reasons):
                feedback_type = "customer_service"
            elif any(
                "expensive" in str(r).lower() or "price" in str(r).lower()
                for r in cancellation_reasons
            ):
                feedback_type = "too_expensive"
            elif any("quality" in str(r).lower() for r in cancellation_reasons):
                feedback_type = "low_quality"
            elif any("feature" in str(r).lower() for r in cancellation_reasons):
                feedback_type = "missing_features"

            cancellation_details = {
                "comment": feedback if feedback else None,
                "feedback": feedback_type,
            }

        # Cancel the subscription at period end
        updated_subscription = stripe.Subscription.modify(
            subscription.id,
            cancel_at_period_end=True,
            cancellation_details=cancellation_details,
            metadata={
                "cancelled_by": user.email,
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
                "cancellation_reasons": str(cancellation_reasons),
            },
        )

        # Get the cancel date
        cancel_at = (
            updated_subscription.cancel_at or updated_subscription.current_period_end
        )
        cancel_date = datetime.fromtimestamp(cancel_at, timezone.utc)

        logger.info(
            f"Subscription {updated_subscription.id} will cancel on {cancel_date} for user: {user.email}"
        )

        # Update user status immediately to CANCELED (they keep access until cancel_at)
        user.membership_status = MembershipStatus.CANCELED
        user.membership_active_until = cancel_date
        db.commit()

        # Send cancellation email
        try:
            access_until_full = cancel_date.strftime("%B %d, %Y at %I:%M %p UTC")
            access_until_short = cancel_date.strftime("%b %d, %Y")

            background_tasks.add_task(
                send_subscription_cancellation_email,
                to_email=user.email,
                user_name=user.name,
                access_until=access_until_full,
                access_until_short=access_until_short,
            )
            logger.info(f"Cancellation email queued for user: {user.email}")
        except Exception as e:
            logger.error(f"Error queueing cancellation email: {e}")

        return {
            "status": True,
            "message": "Subscription will be canceled at the end of the billing period.",
            "subscription_id": updated_subscription.id,
            "cancel_at_period_end": updated_subscription.cancel_at_period_end,
            "cancel_at": cancel_at,
            "cancel_date": cancel_date.isoformat(),
            "access_until": access_until_full,
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")
    except Exception as e:
        logger.error(f"Error canceling subscription: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error canceling subscription: {str(e)}",
        )


@router.post("/reactivate-subscription")
async def reactivate_subscription(
    user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    """
    Reactivate a canceled subscription (that hasn't ended yet).
    """
    try:
        if not user.stripe_customer_id:
            raise HTTPException(
                status_code=400,
                detail="No customer ID found.",
            )

        # Get subscriptions that are set to cancel
        subscriptions = stripe.Subscription.list(
            customer=user.stripe_customer_id,
            status="active",
            limit=1,
        )

        if not subscriptions.data:
            raise HTTPException(
                status_code=404,
                detail="No subscription found to reactivate.",
            )

        subscription = subscriptions.data[0]

        # Check if it's set to cancel
        if not subscription.cancel_at_period_end:
            return {
                "status": True,
                "message": "Subscription is already active.",
            }

        # Reactivate by removing cancel_at_period_end
        updated_subscription = stripe.Subscription.modify(
            subscription.id,
            cancel_at_period_end=False,
        )

        # Update user status
        user.membership_status = MembershipStatus.ACTIVE
        user.membership_active_until = None
        db.commit()

        logger.info(f"Subscription reactivated for user: {user.email}")

        return {
            "status": True,
            "message": "Subscription reactivated successfully.",
            "subscription_id": updated_subscription.id,
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")
    except Exception as e:
        logger.error(f"Error reactivating subscription: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error reactivating subscription: {str(e)}",
        )


@router.get("/subscription-status")
async def get_subscription_status(
    user: User = Depends(deps.get_current_user),
):
    """
    Get the current subscription status from Stripe.
    """
    try:
        if not user.stripe_customer_id:
            return {
                "has_subscription": False,
                "status": "no_subscription",
            }

        # Get active subscriptions
        subscriptions = stripe.Subscription.list(
            customer=user.stripe_customer_id,
            status="active",
            limit=1,
        )

        if not subscriptions.data:
            return {
                "has_subscription": False,
                "status": "no_subscription",
            }

        subscription = subscriptions.data[0]

        return {
            "has_subscription": True,
            "status": subscription.status,
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "current_period_end": subscription.current_period_end,
            "cancel_at": subscription.cancel_at,
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")


@router.post("/stripe-webhook")
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
):
    """
    Handle Stripe webhook events for subscription lifecycle.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except Exception as e:
        logger.error(f"Webhook signature verification failed: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(f"Received webhook event: {event['type']}")

    try:
        # Handle successful payment
        if event["type"] == "invoice.payment_succeeded":
            await handle_payment_succeeded(event, db, background_tasks)

        # Handle subscription deletion or payment failure
        elif event["type"] in [
            "customer.subscription.deleted",
            "invoice.payment_failed",
        ]:
            await handle_subscription_ended(event, db)

        # Handle subscription updates (including cancellation)
        elif event["type"] == "customer.subscription.updated":
            await handle_subscription_updated(event, db, background_tasks)

        # Handle payment method updates
        elif event["type"] == "payment_method.attached":
            logger.info(f"Payment method attached: {event['data']['object']['id']}")

        return JSONResponse({"status": "success"})

    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        # Return 200 anyway to prevent Stripe from retrying
        return JSONResponse({"status": "error", "message": str(e)})


async def handle_payment_succeeded(
    event: dict,
    db: Session,
    background_tasks: BackgroundTasks,
):
    """Handle successful invoice payment."""
    invoice = event["data"]["object"]
    customer_id = invoice["customer"]

    user = db.query(User).filter(User.stripe_customer_id == customer_id).first()

    if not user:
        logger.warning(f"No user found for customer: {customer_id}")
        return

    logger.info(f"Payment succeeded for user: {user.email}")

    # Activate membership
    user.membership_status = MembershipStatus.ACTIVE
    user.membership_plan = "premium"
    user.membership_active_until = None  # Clear any cancellation date
    db.commit()

    # Send receipt email for subscription payments
    if invoice["billing_reason"] in ["subscription_create", "subscription_cycle"]:
        try:
            amount_formatted = f"${invoice['amount_paid'] / 100:.2f}"

            # Get subscription details
            subscription_id = invoice.get("subscription")
            billing_period = "Monthly"
            period_start = datetime.fromtimestamp(invoice["period_start"]).strftime(
                "%b %d, %Y"
            )
            period_end = datetime.fromtimestamp(invoice["period_end"]).strftime(
                "%b %d, %Y"
            )

            if subscription_id:
                try:
                    subscription = stripe.Subscription.retrieve(
                        subscription_id, expand=["items.data"]
                    )
                    if subscription.get("items") and subscription["items"].get("data"):
                        first_item = subscription["items"]["data"][0]
                        interval = first_item["price"]["recurring"]["interval"]
                        billing_frequency = (
                            "Monthly" if interval == "month" else "Yearly"
                        )
                        period_start = datetime.fromtimestamp(
                            first_item["current_period_start"]
                        ).strftime("%b %d, %Y")
                        period_end = datetime.fromtimestamp(
                            first_item["current_period_end"]
                        ).strftime("%b %d, %Y")
                        billing_period = (
                            f"{billing_frequency} ({period_start} - {period_end})"
                        )
                except Exception as e:
                    logger.error(f"Error getting subscription details: {e}")

            invoice_url = invoice.get(
                "hosted_invoice_url",
                f"https://dashboard.stripe.com/invoices/{invoice['id']}",
            )

            background_tasks.add_task(
                send_subscription_receipt_email,
                to_email=user.email,
                user_name=user.name,
                invoice_number=invoice["number"],
                amount=amount_formatted,
                billing_period=billing_period,
                invoice_url=invoice_url,
                payment_date=datetime.fromtimestamp(invoice["created"]).strftime(
                    "%B %d, %Y"
                ),
            )
            logger.info(f"Receipt email queued for user: {user.email}")
        except Exception as e:
            logger.error(f"Error sending receipt email: {e}")


async def handle_subscription_ended(event: dict, db: Session):
    """Handle subscription deletion or payment failure."""
    customer_id = event["data"]["object"]["customer"]
    user = db.query(User).filter(User.stripe_customer_id == customer_id).first()

    if user:
        logger.info(f"Subscription ended for user: {user.email}")
        user.membership_status = MembershipStatus.INACTIVE
        user.membership_plan = "free"
        user.membership_active_until = datetime.now(timezone.utc)
        db.commit()


async def handle_subscription_updated(
    event: dict,
    db: Session,
    background_tasks: BackgroundTasks,
):
    """Handle subscription updates including cancellation."""
    subscription = event["data"]["object"]

    # Check if subscription was just cancelled
    if subscription.get("cancel_at_period_end"):
        customer_id = subscription["customer"]
        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()

        if user and user.membership_status != MembershipStatus.CANCELED:
            # Update user status to CANCELED
            cancel_at = subscription.get("cancel_at") or subscription.get(
                "current_period_end"
            )
            user.membership_status = MembershipStatus.CANCELED
            user.membership_active_until = datetime.fromtimestamp(
                cancel_at, timezone.utc
            )
            db.commit()
            logger.info(f"Subscription marked for cancellation for user: {user.email}")

            # Send cancellation email
            try:
                access_until_datetime = datetime.fromtimestamp(cancel_at, timezone.utc)
                access_until_full = access_until_datetime.strftime(
                    "%B %d, %Y at %I:%M %p UTC"
                )
                access_until_short = access_until_datetime.strftime("%b %d, %Y")

                background_tasks.add_task(
                    send_subscription_cancellation_email,
                    to_email=user.email,
                    user_name=user.name,
                    access_until=access_until_full,
                    access_until_short=access_until_short,
                )
                logger.info(f"Cancellation email queued for user: {user.email}")
            except Exception as e:
                logger.error(f"Error sending cancellation email: {e}")
