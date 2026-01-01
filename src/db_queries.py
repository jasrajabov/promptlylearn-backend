import uuid
from sqlalchemy.orm import Session
from src.models import (
    Course as CourseORM,
    Module as ModuleORM,
    Lesson as LessonORM,
    Roadmap as RoadmapORM,
    RoadmapNode,
    Status,
)
import logging

logging.basicConfig(level=logging.INFO)


def save_roadmap(roadmap_id: str, roadmap_data: dict, db: Session, user_id: int):
    """
    Save a learning roadmap including tracks, courses, modules, and lessons.
    """
    # Create roadmap
    print("Saving roadmap data:", roadmap_data)
    roadmap = db.query(RoadmapORM).filter_by(id=roadmap_id).first()
    roadmap.roadmap_name = roadmap_data.get("roadmap_name", roadmap.roadmap_name)
    roadmap.edges_json = roadmap_data.get("edges", [])
    roadmap.description = roadmap_data.get("description", roadmap.description)
    db.flush()
    # Add courses
    for idx, node_data in enumerate(roadmap_data.get("nodes", [])):
        node = RoadmapNode(
            node_id=node_data.get("node_id") or str(uuid.uuid4()),
            roadmap_id=roadmap.id,
            label=node_data.get("label"),
            description=node_data.get("description"),
            type=node_data.get("type"),
            order_index=node_data.get("order_index") or idx,
            status=Status.NOT_STARTED,
        )
        db.add(node)
    db.commit()
    db.refresh(roadmap)


def save_course_outline_with_modules(
    course_id: str, db: Session, user_id: int, course_data: dict
):
    course = db.query(CourseORM).filter_by(id=course_id).first()

    course.title = course_data.get("title", course.title)
    course.description = course_data.get("description", course.description)

    db.flush()
    logging.info("Updating course: %s %s", course.id, course.title)

    for module_index, mod_data in enumerate(course_data.get("modules", [])):
        module = ModuleORM(
            id=str(uuid.uuid4()),
            title=mod_data["title"],
            course=course,
            order_index=module_index,
        )
        db.add(module)

        for lesson_index, lesson_data in enumerate(mod_data.get("lessons", [])):
            lesson = LessonORM(
                id=str(uuid.uuid4()),
                title=lesson_data["title"],
                module=module,
                user_id=user_id,
                order_index=lesson_index,
            )
            db.add(lesson)

    db.commit()
    db.refresh(course)
    return course


def save_lesson(db: Session, course_id: str, module_id: str, lesson_data):
    lesson = db.query(LessonORM).filter_by(id=lesson_data.id).first()
    if lesson:
        lesson.title = lesson_data.title
        lesson.module_id = module_id
    else:
        lesson = LessonORM(
            id=lesson_data.id or str(uuid.uuid4()),
            title=lesson_data.title,
            module_id=module_id,
        )
    lesson.status = Status.IN_PROGRESS
    lesson.content = lesson_data.content
    db.add(lesson)
    db.commit()
    db.refresh(lesson)

    course = db.query(CourseORM).filter(CourseORM.id == course_id).first()
    print("Course fetched for lesson update:", course.status)
    if course and course.status == Status.NOT_GENERATED:
        course.status = Status.IN_PROGRESS
        db.commit()
        db.refresh(course)
    return lesson
