"""Unified, traceable workflow for the three geochemical algorithms.

The workflow does not claim that ore has been found.  It preserves the exact
data/rule snapshot, turns complete algorithm output into reviewable clues, and
records every hand-off from variation to correlation to 3-D validation.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np
from scipy.spatial import Delaunay, QhullError
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..algorithms.geochem_mining.data_pipeline import dataset_sha256, load_geochem_dataset
from ..algorithms.geochem_mining.professional_rules import COMMON_METAL_ELEMENTS
from ..models import (
    GeochemArtifact,
    GeochemBackgroundProfile,
    GeochemCandidateClue,
    GeochemElementRule,
    GeochemEvidenceLink,
    GeochemMiningJob,
    GeochemReconstructJob,
    GeochemRuleSet,
    GeochemWorkflowRun,
    GeologicalModel,
    ReconstructJob,
)
from ..delivery_text import sanitize_delivery_content
from .geochem_mining_service import DB_PATH, available_options, result_frame, start_job
from .geochem_rule_service import ensure_default_rule_set


WORKFLOW_VERSION = "geochem-evidence-workflow-v1"
MINING_VERSION = "geochem-mining-v6"
_SCOPE_CACHE: dict[str, dict[str, Any]] = {}


def _json(value: str | None, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default
    return parsed


def _clean(value: Any) -> Any:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _reconstruction_error_message(error: str | None) -> str | None:
    text = str(error or "").strip()
    if not text:
        return None
    if "3221225794" in text or "0xC0000142" in text.upper():
        return "三维计算程序启动失败：Windows 运行库或 DLL 初始化失败（0xC0000142），尚未进入插值计算"
    return text


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return prefix + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:28]


def _dataset_hash(model_id: int) -> str:
    return dataset_sha256(load_geochem_dataset(DB_PATH, model_id=model_id))


def create_workflow(
    db: Session,
    *,
    model_id: int,
    rule_set_id: str | None,
    created_by: int | None,
    client_request_id: str | None = None,
) -> GeochemWorkflowRun:
    if db.get(GeologicalModel, model_id) is None:
        raise ValueError("地质模型不存在")
    if client_request_id:
        existing = (
            db.query(GeochemWorkflowRun)
            .filter(
                GeochemWorkflowRun.model_id == model_id,
                GeochemWorkflowRun.client_request_id == client_request_id,
            )
            .first()
        )
        if existing:
            return existing
    rule_set = db.get(GeochemRuleSet, rule_set_id) if rule_set_id else ensure_default_rule_set(
        db, model_id, created_by=created_by
    )
    if rule_set is None or rule_set.model_id != model_id:
        raise ValueError("规则集不存在或不属于当前模型")
    workflow = GeochemWorkflowRun(
        id=uuid.uuid4().hex,
        model_id=model_id,
        rule_set_id=rule_set.id,
        dataset_hash=_dataset_hash(model_id),
        algorithm_version=WORKFLOW_VERSION,
        status="created",
        stage="variation_pending",
        progress=0,
        limitations_json=json.dumps(
            [
                "结果仅用于找矿线索与下一步验证参考，不等同于发现矿体。",
                "工业品位尚未完成项目确认的元素不得生成工业品位结论。",
                "三维插值体必须通过留一孔/空间验证后才可作为空间延伸参考。",
            ],
            ensure_ascii=False,
        ),
        client_request_id=client_request_id,
        created_by=created_by,
    )
    db.add(workflow)
    db.commit()
    db.refresh(workflow)
    return workflow


def _base_filters() -> dict[str, Any]:
    # Empty lists deliberately mean every available element and every chemical
    # borehole.  The workflow never silently shrinks the project to a showcase subset.
    return sanitize_delivery_content({
        "selected_elements": None,
        "hole_ids": None,
        "depth_min": None,
        "depth_max": None,
        "geobody_keys": None,
    })


def run_variation(db: Session, workflow: GeochemWorkflowRun) -> str:
    if workflow.variation_job_id:
        job = db.get(GeochemMiningJob, workflow.variation_job_id)
        if job and job.status in {"queued", "running", "success"}:
            return job.id
    current_hash = _dataset_hash(workflow.model_id)
    if current_hash != workflow.dataset_hash:
        raise ValueError("化验或地质关联数据已变化，请新建工作流以冻结新的数据快照")
    params = {
        "algorithm_mode": "variation",
        "filters": _base_filters(),
        "rule_set_id": workflow.rule_set_id,
        "workflow_id": workflow.id,
        "variation": {
            "minimum_positive_count": 30,
            "minimum_group_positive_count": 3,
            "depth_bin_size_m": 100,
            "background_method": "log_mad",
            "background_scope": "model",
            "merge_gap_m": 0.5,
            "minimum_segment_length_m": 0.0,
            "industrial_grades_ppm": {},
        },
        "correlation": {},
        "generate_figures": True,
        "generate_3d": False,
    }
    job_id = start_job(db, workflow.model_id, params)
    workflow.variation_job_id = job_id
    workflow.status = "running"
    workflow.stage = "variation_running"
    workflow.progress = 10
    db.commit()
    _link(
        db,
        workflow.id,
        "workflow",
        workflow.id,
        "runs",
        "variation_job",
        job_id,
        {"dataset_hash": workflow.dataset_hash, "rule_set_id": workflow.rule_set_id},
    )
    return job_id


def run_correlation(db: Session, workflow: GeochemWorkflowRun, clue_id: str) -> str:
    variation = db.get(GeochemMiningJob, workflow.variation_job_id) if workflow.variation_job_id else None
    if variation is None or variation.status != "success":
        raise ValueError("必须先成功完成当前工作流的变化规律计算")
    sync_workflow(db, workflow)
    clue = db.get(GeochemCandidateClue, clue_id)
    if clue is None or clue.workflow_id != workflow.id or clue.kind != "variation":
        raise ValueError("所选变化规律线索不存在或不属于当前工作流")
    current_hash = _dataset_hash(workflow.model_id)
    if current_hash != workflow.dataset_hash or variation.dataset_hash != workflow.dataset_hash:
        raise ValueError("数据快照已变化，不能把不一致的数据结果串联")
    members = [str(item) for item in _json(clue.members_json, []) if item]
    # The selected variation clue drives the hand-off, while common ore-forming
    # metals receive a display/ranking preference.  This must not restrict the
    # pairwise search scope: target_elements below still contains every
    # quality-eligible element in the dataset.
    focus_elements = list(
        dict.fromkeys([clue.main_element, *members, *COMMON_METAL_ELEMENTS])
    )
    options = available_options(db, workflow.model_id)
    target_elements = [str(item) for item in (options.get("elements") or []) if item]
    if not target_elements:
        target_elements = [
            str(item[0])
            for item in (
                db.query(GeochemElementRule.element)
                .filter(GeochemElementRule.rule_set_id == workflow.rule_set_id)
                .order_by(GeochemElementRule.element.asc())
                .all()
            )
            if item[0]
        ]
    if not target_elements:
        target_elements = [item for item in focus_elements if item]
    params = {
        "algorithm_mode": "correlation",
        "filters": _base_filters(),
        "rule_set_id": workflow.rule_set_id,
        "workflow_id": workflow.id,
        "source_variation_job_id": variation.id,
        "variation": {},
        "correlation": {
            "minimum_positive_count": 30,
            "minimum_pair_common_count": 100,
            "strong_correlation_threshold": 0.5,
            "focus_elements": [item for item in focus_elements if item],
            "target_elements": list(dict.fromkeys(target_elements)),
            "same_hole_gap_m": 2.0,
            "cross_hole_distance_m": 300.0,
            "minimum_lift": 1.2,
        },
        "generate_figures": True,
        "generate_3d": False,
        "source_focus_element": clue.main_element,
        "source_variation_clue_id": clue.id,
    }
    job_id = start_job(db, workflow.model_id, params)
    workflow.selected_clue_id = clue.id
    workflow.correlation_job_id = job_id
    workflow.reconstruct_job_id = None
    workflow.status = "running"
    workflow.stage = "correlation_running"
    workflow.progress = 45
    db.commit()
    _link(
        db,
        workflow.id,
        "variation_clue",
        clue.id,
        "drives",
        "correlation_job",
        job_id,
        {"source_variation_job_id": variation.id},
    )
    return job_id


def run_reconstruction(
    db: Session,
    workflow: GeochemWorkflowRun,
    clue_id: str | None,
    element: str,
) -> str:
    sync_workflow(db, workflow)
    element = str(element or "").strip()
    if element not in _available_reconstruction_elements(db, workflow):
        raise ValueError(f"{element or '所选元素'} 没有有效化验数据或背景统计，不能启动三维重建")
    clue = db.get(GeochemCandidateClue, clue_id) if clue_id else None
    if clue_id and (clue is None or clue.workflow_id != workflow.id):
        raise ValueError("所选线索不存在或不属于当前工作流")
    members = [str(item) for item in _json(clue.members_json, []) if item] if clue else [element]
    if clue and element not in members:
        raise ValueError("三维元素必须是所选线索或组合中的一个成员")
    variation = db.get(GeochemMiningJob, workflow.variation_job_id) if workflow.variation_job_id else None
    if variation is None or variation.status != "success":
        raise ValueError("三维验证必须绑定成功完成的变化规律任务")
    current_hash = _dataset_hash(workflow.model_id)
    if current_hash != workflow.dataset_hash or variation.dataset_hash != workflow.dataset_hash:
        raise ValueError("数据快照已变化，旧线索不能与新数据混合重建")

    constraint_job = None
    for candidate in (
        db.query(ReconstructJob)
        .filter(
            ReconstructJob.model_id == workflow.model_id,
            ReconstructJob.status == "success",
        )
        .order_by(ReconstructJob.created_at.desc())
        .all()
    ):
        try:
            candidate_params = json.loads(candidate.params_json or "{}")
        except json.JSONDecodeError:
            continue
        candidate_out = Path(candidate.out_dir or "")
        if (
            str(candidate_params.get("method") or "").upper() == "B"
            and (candidate_out / "voxels_final.csv").exists()
            and (candidate_out.parent / "inputs" / "borehole_layers.csv").exists()
            and (candidate_out.parent / "inputs" / "lithology_map.csv").exists()
        ):
            constraint_job = candidate
            break
    if constraint_job is None:
        raise ValueError("缺少可用的算法B岩性体素约束；请先完成一次算法B重建，再进行化学元素三维重建")
    from .geochem_service import start_geochem_job

    correlation_job_id = (
        workflow.correlation_job_id if clue and clue.kind == "correlation" else None
    )
    params = {
        "elements": [element],
        "preview_element": element,
        "preview_mode": "concentration",
        "method": "anisotropic_idw_visual_v1",
        "nearest": 48,
        "power": 1.6,
        "chunk_size": 50_000,
        "max_points_ply": 600_000,
        "major_radius": 2400.0,
        "intermediate_radius": 1200.0,
        "minor_radius": 600.0,
        "azimuth": 80.1,
        "plunge": 7.7,
        "max_samples_per_hole": 8,
        "preferred_distinct_holes": 6,
        "compatible_lithology_weight": 0.70,
        "residual_full_radius": 0.65,
        "residual_fade_radius": 1.5,
        "composite_interval": 15.0,
        "transform": "log",
        "constraint_job_id": constraint_job.id,
        "lithology_grouping_enabled": True,
        "rule_set_id": workflow.rule_set_id,
        "source_variation_job_id": variation.id,
        "source_correlation_job_id": correlation_job_id,
        "candidate_elements": members,
        "workflow_id": workflow.id,
        "selected_clue_id": clue.id if clue else None,
        "algorithm_version": "geochem-anisotropic-idw-v1-trend-residual-full-field",
        "algorithm_profile": "algorithm_b_domain_grouped_anisotropic_idw_full_field",
    }
    job_id = start_geochem_job(
        db,
        workflow.model_id,
        params,
    )
    workflow.selected_clue_id = clue.id if clue else None
    workflow.reconstruct_job_id = job_id
    workflow.status = "running"
    workflow.stage = "reconstruction_running"
    workflow.progress = 80
    db.commit()
    source_type = f"{clue.kind}_clue" if clue else "variation_job"
    source_id = clue.id if clue else variation.id
    _link(
        db,
        workflow.id,
        source_type,
        source_id,
        "validates_in_3d",
        "reconstruction_job",
        job_id,
        {
            "element": element,
            "profile": "algorithm_b_domain_grouped_anisotropic_idw_full_field",
            "constraint_job_id": constraint_job.id,
            "source_variation_job_id": variation.id,
            "source_correlation_job_id": correlation_job_id,
            "has_priority_anomaly_clue": clue is not None,
        },
    )
    return job_id


def _role_map(db: Session, workflow: GeochemWorkflowRun) -> dict[str, str]:
    roles = {
        str(element): str(role or "other")
        for element, role in db.query(GeochemElementRule.element, GeochemElementRule.role)
        .filter(GeochemElementRule.rule_set_id == workflow.rule_set_id)
        .all()
    }
    for element in COMMON_METAL_ELEMENTS:
        if roles.get(element, "other") == "other":
            roles[element] = "metal"
    return roles


def _variation_priority(row: dict[str, Any], role: str) -> tuple[Any, ...]:
    level = str(row.get("highest_mining_level") or row.get("highest_anomaly_level") or "")
    level_score = (
        6 if "工业" in level else
        5 if "边界" in level else
        4 if "内带" in level else
        3 if "中带" in level else
        2 if "外带" in level else
        1
    )
    role_score = 2 if role in {"metal", "main_metal", "ore_forming"} else 1 if role == "indicator" else 0
    return (
        role_score,
        level_score,
        int(row.get("_cross_hole_repeat_count") or 0),
        float(row.get("max_value_to_background_ratio") or 0),
        float(row.get("segment_length_m") or 0),
    )


def _hole_topology(records: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Build a collar-like XY topology from real segment coordinates.

    Delaunay edges are preferred.  Degenerate layouts fall back to two nearest
    neighbours, so the algorithm never invents a chemical value for an
    unassayed hole and never treats every pair inside a fixed radius as adjacent.
    """

    coordinates: dict[str, tuple[float, float]] = {}
    grouped: dict[str, list[tuple[float, float]]] = {}
    for row in records:
        hole = str(row.get("hole_id") or "")
        try:
            x, y = float(row.get("x_approx")), float(row.get("y_approx"))
        except (TypeError, ValueError):
            continue
        if hole and math.isfinite(x) and math.isfinite(y):
            grouped.setdefault(hole, []).append((x, y))
    for hole, values in grouped.items():
        coordinates[hole] = (
            float(np.median([value[0] for value in values])),
            float(np.median([value[1] for value in values])),
        )
    holes = sorted(coordinates)
    if len(holes) < 2:
        return set()
    edges: set[tuple[str, str]] = set()
    points = np.asarray([coordinates[hole] for hole in holes], dtype=float)
    if len(holes) >= 3:
        try:
            triangulation = Delaunay(points)
            for simplex in triangulation.simplices:
                for left, right in ((0, 1), (1, 2), (2, 0)):
                    edges.add(tuple(sorted((holes[int(simplex[left])], holes[int(simplex[right])]))))
        except QhullError:
            pass
    if not edges:
        for index, hole in enumerate(holes):
            distances = np.linalg.norm(points - points[index], axis=1)
            for neighbour in np.argsort(distances)[1 : min(3, len(holes))]:
                edges.add(tuple(sorted((hole, holes[int(neighbour)]))))
    return edges


