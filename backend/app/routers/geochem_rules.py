"""API for versioned geochemical interpretation rules."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import GeochemRuleSet
from ..services.geochem_rule_service import (
    create_rule_set,
    ensure_default_rule_set,
    confirm_rule_set,
    rule_set_payload,
)


router = APIRouter(tags=["geochem-rules"])


class ElementRuleIn(BaseModel):
    element: str
    role: Literal["target", "companion", "indicator", "background", "other"] = "other"
    unit: str = "ppm"
    detection_limit: float | None = Field(None, gt=0)
    detection_limit_status: Literal["pending", "confirmed"] = "pending"
    applicable_sample_medium: str = "drill_core_assay"
    boundary_grade_ppm: float | None = Field(None, gt=0)
    boundary_status: Literal["pending", "confirmed"] = "pending"
    boundary_source: str | None = None
    industrial_grade_ppm: float | None = Field(None, gt=0)
    industrial_status: Literal["pending", "confirmed"] = "pending"
    industrial_source: str | None = None
    notes: str | None = None


class RuleSetCreateIn(BaseModel):
    name: str
    background_method: Literal["log_mad", "iterative_upper", "expert_2sigma"] = "log_mad"
    background_scope: Literal["model", "selection"] = "model"
    relative_ratio_cutoffs: list[float] = Field(default_factory=lambda: [1.25, 1.5, 2.0])
    support_probability_cutoff: float = Field(0.5, gt=0, lt=1)
    minimum_positive_count: int = Field(30, ge=3)
    merge_gap_m: float = Field(0.5, ge=0)
    minimum_segment_length_m: float = Field(0.0, ge=0)
    source_document: str | None = None
    notes: str | None = None
    element_rules: list[ElementRuleIn] = Field(default_factory=list)


class ConfirmRuleSetIn(BaseModel):
    acknowledge_pending: bool = False


def _rule_set(db: Session, rule_set_id: str) -> GeochemRuleSet:
    item = db.get(GeochemRuleSet, rule_set_id)
    if item is None:
        raise HTTPException(status_code=404, detail="规则集不存在")
    return item


@router.get("/models/{model_id}/geochem-rules")
def list_rule_sets(
    model_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    try:
        ensure_default_rule_set(db, model_id, getattr(user, "id", None))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    items = (
        db.query(GeochemRuleSet)
        .filter(GeochemRuleSet.model_id == model_id)
        .order_by(GeochemRuleSet.version.desc())
        .all()
    )
    return {"items": [rule_set_payload(item, include_elements=False) for item in items], "total": len(items)}


@router.get("/models/{model_id}/geochem-rules/current")
def current_rule_set(
    model_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    try:
        initial = ensure_default_rule_set(db, model_id, getattr(user, "id", None))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    item = (
        db.query(GeochemRuleSet)
        .filter(GeochemRuleSet.model_id == model_id)
        .order_by(
            (GeochemRuleSet.status == "confirmed").desc(),
            GeochemRuleSet.version.desc(),
        )
        .first()
        or initial
    )
    return rule_set_payload(item)


@router.get("/geochem-rules/{rule_set_id}")
def get_rule_set(
    rule_set_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    return rule_set_payload(_rule_set(db, rule_set_id))


@router.post("/models/{model_id}/geochem-rules")
def add_rule_set(
    model_id: int,
    payload: RuleSetCreateIn,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    try:
        item = create_rule_set(
            db,
            model_id,
            **payload.model_dump(),
            created_by=getattr(user, "id", None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return rule_set_payload(item)


@router.post("/geochem-rules/{rule_set_id}/confirm")
def confirm(
    rule_set_id: str,
    payload: ConfirmRuleSetIn,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    try:
        item = confirm_rule_set(
            db,
            _rule_set(db, rule_set_id),
            payload.acknowledge_pending,
            getattr(user, "id", None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return rule_set_payload(item)
