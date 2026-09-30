# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import GeochemReconstructJob
from ..services.geochem_service import (
    available_constraint_jobs,
    available_elements,
    ensure_summary_metadata,
    list_outputs,
    normalize_scene_display_labels,
    read_log_tail,
    start_geochem_job,
    update_preview_ply,
)

router = APIRouter(tags=["geochem-reconstruct"])


def _write_field_difference_ply(
    baseline_path: Path,
    expert_path: Path,
    element: str,
    output_path: Path,
    max_points: int = 600000,
) -> dict:
    """Write a fixed-scale blue-white-red PLY for expert minus baseline values."""
    import numpy as np

    columns = ["x", "y", "z", element]
    maximum = 0.0
    valid_count = 0
    for left, right in zip(
        pd.read_csv(baseline_path, usecols=columns, chunksize=200_000),
        pd.read_csv(expert_path, usecols=columns, chunksize=200_000),
    ):
        a = pd.to_numeric(left[element], errors="coerce").to_numpy(dtype=float)
        b = pd.to_numeric(right[element], errors="coerce").to_numpy(dtype=float)
        valid = np.isfinite(a) & np.isfinite(b)
        if valid.any():
            delta = b[valid] - a[valid]
            maximum = max(maximum, float(np.max(np.abs(delta))))
            valid_count += int(valid.sum())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    selected = 0

    def color(delta: float) -> tuple[int, int, int]:
        if maximum <= 0:
            return (255, 255, 255)
        position = max(-1.0, min(1.0, delta / maximum))
        if position < 0:
            t = position + 1.0
            return (int(255 * t), int(255 * t), 255)
        t = 1.0 - position
        return (255, int(255 * t), int(255 * t))

    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("ply\nformat ascii 1.0\n")
        handle.write(f"comment element={element} difference=expert_minus_baseline max_abs_ppm={maximum}\n")
        handle.write(f"element vertex {min(valid_count, max_points)}\n")
        handle.write("property float x\nproperty float y\nproperty float z\n")
        handle.write("property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
        for left, right in zip(
            pd.read_csv(baseline_path, usecols=columns, chunksize=200_000),
            pd.read_csv(expert_path, usecols=columns, chunksize=200_000),
        ):
            a = pd.to_numeric(left[element], errors="coerce").to_numpy(dtype=float)
            b = pd.to_numeric(right[element], errors="coerce").to_numpy(dtype=float)
            valid = np.isfinite(a) & np.isfinite(b)
            for row, delta in zip(left.loc[valid].itertuples(index=False), (b[valid] - a[valid])):
                if selected >= max_points:
                    break
                r, g, blue = color(float(delta))
                handle.write(f"{float(row.x):.6f} {float(row.y):.6f} {float(row.z):.6f} {r} {g} {blue}\n")
                selected += 1
            if selected >= max_points:
                break
    return {
        "file": output_path.name,
        "format": "ply",
        "display_mode": "field_difference",
        "visible": True,
        "element": element,
        "point_count": selected,
        "valid_voxel_count": valid_count,
        "max_abs_ppm": maximum,
        "meaning": "固定对称色标：蓝色表示专家模型低于原模型，红色表示专家模型高于原模型，白色表示接近。",
    }


class GeochemComparisonRequest(BaseModel):
    element: str


@router.post("/models/{model_id}/geochem/comparison/start")
def start_geochem_comparison(
    model_id: int,
    payload: GeochemComparisonRequest,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    element = payload.element.strip()
    elements = available_elements(db, model_id)
    if element not in elements:
        raise HTTPException(status_code=400, detail="请选择当前模型中存在的化学元素")
    constraints = available_constraint_jobs(db, model_id)
    if not constraints:
        raise HTTPException(status_code=400, detail="缺少可用的算法 B 地质体体素约束")
    constraint_job_id = str(constraints[0]["id"])
    shared = {
        "elements": [element],
        "constraint_job_id": constraint_job_id,
        "nearest": 48,
        "power": 1.6,
        "search_radius": 1200.0,
        "fault_thickness": 30.0,
        "transform": "log",
        "max_points_ply": 600000,
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
        "chunk_size": 50000,
        "lithology_grouping_enabled": True,
        "direct_background_comparison": True,
        "comparison_group_id": os.urandom(8).hex(),
    }
    try:
        baseline_id = start_geochem_job(
            db, model_id, {**shared, "background_method_override": "log_mad", "comparison_role": "baseline"}
        )
        expert_id = start_geochem_job(
            db, model_id, {**shared, "background_method_override": "expert_2sigma", "comparison_role": "expert"}
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "element": element,
        "constraint_job_id": constraint_job_id,
        "baseline_job_id": baseline_id,
        "expert_job_id": expert_id,
    }


@router.get("/models/{model_id}/geochem/comparison/analysis")
def get_geochem_comparison_analysis(
    model_id: int,
    baseline_job_id: str,
    expert_job_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Compare statistical anomaly masks on the shared voxel grid."""
    baseline = db.get(GeochemReconstructJob, baseline_job_id)
    expert = db.get(GeochemReconstructJob, expert_job_id)
    if not baseline or not expert or baseline.model_id != model_id or expert.model_id != model_id:
        raise HTTPException(status_code=404, detail="comparison jobs not found")
    if baseline.status != "success" or expert.status != "success":
        raise HTTPException(status_code=409, detail="comparison jobs are not complete")

    def summary(job):
        try:
            return json.loads(job.summary_json or "{}")
        except json.JSONDecodeError:
            return {}

    def threshold(job):
        payload = summary(job)
        element = str((json.loads(job.params_json or "{}").get("preview_element") or "")).strip()
        profile = (payload.get("threshold_profiles") or {}).get(element) or {}
        value = profile.get("threshold_T_auto")
        return element, float(value) if value is not None else None

    baseline_element, baseline_threshold = threshold(baseline)
    expert_element, expert_threshold = threshold(expert)
    if not baseline_element or baseline_element != expert_element or baseline_threshold is None or expert_threshold is None:
        raise HTTPException(status_code=422, detail="comparison threshold profile is incomplete")

    baseline_path = Path(baseline.out_dir) / "geochemical_voxels_15m.csv"
    expert_path = Path(expert.out_dir) / "geochemical_voxels_15m.csv"
    if not baseline_path.exists() or not expert_path.exists():
        raise HTTPException(status_code=404, detail="voxel output not found")

    import numpy as np
    import pandas as pd

    columns = ["x", "y", "z", baseline_element]
    totals = {"total": 0, "valid": 0, "both": 0, "baseline_only": 0, "expert_only": 0, "neither": 0}
    baseline_max = expert_max = None
    difference_count = 0
    difference_abs_sum = 0.0
    difference_sq_sum = 0.0
    difference_max_abs = 0.0
    for left, right in zip(
        pd.read_csv(baseline_path, usecols=columns, chunksize=200_000),
        pd.read_csv(expert_path, usecols=columns, chunksize=200_000),
    ):
        left_values = pd.to_numeric(left[baseline_element], errors="coerce").to_numpy(dtype=float)
        right_values = pd.to_numeric(right[baseline_element], errors="coerce").to_numpy(dtype=float)
        valid = np.isfinite(left_values) & np.isfinite(right_values)
        differences = right_values[valid] - left_values[valid]
        if len(differences):
            difference_count += int(len(differences))
            difference_abs_sum += float(np.abs(differences).sum())
            difference_sq_sum += float(np.square(differences).sum())
            difference_max_abs = max(difference_max_abs, float(np.abs(differences).max()))
        baseline_mask = valid & (left_values >= baseline_threshold)
        expert_mask = valid & (right_values >= expert_threshold)
        totals["total"] += len(left)
        totals["valid"] += int(valid.sum())
        totals["both"] += int((baseline_mask & expert_mask).sum())
        totals["baseline_only"] += int((baseline_mask & ~expert_mask).sum())
        totals["expert_only"] += int((expert_mask & ~baseline_mask).sum())
        totals["neither"] += int((~baseline_mask & ~expert_mask & valid).sum())
        chunk_max = left_values[valid]
        if len(chunk_max):
            baseline_max = max(baseline_max or float("-inf"), float(np.nanmax(chunk_max)))
        chunk_max = right_values[valid]
        if len(chunk_max):
            expert_max = max(expert_max or float("-inf"), float(np.nanmax(chunk_max)))
    union = totals["both"] + totals["baseline_only"] + totals["expert_only"]
    try:
        difference_layer = _write_field_difference_ply(
            baseline_path,
            expert_path,
            baseline_element,
            Path(baseline.out_dir) / "geochem_field_difference_expert_minus_baseline.ply",
        )
    except Exception as exc:
        difference_layer = {"error": str(exc), "file": None}
    cell_volume = 15.0 ** 3
    return {
        "element": baseline_element,
        "voxel_size_m": 15.0,
        "voxel_volume_m3": cell_volume,
        "thresholds": {"baseline": baseline_threshold, "expert": expert_threshold},
        "counts": totals,
        "volumes_m3": {key: value * cell_volume for key, value in totals.items() if key in {"both", "baseline_only", "expert_only"}},
        "baseline_max": baseline_max,
        "expert_max": expert_max,
        "field_difference": {
            "voxel_count": difference_count,
            "mean_abs_ppm": difference_abs_sum / difference_count if difference_count else None,
            "rmse_ppm": (difference_sq_sum / difference_count) ** 0.5 if difference_count else None,
            "max_abs_ppm": difference_max_abs if difference_count else None,
            "meaning": "专家公式筛选后的完整浓度场减去自有方法完整浓度场；正值表示专家模型更高。",
            "layer": difference_layer,
        },
        "jaccard": totals["both"] / union if union else None,
        "baseline_job_id": baseline.id,
        "expert_job_id": expert.id,
    }


def _summary(job: GeochemReconstructJob) -> dict:
    try:
        data = json.loads(job.summary_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _progress(job: GeochemReconstructJob) -> int:
    if job.status == "queued":
        return 0
    if job.status in {"success", "failed"}:
        return 100
    names = set(list_outputs(job))
    if any(name.lower().endswith(".ply") for name in names):
        return 92
    if "geochemical_voxels_15m.csv" in names or "geochemical_voxels_20m.csv" in names:
        return 82
    if "used_assay_points.csv" in names:
        return 45
    if "voxel_points_20m.csv" in names:
        return 35
    return 20


def _job_payload(job: GeochemReconstructJob) -> dict:
    return {
        "id": job.id,
        "model_id": job.model_id,
        "status": job.status,
        "progress": _progress(job),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
        "params": json.loads(job.params_json or "{}"),
        "rule_set_id": job.rule_set_id,
        "source_variation_job_id": job.source_variation_job_id,
        "source_correlation_job_id": job.source_correlation_job_id,
        "workflow_id": job.workflow_id,
        "selected_clue_id": job.selected_clue_id,
        "dataset_hash": job.dataset_hash,
        "algorithm_version": job.algorithm_version,
        "algorithm_profile": job.algorithm_profile,
        "validation_status": job.validation_status,
        "scene_manifest_path": job.scene_manifest_path,
        "summary": _summary(job),
        "outputs": list_outputs(job),
        "log_tail": read_log_tail(job),
    }


@router.get("/models/{model_id}/geochem/elements")
def get_geochem_elements(model_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    return {"elements": available_elements(db, model_id)}


@router.get("/models/{model_id}/geochem/constraints")
def get_geochem_constraints(model_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    return {"jobs": available_constraint_jobs(db, model_id)}


@router.post("/models/{model_id}/geochem/start")
async def start_geochem_reconstruct(
    model_id: int,
    model_stl: Optional[UploadFile] = File(None, description="optional geological body STL for legacy mode"),
    fault_stl: Optional[UploadFile] = File(None, description="optional fault STL for scene/context"),
    element: str = Form(""),
    elements: str = Form(""),
    constraint_job_id: str = Form(""),
    cell_size: float = Form(15.0),
    z_cell_size: float = Form(15.0),
    nearest: int = Form(48),
    power: float = Form(1.6),
    max_voxels: int = Form(5000000),
    max_points_ply: int = Form(600000),
    voxelizer: str = Form("trimesh-fill"),
    zero_fill_elements: str = Form(""),
    search_radius: float = Form(1200.0),
    fault_thickness: float = Form(30.0),
    preview_mode: str = Form("concentration"),
    transform: str = Form("log"),
    rule_set_id: str = Form(""),
    source_variation_job_id: str = Form(""),
    source_correlation_job_id: str = Form(""),
    candidate_elements: str = Form(""),
    candidate_tier: str = Form(""),
    support_probability_cutoff: float | None = Form(None),
    industrial_grade_ppm: float | None = Form(None),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    if voxelizer not in {"ray", "trimesh-fill"}:
        raise HTTPException(status_code=400, detail="voxelizer must be ray or trimesh-fill")
    if preview_mode != "concentration":
        raise HTTPException(status_code=400, detail="三维主场景仅提供完整浓度场预览")
    if transform not in {"raw", "log"}:
        raise HTTPException(status_code=400, detail="transform must be raw or log")
    if support_probability_cutoff is not None and not 0 < support_probability_cutoff < 1:
        raise HTTPException(status_code=400, detail="support_probability_cutoff must be between 0 and 1")
    if industrial_grade_ppm is not None and industrial_grade_ppm <= 0:
        raise HTTPException(status_code=400, detail="industrial_grade_ppm must be positive")
    stl_bytes = b""
    fault_stl_bytes = b""
    if model_stl is not None:
        try:
            stl_bytes = await model_stl.read()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"读取 STL 失败: {exc}")
    if fault_stl is not None:
        try:
            fault_stl_bytes = await fault_stl.read()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"读取断层 STL 失败: {exc}")
    selected_element = element.strip()
    if not selected_element and elements.strip():
        selected_element = elements.split(",", 1)[0].strip()
    params = {
        "elements": [selected_element] if selected_element else [],
        "preview_element": selected_element,
        "constraint_job_id": constraint_job_id.strip(),
        "cell_size": cell_size,
        "z_cell_size": z_cell_size,
        "nearest": nearest,
        "power": power,
        "max_voxels": max_voxels,
        "max_points_ply": max_points_ply,
        "voxelizer": voxelizer,
        "zero_fill_elements": zero_fill_elements.strip(),
        "search_radius": search_radius,
        "fault_thickness": fault_thickness,
        "preview_mode": preview_mode,
        "transform": transform,
        "rule_set_id": rule_set_id.strip(),
        "source_variation_job_id": source_variation_job_id.strip(),
        "source_correlation_job_id": source_correlation_job_id.strip(),
        "candidate_elements": [
            item.strip() for item in candidate_elements.split(",") if item.strip()
        ],
        "candidate_tier": candidate_tier.strip(),
        "support_probability_cutoff": support_probability_cutoff,
        "industrial_grades_ppm": ({selected_element: industrial_grade_ppm} if selected_element and industrial_grade_ppm else {}),
    }
    try:
        job_id = start_geochem_job(
            db=db,
            model_id=model_id,
            stl_upload_name=(model_stl.filename if model_stl else "") or "model.stl",
            stl_content=stl_bytes,
            fault_stl_upload_name=(fault_stl.filename if fault_stl else "") or "fault.stl",
            fault_stl_content=fault_stl_bytes,
            params=params,
        )
        return {"job_id": job_id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"启动化学元素重建失败: {exc}")


@router.get("/geochem/jobs/{job_id}")
def get_geochem_job(job_id: str, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    job: Optional[GeochemReconstructJob] = db.get(GeochemReconstructJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    job = ensure_summary_metadata(db, job)
    return _job_payload(job)


@router.get("/geochem/jobs/{job_id}/validation")
def get_geochem_validation(
    job_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    job: Optional[GeochemReconstructJob] = db.get(GeochemReconstructJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    path = Path(job.out_dir) / "loho_validation_summary.csv"
    if not path.exists():
        return {"items": [], "total": 0}
    frame = pd.read_csv(path).astype(object).where(lambda value: pd.notna(value), None)
    return {"items": frame.to_dict("records"), "total": len(frame)}


@router.get("/geochem/jobs/{job_id}/scene")
def get_geochem_scene(
    job_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    job: Optional[GeochemReconstructJob] = db.get(GeochemReconstructJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    path = (
        Path(job.scene_manifest_path)
        if job.scene_manifest_path
        else Path(job.out_dir) / "scene_manifest.json"
    )
    if not path.exists():
        raise HTTPException(status_code=404, detail="scene manifest not found")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["asset_base_url"] = f"/api/geochem/jobs/{job.id}/scene-assets/"
    return normalize_scene_display_labels(payload)


@router.get("/geochem/jobs/{job_id}/scene-assets/{asset_path:path}")
def get_geochem_scene_asset(
    job_id: str,
    asset_path: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    job: Optional[GeochemReconstructJob] = db.get(GeochemReconstructJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if ".." in asset_path or asset_path.startswith(("/", "\\")):
        raise HTTPException(status_code=400, detail="invalid asset path")
    root = Path(job.out_dir).resolve()
    target = (root / asset_path).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise HTTPException(status_code=404, detail="scene asset not found")
    return FileResponse(path=str(target), filename=target.name)


@router.post("/geochem/jobs/{job_id}/preview")
def switch_geochem_preview(
    job_id: str,
    element: str = Form(...),
    display_mode: str = Form("concentration"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    job: Optional[GeochemReconstructJob] = db.get(GeochemReconstructJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if display_mode != "concentration":
        raise HTTPException(
            status_code=400,
            detail="主场景只提供完整浓度场；连续异常体预览已删除。",
        )
    try:
        job = update_preview_ply(db, job, element, display_mode)
        return _job_payload(job)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"switch preview failed: {exc}")


@router.get("/models/{model_id}/geochem/latest")
def latest_geochem_job(
    model_id: int,
    element: str = Query(""),
    source_variation_job_id: str = Query(""),
    source_correlation_job_id: str = Query(""),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    jobs = (
        db.query(GeochemReconstructJob)
        .filter(GeochemReconstructJob.model_id == model_id)
        .order_by(GeochemReconstructJob.created_at.desc())
        .limit(200)
        .all()
    )
    requested_element = element.strip()
    requested_variation = source_variation_job_id.strip()
    requested_correlation = source_correlation_job_id.strip()
    job = None
    for item in jobs:
        params = json.loads(item.params_json or "{}")
        elements = [str(value).strip() for value in params.get("elements", []) if str(value).strip()]
        if requested_element and requested_element not in elements:
            continue
        if requested_variation and item.source_variation_job_id != requested_variation:
            continue
        if requested_correlation and str(params.get("source_correlation_job_id") or "") != requested_correlation:
            continue
        job = item
        break
    if job:
        job = ensure_summary_metadata(db, job)
    return {"job": _job_payload(job) if job else None}


@router.get("/geochem/jobs/{job_id}/download")
def download_geochem_output(
    job_id: str,
    file: str = Query(...),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    job: Optional[GeochemReconstructJob] = db.get(GeochemReconstructJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if ".." in file or file.startswith("/") or file.startswith("\\"):
        raise HTTPException(status_code=400, detail="invalid file")
    out_dir = Path(job.out_dir)
    target = (out_dir / file).resolve()
    if not str(target).startswith(str(out_dir.resolve())):
        raise HTTPException(status_code=400, detail="invalid file")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path=str(target), filename=os.path.basename(str(target)))