def _attach_variation_spatial_repeat(records: list[dict[str, Any]]) -> None:
    edges = _hole_topology(records)
    by_element: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        by_element.setdefault(str(row.get("element") or ""), []).append(row)
    for element_rows in by_element.values():
        for row in element_rows:
            hole = str(row.get("hole_id") or "")
            repeated_holes: set[str] = set()
            for other in element_rows:
                other_hole = str(other.get("hole_id") or "")
                if not hole or hole == other_hole or tuple(sorted((hole, other_hole))) not in edges:
                    continue
                try:
                    left = np.asarray(
                        [row.get("x_approx"), row.get("y_approx"), row.get("z_approx_mid")],
                        dtype=float,
                    )
                    right = np.asarray(
                        [other.get("x_approx"), other.get("y_approx"), other.get("z_approx_mid")],
                        dtype=float,
                    )
                except (TypeError, ValueError):
                    continue
                if not np.isfinite(left).all() or not np.isfinite(right).all():
                    continue
                if float(np.linalg.norm(left - right)) > 300.0:
                    continue
                left_units = set(str(row.get("section_geobody_keys") or "").split(",")) - {""}
                right_units = set(str(other.get("section_geobody_keys") or "").split(",")) - {""}
                if left_units and right_units and not (left_units & right_units):
                    continue
                repeated_holes.add(other_hole)
            row["_cross_hole_repeat_count"] = len(repeated_holes)
            row["_repeated_hole_ids"] = sorted(repeated_holes)


