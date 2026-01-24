from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from typing import List, Literal, Union

from src.models import MembershipStatus


# ----------------------
# Base schema with timestamps
# ----------------------
class BaseSchema(BaseModel):
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"from_attributes": True}


# ----------------------
# User schemas
# ----------------------
class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    personal_info: dict | None = None
    stripe_customer_id: str | None = None
    membership_status: MembershipStatus = MembershipStatus.INACTIVE
    membership_plan: str = "free"


class UserUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    personal_info: dict | None = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseSchema):
    id: str
    email: str
    name: str | None = None
    membership_status: MembershipStatus
    personal_info: dict | None = None
    membership_plan: str
    membership_active_until: datetime | None = None
    credits: int
    credits_reset_at: datetime | None = None


# Enums matching the model
class UserRole(str, Enum):
    USER = "USER"
    ADMIN = "ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


class UserStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


class MembershipStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    CANCELED = "CANCELED"


# --- Admin User Management Schemas ---


class UserListFilter(BaseModel):
    """Query parameters for filtering users"""

    search: str | None = None  # Search by name or email
    role: UserRole | None = None
    status: UserStatus | None = None
    membership_plan: str | None = None
    membership_status: MembershipStatus | None = None
    is_email_verified: bool | None = None
    skip: int = 0
    limit: int = 50
    sort_by: str = "created_at"
    sort_order: str = "desc"  # asc or desc


class UserSummary(BaseModel):
    """Lightweight user info for lists"""

    id: str
    name: str | None
    email: str
    role: UserRole
    status: UserStatus
    membership_plan: str
    membership_status: MembershipStatus
    credits: int
    is_email_verified: bool
    last_login_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class UserDetailResponse(BaseModel):
    """Full user details for admin view"""

    id: str
    name: str | None
    email: str
    role: UserRole
    status: UserStatus
    is_email_verified: bool

    # Suspension info
    suspended_at: datetime | None
    suspended_reason: str | None
    suspended_by: str | None

    # Membership & Billing
    membership_plan: str
    membership_status: MembershipStatus
    membership_active_until: datetime | None
    stripe_customer_id: str | None

    # Credits
    credits: int
    credits_reset_at: datetime | None
    total_credits_used: int

    # Usage
    last_login_at: datetime | None
    login_count: int

    # Admin notes
    admin_notes: str | None

    # Statistics (computed)
    total_courses: int = 0
    total_roadmaps: int = 0
    completed_courses: int = 0

    # Timestamps
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    class Config:
        from_attributes = True


class UpdateUserCredits(BaseModel):
    """Update user credits"""

    credits: int = Field(..., ge=0, description="New credit balance")
    reset_at: datetime | None = None
    reason: str = Field(..., min_length=3, max_length=500)


class UpdateUserMembership(BaseModel):
    """Update user membership"""

    membership_plan: str = Field(..., pattern="^(free|premium)$")
    membership_status: MembershipStatus
    membership_active_until: datetime | None = None
    reason: str = Field(..., min_length=3, max_length=500)


class SuspendUserRequest(BaseModel):
    """Suspend a user"""

    reason: str = Field(..., min_length=10, max_length=1000)
    duration_days: int | None = Field(None, ge=1, le=365)


class UpdateUserRole(BaseModel):
    """Change user role"""

    role: UserRole
    reason: str = Field(..., min_length=3, max_length=500)


class UpdateAdminNotes(BaseModel):
    """Update admin notes for a user"""

    notes: str = Field(..., max_length=5000)


class DeleteAccountRequest(BaseModel):
    password: str
    confirm_text: str


# --- Admin Statistics Schemas ---
class DashboardStats(BaseModel):
    """Overall dashboard statistics"""

    total_users: int
    active_users: int
    suspended_users: int
    premium_users: int
    free_users: int

    total_courses: int
    completed_courses: int
    failed_courses: int
    generating_courses: int

    total_roadmaps: int
    completed_roadmaps: int

    total_credits_issued: int
    total_credits_used: int

    new_users_today: int
    new_users_this_week: int
    new_users_this_month: int


class UserActivityStats(BaseModel):
    """User activity statistics"""

    user_id: str
    total_logins: int
    last_login: datetime | None
    courses_created: int
    roadmaps_created: int
    credits_used: int
    member_since: datetime


# --- Admin Audit Log Schemas ---


class AuditLogEntry(BaseModel):
    """Audit log entry"""

    id: str
    admin_user_id: str
    admin_email: str  # Populated via join
    target_user_id: str | None
    target_email: str | None  # Populated via join

    action: str
    entity_type: str | None
    entity_id: str | None
    details: dict | None
    ip_address: str | None

    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogFilter(BaseModel):
    """Filter audit logs"""

    admin_user_id: str | None = None
    target_user_id: str | None = None
    action: str | None = None
    entity_type: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    skip: int = 0
    limit: int = 100


