"""Versioned geochemical rule-set management."""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..algorithms.geochem_mining.professional_rules import (
    BOUNDARY_GRADES_PPM,
    COMMON_METAL_ELEMENTS,
    INDUSTRIAL_GRADES_PPM,
)
from ..models import GeochemElementRule, GeochemRuleSet, GeologicalModel


DEFAULT_RELATIVE_RATIO_CUTOFFS = (1.25, 1.5, 2.0)
DEFAULT_MINIMUM_POSITIVE_COUNT = 30
DEFAULT_MERGE_GAP_M = 0.5
DEFAULT_MINIMUM_SEGMENT_LENGTH_M = 0.0
DEFAULT_SOURCE_DOCUMENT = "德达项目三个化学元素算法及系统优化需求确认稿(1).docx"
DEFAULT_RULE_NAME = "德达化学元素解释规则（草案）"
GRADE_SOURCE = "项目提供的《常见矿物元素品位值.xlsx》（2026-07-30确认更新）"
RELATIVE_SOURCE_NOTE = (
    "相对背景倍数采用项目级统一的1.25/1.5/2倍数学草案；"
    "颜色语义全元素一致，数值完成项目确认后方可转为已确认版本。"
)
LEGACY_RELATIVE_SOURCE_NOTE = (
    "相对背景倍数采用项目级统一的1.25/1.5/2/4倍数学草案；"
    "颜色语义全元素一致，数值完成项目确认后方可转为已确认版本。"
)

DEFAULT_INDICATOR_ELEMENTS = {"As", "Bi", "S", "Se", "Sb", "Hg"}


def default_element_role(element: str) -> str:
    """Return a conservative display role, never an ore-genetic conclusion."""

    if element in DEFAULT_INDICATOR_ELEMENTS:
        return "indicator"
    if element in COMMON_METAL_ELEMENTS:
        return "metal"
    return "other"


def _positive_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("品位阈值必须为有限正数")
    return number


def validate_cutoffs(values: Iterable[Any]) -> list[float]:
    cutoffs = [float(value) for value in values]
    if len(cutoffs) not in {3, 4}:
        raise ValueError("相对背景分级必须提供3个切点")
    cutoffs = cutoffs[:3]
    if any(not math.isfinite(value) or value <= 1 for value in cutoffs):
        raise ValueError("相对背景分级切点必须是大于1的有限数")
    if any(right <= left for left, right in zip(cutoffs, cutoffs[1:])):
        raise ValueError("相对背景分级切点必须严格递增")
    return cutoffs


def ensure_default_rule_set(db: Session, model_id: int, created_by: int | None = None) -> GeochemRuleSet:
    model = db.get(GeologicalModel, model_id)
    if model is None:
        raise ValueError("地质模型不存在")
    existing = (
        db.query(GeochemRuleSet)
        .filter(GeochemRuleSet.model_id == model_id)
        .order_by(GeochemRuleSet.version.desc())
        .first()
    )
    if existing:
        changed = False
        if not existing.source_document:
            existing.source_document = DEFAULT_SOURCE_DOCUMENT
            changed = True
        if existing.notes == LEGACY_RELATIVE_SOURCE_NOTE:
            existing.notes = RELATIVE_SOURCE_NOTE
            changed = True
        try:
            stored_cutoffs = json.loads(existing.relative_ratio_cutoffs_json or "[]")
        except json.JSONDecodeError:
            stored_cutoffs = []
        if stored_cutoffs == [1.25, 1.5, 2.0, 4.0]:
            existing.relative_ratio_cutoffs_json = json.dumps(DEFAULT_RELATIVE_RATIO_CUTOFFS)
            changed = True
        # Old installations seeded every role as ``other``.  Populate only
        # those untouched defaults so metal/indicator clues can be prioritised
        # while preserving any explicit expert edit.
        for item in existing.element_rules:
            role = default_element_role(item.element)
            if item.role == "other" and role != "other":
                item.role = role
                if not item.notes:
                    item.notes = "项目默认展示角色，待项目确认；不作为成矿结论"
                changed = True
            expected_boundary = BOUNDARY_GRADES_PPM.get(item.element)
            expected_industrial = INDUSTRIAL_GRADES_PPM.get(item.element)
            # The workbook is the authoritative project source.  Some legacy
            # rows were incorrectly tagged with the same source label while
            # retaining old values (for example Rb=365 instead of 914 ppm),
            # so source text alone is not a safe migration guard.
            if expected_boundary is not None and (
                item.boundary_grade_ppm != expected_boundary
                or item.boundary_source != GRADE_SOURCE
                or item.boundary_status != "confirmed"
            ):
                item.boundary_grade_ppm = expected_boundary
                item.boundary_status = "confirmed"
                item.boundary_source = GRADE_SOURCE
                changed = True
            if (
                item.industrial_grade_ppm != expected_industrial
                or item.industrial_source != GRADE_SOURCE
                or item.industrial_status != ("confirmed" if expected_industrial is not None else "not_available")
            ):
                item.industrial_grade_ppm = expected_industrial
                item.industrial_status = "confirmed" if expected_industrial is not None else "not_available"
                item.industrial_source = GRADE_SOURCE
                changed = True
        # Existing rule sets may intentionally have a narrow set of analysis
        # elements.  Built-in grades cover any newly available element at
        # runtime; do not silently inject all 34 rows into an existing set,
        # because that would alter the scope of the correlation algorithm.
        if changed:
            db.commit()
            db.refresh(existing)
        return existing

    rule_set = GeochemRuleSet(
        id=uuid.uuid4().hex,
        model_id=model_id,
        name=DEFAULT_RULE_NAME,
        version=1,
        status="draft",
        background_method="log_mad",
        background_scope="model",
        relative_ratio_cutoffs_json=json.dumps(DEFAULT_RELATIVE_RATIO_CUTOFFS),
        support_probability_cutoff=0.5,
        minimum_positive_count=DEFAULT_MINIMUM_POSITIVE_COUNT,
        merge_gap_m=DEFAULT_MERGE_GAP_M,
        minimum_segment_length_m=DEFAULT_MINIMUM_SEGMENT_LENGTH_M,
        source_document=DEFAULT_SOURCE_DOCUMENT,
        notes=RELATIVE_SOURCE_NOTE,
        created_by=created_by,
    )
    rule_set.element_rules = [
        GeochemElementRule(
            element=element,
            role=default_element_role(element),
            unit="ppm",
            detection_limit=None,
            detection_limit_status="pending",
            applicable_sample_medium="drill_core_assay",
            boundary_grade_ppm=boundary,
            boundary_status="confirmed",
            boundary_source=GRADE_SOURCE,
            industrial_grade_ppm=INDUSTRIAL_GRADES_PPM.get(element),
            industrial_status="confirmed" if element in INDUSTRIAL_GRADES_PPM else "not_available",
            industrial_source=GRADE_SOURCE,
            notes="项目默认展示角色，待项目确认；不作为成矿结论",
        )
        for element, boundary in BOUNDARY_GRADES_PPM.items()
    ]
    db.add(rule_set)
    db.commit()
    db.refresh(rule_set)
    return rule_set