def _sync_variation_clues(db: Session, workflow: GeochemWorkflowRun, job: GeochemMiningJob) -> None:
    if (
        db.query(GeochemCandidateClue.id)
        .filter(
            GeochemCandidateClue.workflow_id == workflow.id,
            GeochemCandidateClue.source_job_id == job.id,
            GeochemCandidateClue.kind == "variation",
        )
        .first()
    ):
        return
    frame = result_frame(job, "element_variation/merged_anomaly_segments.csv")
    if frame.empty:
        return
    roles = _role_map(db, workflow)
    records = frame.astype(object).where(pd.notna(frame), None).to_dict("records")
    _attach_variation_spatial_repeat(records)
    records = [
        row
        for row in records
        if (
            any(
                label in str(row.get("highest_mining_level") or row.get("highest_anomaly_level") or "")
                for label in ("统计异常", "边界品位", "工业品位", "内带", "中带", "外带")
            )
            or (
                roles.get(str(row.get("element")), "other") in {"metal", "indicator"}
                and float(row.get("max_value_to_background_ratio") or 0) >= 2.0
            )
        )
    ]
    records.sort(
        key=lambda row: _variation_priority(row, roles.get(str(row.get("element")), "other")),
        reverse=True,
    )
    for rank, row in enumerate(records, start=1):
        source_key = "|".join(
            str(row.get(key) or "")
            for key in ("hole_id", "element", "segment_from_depth", "segment_to_depth")
        )
        if (
            db.query(GeochemCandidateClue.id)
            .filter(
                GeochemCandidateClue.workflow_id == workflow.id,
                GeochemCandidateClue.source_key == source_key,
            )
            .first()
        ):
            continue
        element = str(row.get("element") or "")
        evidence = {key: _clean(value) for key, value in row.items()}
        clue = GeochemCandidateClue(
            id=_stable_id("vc_", workflow.id, job.id, source_key),
            workflow_id=workflow.id,
            source_job_id=job.id,
            source_key=source_key,
            kind="variation",
            main_element=element,
            members_json=json.dumps([element], ensure_ascii=False),
            hole_ids_json=json.dumps(
                [str(row.get("hole_id")), *row.get("_repeated_hole_ids", [])],
                ensure_ascii=False,
            ),
            from_depth=_clean(row.get("segment_from_depth")),
            to_depth=_clean(row.get("segment_to_depth")),
            highest_level=str(row.get("highest_mining_level") or row.get("highest_anomaly_level") or ""),
            max_background_ratio=_clean(row.get("max_value_to_background_ratio")),
            cross_hole_count=int(row.get("_cross_hole_repeat_count") or 0),
            professional_evidence_json=json.dumps(
                {
                    "element_role": roles.get(element, "other"),
                    "boundary_grade_ppm": _clean(row.get("boundary_grade_ppm")),
                    "industrial_grade_ppm": _clean(row.get("industrial_grade_ppm")),
                    "meets_boundary_grade": bool(row.get("meets_boundary_grade")),
                    "meets_industrial_grade": bool(row.get("meets_industrial_grade")),
                    "rule_set_id": workflow.rule_set_id,
                    "cross_hole_repeat_count": int(row.get("_cross_hole_repeat_count") or 0),
                    "topology_method": "delaunay_or_two_nearest_with_3d_cap",
                },
                ensure_ascii=False,
            ),
            evidence_json=json.dumps(evidence, ensure_ascii=False, default=str),
            limitations_json=json.dumps(
                ["该记录是异常/品位证据线索，不等同于矿体。", "需要相关性和三维空间验证。"],
                ensure_ascii=False,
            ),
            status="candidate",
            rank=rank,
        )
        db.add(clue)
        _link(
            db,
            workflow.id,
            "variation_job",
            job.id,
            "produces",
            "variation_clue",
            clue.id,
            {"source_file": "element_variation/merged_anomaly_segments.csv"},
            commit=False,
        )
    db.commit()


