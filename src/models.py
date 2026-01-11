from sqlalchemy import Column, Boolean, DateTime, Integer, String, Enum, func
from .database import Base
from sqlalchemy import ForeignKey, Text, JSON
import uuid
import enum
from sqlalchemy.orm import relationship


class Status(enum.Enum):
    FAILED = "FAILED"
    GENERATING = "GENERATING"
    NOT_GENERATED = "NOT_GENERATED"
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class MembershipStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    CANCELED = "CANCELED"


class UserRole(enum.Enum):
    USER = "USER"
    ADMIN = "ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


class UserStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


# --- Enhanced User Model ---
class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    personal_info = Column(JSON, nullable=True)

    # Admin & Status fields
    role = Column(Enum(UserRole), default=UserRole.USER, nullable=False)
    status = Column(Enum(UserStatus), default=UserStatus.ACTIVE, nullable=False)
    is_email_verified = Column(Boolean, default=False)

    # Suspension/Ban tracking
    suspended_at = Column(DateTime, nullable=True)
    suspended_reason = Column(Text, nullable=True)
    suspended_by = Column(String, ForeignKey("users.id"), nullable=True)

    # Relationship: a user can have many courses
    courses = relationship(
        "Course", back_populates="user", cascade="all, delete-orphan"
    )

    # Relationship: a user can have many roadmaps (tracks)
    roadmaps = relationship(
        "Roadmap", back_populates="user", cascade="all, delete-orphan"
    )

    # Membership & Billing
    membership_plan = Column(String, default="free")  # free / premium
    membership_status = Column(
        Enum(MembershipStatus), default=MembershipStatus.INACTIVE
    )
    membership_active_until = Column(DateTime, nullable=True)
    stripe_customer_id = Column(String, nullable=True, index=True)

    # Credits system
    credits = Column(Integer, default=100)
    credits_reset_at = Column(DateTime, nullable=True)
    total_credits_used = Column(Integer, default=0)  # Lifetime tracking

    # Usage tracking
    last_login_at = Column(DateTime, nullable=True)
    login_count = Column(Integer, default=0)

    # Admin notes
    admin_notes = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at = Column(DateTime, nullable=True)  # Soft delete support


# ------------------- COURSE -------------------
class Course(Base):
    __tablename__ = "courses"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    roadmap_id = Column(
        String, ForeignKey("roadmaps.id", ondelete="CASCADE"), nullable=True, index=True
    )
    roadmap_node_id = Column(String, nullable=True)
    title = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    level = Column(String, nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    user = relationship("User", back_populates="courses")
    modules = relationship(
        "Module",
        back_populates="course",
        cascade="all, delete-orphan",
        order_by="Module.order_index",
    )
    task_id = Column(String, nullable=True)
    status = Column(
        Enum(Status, name="course_status"), default=Status.NOT_GENERATED, nullable=True
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )


# ------------------- MODULE -------------------
class Module(Base):
    __tablename__ = "modules"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    course_id = Column(
        String, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title = Column(String, nullable=False)
    order_index = Column(Integer, nullable=False, default=0)
    course = relationship("Course", back_populates="modules")
    lessons = relationship(
        "Lesson",
        back_populates="module",
        cascade="all, delete-orphan",
        order_by="Lesson.order_index",
    )
    status = Column(
        Enum(Status, name="module_status"), default=Status.NOT_GENERATED, nullable=True
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )


# ------------------- LESSON -------------------
class Lesson(Base):
    __tablename__ = "lessons"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    module_id = Column(
        String, ForeignKey("modules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title = Column(String, nullable=False)
    content = Column(Text, nullable=True)
    order_index = Column(Integer, nullable=False, default=0)
    module = relationship("Module", back_populates="lessons")
    status = Column(
        Enum(Status, name="lesson_status"), default=Status.NOT_GENERATED, nullable=True
    )


# --------------------- ROADMAP ----------------------
class Roadmap(Base):
    __tablename__ = "roadmaps"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String, nullable=True)
    roadmap_name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Enum(Status, name="roadmap_status"), nullable=True)

    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user = relationship("User", back_populates="roadmaps")

    nodes_json = Column(JSON, nullable=True)
    edges_json = Column(JSON, nullable=True)

    nodes = relationship(
        "RoadmapNode", back_populates="roadmap", cascade="all, delete-orphan"
    )

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# --------------------- ROADMAP NODE ----------------------
class RoadmapNode(Base):
    __tablename__ = "roadmap_nodes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    node_id = Column(String, nullable=False)
    roadmap_id = Column(
        String, ForeignKey("roadmaps.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(Enum(Status, name="roadmap_node_status"), nullable=True)
    label = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    order_index = Column(Integer, nullable=True)
    type = Column(String, nullable=True)

    roadmap = relationship("Roadmap", back_populates="nodes")

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# --------------------- ADMIN AUDIT LOG ----------------------
class AdminAuditLog(Base):
    """Track all admin actions for accountability"""

    __tablename__ = "admin_audit_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    admin_user_id = Column(
        String,
        ForeignKey("users.id", ondelete="SET NULL"),  # Added ondelete
        nullable=True,  # Changed to nullable
        index=True,
    )
    target_user_id = Column(
        String,
        ForeignKey("users.id", ondelete="SET NULL"),  # Added ondelete
        nullable=True,
        index=True,
    )

    action = Column(String, nullable=False)  # e.g., "UPDATE_CREDITS", "SUSPEND_USER"
    entity_type = Column(String, nullable=True)  # "user", "course", "roadmap"
    entity_id = Column(String, nullable=True)

    details = Column(JSON, nullable=True)  # Store before/after values
    ip_address = Column(String, nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
