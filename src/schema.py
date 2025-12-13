from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from typing import List, Literal, Union
from .enums import Language


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


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseSchema):
    id: str
    email: str
    name: str | None = None


class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserOut


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


# ----------------------
# Clarification schemas
# ----------------------
class ClarificationAnswerBlockSchema(BaseSchema):
    text: str
    code: Union[str, List[str], None] = None
    code_language: Language | None = None  # maps to code_language in DB
    output: Union[str, List[str], None] = None

    model_config = {
        "from_attributes": True,
        "populate_by_name": True,
        "alias_generator": lambda field: {"codeLanguage": "code_language"}.get(
            field, field
        ),
    }


class ClarificationBlockSchema(BaseSchema):
    question: str
    answers: List[ClarificationAnswerBlockSchema] = Field(default_factory=list)


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


class GenerateModuleRequest(BaseModel):
    course_id: str
    course_title: str
    module: ModuleSchema


class ClarifyLessonRequest(BaseModel):
    question: str
    content_block_id: str
    content: str


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
    roadmap_name: str = Field(..., description="Name of the roadmap to generate")


class RoadmapNodeCourseIdUpdate(BaseModel):
    node_id: str = Field(..., description="ID of the roadmap node to update")
    course_id: str = Field(
        ..., description="ID of the course to associate with the node"
    )


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
