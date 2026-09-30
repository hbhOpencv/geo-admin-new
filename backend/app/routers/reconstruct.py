# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import ReconstructJob
from ..services.reconstruct_service import (
    start_reconstruct_job,
    start_reconstruct_b_job,
    list_outputs,
    read_log_tail,
)

router = APIRouter(tags=["reconstruct"])


def _estimate_progress(job: ReconstructJob) -> int:
    if job.status == "queued":
        return 0
    if job.status == "success":
        return 100
    if job.status == "failed":
        return 100

    outputs = list_outputs(job)
    names = {x.replace("\\", "/").split("/")[-1].lower() for x in outputs}
    params = json.loads(job.params_json or "{}")
    method = str(params.get("method", "A")).upper()

    if method == "B":
        if "result_b.ply" in names:
            return 95
        if {"view_b_xy.png", "view_b_xz.png", "view_b_yz.png"} & names:
            return 80
        if "lithology_color_map.json" in names:
            return 70
        return 35

    if any(name.endswith(".ply") for name in names):
        return 95
    if "lithology_color_map.json" in names:
        return 85
    if "02_cdt_constrained.png" in names:
        return 70
    if "01_raw_delaunay_boreholes.png" in names:
        return 50
    return 30


@router.post("/models/{model_id}/reconstruct/start")
async def start_reconstruct(
    model_id: int,
    fault_stl: UploadFile = File(..., description="fault stl"),
    model_stl: UploadFile = File(..., description="model stl"),

    # params (Form)
    snap_tol: float = Form(0.1),
    model_face_step: int = Form(5),
    domain_simplify: float = Form(70.0),
    domain_buffer: float = Form(100.0),
    fault_step: int = Form(2),
    fault_simplify: float = Form(2.0),
    cdt_q: float = Form(5.0),
    triangle_extra: str = Form(""),
    tri_max_show: int = Form(1500),
    clip: str = Form("sample"),
    clip_threshold: float = Form(0.7),
    z_min: Optional[float] = Form(None),

    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    # Read file bytes
    try:
        fault_stl_bytes = await fault_stl.read()
        model_bytes = await model_stl.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"读取上传文件失败: {e}")

    params = {
        "snap_tol": snap_tol,
        "model_face_step": model_face_step,
        "domain_simplify": domain_simplify,
        "domain_buffer": domain_buffer,
        "fault_step": fault_step,
        "fault_simplify": fault_simplify,
        "cdt_q": cdt_q,
        "triangle_extra": triangle_extra,
        "tri_max_show": tri_max_show,
        "clip": clip,
        "clip_threshold": clip_threshold,
        "z_min": z_min,
    }

    try:
        job_id = start_reconstruct_job(
            db=db,
            model_id=model_id,
            fault_stl_upload_name=fault_stl.filename or "fault.stl",
            fault_stl_content=fault_stl_bytes,
            model_upload_name=model_stl.filename or "model.stl",
            model_content=model_bytes,
            params=params,
        )
        return {"job_id": job_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动重建失败: {e}")


@router.post("/models/{model_id}/reconstruct/b/start")
async def start_reconstruct_b(
    model_id: int,
    model_stl: UploadFile = File(..., description="model stl"),
    fault_stl: Optional[UploadFile] = File(None, description="fault stl (optional)"),

    # params (Form)
    voxel_size: float = Form(15.0),
    n_virtual_per_tri: int = Form(5),
    min_dist_to_real: float = Form(80.0),
    min_dist_to_virtual: float = Form(40.0),
    idw_power: float = Form(2.0),
    n_neighbors: int = Form(12),
    search_radius: float = Form(1200.0),
    range_a: float = Form(1756.8335),
    nugget_c0: float = Form(0.0861434),
    sill_c: float = Form(0.0372633),
    use_condsim: bool = Form(False),
    n_sim: int = Form(100),
    vote_threshold: float = Form(0.51),
    fault_thickness: float = Form(30.0),
    max_points_ply: int = Form(600000),
    seed: int = Form(202501),

    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Start Algorithm B (voxel + interpolation) reconstruction."""
    try:
        model_bytes = await model_stl.read()
        fault_bytes = await fault_stl.read() if fault_stl else None
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"读取上传文件失败: {e}")

    params = {
        "voxel_size": voxel_size,
        "n_virtual_per_tri": n_virtual_per_tri,
        "min_dist_to_real": min_dist_to_real,
        "min_dist_to_virtual": min_dist_to_virtual,
        "idw_power": idw_power,
        "n_neighbors": n_neighbors,
        "search_radius": search_radius,
        "range_a": range_a,
        "nugget_c0": nugget_c0,
        "sill_c": sill_c,
        "use_condsim": 1 if use_condsim else 0,
        "n_sim": n_sim,
        "vote_threshold": vote_threshold,
        "fault_thickness": fault_thickness,
        "max_points_ply": max_points_ply,
        "seed": seed,
    }

    try:
        job_id = start_reconstruct_b_job(
            db=db,
            model_id=model_id,
            model_upload_name=model_stl.filename or "model.stl",
            model_content=model_bytes,
            fault_upload_name=(fault_stl.filename if fault_stl else None),
            fault_content=fault_bytes,
            params=params,
        )
        return {"job_id": job_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动算法B失败: {e}")


@router.get("/reconstruct/jobs/{job_id}")
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    job: ReconstructJob | None = db.get(ReconstructJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    return {
        "id": job.id,
        "model_id": job.model_id,
        "status": job.status,
        "progress": _estimate_progress(job),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
        "params": json.loads(job.params_json or "{}"),
        "outputs": list_outputs(job),
        "log_tail": read_log_tail(job),
    }


@router.get("/models/{model_id}/reconstruct/latest")
def latest_reconstruct_job(
    model_id: int,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    job = (
        db.query(ReconstructJob)
        .filter(ReconstructJob.model_id == model_id, ReconstructJob.status == "success")
        .order_by(ReconstructJob.created_at.desc())
        .first()
    )
    return {"job": get_job(job.id, db, _user) if job else None}


@router.get("/reconstruct/jobs/{job_id}/download")
def download_output(
    job_id: str,
    file: str = Query(..., description="relative path under outputs/"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    job: ReconstructJob | None = db.get(ReconstructJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    # prevent path traversal
    if ".." in file or file.startswith("/") or file.startswith("\\"):
        raise HTTPException(status_code=400, detail="invalid file")

    import os
    from pathlib import Path

    out_dir = Path(job.out_dir)
    target = (out_dir / file).resolve()
    if not str(target).startswith(str(out_dir.resolve())):
        raise HTTPException(status_code=400, detail="invalid file")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    return FileResponse(path=str(target), filename=os.path.basename(str(target)))