# --- Response Models ---


class PaginatedResponse(BaseModel):
    """Generic paginated response"""

    items: List[dict]
    total: int
    skip: int
    limit: int
    has_more: bool


class AdminActionResponse(BaseModel):
    """Response for admin actions"""

    success: bool
    message: str
    user_id: str | None = None
    audit_log_id: str | None = None


class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserDetailResponse


# ----------------------
# Status enum
# ----------------------
class StatusEnum(str, Enum):
    GENERATING = "GENERATING"
    NOT_STARTED = "NOT_STARTED"
    NOT_GENERATED = "NOT_GENERATED"
    LOADING = "LOADING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StatusUpdateSchema(BaseModel):
    status: StatusEnum


class SegmentText(BaseModel):
    type: Literal["text"]
    content: str


class SegmentCode(BaseModel):
    type: Literal["code"]
    content: str
    code_language: str | None = None
    expected_output: str | None = None


Segment = Union[SegmentText, SegmentCode]


class ContentBlockSchema(BaseModel):
    id: str | None = None
    title: str | None = None
    segments: List[Segment] = Field(default_factory=list)
    output_language: str | None = None


# ----------------------
# Lesson schema
# ----------------------
class LessonSchema(BaseSchema):
    id: str
    title: str
    content: str | None = None
    status: StatusEnum = StatusEnum.NOT_GENERATED


# ----------------------
# Module schema
# ----------------------
class ModuleLightSchema(BaseSchema):
    id: str
    title: str
    status: StatusEnum = StatusEnum.NOT_GENERATED

    model_config = {"from_attributes": True}


class ModuleSchema(BaseSchema):
    id: str
    title: str
    lessons: List[LessonSchema] = Field(default_factory=list)
    quiz: List[dict] = Field(default_factory=list)
    status: StatusEnum = StatusEnum.NOT_GENERATED


# ----------------------
# Course schema
# ----------------------
class CourseSchema(BaseSchema):
    id: str
    title: str | None = None
    description: str | None = None
    level: Literal["beginner", "intermediate", "advanced"]
    modules: List[ModuleSchema] = Field(default_factory=list)
    status: StatusEnum = StatusEnum.IN_PROGRESS
    task_id: str | None = None
    roadmap_id: str | None = None
    roadmap_node_id: str | None = None


class CourseAllSchema(BaseSchema):
    id: str
    title: str | None = None
    description: str | None = None
    level: Literal["beginner", "intermediate", "advanced"]
    modules: List[ModuleLightSchema] = Field(default_factory=list)
    status: StatusEnum = StatusEnum.IN_PROGRESS
    task_id: str | None = None
    roadmap_id: str | None = None
    roadmap_node_id: str | None = None

    model_config = {"from_attributes": True}


# ----------------------
# Requests
# ----------------------
class GenerateCourseRequest(BaseModel):
    topic: str
    level: str
    roadmap_node_id: str | None = None
    roadmap_id: str | None = None
    custom_prompt: str | None = None


class GenerateModuleRequest(BaseModel):
    course_id: str
    course_title: str
    module: ModuleSchema


class GenerateQuizRequest(BaseModel):
    lesson_name: str
    content: list[str]
    num_questions: int = 10
    question_type: Literal["multiple_choice", "true_false", "short_answer"] = (
        "multiple_choice"
    )


class GenerateQuizResponse(BaseModel):
    questions: list[dict]


class RoadmapRequest(BaseModel):
    roadmap_name: str
    custom_prompt: str | None


class RoadmapNodeCourseIdUpdate(BaseModel):
    node_id: str
    course_id: str


class RoadmapNodeResponse(BaseModel):
    node_id: str
    label: str
    description: str | None = None
    type: str | None = None
    order_index: int | None = None
    course_id: str | None = None
    status: StatusEnum | None = None

    model_config = ConfigDict(from_attributes=True)


class RoadmapResponseSchema(BaseModel):
    id: str
    roadmap_name: str
    description: str | None = None
    nodes_json: List[RoadmapNodeResponse] = []
    edges_json: list[dict] | None = None
    status: StatusEnum = StatusEnum.NOT_STARTED
    model_config = ConfigDict(from_attributes=True)
    task_id: str | None = None
    created_at: datetime


class RoadmapNodeUpdateRequest(BaseModel):
    roadmap_id: str
    node_id: str
    label: str | None = None
    description: str | None = None
    type: str | None = None
    status: StatusEnum | None = None


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class ResetPasswordResponse(BaseModel):
    message: str