def _sync_correlation_clues(db: Session, workflow: GeochemWorkflowRun, job: GeochemMiningJob) -> None:
    frame = result_frame(job, "element_correlation/candidate_combination_cards.csv")
    cluster_frame = result_frame(job, "element_correlation/r_type_cluster_groups.csv")
    overlap_frame = result_frame(job, "element_correlation/cluster_anomaly_overlap.csv")
    geology_frame = result_frame(job, "element_correlation/cluster_geology_summary.csv")
    if frame.empty and cluster_frame.empty:
        return
    job_params = _json(job.params_json, {})
    source_focus_element = str(job_params.get("source_focus_element") or "")
    source_variation_clue_id = str(job_params.get("source_variation_clue_id") or "")
    next_rank = 1

    overlap_by_cluster = {
        int(row["cluster_id"]): row
        for row in overlap_frame.astype(object).where(pd.notna(overlap_frame), None).to_dict("records")
        if row.get("cluster_id") is not None
    }
    geology_by_cluster = {
        int(row["cluster_id"]): row
        for row in geology_frame.astype(object).where(pd.notna(geology_frame), None).to_dict("records")
        if row.get("cluster_id") is not None
    }
    cluster_rows = (
        cluster_frame.astype(object).where(pd.notna(cluster_frame), None).to_dict("records")
        if not cluster_frame.empty
        else []
    )
    cluster_rows = [
        row
        for row in cluster_rows
        if str(row.get("is_multielement_combination")).lower() in {"true", "1"}
    ]
    confidence_order = {"高": 3, "中": 2, "低": 1}
    cluster_rows.sort(
        key=lambda row: (
            bool(row.get("contains_focus_metal")),
            confidence_order.get(str(row.get("confidence_level")), 0),
            float(row.get("stable_pair_ratio") or 0),
            float(row.get("mean_internal_spearman_r") or 0),
            int(row.get("element_count") or 0),
        ),
        reverse=True,
    )
    for row in cluster_rows:
        cluster_id = int(row.get("cluster_id"))
        members = [item for item in str(row.get("elements") or "").split(",") if item]
        if len(members) < 2:
            continue
        source_key = f"correlation_group|{job.id}|{cluster_id}|{'-'.join(members)}"
        if (
            db.query(GeochemCandidateClue.id)
            .filter(
                GeochemCandidateClue.workflow_id == workflow.id,
                GeochemCandidateClue.source_job_id == job.id,
                GeochemCandidateClue.source_key == source_key,
            )
            .first()
        ):
            next_rank += 1
            continue
        overlap = overlap_by_cluster.get(cluster_id, {})
        geology = geology_by_cluster.get(cluster_id, {})
        holes = [
            item
            for item in str(overlap.get("dominant_holes") or geology.get("dominant_holes") or "").split(",")
            if item
        ]
        co_anomaly_count = int(overlap.get("assays_with_two_or_more_members_anomalous") or 0)
        spatial_grade = "multi_hole_overlap" if len(holes) >= 2 and co_anomaly_count else "statistical_only"
        evidence = {
            "association_type": "r_type_multielement_cluster",
            "cluster_id": cluster_id,
            "element_count": len(members),
            "confidence_level": row.get("confidence_level"),
            "confidence_reason": row.get("confidence_reason"),
            "mean_internal_pearson_r": _clean(row.get("mean_internal_pearson_r")),
            "min_internal_pearson_r": _clean(row.get("min_internal_pearson_r")),
            "mean_internal_spearman_r": _clean(row.get("mean_internal_spearman_r")),
            "stable_pair_ratio": _clean(row.get("stable_pair_ratio")),
            "co_anomaly_count": co_anomaly_count,
            "co_anomaly_ratio": _clean(overlap.get("co_anomaly_ratio")),
            "supporting_drillhole_count": len(holes),
            "dominant_holes": holes,
            "dominant_depth_range": geology.get("dominant_depth_range"),
            "spatial_evidence_status": geology.get("spatial_evidence_status"),
        }
        main_element = (
            source_focus_element
            if source_focus_element in members
            else next((element for element in members if element in COMMON_METAL_ELEMENTS), members[0])
        )
        clue = GeochemCandidateClue(
            id=_stable_id("cg_", workflow.id, job.id, source_key),
            workflow_id=workflow.id,
            source_job_id=job.id,
            source_key=source_key,
            kind="correlation",
            main_element=main_element,
            members_json=json.dumps(members, ensure_ascii=False),
            hole_ids_json=json.dumps(holes, ensure_ascii=False),
            highest_level=spatial_grade,
            same_hole_count=co_anomaly_count,
            cross_hole_count=0,
            professional_evidence_json=json.dumps(
                {
                    "association_type": "r_type_multielement_cluster",
                    "rule_set_status": row.get("rule_set_status") or "draft",
                    "spatial_grade": spatial_grade,
                    "source_focus_element": source_focus_element,
                    "source_variation_clue_id": source_variation_clue_id,
                    "analysis_scope": "all_quality_eligible_elements_r_type_cluster",
                },
                ensure_ascii=False,
            ),
            evidence_json=json.dumps(evidence, ensure_ascii=False, default=str),
            limitations_json=json.dumps(
                [
                    "该多元素组由R型聚类和共同正异常重叠形成，是找矿复核线索，不是已确认矿体。",
                    "当前只证明多孔分布，尚未形成严格的跨孔三维邻近重复证据。",
                ],
                ensure_ascii=False,
            ),
            status="candidate",
            rank=next_rank,
        )
        db.add(clue)
        _link(
            db,
            workflow.id,
            "correlation_job",
            job.id,
            "produces",
            "correlation_clue",
            clue.id,
            {"source_file": "element_correlation/r_type_cluster_groups.csv"},
            commit=False,
        )
        next_rank += 1

    rows = (
        frame.astype(object).where(pd.notna(frame), None).to_dict("records")
        if not frame.empty
        else []
    )
    spatial_order = {"cross_hole": 3, "same_hole": 2, "statistical_only": 1}
    rows.sort(
        key=lambda row: (
            spatial_order.get(str(row.get("spatial_grade")), 0),
            bool(row.get("fdr_pass_0_10")),
            int(row.get("supporting_drillhole_count") or 0),
            int(row.get("cross_hole_proximity_count") or 0),
            int(row.get("same_hole_event_count") or 0),
            float(row.get("lift") or 0),
            abs(float(row.get("spearman_r") or 0)),
        ),
        reverse=True,
    )
    for row in rows:
        target = str(row.get("target_element") or "")
        candidate = str(row.get("candidate_element") or "")
        source_key = f"correlation|{job.id}|{target}|{candidate}"
        if (
            db.query(GeochemCandidateClue.id)
            .filter(
                GeochemCandidateClue.workflow_id == workflow.id,
                GeochemCandidateClue.source_key == source_key,
            )
            .first()
        ):
            continue
        clue = GeochemCandidateClue(
            id=_stable_id("cc_", workflow.id, job.id, source_key),
            workflow_id=workflow.id,
            source_job_id=job.id,
            source_key=source_key,
            kind="correlation",
            main_element=target,
            members_json=json.dumps([target, candidate], ensure_ascii=False),
            hole_ids_json=json.dumps([], ensure_ascii=False),
            highest_level=str(row.get("spatial_grade") or "statistical_only"),
            same_hole_count=int(row.get("same_hole_event_count") or 0),
            cross_hole_count=int(row.get("cross_hole_proximity_count") or 0),
            professional_evidence_json=json.dumps(
                {
                    "formal_candidate": bool(row.get("formal_candidate")),
                    "rule_set_status": row.get("rule_set_status"),
                    "spatial_grade": row.get("spatial_grade"),
                    "fdr_pass_0_10": bool(row.get("fdr_pass_0_10")),
                    "source_focus_element": source_focus_element,
                    "source_variation_clue_id": source_variation_clue_id,
                    "analysis_scope": "all_quality_eligible_element_pairs",
                },
                ensure_ascii=False,
            ),
            evidence_json=json.dumps(
                {key: _clean(value) for key, value in row.items()},
                ensure_ascii=False,
                default=str,
            ),
            limitations_json=json.dumps(
                [
                    "相关性表示共同变化或共同异常，不直接证明成矿因果。",
                    "没有跨孔空间重复时只能作为待复核组合。",
                ],
                ensure_ascii=False,
            ),
            status="candidate",
            rank=next_rank,
        )
        db.add(clue)
        _link(
            db,
            workflow.id,
            "correlation_job",
            job.id,
            "produces",
            "correlation_clue",
            clue.id,
            {"source_file": "element_correlation/candidate_combination_cards.csv"},
            commit=False,
        )
        next_rank += 1
    db.commit()


