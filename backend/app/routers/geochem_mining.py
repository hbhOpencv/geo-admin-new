"""API for element variation and correlation mining."""

from __future__ import annotations

import io
import json
import math
import os
import zipfile
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import GeochemMiningJob
from ..algorithms.geochem_mining.correlation import build_cluster_overlap
from ..algorithms.geochem_mining.professional_rules import build_threshold_profile, classify_mining_level
from ..services.geochem_mining_service import (
    available_options,
    job_payload,
    paginate,
    result_frame,
    safe_output_path,
    start_job,
)


router = APIRouter(tags=["geochem-mining"])


def _records(frame: pd.DataFrame) -> list[dict]:
    clean = frame.astype(object).where(pd.notna(frame), None)
    return clean.to_dict("records")


class VariationParams(BaseModel):
    minimum_positive_count: int = Field(30, ge=3)
    minimum_group_positive_count: int = Field(3, ge=1)
    depth_bin_size_m: int = Field(100, ge=1)
    background_method: Literal["log_mad", "iterative_upper", "expert_2sigma"] = "log_mad"
    background_scope: Literal["model", "selection"] = "model"
    merge_gap_m: float = Field(0.5, ge=0)
    minimum_segment_length_m: float = Field(0.0, ge=0)
    industrial_grades_ppm: dict[str, float] = Field(default_factory=dict)


class CorrelationParams(BaseModel):
    minimum_positive_count: int = Field(30, ge=3)
    minimum_pair_common_count: int = Field(100, ge=3)
    strong_correlation_threshold: float = Field(0.5, gt=0, lt=1)
    focus_elements: list[str] = Field(default_factory=list)
    same_hole_gap_m: float = Field(2.0, ge=0)
    cross_hole_distance_m: float = Field(300.0, gt=0)
    minimum_lift: float = Field(1.2, gt=0)


class MiningJobRequest(BaseModel):
    algorithm_mode: Literal["variation", "correlation", "both"] = "both"
    elements: list[str] = Field(default_factory=list)
    hole_ids: list[str] = Field(default_factory=list)
    depth_min: float | None = None
    depth_max: float | None = None
    geobody_keys: list[str] = Field(default_factory=list)
    rule_set_id: str | None = None
    source_variation_job_id: str | None = None
    variation: VariationParams = Field(default_factory=VariationParams)
    correlation: CorrelationParams = Field(default_factory=CorrelationParams)
    generate_figures: bool = True
    generate_3d: bool = True


def _job(db: Session, job_id: str) -> GeochemMiningJob:
    job = db.get(GeochemMiningJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="分析任务不存在")
    return job


def _successful_job(db: Session, job_id: str) -> GeochemMiningJob:
    job = _job(db, job_id)
    if job.status != "success":
        raise HTTPException(status_code=409, detail="分析任务尚未成功完成")
    return job


def _filter_frame(
    frame: pd.DataFrame,
    element: str | None = None,
    hole_id: str | None = None,
    geobody_key: str | None = None,
    anomaly_level: str | None = None,
    depth_min: float | None = None,
    depth_max: float | None = None,
) -> pd.DataFrame:
    if element and "element" in frame:
        frame = frame.loc[frame["element"] == element]
    if hole_id and "hole_id" in frame:
        frame = frame.loc[frame["hole_id"] == hole_id]
    if geobody_key:
        for column in ("section_geobody_key", "section_geobody_keys"):
            if column in frame:
                frame = frame.loc[frame[column].fillna("").astype(str).str.contains(geobody_key, regex=False)]
                break
    if anomaly_level:
        level_column = "mining_level" if "mining_level" in frame else "anomaly_level"
        if level_column in frame:
            frame = frame.loc[frame[level_column] == anomaly_level]
    start_column = "from_depth" if "from_depth" in frame else "segment_from_depth"
    end_column = "to_depth" if "to_depth" in frame else "segment_to_depth"
    if depth_min is not None and end_column in frame:
        frame = frame.loc[pd.to_numeric(frame[end_column], errors="coerce") >= depth_min]
    if depth_max is not None and start_column in frame:
        frame = frame.loc[pd.to_numeric(frame[start_column], errors="coerce") <= depth_max]
    return frame


