import os
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from typing import List, Optional
from datetime import datetime, timedelta
import uuid

from src.models import (
    User,
    UserRole,
    UserStatus,
    MembershipStatus,
    Course,
    Roadmap,
    Status,
    AdminAuditLog,
)
from src.schema import (
    UserSummary,
    UserDetailResponse,
    UpdateUserCredits,
    UpdateUserMembership,
    SuspendUserRequest,
    UpdateUserRole,
    UpdateAdminNotes,
    DashboardStats,
    AuditLogEntry,
    PaginatedResponse,
    AdminActionResponse,
)
from src.deps import require_admin, require_super_admin, get_db

router = APIRouter(prefix="/admin", tags=["Admin"])


# --- Helper Functions ---
@router.post("/setup/create-first-admin")
async def create_first_admin(
    email: str,
    password: str,
    secret: str,  # Environment variable
    db: Session = Depends(get_db),
):
    """Create first admin - only works if no admins exist"""

    # Check secret key
    if secret != os.getenv("ADMIN_SETUP_SECRET"):
        raise HTTPException(status_code=403)

    # Check if any admin exists
    admin_exists = (
        db.query(User)
        .filter(User.role.in_([UserRole.ADMIN, UserRole.SUPER_ADMIN]))
        .first()
    )

    if admin_exists:
        raise HTTPException(status_code=400, detail="Admin already exists")


async def log_admin_action(
    db: Session,
    admin_user: User,
    action: str,
    target_user_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> str:
    """Log admin action to audit trail"""
    audit_log = AdminAuditLog(
        id=str(uuid.uuid4()),
        admin_user_id=admin_user.id,
        target_user_id=target_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        ip_address=ip_address,
    )
    db.add(audit_log)
    db.commit()
    return audit_log.id


def get_client_ip(request: Request) -> str:
    """Extract client IP from request"""
    if request.client:
        return request.client.host
    return "unknown"


# --- Dashboard & Statistics ---


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard_stats(
    current_user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Get dashboard statistics"""

    # User stats
    total_users = db.query(User).filter(User.status != UserStatus.DELETED).count()
    active_users = db.query(User).filter(User.status == UserStatus.ACTIVE).count()
    suspended_users = db.query(User).filter(User.status == UserStatus.SUSPENDED).count()
    premium_users = db.query(User).filter(User.membership_plan == "premium").count()
    free_users = db.query(User).filter(User.membership_plan == "free").count()

    # Course stats
    total_courses = db.query(Course).count()
    completed_courses = (
        db.query(Course).filter(Course.status == Status.COMPLETED).count()
    )
    failed_courses = db.query(Course).filter(Course.status == Status.FAILED).count()
    generating_courses = (
        db.query(Course).filter(Course.status == Status.GENERATING).count()
    )

    # Roadmap stats
    total_roadmaps = db.query(Roadmap).count()
    completed_roadmaps = (
        db.query(Roadmap).filter(Roadmap.status == Status.COMPLETED).count()
    )

    # Credits stats
    credits_result = (
        db.query(
            func.sum(User.credits).label("total_issued"),
            func.sum(User.total_credits_used).label("total_used"),
        )
        .filter(User.status != UserStatus.DELETED)
        .first()
    )

    total_credits_issued = credits_result.total_issued or 0
    total_credits_used = credits_result.total_used or 0

    # New users
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    new_users_today = db.query(User).filter(User.created_at >= today_start).count()
    new_users_this_week = db.query(User).filter(User.created_at >= week_start).count()
    new_users_this_month = db.query(User).filter(User.created_at >= month_start).count()

    return DashboardStats(
        total_users=total_users,
        active_users=active_users,
        suspended_users=suspended_users,
        premium_users=premium_users,
        free_users=free_users,
        total_courses=total_courses,
        completed_courses=completed_courses,
        failed_courses=failed_courses,
        generating_courses=generating_courses,
        total_roadmaps=total_roadmaps,
        completed_roadmaps=completed_roadmaps,
        total_credits_issued=total_credits_issued,
        total_credits_used=total_credits_used,
        new_users_today=new_users_today,
        new_users_this_week=new_users_this_week,
        new_users_this_month=new_users_this_month,
    )


# --- User Management ---


@router.get("/users", response_model=PaginatedResponse)
async def list_users(
    search: Optional[str] = None,
    role: Optional[UserRole] = None,
    status_filter: Optional[UserStatus] = None,
    membership_plan: Optional[str] = None,
    membership_status: Optional[MembershipStatus] = None,
    is_email_verified: Optional[bool] = None,
    skip: int = 0,
    limit: int = 50,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List users with filtering and pagination"""

    query = db.query(User).filter(User.status != UserStatus.DELETED)

    # Apply filters
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(User.email.ilike(search_term), User.name.ilike(search_term))
        )

    if role:
        query = query.filter(User.role == role)

    if status_filter:
        query = query.filter(User.status == status_filter)

    if membership_plan:
        query = query.filter(User.membership_plan == membership_plan)

    if membership_status:
        query = query.filter(User.membership_status == membership_status)

    if is_email_verified is not None:
        query = query.filter(User.is_email_verified == is_email_verified)

    # Get total count
    total = query.count()

    # Apply sorting
    sort_column = getattr(User, sort_by, User.created_at)
    if sort_order == "asc":
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())

    # Apply pagination
    users = query.offset(skip).limit(limit).all()

    # Convert to response
    user_summaries = [UserSummary.model_validate(user) for user in users]

    return PaginatedResponse(
        items=[user.model_dump() for user in user_summaries],
        total=total,
        skip=skip,
        limit=limit,
        has_more=(skip + limit) < total,
    )


