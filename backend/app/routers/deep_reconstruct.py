# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import DeepReconstructJob
from ..services.deep_service import available_source_b_jobs, list_outputs, read_log_tail, start_deep_job

router = APIRouter(tags=["deep-reconstruct"])


def _progress(job: DeepReconstructJob) -> int:
    if job.status == "queued":
        return 0
    if job.status in ("success", "failed"):
        return 100
    names = set(list_outputs(job))
    if "deep_result.ply" in names:
        return 92
    if "deep_loss_curve.png" in names:
        return 65
    if "deep_train_log.csv" in names:
        return 45
    return 20


@router.post("/models/{model_id}/reconstruct/deep/start")
def start_deep_reconstruct(
    model_id: int,
    source_b_job_id: str = Query(...),
    epochs: int = Query(80),
    hidden1: int = Query(96),
    hidden2: int = Query(64),
    lr: float = Query(0.001),
    pseudo_threshold: float = Query(0.8),
    max_pseudo_samples: int = Query(50000),
    real_weight: float = Query(5.0),
    pseudo_weight: float = Query(1.0),
    max_points_ply: int = Query(600000),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    params = {
        "source_b_job_id": source_b_job_id.strip(),
        "epochs": epochs,
        "hidden1": hidden1,
        "hidden2": hidden2,
        "lr": lr,
        "pseudo_threshold": pseudo_threshold,
        "max_pseudo_samples": max_pseudo_samples,
        "real_weight": real_weight,
        "pseudo_weight": pseudo_weight,
        "max_points_ply": max_points_ply,
        "seed": 202501,
    }
    try:
        return {"job_id": start_deep_job(db, model_id, params)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"启动算法C失败: {exc}")


@router.get("/models/{model_id}/reconstruct/deep/sources")
def list_deep_sources(model_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    return {"jobs": available_source_b_jobs(db, model_id)}


@router.get("/deep/jobs/{job_id}")
def get_deep_job(job_id: str, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    job: DeepReconstructJob | None = db.get(DeepReconstructJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        summary = json.loads(job.summary_json or "{}")
    except Exception:
        summary = {}
    if not summary:
        metrics_path = Path(job.out_dir or "") / "deep_metrics.json"
        if metrics_path.exists():
            try:
                summary = json.loads(metrics_path.read_text(encoding="utf-8"))
            except Exception:
                summary = {}
    try:
        params = json.loads(job.params_json or "{}")
    except Exception:
        params = {}
    if summary and params.get("source_b_job_id") and not summary.get("source_b_job_id"):
        summary["source_b_job_id"] = params.get("source_b_job_id")
    return {
        "id": job.id,
        "model_id": job.model_id,
        "status": job.status,
        "progress": _progress(job),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
        "params": params,
        "summary": summary,
        "outputs": list_outputs(job),
        "log_tail": read_log_tail(job),
    }


@router.get("/models/{model_id}/reconstruct/deep/latest")
def latest_deep_job(model_id: int, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    job = (
        db.query(DeepReconstructJob)
        .filter(DeepReconstructJob.model_id == model_id)
        .order_by(DeepReconstructJob.created_at.desc())
        .first()
    )
    if not job:
        return {"job": None}
    return {"job": get_deep_job(job.id, db, _user)}


@router.get("/deep/jobs/{job_id}/download")
def download_deep_output(
    job_id: str,
    file: str = Query(...),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    job: DeepReconstructJob | None = db.get(DeepReconstructJob, job_id)
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
