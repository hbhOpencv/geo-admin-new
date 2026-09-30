"""API for the user-visible geochemical evidence workflow."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..delivery_text import sanitize_delivery_content
from ..models import (
    GeochemArtifact,
    GeochemCandidateClue,
    GeochemEvidenceLink,
    GeochemWorkflowRun,
)
from ..services.geochem_workflow_service import (
    clue_payload,
    create_workflow,
    run_correlation,
    run_reconstruction,
    run_variation,
    sync_workflow,
    workflow_payload,
)
from ..services.geochem_mining_service import available_options


router = APIRouter(tags=["geochem-workflows"])


class CreateWorkflowRequest(BaseModel):
    rule_set_id: str | None = None
    client_request_id: str | None = Field(default=None, max_length=120)


class RunCorrelationRequest(BaseModel):
    clue_id: str


class RunReconstructionRequest(BaseModel):
    element: str


def _workflow(db: Session, workflow_id: str) -> GeochemWorkflowRun:
    workflow = db.get(GeochemWorkflowRun, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="化学元素找矿工作流不存在")
    return workflow


@router.post("/models/{model_id}/geochem-workflows")
def create_model_workflow(
    model_id: int,
    payload: CreateWorkflowRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    try:
        workflow = create_workflow(
            db,
            model_id=model_id,
            rule_set_id=payload.rule_set_id,
            created_by=getattr(user, "id", None),
            client_request_id=payload.client_request_id,
        )
        return workflow_payload(db, workflow)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/models/{model_id}/geochem-workflows/latest")
def latest_model_workflow(
    model_id: int,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    workflow = (
        db.query(GeochemWorkflowRun)
        .filter(GeochemWorkflowRun.model_id == model_id)
        .order_by(GeochemWorkflowRun.created_at.desc())
        .first()
    )
    return workflow_payload(db, workflow) if workflow else None


@router.get("/geochem-workflows/{workflow_id}")
def get_workflow(
    workflow_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    return workflow_payload(db, _workflow(db, workflow_id))


@router.get("/geochem-workflows/{workflow_id}/status")
def get_workflow_status(
    workflow_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    return workflow_payload(db, _workflow(db, workflow_id))


@router.post("/geochem-workflows/{workflow_id}/actions/run-quality-check")
def run_workflow_quality_check(
    workflow_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    workflow = _workflow(db, workflow_id)
    options = available_options(db, workflow.model_id)
    workflow.stage = "variation_pending"
    workflow.status = "created"
    workflow.progress = 5
    db.commit()
    return {
        "workflow": workflow_payload(db, workflow),
        "quality": {
            "project_drillhole_count": options["geological_borehole_count"],
            "chemical_drillhole_count": options["chemical_borehole_count"],
            "missing_chemical_drillhole_count": options["missing_chemical_borehole_count"],
            "missing_chemical_drillhole_ids": options["missing_chemical_borehole_ids"],
            "element_count": len(options["elements"]),
            "assay_count": options["assay_count"],
            "limitations": [
                "无化验数据的钻孔仅作三维空间参照，不参与化学统计与插值。"
            ],
        },
    }


@router.post("/geochem-workflows/{workflow_id}/actions/run-variation")
def run_workflow_variation(
    workflow_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    workflow = _workflow(db, workflow_id)
    try:
        return {"job_id": run_variation(db, workflow), "workflow": workflow_payload(db, workflow)}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/geochem-workflows/{workflow_id}/actions/run-correlation")
def run_workflow_correlation(
    workflow_id: str,
    payload: RunCorrelationRequest,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    workflow = _workflow(db, workflow_id)
    try:
        job_id = run_correlation(db, workflow, payload.clue_id)
        return {"job_id": job_id, "workflow": workflow_payload(db, workflow)}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/geochem-workflows/{workflow_id}/clues/{clue_id}/actions/run-correlation")
@router.post("/geochem-workflows/{workflow_id}/clues/{clue_id}/correlation")
def run_clue_correlation(
    workflow_id: str,
    clue_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    workflow = _workflow(db, workflow_id)
    try:
        job_id = run_correlation(db, workflow, clue_id)
        return {"job_id": job_id, "workflow": workflow_payload(db, workflow)}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/geochem-workflows/{workflow_id}/clues")
def list_workflow_clues(
    workflow_id: str,
    kind: str | None = Query(default=None, pattern="^(variation|correlation)$"),
    element: str | None = None,
    role: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=1000),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    workflow = sync_workflow(db, _workflow(db, workflow_id))
    query = db.query(GeochemCandidateClue).filter(GeochemCandidateClue.workflow_id == workflow.id)
    if kind:
        query = query.filter(GeochemCandidateClue.kind == kind)
        current_job_id = (
            workflow.variation_job_id if kind == "variation" else workflow.correlation_job_id
        )
        if current_job_id:
            query = query.filter(GeochemCandidateClue.source_job_id == current_job_id)
    if element:
        query = query.filter(GeochemCandidateClue.main_element == element)
    if status:
        query = query.filter(GeochemCandidateClue.status == status)
    clues = query.order_by(GeochemCandidateClue.rank.asc()).all()
    if role:
        clues = [
            clue
            for clue in clues
            if json.loads(clue.professional_evidence_json or "{}").get("element_role") == role
        ]
    total = len(clues)
    start = (page - 1) * page_size
    return {
        "items": [clue_payload(item) for item in clues[start : start + page_size]],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/geochem-workflows/{workflow_id}/clues/{clue_id}")
def get_workflow_clue(
    workflow_id: str,
    clue_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    workflow = sync_workflow(db, _workflow(db, workflow_id))
    clue = db.get(GeochemCandidateClue, clue_id)
    if clue is None or clue.workflow_id != workflow.id:
        raise HTTPException(status_code=404, detail="候选线索不存在")
    return {
        **clue_payload(clue),
        "dataset_hash": workflow.dataset_hash,
        "rule_set_id": workflow.rule_set_id,
        "algorithm_version": workflow.algorithm_version,
        "source_job_ids": {
            "variation": workflow.variation_job_id,
            "correlation": workflow.correlation_job_id,
        },
        "computed_at": clue.created_at,
        "action_allowed": {
            "view_evidence": True,
            "run_correlation": clue.kind == "variation",
            "run_single_element_3d": True,
            "claim_orebody": False,
        },
    }


@router.get("/geochem-workflows/{workflow_id}/clues/{clue_id}/correlation")
def get_clue_correlation(
    workflow_id: str,
    clue_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    workflow = sync_workflow(db, _workflow(db, workflow_id))
    source = db.get(GeochemCandidateClue, clue_id)
    if source is None or source.workflow_id != workflow.id:
        raise HTTPException(status_code=404, detail="候选线索不存在")
    combinations = (
        db.query(GeochemCandidateClue)
        .filter(
            GeochemCandidateClue.workflow_id == workflow.id,
            GeochemCandidateClue.kind == "correlation",
            GeochemCandidateClue.main_element == source.main_element,
        )
        .order_by(GeochemCandidateClue.rank.asc())
        .all()
    )
    return {
        "source_clue": clue_payload(source),
        "items": [clue_payload(item) for item in combinations],
        "total": len(combinations),
        "job_id": workflow.correlation_job_id,
        "limitations": json.loads(workflow.limitations_json or "[]"),
    }


@router.post(
    "/geochem-workflows/{workflow_id}/clues/{clue_id}/actions/run-reconstruction"
)
@router.post("/geochem-workflows/{workflow_id}/clues/{clue_id}/reconstructions")
def run_clue_reconstruction(
    workflow_id: str,
    clue_id: str,
    payload: RunReconstructionRequest,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    workflow = _workflow(db, workflow_id)
    try:
        job_id = run_reconstruction(db, workflow, clue_id, payload.element)
        return {"job_id": job_id, "workflow": workflow_payload(db, workflow)}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/geochem-workflows/{workflow_id}/actions/run-element-reconstruction")
def run_independent_element_reconstruction(
    workflow_id: str,
    payload: RunReconstructionRequest,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Run any element backed by the frozen variation task's valid assay data."""
    workflow = sync_workflow(db, _workflow(db, workflow_id))
    element = str(payload.element or "").strip()
    source = (
        db.query(GeochemCandidateClue)
        .filter(
            GeochemCandidateClue.workflow_id == workflow.id,
            GeochemCandidateClue.kind == "variation",
            GeochemCandidateClue.source_job_id == workflow.variation_job_id,
            GeochemCandidateClue.main_element == element,
        )
        .order_by(GeochemCandidateClue.rank.asc())
        .first()
    )
    try:
        job_id = run_reconstruction(db, workflow, source.id if source else None, element)
        return {
            "job_id": job_id,
            "source_clue_id": source.id if source else None,
            "has_priority_anomaly_clue": source is not None,
            "workflow": workflow_payload(db, workflow),
        }
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get(
    "/geochem-workflows/{workflow_id}/reconstructions/{job_id}/scene"
)
def workflow_reconstruction_scene(
    workflow_id: str,
    job_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    workflow = sync_workflow(db, _workflow(db, workflow_id))
    if workflow.reconstruct_job_id != job_id:
        raise HTTPException(status_code=404, detail="三维任务不属于当前工作流")
    from ..models import GeochemReconstructJob

    job = db.get(GeochemReconstructJob, job_id)
    if job is None or not job.scene_manifest_path:
        raise HTTPException(status_code=404, detail="三维场景尚未生成")
    path = Path(job.scene_manifest_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="三维场景产物已丢失或过期")
    result = json.loads(path.read_text(encoding="utf-8"))
    result["asset_base_url"] = f"/api/geochem/jobs/{job.id}/scene-assets/"
    from ..services.geochem_service import normalize_scene_display_labels

    return sanitize_delivery_content(normalize_scene_display_labels(result))


@router.get(
    "/geochem-workflows/{workflow_id}/reconstructions/{job_id}/validation"
)
def workflow_reconstruction_validation(
    workflow_id: str,
    job_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    workflow = sync_workflow(db, _workflow(db, workflow_id))
    if workflow.reconstruct_job_id != job_id:
        raise HTTPException(status_code=404, detail="三维任务不属于当前工作流")
    from ..models import GeochemReconstructJob

    job = db.get(GeochemReconstructJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="三维任务不存在")
    summary = json.loads(job.summary_json or "{}")
    return sanitize_delivery_content({
        "job_id": job.id,
        "validation_status": job.validation_status,
        "items": summary.get("validation") or [],
        "candidate_gate": summary.get("candidate_gate") or {},
        "limitations": (summary.get("scene_manifest") or {}).get("limitations") or [],
    })


@router.get("/geochem-workflows/{workflow_id}/evidence-graph")
def workflow_evidence_graph(
    workflow_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    workflow = sync_workflow(db, _workflow(db, workflow_id))
    edges = (
        db.query(GeochemEvidenceLink)
        .filter(GeochemEvidenceLink.workflow_id == workflow.id)
        .order_by(GeochemEvidenceLink.created_at.asc())
        .all()
    )
    return {
        "workflow_id": workflow.id,
        "edges": [
            {
                "id": edge.id,
                "from_type": edge.from_type,
                "from_id": edge.from_id,
                "relation": edge.relation,
                "to_type": edge.to_type,
                "to_id": edge.to_id,
                "evidence": json.loads(edge.evidence_json or "{}"),
            }
            for edge in edges
        ],
    }


@router.get("/geochem-workflows/{workflow_id}/artifacts")
def workflow_artifacts(
    workflow_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    workflow = sync_workflow(db, _workflow(db, workflow_id))
    rows = (
        db.query(GeochemArtifact)
        .filter(GeochemArtifact.workflow_id == workflow.id)
        .order_by(GeochemArtifact.created_at.asc())
        .all()
    )
    return {
        "items": [
            {
                "id": item.id,
                "job_id": item.job_id,
                "artifact_type": item.artifact_type,
                "path": item.path,
                "sha256": item.sha256,
                "byte_size": item.byte_size,
                "is_intermediate": item.is_intermediate,
                "retention_policy": item.retention_policy,
                "reference_count": item.reference_count,
            }
            for item in rows
        ]
    }