def _sync_artifacts(
    db: Session,
    workflow: GeochemWorkflowRun,
    job: GeochemMiningJob | GeochemReconstructJob,
) -> None:
    root = Path(job.out_dir or "")
    if not root.exists():
        return
    artifact_type = getattr(job, "algorithm_mode", None) or "reconstruction"
    if (
        db.query(GeochemArtifact.id)
        .filter(
            GeochemArtifact.workflow_id == workflow.id,
            GeochemArtifact.job_id == job.id,
            GeochemArtifact.artifact_type == artifact_type,
        )
        .first()
    ):
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = str(path.relative_to(root)).replace("\\", "/")
        existing = (
            db.query(GeochemArtifact.id)
            .filter(
                GeochemArtifact.workflow_id == workflow.id,
                GeochemArtifact.artifact_type == artifact_type,
                GeochemArtifact.path == relative,
            )
            .first()
        )
        if existing:
            continue
        size = path.stat().st_size
        is_intermediate = artifact_type == "reconstruction" and (
            relative in {
                "voxel_points_20m.csv",
                "geochemical_voxels_20m.csv",
                "geochemical_voxels_15m.csv",
            }
            or relative.endswith("_details.csv")
        )
        digest = None
        if size <= 50 * 1024 * 1024:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        # Status, clue, scene and validation endpoints can be requested at the
        # same time.  Let SQLite enforce idempotency atomically instead of the
        # old SELECT-then-INSERT race which occasionally returned HTTP 500.
        statement = (
            sqlite_insert(GeochemArtifact)
            .values(
                id=uuid.uuid4().hex,
                workflow_id=workflow.id,
                job_id=job.id,
                artifact_type=artifact_type,
                path=relative,
                sha256=digest,
                byte_size=size,
                is_intermediate=is_intermediate,
                retention_policy="temporary_7d" if is_intermediate else "keep",
                reference_count=1,
                expires_at=datetime.utcnow() + timedelta(days=7) if is_intermediate else None,
            )
            .on_conflict_do_nothing(
                index_elements=["workflow_id", "artifact_type", "path"]
            )
        )
        db.execute(statement)
    db.commit()


