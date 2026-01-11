"""
Script to create an admin user for the AI Course Generator
Usage:
  # Recommended (secure):
  python create_admin.py

  # With email only (prompts for password):
  python create_admin.py --email admin@example.com

  # Full CLI (NOT RECOMMENDED - password in history):
  python create_admin.py --email admin@example.com --password SecurePass123
"""

import sys
import os
from getpass import getpass
import uuid
from datetime import datetime
import logging

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import SessionLocal
from src.models import User, UserRole, UserStatus, MembershipStatus, AdminAuditLog
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("admin_creation.log"), logging.StreamHandler()],
)


def hash_password(password: str) -> str:
    """Hash a password for storing."""
    return pwd_context.hash(password)


def create_admin_user(
    email: str,
    password: str,
    name: str = "Admin User",
    role: UserRole = UserRole.SUPER_ADMIN,
):
    """Create an admin user in the database."""

    db = SessionLocal()

    try:
        # Check if user already exists
        existing_user = db.query(User).filter(User.email == email).first()

        if existing_user:
            logging.warning(f"User with email {email} already exists!")
            print(f"❌ User with email {email} already exists!")

            # Ask if they want to promote to admin
            promote = input("Would you like to promote this user to admin? (y/n): ")
            if promote.lower() == "y":
                old_role = existing_user.role
                existing_user.role = role
                existing_user.status = UserStatus.ACTIVE
                existing_user.is_email_verified = True
                db.commit()

                # Log the action
                audit_log = AdminAuditLog(
                    id=str(uuid.uuid4()),
                    admin_user_id=existing_user.id,
                    target_user_id=existing_user.id,
                    action="PROMOTE_TO_ADMIN_VIA_CLI",
                    entity_type="user",
                    entity_id=existing_user.id,
                    details={
                        "old_role": old_role.value,
                        "new_role": role.value,
                        "method": "CLI script",
                    },
                    created_at=datetime.utcnow(),
                )
                db.add(audit_log)
                db.commit()

                logging.info(
                    f"User {email} promoted from {old_role.value} to {role.value}"
                )
                print(f"✅ User promoted to {role.value}")
            return

        # Create new admin user
        admin_user = User(
            id=str(uuid.uuid4()),
            email=email,
            hashed_password=hash_password(password),
            name=name,
            role=role,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
            membership_plan="premium",
            membership_status=MembershipStatus.ACTIVE,
            credits=10000,  # Give admin plenty of credits
            total_credits_used=0,
            login_count=0,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)

        # Log the action
        audit_log = AdminAuditLog(
            id=str(uuid.uuid4()),
            admin_user_id=admin_user.id,
            target_user_id=admin_user.id,
            action="CREATE_ADMIN_VIA_CLI",
            entity_type="user",
            entity_id=admin_user.id,
            details={"role": role.value, "method": "CLI script", "email": email},
            created_at=datetime.utcnow(),
        )
        db.add(audit_log)
        db.commit()

        logging.info(f"Admin user created: {email} with role {role.value}")
        print("✅ Admin user created successfully!")
        print(f"   Email: {email}")
        print(f"   Role: {role.value}")
        print(f"   ID: {admin_user.id}")
        print("\n📝 Action logged in admin_creation.log")

    except Exception as e:
        db.rollback()
        logging.error(f"Error creating admin user: {str(e)}")
        print(f"❌ Error creating admin user: {str(e)}")
        raise
    finally:
        db.close()


def interactive_create_admin():
    """Interactive prompt to create admin user."""

    print("=" * 50)
    print("Create Admin User for AI Course Generator")
    print("=" * 50)
    print()

    # Get email
    while True:
        email = input("Admin email: ").strip()
        if email and "@" in email:
            break
        print("❌ Please enter a valid email address")

    # Get name
    name = input("Admin name (default: 'Admin User'): ").strip()
    if not name:
        name = "Admin User"

    # Get password
    while True:
        password = getpass("Password (min 8 characters): ")
        if len(password) >= 8:
            password_confirm = getpass("Confirm password: ")
            if password == password_confirm:
                break
            print("❌ Passwords don't match")
        else:
            print("❌ Password must be at least 8 characters")

    # Get role
    print("\nSelect role:")
    print("1. Super Admin (full access)")
    print("2. Admin (limited access)")

    role_choice = input("Choice (1 or 2, default: 1): ").strip()
    role = UserRole.SUPER_ADMIN if role_choice != "2" else UserRole.ADMIN

    print()
    print("Creating admin user...")
    create_admin_user(email, password, name, role)


def main():
    """Main function."""
    import argparse

    parser = argparse.ArgumentParser(description="Create admin user")
    parser.add_argument("--email", help="Admin email")
    parser.add_argument("--name", help="Admin name", default="Admin User")
    parser.add_argument(
        "--role",
        choices=["admin", "super_admin"],
        default="super_admin",
        help="Admin role",
    )
    # SECURITY: Never accept password as CLI argument in production
    # It would be visible in process list and command history
    parser.add_argument(
        "--password",
        help="Admin password (NOT RECOMMENDED - use interactive mode instead)",
    )

    args = parser.parse_args()

    # Warn if password provided via CLI
    if args.password:
        print("⚠️  WARNING: Providing password via CLI argument is insecure!")
        print("   Password will be visible in command history and process list.")
        print("   Use interactive mode instead (run without --password flag)")
        print()
        confirm = input("Continue anyway? (yes/no): ")
        if confirm.lower() != "yes":
            print("Aborted.")
            return

    # If email provided but no password, prompt for it
    if args.email and not args.password:
        print(f"Creating admin user: {args.email}")
        while True:
            password = getpass("Password (min 8 characters): ")
            if len(password) >= 8:
                password_confirm = getpass("Confirm password: ")
                if password == password_confirm:
                    break
                print("❌ Passwords don't match")
            else:
                print("❌ Password must be at least 8 characters")

        role = UserRole.SUPER_ADMIN if args.role == "super_admin" else UserRole.ADMIN
        create_admin_user(args.email, password, args.name, role)
    elif args.email and args.password:
        # Both provided - use them (with warning already shown)
        role = UserRole.SUPER_ADMIN if args.role == "super_admin" else UserRole.ADMIN
        create_admin_user(args.email, args.password, args.name, role)
    else:
        # Otherwise, use fully interactive mode
        interactive_create_admin()


if __name__ == "__main__":
    main()
