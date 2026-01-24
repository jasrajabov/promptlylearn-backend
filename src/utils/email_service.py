import os
from dotenv import load_dotenv
import logging
from pathlib import Path
from datetime import datetime
import httpx

load_dotenv()

logger = logging.getLogger(__name__)

# Constants
STATIC_DIR = Path("src/static")
LOGO_PATH = STATIC_DIR / "logo.svg"
WELCOME_TEMPLATE_PATH = STATIC_DIR / "welcome_email.html"
DELETION_TEMPLATE_PATH = STATIC_DIR / "account_deletion_email.html"
SUBSCRIPTION_RECEIPT_TEMPLATE_PATH = STATIC_DIR / "subscription_receipt_email.html"
FORGOT_PASSWORD_TEMPLATE_PATH = STATIC_DIR / "forgot_password_email.html"
SUBSCRIPTION_CANCELLATION_TEMPLATE_PATH = (
    STATIC_DIR / "subscription_cancellation_email.html"
)


def load_email_template(template_path: Path, **kwargs) -> str:
    """
    Load an HTML email template and replace placeholders with provided values.

    Args:
        template_path: Path to the HTML template file
        **kwargs: Key-value pairs for template variable substitution

    Returns:
        Rendered HTML string with substituted values
    """
    try:
        with open(template_path, "r", encoding="utf-8") as file:
            template = file.read()

        # Replace placeholders with provided values
        return template.format(**kwargs)
    except FileNotFoundError:
        logger.error(f"Template file not found at {template_path}")
        raise
    except KeyError as e:
        logger.error(f"Missing template variable: {e}")
        raise


async def send_email_with_logo(to_email: str, subject: str, html_content: str):
    """Send email via Zoho ZeptoMail API"""

    url = "https://api.zeptomail.com/v1.1/email"
    print("Token: ", os.getenv("ZEPTOMAIL_TOKEN"))

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": os.getenv("ZEPTOMAIL_TOKEN"),
    }

    payload = {
        "from": {"address": "noreply@promptlylearn.app"},
        "to": [
            {
                "email_address": {
                    "address": to_email,
                    "name": to_email.split("@")[0],  # Use email prefix as name
                }
            }
        ],
        "subject": subject,
        "htmlbody": html_content,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)

            if 200 <= response.status_code < 300:
                logger.info(f"Email sent to {to_email} via ZeptoMail")
                return True
            else:
                logger.error(f"ZeptoMail error {response.status_code}: {response.text}")
                return False

    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False


async def send_welcome_email(to_email: str, user_name: str):
    """
    Send welcome email to new user with logo.

    Args:
        to_email: Recipient email address
        user_name: Name of the user

    Returns:
        Dict with success status and message
    """
    app_url = os.getenv("APP_URL")
    subject = "Welcome to PromptlyLearn! 🚀"

    # Load and render the HTML template
    try:
        html_content = load_email_template(
            WELCOME_TEMPLATE_PATH, user_name=user_name, app_url=app_url
        )
    except Exception as e:
        logger.error(f"Error loading email template: {e}")
        return {"success": False, "message": f"Template error: {str(e)}"}

    return await send_email_with_logo(to_email, subject, html_content)


async def send_account_deletion_email(
    to_email: str, user_name: str, subscription_cancelled: bool = False
):
    """
    Send account deletion confirmation email.

    Args:
        to_email: Recipient email address
        user_name: Name of the user
        subscription_cancelled: Whether the user had an active subscription

    Returns:
        Dict with success status and message
    """
    app_url = os.getenv("APP_URL")
    subject = "Your Account Has Been Deleted"

    # Prepare subscription info section
    subscription_info = ""
    if subscription_cancelled:
        subscription_info = """
        <div style="background-color: #fef3c7; border-left: 4px solid #f59e0b; padding: 20px; margin: 0 0 30px 0; border-radius: 4px;">
            <h3 style="color: #92400e; margin: 0 0 10px 0; font-size: 16px;">Premium Subscription Cancelled</h3>
            <p style="color: #78350f; font-size: 14px; margin: 0; line-height: 1.6;">
                Your premium subscription has been cancelled. You will not be charged again. Any pending charges have been voided.
            </p>
        </div>
        """

    # Prepare subscription list item
    subscription_list_item = (
        "<li>Your premium subscription has been cancelled</li>"
        if subscription_cancelled
        else ""
    )

    # Load and render the HTML template
    try:
        html_content = load_email_template(
            DELETION_TEMPLATE_PATH,
            user_name=user_name,
            app_url=app_url,
            subscription_info=subscription_info,
            subscription_list_item=subscription_list_item,
        )
    except Exception as e:
        logger.error(f"Error loading email template: {e}")
        return {"success": False, "message": f"Template error: {str(e)}"}

    return await send_email_with_logo(to_email, subject, html_content)