def sync_workflow(db: Session, workflow: GeochemWorkflowRun) -> GeochemWorkflowRun:
    variation = db.get(GeochemMiningJob, workflow.variation_job_id) if workflow.variation_job_id else None
    correlation = db.get(GeochemMiningJob, workflow.correlation_job_id) if workflow.correlation_job_id else None
    reconstruction = (
        db.get(GeochemReconstructJob, workflow.reconstruct_job_id)
        if workflow.reconstruct_job_id
        else None
    )
    if variation and variation.status == "success":
        _sync_variation_clues(db, workflow, variation)
        _sync_artifacts(db, workflow, variation)
    if correlation and correlation.status == "success":
        _sync_correlation_clues(db, workflow, correlation)
        _sync_artifacts(db, workflow, correlation)

    if reconstruction and reconstruction.status == "success":
        _sync_artifacts(db, workflow, reconstruction)
        if reconstruction.validation_status == "passed":
            workflow.status, workflow.stage, workflow.progress = "ready", "ready", 100
        else:
            workflow.status, workflow.stage, workflow.progress = (
                "ready_with_limits",
                "ready_with_limits",
                100,
            )
    elif reconstruction and reconstruction.status == "failed":
        workflow.status, workflow.stage, workflow.progress = "failed", "reconstruction_failed", 100
    elif reconstruction:
        workflow.status, workflow.stage, workflow.progress = "running", "reconstruction_running", 85
    elif correlation and correlation.status == "success":
        workflow.status, workflow.stage, workflow.progress = "ready_for_3d", "reconstruct_pending", 75
    elif correlation and correlation.status == "failed":
        workflow.status, workflow.stage, workflow.progress = "failed", "correlation_failed", 100
    elif correlation:
        workflow.status, workflow.stage, workflow.progress = "running", "correlation_running", max(45, correlation.progress)
    elif variation and variation.status == "success":
        workflow.status, workflow.stage, workflow.progress = "variation_ready", "clue_selection", 40
    elif variation and variation.status == "failed":
        workflow.status, workflow.stage, workflow.progress = "failed", "variation_failed", 100
    elif variation:
        workflow.status, workflow.stage, workflow.progress = "running", "variation_running", min(40, variation.progress)
    db.commit()
    db.refresh(workflow)
    return workflow


