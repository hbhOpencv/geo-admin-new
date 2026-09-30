# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..algorithms.deep_mlp import run_deep_mlp
from ..models import DeepReconstructJob, ReconstructJob, get_session
from .reconstruct_service import export_model_sections_to_layer_csv


def _storage_root() -> Path:
    return Path(__file__).resolve().parents[2] / "storage" / "deep"


def _job_method(job: ReconstructJob) -> str:
    try:
        params = json.loads(job.params_json or "{}")
    except Exception:
        params = {}
    return str(params.get("method", "A")).upper()


def _source_b_files(job: ReconstructJob) -> tuple[Path, Path, Path] | None:
    if _job_method(job) != "B" or job.status != "success":
        return None
    out_dir = Path(job.out_dir or "")
    ikrig = out_dir / "voxels_ikrig.csv"
    final = out_dir / "voxels_final.csv"
    color_map = out_dir / "lithology_color_map.json"
    if not ikrig.exists() or not final.exists() or not color_map.exists():
        return None
    return ikrig, final, color_map


def available_source_b_jobs(db: Session, model_id: int) -> List[Dict[str, Any]]:
    rows = (
        db.query(ReconstructJob)
        .filter(ReconstructJob.model_id == model_id, ReconstructJob.status == "success")
        .order_by(ReconstructJob.finished_at.desc().nullslast(), ReconstructJob.created_at.desc())
        .all()
    )
    result: List[Dict[str, Any]] = []
    for job in rows:
        if _source_b_files(job) is None:
            continue
        try:
            params = json.loads(job.params_json or "{}")
        except Exception:
            params = {}
        result.append(
            {
                "id": job.id,
                "created_at": job.created_at,
                "finished_at": job.finished_at,
                "voxel_size": params.get("voxel_size"),
                "fault_thickness": params.get("fault_thickness"),
            }
        )
    return result


def _resolve_source_b(db: Session, model_id: int, source_b_job_id: str) -> tuple[Path, Path, Path, str]:
    source_id = str(source_b_job_id or "").strip()
    if not source_id:
        raise ValueError("请选择一个已完成的算法 B 任务作为算法 C 的明确来源。")
    job = db.get(ReconstructJob, source_id)
    if job is None or job.model_id != model_id:
        raise ValueError("所选算法 B 任务不存在或不属于当前模型。")
    files = _source_b_files(job)
    if files is None:
        raise ValueError("所选算法 B 任务未成功完成，或缺少概率场、最终体素和岩性颜色表。")
    ikrig, final, color_map = files
    return ikrig, final, color_map, job.id


def create_job(db: Session, model_id: int, params: Dict[str, Any]) -> DeepReconstructJob:
    job_id = uuid.uuid4().hex
    job_dir = _storage_root() / job_id
    (job_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (job_dir / "outputs").mkdir(parents=True, exist_ok=True)
    job = DeepReconstructJob(
        id=job_id,
        model_id=model_id,
        status="queued",
        params_json=json.dumps(params, ensure_ascii=False),
        out_dir=str(job_dir / "outputs"),
        log_path=str(job_dir / "run.log"),
        created_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def list_outputs(job: DeepReconstructJob) -> List[str]:
    out_dir = Path(job.out_dir or "")
    if not out_dir.exists():
        return []
    return sorted(str(p.relative_to(out_dir)).replace("\\", "/") for p in out_dir.rglob("*") if p.is_file())


def read_log_tail(job: DeepReconstructJob, max_chars: int = 8000) -> str:
    p = Path(job.log_path or "")
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")[-max_chars:]


def _run_job(job_id: str) -> None:
    db = get_session()
    job: Optional[DeepReconstructJob] = None
    try:
        job = db.get(DeepReconstructJob, job_id)
        if not job:
            return
        job.status = "running"
        job.started_at = datetime.utcnow()
        db.commit()
        params = json.loads(job.params_json or "{}")
        job_dir = Path(job.out_dir).parent
        borehole_csv = job_dir / "inputs" / "borehole_layers.csv"
        export_model_sections_to_layer_csv(db, job.model_id, borehole_csv)
        ikrig, final, color_map, source_b_job_id = _resolve_source_b(
            db, job.model_id, str(params.get("source_b_job_id", ""))
        )
        summary = run_deep_mlp(
            borehole_csv=borehole_csv,
            ikrig_csv=ikrig,
            final_csv=final,
            source_color_map=color_map,
            outdir=Path(job.out_dir),
            log_path=Path(job.log_path),
            params=params,
        )
        summary["source_b_job_id"] = source_b_job_id
        job = db.get(DeepReconstructJob, job_id)
        if job:
            job.summary_json = json.dumps(summary, ensure_ascii=False)
            job.status = "success"
            job.finished_at = datetime.utcnow()
            db.commit()
    except Exception as exc:
        if job is None:
            job = db.get(DeepReconstructJob, job_id)
        if job:
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = datetime.utcnow()
            try:
                Path(job.log_path).parent.mkdir(parents=True, exist_ok=True)
                with Path(job.log_path).open("a", encoding="utf-8") as f:
                    f.write(f"ERROR: {exc}\n")
            except Exception:
                pass
            db.commit()
    finally:
        db.close()


def start_deep_job(db: Session, model_id: int, params: Dict[str, Any]) -> str:
    _, _, _, source_b_job_id = _resolve_source_b(db, model_id, str(params.get("source_b_job_id", "")))
    params = {**params, "source_b_job_id": source_b_job_id}
    job = create_job(db, model_id, params)
    t = threading.Thread(target=_run_job, args=(job.id,), daemon=True)
    t.start()
    return job.id