async def send_subscription_receipt_email(
    to_email: str,
    user_name: str,
    invoice_number: str,
    amount: str,
    billing_period: str,
    invoice_url: str,
    payment_date: str = None,
):
    """
    Send subscription payment receipt email.

    Args:
        to_email: Recipient email address
        user_name: Name of the user
        invoice_number: Stripe invoice number
        amount: Amount paid (formatted, e.g., "$9.99")
        billing_period: Billing period (e.g., "Monthly", "Jan 10, 2026 - Feb 10, 2026")
        invoice_url: URL to view the invoice on Stripe
        payment_date: Date of payment (defaults to current date if not provided)

    Returns:
        Dict with success status and message
    """
    app_url = os.getenv("APP_URL")
    subject = "Your PromptlyLearn Premium Receipt 🧾"

    # Use current date if payment_date not provided
    if not payment_date:
        payment_date = datetime.now().strftime("%B %d, %Y")

    # Load and render the HTML template
    try:
        html_content = load_email_template(
            SUBSCRIPTION_RECEIPT_TEMPLATE_PATH,
            user_name=user_name,
            invoice_number=invoice_number,
            payment_date=payment_date,
            billing_period=billing_period,
            amount=amount,
            app_url=app_url,
            invoice_url=invoice_url,
        )
    except Exception as e:
        logger.error(f"Error loading email template: {e}")
        return {"success": False, "message": f"Template error: {str(e)}"}

    return await send_email_with_logo(to_email, subject, html_content)


async def send_subscription_cancellation_email(
    to_email: str, user_name: str, access_until: str, access_until_short: str = None
):
    """
    Send subscription cancellation confirmation email.

    Args:
        to_email: Recipient email address
        user_name: Name of the user
        access_until: Full formatted date when access ends (e.g., "February 10, 2026 at 11:59 PM")
        access_until_short: Short formatted date (e.g., "Feb 10, 2026") - defaults to access_until if not provided

    Returns:
        Dict with success status and message
    """
    app_url = os.getenv("APP_URL")
    subject = "Your PromptlyLearn Subscription Has Been Cancelled"

    # Use access_until for short version if not provided
    if not access_until_short:
        access_until_short = access_until

    # Load and render the HTML template
    try:
        html_content = load_email_template(
            SUBSCRIPTION_CANCELLATION_TEMPLATE_PATH,
            user_name=user_name,
            access_until=access_until,
            access_until_short=access_until_short,
            app_url=app_url,
        )
    except Exception as e:
        logger.error(f"Error loading email template: {e}")
        return {"success": False, "message": f"Template error: {str(e)}"}

    return await send_email_with_logo(to_email, subject, html_content)


async def send_password_reset_email(
    email: str, reset_token: str, user_name: str = None
):
    """
    Send password reset email using HTML template from static folder.

    Args:
        email: User's email address
        reset_token: The reset token
        username: User's username (optional)
    """
    # Get frontend URL from environment
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    reset_link = f"{frontend_url}/reset-password?token={reset_token}"

    # Load HTML template from static folder
    try:
        html_template = load_email_template(
            FORGOT_PASSWORD_TEMPLATE_PATH, user_name=user_name, reset_link=reset_link
        )
    except FileNotFoundError as e:
        logger.error(f"Failed to load email template: {e}")
        raise

    # Send email using your existing function
    result = await send_email_with_logo(
        to_email=email,
        subject="Reset Your Password - NO REPLY",
        html_content=html_template,
    )

    logger.info(f"Password reset email sent to {email}: {result}")
    return result
