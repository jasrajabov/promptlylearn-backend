from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import logging

from src import deps
from src.schema import (
    RoadmapNodeCourseIdUpdate,
    RoadmapNodeResponse,
    RoadmapRequest,
    RoadmapResponseSchema,
    StatusUpdateSchema,
)
from src.models import Roadmap, RoadmapNode, Status
from src.models import User
from src.tasks.generate_roadmap import generate_roadmap_outline
from sqlalchemy.orm import selectinload
from src.utils.credit_helper import consume_credits

# Create logger for this module
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/roadmap", tags=["roadmap"])


@router.post("/generate-roadmap")
async def generate_roadmap(
    request: RoadmapRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    logger.info(f"User {current_user.id} generating roadmap: {request.roadmap_name}")

    GENERATION_COST = 10

    try:
        consume_credits(current_user, db, GENERATION_COST)
        logger.debug(f"Credits consumed: {GENERATION_COST} for user {current_user.id}")
    except Exception as e:
        logger.error(f"Credit consumption failed for user {current_user.id}: {str(e)}")
        raise

    roadmap_name = request.roadmap_name.strip()
    custom_prompt = request.custom_prompt.strip() if request.custom_prompt else None

    roadmap = Roadmap(
        roadmap_name=roadmap_name,
        description=f"AI-generated roadmap for {roadmap_name}",
        user_id=current_user.id,
        nodes_json=None,
        edges_json=None,
        status=Status.GENERATING,
        task_id=None,
    )
    db.add(roadmap)
    db.commit()
    db.refresh(roadmap)

    logger.info(f"Roadmap created with ID: {roadmap.id} for user {current_user.id}")

    task = generate_roadmap_outline.delay(
        roadmap_name=request.roadmap_name,
        roadmap_id=roadmap.id,
        user_id=current_user.id,
        custom_prompt=custom_prompt,
    )
    roadmap.task_id = task.id
    db.commit()

    logger.info(f"Roadmap generation task started: {task.id} for roadmap {roadmap.id}")
    return {"task_id": task.id, "roadmap_id": roadmap.id, "status": "GENERATING"}


@router.get("/get_all_roadmaps", response_model=list[RoadmapResponseSchema])
async def get_all_roadmaps(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    logger.info(f"Fetching all roadmaps for user: {current_user.id}")

    roadmaps = db.query(Roadmap).filter(Roadmap.user_id == current_user.id).all()

    logger.info(f"Found {len(roadmaps)} roadmaps for user {current_user.id}")

    return [
        RoadmapResponseSchema(
            id=roadmap.id,
            roadmap_name=roadmap.roadmap_name,
            nodes_json=[
                RoadmapNodeResponse.model_validate(node) for node in roadmap.nodes
            ],
            edges_json=roadmap.edges_json or [],
            status=roadmap.status,
            task_id=roadmap.task_id,
            created_at=roadmap.created_at,
            description=roadmap.description,
        )
        for roadmap in roadmaps
    ]


@router.get("/{roadmap_id}", response_model=RoadmapResponseSchema)
async def get_generated_roadmap(
    roadmap_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    logger.info(f"User {current_user.id} fetching roadmap: {roadmap_id}")

    roadmap = (
        db.query(Roadmap)
        .filter(Roadmap.id == roadmap_id, Roadmap.user_id == current_user.id)
        .first()
    )

    if not roadmap:
        logger.warning(f"Roadmap {roadmap_id} not found for user {current_user.id}")
        raise HTTPException(status_code=404, detail="Roadmap not found")

    logger.debug(
        f"Roadmap {roadmap_id} retrieved successfully with {len(roadmap.nodes)} nodes"
    )

    return RoadmapResponseSchema(
        id=roadmap.id,
        roadmap_name=roadmap.roadmap_name,
        nodes_json=[RoadmapNodeResponse.model_validate(node) for node in roadmap.nodes],
        edges_json=roadmap.edges_json or [],
        created_at=roadmap.created_at,
        status=roadmap.status,
        description=roadmap.description,
    )


@router.delete("/{roadmap_id}")
async def delete_roadmap(
    roadmap_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    logger.info(f"User {current_user.id} attempting to delete roadmap: {roadmap_id}")

    roadmap = (
        db.query(Roadmap)
        .filter(Roadmap.id == roadmap_id, Roadmap.user_id == current_user.id)
        .first()
    )

    if not roadmap:
        logger.warning(
            f"Delete failed: Roadmap {roadmap_id} not found for user {current_user.id}"
        )
        raise HTTPException(status_code=404, detail="Roadmap not found")

    db.delete(roadmap)
    db.commit()

    logger.info(f"Roadmap {roadmap_id} deleted successfully by user {current_user.id}")

    return {"detail": "Roadmap deleted successfully"}


@router.patch("/{roadmap_id}/status", response_model=dict)
async def update_roadmap_status(
    roadmap_id: str,
    payload: StatusUpdateSchema,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    status = payload.status
    roadmap = (
        db.query(Roadmap)
        .filter(Roadmap.id == roadmap_id, Roadmap.user_id == current_user.id)
        .first()
    )
    if not roadmap:
        logger.warning(
            f"Status update failed: Roadmap {roadmap_id} not found for user {current_user.id}"
        )
        raise HTTPException(status_code=404, detail="Roadmap not found")

    roadmap.status = status
    db.commit()
    db.refresh(roadmap)
    return {"roadmap_id": roadmap.id, "status": roadmap.status}


@router.patch("/{roadmap_id}/{roadmap_node_id}/status", response_model=dict)
async def update_roadmap_node(
    roadmap_id: str,
    roadmap_node_id: str,
    payload: StatusUpdateSchema,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    roadmap = (
        db.query(Roadmap)
        .options(selectinload(Roadmap.nodes))
        .filter(Roadmap.id == roadmap_id, Roadmap.user_id == current_user.id)
        .first()
    )
    if not roadmap:
        logger.warning(
            f"Node update failed: Roadmap {roadmap_id} not found for user {current_user.id}"
        )
        raise HTTPException(status_code=404, detail="Roadmap not found")

    node = next((n for n in roadmap.nodes if n.node_id == roadmap_node_id), None)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    node.status = payload.status
    db.commit()
    db.refresh(roadmap)

    statuses = [n.status for n in roadmap.nodes]

    if all(s == Status.NOT_STARTED for s in statuses):
        roadmap.status = Status.NOT_STARTED
    elif all(s == Status.COMPLETED for s in statuses):
        roadmap.status = Status.COMPLETED
    else:
        roadmap.status = Status.IN_PROGRESS

    db.commit()
    db.refresh(roadmap)

    return {
        "node_id": node.node_id,
        "node_status": node.status,
        "roadmap_status": roadmap.status,
    }


@router.post("/{roadmap_id}", response_model=RoadmapResponseSchema)
async def update_roadmap(
    roadmap_id: str,
    request: RoadmapNodeCourseIdUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    roadmap = (
        db.query(Roadmap)
        .filter(Roadmap.id == roadmap_id, Roadmap.user_id == current_user.id)
        .first()
    )
    if not roadmap:
        raise HTTPException(status_code=404, detail="Roadmap not found")

    node = (
        db.query(RoadmapNode)
        .filter(
            RoadmapNode.node_id == str(request.node_id),
            RoadmapNode.roadmap_id == roadmap.id,
        )
        .first()
    )
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    node.course_id = request.course_id
    db.commit()
    db.refresh(roadmap)
    return RoadmapResponseSchema.model_validate(roadmap)
