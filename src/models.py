from sqlalchemy import Column, DateTime, Integer, String, Enum, func
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


# --- SQLAlchemy models ---
class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)

    # Relationship: a user can have many courses
    courses = relationship(
        "Course", back_populates="user", cascade="all, delete-orphan"
    )

    # Relationship: a user can have many roadmaps (tracks)
    roadmaps = relationship(
        "Roadmap", back_populates="user", cascade="all, delete-orphan"
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
    level = Column(String, nullable=False)  # "beginner", "intermediate", "advanced"

    # Foreign key to user
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    user = relationship("User", back_populates="courses")

    # Relationship: course -> modules
    modules = relationship(
        "Module", back_populates="course", cascade="all, delete-orphan"
    )
    task_id = Column(String, nullable=True)  # Celery task ID for tracking generation
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

    course = relationship("Course", back_populates="modules")
    lessons = relationship(
        "Lesson", back_populates="module", cascade="all, delete-orphan"
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
    module = relationship("Module", back_populates="lessons")
    status = Column(
        Enum(Status, name="lesson_status"), default=Status.NOT_GENERATED, nullable=True
    )


# --------------------- ROADMAP ----------------------
class Roadmap(Base):
    __tablename__ = "roadmaps"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String, nullable=True)  # Celery task ID for tracking generation
    roadmap_name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Enum(Status, name="roadmap_status"), nullable=True)

    # Link to user who created it
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user = relationship("User", back_populates="roadmaps")

    # JSON representation for quick access or AI output
    nodes_json = Column(JSON, nullable=True)
    edges_json = Column(JSON, nullable=True)

    # Relationship to structured nodes
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
    label = Column(String, nullable=False)  # e.g. “Learn Python Basics”
    description = Column(Text, nullable=True)
    order_index = Column(Integer, nullable=True)  # for sorting/flow layout order
    type = Column(String, nullable=True)  # e.g. “core”, “project”, “optional”

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