def _params(job: GeochemMiningJob) -> dict:
    try:
        return json.loads(job.params_json or "{}")
    except json.JSONDecodeError:
        return {}


def _filter_signature(job: GeochemMiningJob) -> dict:
    filters = (_params(job).get("filters") or {}).copy()
    for key in ("selected_elements", "hole_ids", "geobody_keys"):
        filters[key] = sorted(filters.get(key) or [])
    return filters


def _compatibility(correlation_job: GeochemMiningJob, variation_job: GeochemMiningJob) -> dict:
    left, right = _filter_signature(correlation_job), _filter_signature(variation_job)
    mismatches = [key for key in sorted(set(left) | set(right)) if left.get(key) != right.get(key)]
    if correlation_job.rule_set_id != variation_job.rule_set_id:
        mismatches.append("rule_set_id")
    correlation_hash = _params(correlation_job).get("analysis_data_sha256")
    variation_hash = _params(variation_job).get("analysis_data_sha256")
    if not correlation_hash or not variation_hash or correlation_hash != variation_hash:
        mismatches.append("analysis_data_sha256")
    return {"status": "exact" if not mismatches else "different_inputs", "mismatch_fields": mismatches}


@router.get("/models/{model_id}/geochem-mining/options")
def get_options(model_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    try:
        return available_options(db, model_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/models/{model_id}/geochem-mining/jobs")
def create_mining_job(
    model_id: int,
    payload: MiningJobRequest,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    if payload.depth_min is not None and payload.depth_max is not None and payload.depth_min > payload.depth_max:
        raise HTTPException(status_code=400, detail="最小深度不能大于最大深度")
    params = payload.model_dump()
    params["filters"] = {
        "selected_elements": payload.elements or None,
        "hole_ids": payload.hole_ids or None,
        "depth_min": payload.depth_min,
        "depth_max": payload.depth_max,
        "geobody_keys": payload.geobody_keys or None,
    }
    for key in ("elements", "hole_ids", "depth_min", "depth_max", "geobody_keys"):
        params.pop(key, None)
    try:
        return {"job_id": start_job(db, model_id, params)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/geochem-mining/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    return job_payload(_job(db, job_id))


@router.get("/models/{model_id}/geochem-mining/latest")
def get_latest(
    model_id: int,
    algorithm_mode: Literal["variation", "correlation", "both"] | None = None,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    query = db.query(GeochemMiningJob).filter(GeochemMiningJob.model_id == model_id)
    if algorithm_mode:
        query = query.filter(GeochemMiningJob.algorithm_mode == algorithm_mode)
    job = query.order_by(GeochemMiningJob.created_at.desc()).first()
    return {"job": job_payload(job) if job else None}


@router.get("/models/{model_id}/geochem-mining/jobs")
def get_history(
    model_id: int,
    algorithm_mode: Literal["variation", "correlation", "both"] | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    query = db.query(GeochemMiningJob).filter(GeochemMiningJob.model_id == model_id)
    if algorithm_mode:
        query = query.filter(GeochemMiningJob.algorithm_mode == algorithm_mode)
    total = query.count()
    jobs = query.order_by(GeochemMiningJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [job_payload(job) for job in jobs], "total": total, "page": page, "page_size": page_size}


@router.get("/geochem-mining/jobs/{job_id}/variation/elements")
def variation_elements(job_id: str, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    frame = result_frame(_successful_job(db, job_id), "element_variation/element_background_statistics.csv")
    return {"items": _records(frame), "total": len(frame)}


@router.get("/geochem-mining/jobs/{job_id}/variation/quality")
def variation_quality(job_id: str, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    frame = result_frame(_successful_job(db, job_id), "element_variation/variation_data_quality.csv")
    return {"items": _records(frame), "total": len(frame)}


@router.get("/geochem-mining/jobs/{job_id}/variation/anomalies")
def variation_anomalies(
    job_id: str, page: int = 1, page_size: int = 100, element: str | None = None,
    hole_id: str | None = None, geobody_key: str | None = None,
    anomaly_level: str | None = None, depth_min: float | None = None, depth_max: float | None = None,
    db: Session = Depends(get_db), _user=Depends(get_current_user),
):
    frame = result_frame(_successful_job(db, job_id), "element_variation/anomaly_intervals.csv")
    return paginate(_filter_frame(frame, element, hole_id, geobody_key, anomaly_level, depth_min, depth_max), page, page_size)


@router.get("/geochem-mining/jobs/{job_id}/variation/segments")
def variation_segments(
    job_id: str, page: int = 1, page_size: int = 100, element: str | None = None,
    hole_id: str | None = None, geobody_key: str | None = None,
    depth_min: float | None = None, depth_max: float | None = None,
    db: Session = Depends(get_db), _user=Depends(get_current_user),
):
    frame = result_frame(_successful_job(db, job_id), "element_variation/merged_anomaly_segments.csv")
    return paginate(_filter_frame(frame, element, hole_id, geobody_key, None, depth_min, depth_max), page, page_size)


@router.get("/geochem-mining/jobs/{job_id}/variation/enrichment")
def variation_enrichment(
    job_id: str, group: Literal["hole", "section", "depth"] = "hole",
    db: Session = Depends(get_db), _user=Depends(get_current_user),
):
    names = {"hole": "hole_element_variation.csv", "section": "section_element_enrichment.csv", "depth": "depth_element_variation.csv"}
    frame = result_frame(_successful_job(db, job_id), f"element_variation/{names[group]}")
    return {"items": _records(frame), "total": len(frame)}


@router.get("/geochem-mining/jobs/{job_id}/variation/profile")
def variation_profile(
    job_id: str,
    element: str = Query(...),
    hole_id: str = Query(...),
    depth_min: float | None = None,
    depth_max: float | None = None,
    max_points: int = Query(5000, ge=10, le=20000),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    job = _successful_job(db, job_id)
    fused = result_frame(job, "element_variation/fused_assay_dataset.csv")
    stats = result_frame(job, "element_variation/element_background_statistics.csv")
    if element not in fused.columns:
        raise HTTPException(status_code=404, detail="当前任务中不存在该元素")
    frame = fused.loc[fused["hole_id"].astype(str) == hole_id].copy()
    frame = _filter_frame(frame, depth_min=depth_min, depth_max=depth_max)
    if frame.empty:
        return {"element": element, "hole_id": hole_id, "items": [], "total": 0}
    stat = stats.loc[stats["element"] == element]
    if stat.empty:
        raise HTTPException(status_code=404, detail="当前元素没有背景统计结果")
    values = pd.to_numeric(frame[element], errors="coerce")
    stat_row = stat.iloc[0].to_dict()
    background = float(stat_row["background_mean"])
    threshold = float(stat_row["threshold_T_auto"])
    industrial = stat_row.get("industrial_grade_ppm")
    industrial_overrides = {element: industrial} if industrial is not None and pd.notna(industrial) else {}
    profile = build_threshold_profile(
        element,
        pd.to_numeric(fused[element], errors="coerce"),
        background,
        threshold,
        industrial_overrides,
        boundary_grades_ppm=(
            {element: stat_row.get("boundary_grade_ppm")}
            if pd.notna(stat_row.get("boundary_grade_ppm")) else {}
        ),
        relative_ratio_cutoffs=[
            stat_row.get(f"relative_band_cut_{index}") for index in range(1, 4)
        ],
        rule_set_id=stat_row.get("rule_set_id"),
        rule_set_status=str(stat_row.get("rule_set_status") or "draft"),
    )
    frame["element"] = element
    frame["value"] = values
    frame["background_mean"] = background
    frame["threshold_T_auto"] = threshold
    frame["threshold_2T"] = threshold * 2
    frame["threshold_4T"] = threshold * 4
    frame["value_to_threshold_ratio"] = values / threshold if threshold > 0 else math.nan
    classifications = [classify_mining_level(value, profile) for value in values]
    for column in (
        "mining_level", "mining_level_rank", "display_color_hex",
        "value_to_background_ratio", "relative_deviation",
        "meets_statistical_threshold", "meets_boundary_grade", "meets_industrial_grade",
    ):
        frame[column] = [item[column] for item in classifications]
    frame["boundary_grade_ppm"] = profile.get("boundary_grade_ppm")
    frame["industrial_grade_ppm"] = profile.get("industrial_grade_ppm")
    frame["industrial_grade_source"] = profile.get("industrial_grade_source", "pending_confirmation")
    columns = [
        "assay_id", "hole_id", "element", "from_depth", "to_depth", "mid_depth", "value",
        "background_mean", "threshold_T_auto", "threshold_2T", "threshold_4T",
        "value_to_threshold_ratio", "value_to_background_ratio", "relative_deviation",
        "mining_level", "mining_level_rank", "display_color_hex",
        "boundary_grade_ppm", "industrial_grade_ppm", "industrial_grade_source",
        "meets_statistical_threshold", "meets_boundary_grade", "meets_industrial_grade",
        "section_geobody_key", "stratum_code",
        "lithology", "weathering", "section_description", "x_approx", "y_approx", "z_approx",
    ]
    frame = frame.sort_values("mid_depth").head(max_points)
    return {"element": element, "hole_id": hole_id, "items": _records(frame[columns]), "total": len(frame)}


@router.get("/geochem-mining/jobs/{job_id}/variation/3d")
def variation_3d(
    job_id: str, element: str | None = None, hole_id: str | None = None,
    geobody_key: str | None = None, anomaly_level: str | None = None,
    max_segments: int = Query(10000, ge=1, le=50000),
    db: Session = Depends(get_db), _user=Depends(get_current_user),
):
    frame = result_frame(_successful_job(db, job_id), "element_variation/merged_anomaly_segments.csv")
    frame = _filter_frame(frame, element, hole_id, geobody_key)
    if anomaly_level:
        level_column = "highest_mining_level" if "highest_mining_level" in frame else "highest_anomaly_level"
        if level_column in frame:
            frame = frame.loc[frame[level_column] == anomaly_level]
    frame = frame.head(max_segments)
    items = []
    for row in _records(frame):
        z_mid = row.get("z_approx_mid")
        start = row.get("segment_from_depth")
        end = row.get("segment_to_depth")
        length = (float(end) - float(start)) if start is not None and end is not None else 0.0
        items.append({
            **row,
            "x": row.get("x_approx"), "y": row.get("y_approx"),
            "z_top": float(z_mid) + length / 2 if z_mid is not None else None,
            "z_bottom": float(z_mid) - length / 2 if z_mid is not None else None,
        })
    return {"coordinate_quality": "approximate_vertical", "segments": items, "total": len(frame)}


@router.get("/geochem-mining/jobs/{job_id}/correlation/matrix")
def correlation_matrix(
    job_id: str, method: Literal["pearson", "spearman"] = "pearson",
    db: Session = Depends(get_db), _user=Depends(get_current_user),
):
    frame = result_frame(_successful_job(db, job_id), f"element_correlation/{method}_correlation_matrix.csv")
    if frame.empty:
        return {"elements": [], "values": []}
    elements = [str(value) for value in frame["element"].tolist()]
    matrix = frame.drop(columns=["element"]).apply(pd.to_numeric, errors="coerce")
    values = [[None if not math.isfinite(float(value)) else float(value) for value in row] for row in matrix.to_numpy()]
    return {"elements": elements, "values": values, "method": method}


@router.get("/geochem-mining/jobs/{job_id}/correlation/pairs")
def correlation_pairs(
    job_id: str, page: int = 1, page_size: int = 100, min_abs_r: float | None = None,
    element_a: str | None = None, element_b: str | None = None,
    db: Session = Depends(get_db), _user=Depends(get_current_user),
):
    frame = result_frame(_successful_job(db, job_id), "element_correlation/correlation_pairs.csv")
    if min_abs_r is not None and "pearson_r" in frame:
        frame = frame.loc[pd.to_numeric(frame["pearson_r"], errors="coerce").abs() >= min_abs_r]
    if element_a and element_b and {"element_a", "element_b"}.issubset(frame.columns):
        frame = frame.loc[
            ((frame["element_a"] == element_a) & (frame["element_b"] == element_b))
            | ((frame["element_a"] == element_b) & (frame["element_b"] == element_a))
        ]
    if "pearson_r" in frame:
        frame = frame.assign(_abs=pd.to_numeric(frame["pearson_r"], errors="coerce").abs()).sort_values("_abs", ascending=False).drop(columns=["_abs"])
    return paginate(frame, page, page_size)


@router.get("/geochem-mining/jobs/{job_id}/correlation/quality")
def correlation_quality(job_id: str, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    frame = result_frame(_successful_job(db, job_id), "element_correlation/correlation_data_quality.csv")
    return {"items": _records(frame), "total": len(frame)}


@router.get("/geochem-mining/jobs/{job_id}/correlation/clusters")
def correlation_clusters(job_id: str, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    frame = result_frame(_successful_job(db, job_id), "element_correlation/r_type_cluster_groups.csv")
    return {"items": _records(frame), "total": len(frame)}


@router.get("/geochem-mining/jobs/{job_id}/correlation/dendrogram")
def correlation_dendrogram(job_id: str, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    job = _successful_job(db, job_id)
    linkage = result_frame(job, "element_correlation/r_type_linkage.csv")
    groups = result_frame(job, "element_correlation/r_type_cluster_groups.csv")
    return {
        "linkage": _records(linkage),
        "groups": _records(groups),
        "distance": "1 - Pearson r",
        "linkage_method": "average",
    }


@router.get("/geochem-mining/jobs/{job_id}/correlation/geology")
def correlation_geology(job_id: str, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    frame = result_frame(_successful_job(db, job_id), "element_correlation/cluster_geology_summary.csv")
    return {"items": _records(frame), "total": len(frame)}


@router.get("/geochem-mining/jobs/{job_id}/correlation/coanomaly")
def correlation_coanomaly(
    job_id: str,
    page: int = 1,
    page_size: int = 100,
    target_element: str | None = None,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    frame = result_frame(_successful_job(db, job_id), "element_correlation/target_pair_coanomaly.csv")
    if target_element and "target_element" in frame:
        frame = frame.loc[frame["target_element"] == target_element]
    return paginate(frame, page, page_size)


@router.get("/geochem-mining/jobs/{job_id}/correlation/spatial")
def correlation_spatial(
    job_id: str,
    scale: Literal["same_hole", "cross_hole"] = "same_hole",
    page: int = 1,
    page_size: int = 100,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    name = "same_hole_coanomaly.csv" if scale == "same_hole" else "cross_hole_coanomaly.csv"
    frame = result_frame(_successful_job(db, job_id), f"element_correlation/{name}")
    return paginate(frame, page, page_size)


@router.get("/geochem-mining/jobs/{job_id}/correlation/candidates")
def correlation_candidates(
    job_id: str,
    page: int = 1,
    page_size: int = 100,
    evidence_tier: Literal["A", "B", "C"] | None = None,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    frame = result_frame(
        _successful_job(db, job_id),
        "element_correlation/candidate_combination_cards.csv",
    )
    if evidence_tier and "evidence_tier" in frame:
        frame = frame.loc[frame["evidence_tier"] == evidence_tier]
    return paginate(frame, page, page_size)


@router.get("/geochem-mining/jobs/{job_id}/correlation/sensitivity")
def correlation_sensitivity(job_id: str, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    frame = result_frame(_successful_job(db, job_id), "element_correlation/cluster_threshold_sensitivity.csv")
    return {"items": _records(frame), "total": len(frame)}


@router.get("/geochem-mining/jobs/{job_id}/correlation/compatible-variation-jobs")
def compatible_variation_jobs(
    job_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    correlation_job = _successful_job(db, job_id)
    if correlation_job.algorithm_mode not in {"correlation", "both"}:
        raise HTTPException(status_code=400, detail="该任务不是相关性分析任务")
    jobs = (
        db.query(GeochemMiningJob)
        .filter(
            GeochemMiningJob.model_id == correlation_job.model_id,
            GeochemMiningJob.algorithm_mode.in_(["variation", "both"]),
            GeochemMiningJob.status == "success",
        )
        .order_by(GeochemMiningJob.created_at.desc())
        .limit(50)
        .all()
    )
    items = []
    for job in jobs:
        compatibility = _compatibility(correlation_job, job)
        items.append({
            "id": job.id,
            "created_at": job.created_at,
            "compatibility": compatibility,
            "params": _params(job),
        })
    return {"items": items, "exact_match_id": next((item["id"] for item in items if item["compatibility"]["status"] == "exact"), None)}


@router.get("/geochem-mining/jobs/{job_id}/correlation/overlap")
def correlation_overlap(
    job_id: str,
    variation_job_id: str | None = None,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    correlation_job = _successful_job(db, job_id)
    compatibility = {"status": "embedded", "mismatch_fields": []}
    if variation_job_id:
        variation_job = _successful_job(db, variation_job_id)
        if variation_job.model_id != correlation_job.model_id or variation_job.algorithm_mode not in {"variation", "both"}:
            raise HTTPException(status_code=400, detail="所选变化规律任务与当前模型或算法类型不匹配")
        compatibility = _compatibility(correlation_job, variation_job)
        if compatibility["status"] != "exact":
            raise HTTPException(status_code=409, detail=f"两个任务筛选条件不一致：{', '.join(compatibility['mismatch_fields'])}")
        clusters = result_frame(correlation_job, "element_correlation/r_type_cluster_groups.csv")
        frame = build_cluster_overlap(clusters, Path(variation_job.out_dir) / "element_variation")
    else:
        frame = result_frame(correlation_job, "element_correlation/cluster_anomaly_overlap.csv")
    return {"items": _records(frame), "total": len(frame), "compatibility": compatibility, "variation_job_id": variation_job_id}


@router.get("/geochem-mining/jobs/{job_id}/report")
def view_report(job_id: str, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    job = _successful_job(db, job_id)
    relative = "任务分析报告.md"
    path = safe_output_path(job, relative)
    if not path.exists():
        relative = "element_variation/variation_report.md" if job.algorithm_mode == "variation" else "element_correlation/correlation_report.md"
        path = safe_output_path(job, relative)
        if not path.exists():
            raise HTTPException(status_code=404, detail="报告不存在")
    return Response(path.read_text(encoding="utf-8-sig"), media_type="text/markdown; charset=utf-8")


@router.get("/geochem-mining/jobs/{job_id}/bundle")
def download_bundle(job_id: str, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    job = _successful_job(db, job_id)
    root = Path(job.out_dir).resolve()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if path.is_file():
                archive.write(path, str(path.relative_to(root)).replace("\\", "/"))
        params_path = root.parent / "params.json"
        if params_path.exists():
            archive.write(params_path, "params.json")
    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="geochem-mining-{job.id}.zip"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)


@router.get("/geochem-mining/jobs/{job_id}/download")
def download_result(
    job_id: str, file: str = Query(...), db: Session = Depends(get_db), _user=Depends(get_current_user),
):
    job = _successful_job(db, job_id)
    try:
        target = safe_output_path(job, file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(str(target), filename=os.path.basename(str(target)))


@router.get("/geochem-mining/jobs/{job_id}/figure")
def get_figure(
    job_id: str, file: str = Query(...), db: Session = Depends(get_db), _user=Depends(get_current_user),
):
    if not file.replace("\\", "/").startswith("figures/"):
        raise HTTPException(status_code=400, detail="只能读取任务图件")
    return download_result(job_id, file, db, _user)