def rule_set_payload(rule_set: GeochemRuleSet, include_elements: bool = True) -> dict[str, Any]:
    try:
        cutoffs = validate_cutoffs(json.loads(rule_set.relative_ratio_cutoffs_json or "[]"))
    except (ValueError, TypeError, json.JSONDecodeError):
        cutoffs = list(DEFAULT_RELATIVE_RATIO_CUTOFFS)
    payload: dict[str, Any] = {
        "id": rule_set.id,
        "model_id": rule_set.model_id,
        "name": rule_set.name,
        "version": rule_set.version,
        "status": rule_set.status,
        "background_method": rule_set.background_method,
        "background_scope": rule_set.background_scope,
        "relative_ratio_cutoffs": cutoffs,
        "support_probability_cutoff": rule_set.support_probability_cutoff,
        "minimum_positive_count": rule_set.minimum_positive_count,
        "merge_gap_m": rule_set.merge_gap_m,
        "minimum_segment_length_m": rule_set.minimum_segment_length_m,
        "source_document": rule_set.source_document,
        "notes": rule_set.notes,
        "created_by": rule_set.created_by,
        "confirmed_by": rule_set.confirmed_by,
        "created_at": rule_set.created_at.isoformat() if rule_set.created_at else None,
        "confirmed_at": rule_set.confirmed_at.isoformat() if rule_set.confirmed_at else None,
    }
    if include_elements:
        payload["element_rules"] = [
            {
                "element": item.element,
                "role": item.role,
                "unit": item.unit,
                "detection_limit": item.detection_limit,
                "detection_limit_status": item.detection_limit_status,
                "applicable_sample_medium": item.applicable_sample_medium,
                "boundary_grade_ppm": item.boundary_grade_ppm,
                "boundary_status": item.boundary_status,
                "boundary_source": item.boundary_source,
                "industrial_grade_ppm": item.industrial_grade_ppm,
                "industrial_status": item.industrial_status,
                "industrial_source": item.industrial_source,
                "notes": item.notes,
            }
            for item in rule_set.element_rules
        ]
    return payload