def _link(
    db: Session,
    workflow_id: str,
    from_type: str,
    from_id: str,
    relation: str,
    to_type: str,
    to_id: str,
    evidence: dict[str, Any] | None = None,
    *,
    commit: bool = True,
) -> None:
    exists = (
        db.query(GeochemEvidenceLink.id)
        .filter(
            GeochemEvidenceLink.workflow_id == workflow_id,
            GeochemEvidenceLink.from_type == from_type,
            GeochemEvidenceLink.from_id == from_id,
            GeochemEvidenceLink.relation == relation,
            GeochemEvidenceLink.to_type == to_type,
            GeochemEvidenceLink.to_id == to_id,
        )
        .first()
    )
    if not exists:
        db.add(
            GeochemEvidenceLink(
                id=uuid.uuid4().hex,
                workflow_id=workflow_id,
                from_type=from_type,
                from_id=from_id,
                relation=relation,
                to_type=to_type,
                to_id=to_id,
                evidence_json=json.dumps(evidence or {}, ensure_ascii=False),
            )
        )
    if commit:
        db.commit()


def workflow_payload(db: Session, workflow: GeochemWorkflowRun) -> dict[str, Any]:
    sync_workflow(db, workflow)
    counts = {"variation": 0, "correlation": 0}
    counts["variation"] = int(
        db.query(func.count(GeochemCandidateClue.id))
        .filter(
            GeochemCandidateClue.workflow_id == workflow.id,
            GeochemCandidateClue.kind == "variation",
            GeochemCandidateClue.source_job_id == workflow.variation_job_id,
        )
        .scalar()
        or 0
    )
    counts["correlation"] = int(
        db.query(func.count(GeochemCandidateClue.id))
        .filter(
            GeochemCandidateClue.workflow_id == workflow.id,
            GeochemCandidateClue.kind == "correlation",
            GeochemCandidateClue.source_job_id == workflow.correlation_job_id,
        )
        .scalar()
        or 0
    )
    reconstruction_elements = _available_reconstruction_elements(db, workflow)
    rule_set = db.get(GeochemRuleSet, workflow.rule_set_id)
    reconstruction = (
        db.get(GeochemReconstructJob, workflow.reconstruct_job_id)
        if workflow.reconstruct_job_id else None
    )
    available_actions: list[str] = []
    if workflow.stage in {"variation_pending", "variation_failed"}:
        available_actions.append("run_variation")
    if workflow.stage == "clue_selection":
        available_actions.append("select_clue_and_run_correlation")
    if workflow.stage == "reconstruct_pending":
        available_actions.append("select_element_and_run_reconstruction")
    scope = _SCOPE_CACHE.get(workflow.dataset_hash)
    if scope is None:
        options = available_options(db, workflow.model_id)
        scope = {
            "project_drillhole_count": options.get("geological_borehole_count", 0),
            "chemical_drillhole_count": options.get("chemical_borehole_count", 0),
            "missing_chemical_drillhole_count": options.get("missing_chemical_borehole_count", 0),
            "assay_count": options.get("assay_count", 0),
            "element_count": len(options.get("elements") or []),
            "elements": options.get("elements") or [],
        }
        _SCOPE_CACHE[workflow.dataset_hash] = scope
    enabled_reconstruction_elements = set(reconstruction_elements)
    reconstruction_element_options = [
        {
            "element": str(element),
            "available": str(element) in enabled_reconstruction_elements,
            "reason": (
                None
                if str(element) in enabled_reconstruction_elements
                else "当前变化规律任务未形成有效背景统计，不能启动三维重建"
            ),
        }
        for element in scope.get("elements") or []
    ]
    return sanitize_delivery_content({
        "id": workflow.id,
        "workflow_id": workflow.id,
        "model_id": workflow.model_id,
        "rule_set_id": workflow.rule_set_id,
        "dataset_hash": workflow.dataset_hash,
        "algorithm_version": workflow.algorithm_version,
        "status": workflow.status,
        "stage": workflow.stage,
        "progress": workflow.progress,
        "variation_job_id": workflow.variation_job_id,
        "correlation_job_id": workflow.correlation_job_id,
        "reconstruct_job_id": workflow.reconstruct_job_id,
        "reconstruction_error": _reconstruction_error_message(reconstruction.error) if reconstruction else None,
        "selected_clue_id": workflow.selected_clue_id,
        "limitations": _json(workflow.limitations_json, []),
        "rule_set": {
            "id": workflow.rule_set_id,
            "status": rule_set.status if rule_set else "missing",
            "name": rule_set.name if rule_set else None,
            "version": rule_set.version if rule_set else None,
            "background_method": rule_set.background_method if rule_set else None,
            "background_scope": rule_set.background_scope if rule_set else None,
        },
        "source_job_ids": {
            "variation": workflow.variation_job_id,
            "correlation": workflow.correlation_job_id,
            "reconstruction": workflow.reconstruct_job_id,
        },
        "available_actions": available_actions,
        "action_allowed": bool(available_actions),
        "clue_counts": counts,
        "reconstruction_elements": reconstruction_elements,
        "reconstruction_element_options": reconstruction_element_options,
        "data_scope": dict(scope),
        "computed_at": workflow.updated_at,
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
    })