@router.get("/users/{user_id}", response_model=UserDetailResponse)
async def get_user_details(
    user_id: str,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Get detailed user information"""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Compute statistics
    total_courses = db.query(Course).filter(Course.user_id == user_id).count()
    total_roadmaps = db.query(Roadmap).filter(Roadmap.user_id == user_id).count()
    completed_courses = (
        db.query(Course)
        .filter(and_(Course.user_id == user_id, Course.status == Status.COMPLETED))
        .count()
    )

    user_dict = UserDetailResponse.model_validate(user).model_dump()
    user_dict.update(
        {
            "total_courses": total_courses,
            "total_roadmaps": total_roadmaps,
            "completed_courses": completed_courses,
        }
    )

    return user_dict


@router.patch("/users/{user_id}/credits", response_model=AdminActionResponse)
async def update_user_credits(
    user_id: str,
    payload: UpdateUserCredits,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update user credit balance"""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    old_credits = user.credits
    user.credits = payload.credits

    if payload.reset_at:
        user.credits_reset_at = payload.reset_at

    db.commit()

    # Log action
    audit_log_id = await log_admin_action(
        db=db,
        admin_user=current_user,
        action="UPDATE_CREDITS",
        target_user_id=user_id,
        entity_type="user",
        entity_id=user_id,
        details={
            "old_credits": old_credits,
            "new_credits": payload.credits,
            "reason": payload.reason,
        },
        ip_address=get_client_ip(request),
    )

    return AdminActionResponse(
        success=True,
        message=f"Credits updated from {old_credits} to {payload.credits}",
        user_id=user_id,
        audit_log_id=audit_log_id,
    )


@router.patch("/users/{user_id}/membership", response_model=AdminActionResponse)
async def update_user_membership(
    user_id: str,
    payload: UpdateUserMembership,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update user membership details"""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    old_plan = user.membership_plan
    old_status = user.membership_status

    user.membership_plan = payload.membership_plan
    user.membership_status = payload.membership_status

    if payload.membership_active_until:
        user.membership_active_until = payload.membership_active_until

    db.commit()

    # Log action
    audit_log_id = await log_admin_action(
        db=db,
        admin_user=current_user,
        action="UPDATE_MEMBERSHIP",
        target_user_id=user_id,
        entity_type="user",
        entity_id=user_id,
        details={
            "old_plan": old_plan,
            "new_plan": payload.membership_plan,
            "old_status": old_status.value,
            "new_status": payload.membership_status.value,
            "active_until": payload.membership_active_until.isoformat()
            if payload.membership_active_until
            else None,
            "reason": payload.reason,
        },
        ip_address=get_client_ip(request),
    )

    return AdminActionResponse(
        success=True,
        message=f"Membership updated to {payload.membership_plan} ({payload.membership_status.value})",
        user_id=user_id,
        audit_log_id=audit_log_id,
    )


@router.post("/users/{user_id}/suspend", response_model=AdminActionResponse)
async def suspend_user(
    user_id: str,
    payload: SuspendUserRequest,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Suspend a user account"""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if (
        user.role in [UserRole.ADMIN, UserRole.SUPER_ADMIN]
        and current_user.role != UserRole.SUPER_ADMIN
    ):
        raise HTTPException(
            status_code=403, detail="Only super admins can suspend other admins"
        )

    user.status = UserStatus.SUSPENDED
    user.suspended_at = datetime.utcnow()
    user.suspended_reason = payload.reason
    user.suspended_by = current_user.id

    db.commit()

    # Log action
    audit_log_id = await log_admin_action(
        db=db,
        admin_user=current_user,
        action="SUSPEND_USER",
        target_user_id=user_id,
        entity_type="user",
        entity_id=user_id,
        details={"reason": payload.reason, "duration_days": payload.duration_days},
        ip_address=get_client_ip(request),
    )

    return AdminActionResponse(
        success=True,
        message=f"User suspended: {payload.reason}",
        user_id=user_id,
        audit_log_id=audit_log_id,
    )


@router.post("/users/{user_id}/unsuspend", response_model=AdminActionResponse)
async def unsuspend_user(
    user_id: str,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Unsuspend a user account"""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.status = UserStatus.ACTIVE
    user.suspended_at = None
    user.suspended_reason = None
    user.suspended_by = None

    db.commit()

    # Log action
    audit_log_id = await log_admin_action(
        db=db,
        admin_user=current_user,
        action="UNSUSPEND_USER",
        target_user_id=user_id,
        entity_type="user",
        entity_id=user_id,
        details={},
        ip_address=get_client_ip(request),
    )

    return AdminActionResponse(
        success=True,
        message="User unsuspended successfully",
        user_id=user_id,
        audit_log_id=audit_log_id,
    )


@router.patch("/users/{user_id}/role", response_model=AdminActionResponse)
async def update_user_role(
    user_id: str,
    payload: UpdateUserRole,
    request: Request,
    current_user: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Update user role (super admin only)"""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    old_role = user.role
    user.role = payload.role

    db.commit()

    # Log action
    audit_log_id = await log_admin_action(
        db=db,
        admin_user=current_user,
        action="UPDATE_ROLE",
        target_user_id=user_id,
        entity_type="user",
        entity_id=user_id,
        details={
            "old_role": old_role.value,
            "new_role": payload.role.value,
            "reason": payload.reason,
        },
        ip_address=get_client_ip(request),
    )

    return AdminActionResponse(
        success=True,
        message=f"User role updated from {old_role.value} to {payload.role.value}",
        user_id=user_id,
        audit_log_id=audit_log_id,
    )


@router.patch("/users/{user_id}/notes", response_model=AdminActionResponse)
async def update_admin_notes(
    user_id: str,
    payload: UpdateAdminNotes,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update admin notes for a user"""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.admin_notes = payload.notes
    db.commit()

    # Log action
    audit_log_id = await log_admin_action(
        db=db,
        admin_user=current_user,
        action="UPDATE_NOTES",
        target_user_id=user_id,
        entity_type="user",
        entity_id=user_id,
        details={"notes_length": len(payload.notes)},
        ip_address=get_client_ip(request),
    )

    return AdminActionResponse(
        success=True,
        message="Admin notes updated",
        user_id=user_id,
        audit_log_id=audit_log_id,
    )


@router.delete("/users/{user_id}", response_model=AdminActionResponse)
async def delete_user(
    user_id: str,
    request: Request,
    current_user: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Permanently delete a user and all related data (super admin only)"""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.role in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
        raise HTTPException(status_code=403, detail="Cannot delete admin users")

    # Save user info before deletion
    user_email = user.email

    # IMPORTANT: Update ALL existing audit logs that reference this user
    # Set target_user_id to NULL for all audit logs referencing this user
    db.query(AdminAuditLog).filter(AdminAuditLog.target_user_id == user_id).update(
        {"target_user_id": None}
    )

    # Also check if this user performed any admin actions
    # (admin_user_id foreign key)
    db.query(AdminAuditLog).filter(AdminAuditLog.admin_user_id == user_id).update(
        {"admin_user_id": None}
    )

    db.flush()  # Apply updates before deletion

    # Log the deletion action (without target_user_id)
    audit_log_id = await log_admin_action(
        db=db,
        admin_user=current_user,
        action="DELETE_USER",
        target_user_id=None,
        entity_type="user",
        entity_id=user_id,
        details={"email": user_email, "user_id": user_id, "permanent": True},
        ip_address=get_client_ip(request),
    )

    # Delete user (cascade to courses/roadmaps/etc. if FK constraints allow)
    db.delete(user)
    db.commit()

    return AdminActionResponse(
        success=True,
        message=f"User {user_email} permanently deleted",
        user_id=user_id,
        audit_log_id=audit_log_id,
    )


# --- Audit Logs ---


@router.get("/audit-logs", response_model=List[AuditLogEntry])
async def get_audit_logs(
    admin_user_id: Optional[str] = None,
    target_user_id: Optional[str] = None,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Get audit logs with filtering"""

    query = db.query(AdminAuditLog).join(User, AdminAuditLog.admin_user_id == User.id)

    # Apply filters
    if admin_user_id:
        query = query.filter(AdminAuditLog.admin_user_id == admin_user_id)

    if target_user_id:
        query = query.filter(AdminAuditLog.target_user_id == target_user_id)

    if action:
        query = query.filter(AdminAuditLog.action == action)