def runtime_rule_config(rule_set: GeochemRuleSet) -> dict[str, Any]:
    payload = rule_set_payload(rule_set)
    elements = payload.pop("element_rules")
    payload["element_rules"] = {item["element"]: item for item in elements}
    industrial = dict(INDUSTRIAL_GRADES_PPM)
    industrial.update({
        item["element"]: item["industrial_grade_ppm"]
        for item in elements
        if item["industrial_status"] == "confirmed" and item["industrial_grade_ppm"] is not None
    })
    boundary = dict(BOUNDARY_GRADES_PPM)
    boundary.update({
        item["element"]: item["boundary_grade_ppm"]
        for item in elements
        if item["boundary_status"] == "confirmed" and item["boundary_grade_ppm"] is not None
    })
    payload["industrial_grades_ppm"] = industrial
    payload["boundary_grades_ppm"] = boundary
    payload["grade_source"] = GRADE_SOURCE
    return payload


def create_rule_set(
    db: Session,
    model_id: int,
    *,
    name: str,
    background_method: str,
    background_scope: str,
    relative_ratio_cutoffs: Iterable[Any],
    support_probability_cutoff: float,
    minimum_positive_count: int,
    merge_gap_m: float,
    minimum_segment_length_m: float,
    source_document: str | None,
    notes: str | None,
    element_rules: list[dict[str, Any]],
    created_by: int | None,
) -> GeochemRuleSet:
    if db.get(GeologicalModel, model_id) is None:
        raise ValueError("地质模型不存在")
    cutoffs = validate_cutoffs(relative_ratio_cutoffs)
    support = float(support_probability_cutoff)
    if not 0 < support < 1:
        raise ValueError("指示概率阈值必须在0和1之间")
    minimum_positive_count = int(minimum_positive_count)
    merge_gap_m = float(merge_gap_m)
    minimum_segment_length_m = float(minimum_segment_length_m)
    if minimum_positive_count < 3:
        raise ValueError("正式背景的最小正值样本数不能少于3")
    if merge_gap_m < 0 or minimum_segment_length_m < 0:
        raise ValueError("异常段合并间隔和最小长度不能为负数")
    version = (
        db.query(GeochemRuleSet.version)
        .filter(GeochemRuleSet.model_id == model_id)
        .order_by(GeochemRuleSet.version.desc())
        .limit(1)
        .scalar()
        or 0
    ) + 1
    rule_set = GeochemRuleSet(
        id=uuid.uuid4().hex,
        model_id=model_id,
        name=name.strip() or f"化学元素解释规则 v{version}",
        version=version,
        status="draft",
        background_method=background_method,
        background_scope=background_scope,
        relative_ratio_cutoffs_json=json.dumps(cutoffs),
        support_probability_cutoff=support,
        minimum_positive_count=minimum_positive_count,
        merge_gap_m=merge_gap_m,
        minimum_segment_length_m=minimum_segment_length_m,
        source_document=source_document,
        notes=notes,
        created_by=created_by,
    )
    seen: set[str] = set()
    for item in element_rules:
        element = str(item.get("element") or "").strip()
        if not element or element in seen:
            raise ValueError("元素规则中的元素名不能为空或重复")
        seen.add(element)
        boundary = _positive_or_none(item.get("boundary_grade_ppm"))
        industrial = _positive_or_none(item.get("industrial_grade_ppm"))
        if boundary is not None and industrial is not None and industrial < boundary:
            raise ValueError(f"{element} 的工业品位不能低于最低边界品位")
        rule_set.element_rules.append(
            GeochemElementRule(
                element=element,
                role=str(item.get("role") or "other"),
                unit=str(item.get("unit") or "ppm"),
                detection_limit=_positive_or_none(item.get("detection_limit")),
                detection_limit_status=str(item.get("detection_limit_status") or "pending"),
                applicable_sample_medium=str(
                    item.get("applicable_sample_medium") or "drill_core_assay"
                ),
                boundary_grade_ppm=boundary,
                boundary_status=str(item.get("boundary_status") or ("confirmed" if boundary else "pending")),
                boundary_source=item.get("boundary_source"),
                industrial_grade_ppm=industrial,
                industrial_status=str(item.get("industrial_status") or ("confirmed" if industrial else "pending")),
                industrial_source=item.get("industrial_source"),
                notes=item.get("notes"),
            )
        )
    db.add(rule_set)
    db.commit()
    db.refresh(rule_set)
    return rule_set


def confirm_rule_set(
    db: Session,
    rule_set: GeochemRuleSet,
    acknowledge_pending: bool = False,
    confirmed_by: int | None = None,
) -> GeochemRuleSet:
    if rule_set.background_scope != "model":
        raise ValueError("当前筛选背景仅用于局部探索，不能确认成正式项目规则")
    pending = [
        item.element
        for item in rule_set.element_rules
        if item.boundary_status != "confirmed" or item.industrial_status != "confirmed"
    ]
    if pending and not acknowledge_pending:
        raise ValueError(
            "仍有未确认的元素阈值，不能标记为已确认；如项目审核接受缺省项，需显式确认待定项"
        )
    rule_set.status = "confirmed"
    rule_set.confirmed_by = confirmed_by
    rule_set.confirmed_at = datetime.utcnow()
    db.commit()
    db.refresh(rule_set)
    return rule_set