def _available_reconstruction_elements(
    db: Session,
    workflow: GeochemWorkflowRun,
) -> list[str]:
    """Return every element with a valid frozen background profile.

    A variation clue is not required here: elements without a preferred
    anomaly segment still have real assay data and may be reconstructed as an
    independent, explicitly non-prioritised result.
    """
    if not workflow.variation_job_id:
        return []
    rows = (
        db.query(GeochemBackgroundProfile.element)
        .filter(
            GeochemBackgroundProfile.variation_job_id == workflow.variation_job_id,
            GeochemBackgroundProfile.background_value > 0,
        )
        .distinct()
        .order_by(GeochemBackgroundProfile.element.asc())
        .all()
    )
    return [str(row[0]) for row in rows if row[0]]


def clue_payload(clue: GeochemCandidateClue) -> dict[str, Any]:
    return {
        "id": clue.id,
        "workflow_id": clue.workflow_id,
        "source_job_id": clue.source_job_id,
        "kind": clue.kind,
        "main_element": clue.main_element,
        "members": _json(clue.members_json, []),
        "hole_ids": _json(clue.hole_ids_json, []),
        "from_depth": clue.from_depth,
        "to_depth": clue.to_depth,
        "highest_level": clue.highest_level,
        "max_background_ratio": clue.max_background_ratio,
        "same_hole_count": clue.same_hole_count,
        "cross_hole_count": clue.cross_hole_count,
        "professional_evidence": _json(clue.professional_evidence_json, {}),
        "evidence": _json(clue.evidence_json, {}),
        "limitations": _json(clue.limitations_json, []),
        "status": clue.status,
        "rank": clue.rank,
    }
