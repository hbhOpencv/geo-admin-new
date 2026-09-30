"""Background job and result helpers for geochemical mining."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import (
    Borehole,
    ChemicalAssay,
    ChemicalBorehole,
    GeochemBackgroundProfile,
    GeochemMiningJob,
    GeochemRuleSet,
    GeologicalModel,
    get_session,
)
from ..algorithms.geochem_mining.professional_rules import public_rule_catalog
from ..algorithms.geochem_mining.data_pipeline import dataset_sha256, load_geochem_dataset
from .geochem_rule_service import ensure_default_rule_set, rule_set_payload, runtime_rule_config


BACKEND_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BACKEND_DIR / "geology_norm.db"
_REPORT_RENDER_LOCK = threading.Lock()


def storage_root() -> Path:
    root = BACKEND_DIR / "storage" / "geochem_mining"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _json_dict(value: str | None) -> dict[str, Any]:
    try:
        data = json.loads(value or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def conflicting_algorithm_modes(requested_mode: str) -> tuple[str, ...]:
    """Return active task modes that occupy the requested execution slot."""
    modes = {
        "variation": ("variation", "both"),
        "correlation": ("correlation", "both"),
        "both": ("variation", "correlation", "both"),
    }
    try:
        return modes[requested_mode]
    except KeyError as exc:
        raise ValueError(f"不支持的化学元素挖掘算法类型：{requested_mode}") from exc


def available_options(db: Session, model_id: int) -> dict[str, Any]:
    if db.get(GeologicalModel, model_id) is None:
        raise ValueError("地质模型不存在")
    holes = (
        db.query(ChemicalBorehole)
        .filter(ChemicalBorehole.model_id == model_id)
        .order_by(ChemicalBorehole.hole_id.asc())
        .all()
    )
    geological_hole_ids = {
        str(item[0])
        for item in db.query(Borehole.Borehole)
        .filter(Borehole.model_id == model_id)
        .all()
        if item[0]
    }
    chemical_hole_ids = {str(hole.hole_id) for hole in holes if hole.hole_id}
    rows = (
        db.query(ChemicalAssay.elements_json, ChemicalAssay.from_depth, ChemicalAssay.to_depth)
        .filter(ChemicalAssay.model_id == model_id)
        .all()
    )
    elements: set[str] = set()
    depth_values: list[float] = []
    for payload, start, end in rows:
        elements.update(_json_dict(payload))
        if start is not None:
            depth_values.append(float(start))
        if end is not None:
            depth_values.append(float(end))

    # Geobody section keys are queried through the borehole relation to preserve model isolation.
    section_rows = db.execute(
        text(
            """
            SELECT DISTINCT section.geobody_key
            FROM borehole_sections AS section
            JOIN boreholes AS hole ON hole.id = section.borehole_id
            WHERE hole.model_id = :model_id AND section.geobody_key IS NOT NULL
            ORDER BY section.geobody_key
            """
        ),
        {"model_id": model_id},
    ).fetchall()
    rule_set = ensure_default_rule_set(db, model_id)
    return {
        "model_id": model_id,
        "geological_borehole_count": len(geological_hole_ids),
        "chemical_borehole_count": len(chemical_hole_ids),
        "missing_chemical_borehole_count": len(geological_hole_ids - chemical_hole_ids),
        "missing_chemical_borehole_ids": sorted(geological_hole_ids - chemical_hole_ids),
        "elements": sorted(elements),
        "holes": [
            {
                "hole_id": hole.hole_id,
                "collar_x": hole.collar_x,
                "collar_y": hole.collar_y,
                "collar_z": hole.collar_z,
                "depth_min": hole.depth_min,
                "depth_max": hole.depth_max,
                "sample_count": hole.sample_count,
            }
            for hole in holes
        ],
        "geobody_keys": [str(row[0]) for row in section_rows if row[0]],
        "assay_count": len(rows),
        "depth_min": min(depth_values) if depth_values else None,
        "depth_max": max(depth_values) if depth_values else None,
        "current_rule_set": rule_set_payload(rule_set),
        **public_rule_catalog(sorted(elements)),
    }


def list_outputs(job: GeochemMiningJob) -> list[str]:
    root = Path(job.out_dir or "")
    if not root.exists():
        return []
    return sorted(
        str(path.relative_to(root)).replace("\\", "/")
        for path in root.rglob("*")
        if path.is_file()
    )


def read_log_tail(job: GeochemMiningJob, max_chars: int = 8000) -> str:
    path = Path(job.log_path or "")
    return path.read_text(encoding="utf-8", errors="replace")[-max_chars:] if path.exists() else ""


def job_payload(job: GeochemMiningJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "model_id": job.model_id,
        "algorithm_mode": job.algorithm_mode,
        "status": job.status,
        "progress": job.progress,
        "stage": job.stage,
        "params": _json_dict(job.params_json),
        "rule_set_id": job.rule_set_id,
        "source_variation_job_id": job.source_variation_job_id,
        "workflow_id": job.workflow_id,
        "dataset_hash": job.dataset_hash,
        "algorithm_version": job.algorithm_version,
        "summary": _json_dict(job.summary_json),
        "outputs": list_outputs(job),
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "log_tail": read_log_tail(job),
    }


def create_job(db: Session, model_id: int, params: dict[str, Any]) -> GeochemMiningJob:
    # Each standalone algorithm owns its own execution slot. A legacy "both"
    # task occupies both slots, but variation and correlation may run together.
    requested_mode = params["algorithm_mode"]
    conflicting_modes = conflicting_algorithm_modes(requested_mode)
    active = (
        db.query(GeochemMiningJob)
        .filter(
            GeochemMiningJob.model_id == model_id,
            GeochemMiningJob.status.in_(["queued", "running"]),
            GeochemMiningJob.algorithm_mode.in_(conflicting_modes),
        )
        .first()
    )
    if active:
        algorithm_name = {
            "variation": "化学元素变化规律",
            "correlation": "化学元素相关性",
            "both": "化学元素综合分析",
        }.get(active.algorithm_mode, active.algorithm_mode)
        raise ValueError(f"{algorithm_name}已有运行中的任务：{active.id}")

    requested_rule_set_id = params.get("rule_set_id")
    rule_set = db.get(GeochemRuleSet, requested_rule_set_id) if requested_rule_set_id else ensure_default_rule_set(db, model_id)
    if rule_set is None or rule_set.model_id != model_id:
        raise ValueError("所选规则集不存在或不属于当前模型")

    filters = params.get("filters") or {}
    analysis_dataset = load_geochem_dataset(
        DB_PATH,
        model_id=model_id,
    )
    analysis_data_sha256 = dataset_sha256(analysis_dataset)
    source_variation_job: GeochemMiningJob | None = None
    source_variation_job_id = params.get("source_variation_job_id")
    if requested_mode == "correlation" and not source_variation_job_id:
        raise ValueError("正式相关性任务必须显式绑定一个已成功完成的变化规律任务")
    if requested_mode == "correlation" and source_variation_job_id:
        source_variation_job = db.get(GeochemMiningJob, source_variation_job_id)
        if (
            source_variation_job is None
            or source_variation_job.model_id != model_id
            or source_variation_job.status != "success"
            or source_variation_job.algorithm_mode not in {"variation", "both"}
        ):
            raise ValueError("所选变化规律任务不存在、未成功或不属于当前模型")
        source_params = _json_dict(source_variation_job.params_json)
        if source_params.get("rule_set_id") != rule_set.id:
            raise ValueError("相关性任务与变化规律任务必须使用同一规则集")
        source_analysis_data_sha256 = source_params.get("analysis_data_sha256")
        if not source_analysis_data_sha256:
            source_summary = _json_dict(source_variation_job.summary_json)
            source_analysis_data_sha256 = (
                source_summary.get("provenance") or {}
            ).get("analysis_data_sha256")
        if not source_analysis_data_sha256:
            raise ValueError("所选变化规律任务缺少分析数据快照哈希，请先用当前版本重新运行变化规律任务")
        if source_analysis_data_sha256 != analysis_data_sha256:
            raise ValueError("化验或地质关联数据已在变化规律任务完成后发生变化，请重新运行变化规律任务后再做正式共现分析")

        def normalised_filters(value: dict[str, Any]) -> dict[str, Any]:
            result = dict(value or {})
            for key in ("selected_elements", "hole_ids", "geobody_keys"):
                result[key] = sorted(result.get(key) or [])
            return result
        if normalised_filters(source_params.get("filters") or {}) != normalised_filters(
            params.get("filters") or {}
        ):
            raise ValueError("相关性任务与变化规律任务的元素、钻孔、深度和地质筛选必须完全一致")
        params["variation_output_dir"] = str(Path(source_variation_job.out_dir) / "element_variation")
        params["evidence_mode"] = "bound_variation"
    else:
        params["evidence_mode"] = "embedded_variation"

    params["rule_set_id"] = rule_set.id
    params["rule_set"] = runtime_rule_config(rule_set)
    params["analysis_data_sha256"] = analysis_data_sha256
    params["random_seed"] = None
    job_id = uuid.uuid4().hex
    job_dir = storage_root() / job_id
    output_dir = job_dir / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    params = {**params, "model_id": model_id, "db_path": str(DB_PATH)}
    params_path = job_dir / "params.json"
    params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    job = GeochemMiningJob(
        id=job_id,
        model_id=model_id,
        algorithm_mode=params["algorithm_mode"],
        status="queued",
        progress=0,
        stage="已排队",
        params_json=json.dumps(params, ensure_ascii=False),
        out_dir=str(output_dir),
        log_path=str(job_dir / "run.log"),
        rule_set_id=rule_set.id,
        source_variation_job_id=source_variation_job.id if source_variation_job else None,
        workflow_id=params.get("workflow_id"),
        dataset_hash=analysis_data_sha256,
        algorithm_version="geochem-mining-v6",
        created_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _run_job(job_id: str) -> None:
    db = get_session()
    job = db.get(GeochemMiningJob, job_id)
    if not job:
        db.close()
        return
    try:
        job.status = "running"
        job.progress = 10
        job.stage = "正在读取数据并执行两个算法"
        job.started_at = datetime.utcnow()
        db.commit()

        job_dir = Path(job.out_dir).parent
        cmd = [
            sys.executable,
            "-m",
            "app.algorithms.geochem_mining.runner",
            "--params",
            str(job_dir / "params.json"),
            "--output",
            str(job.out_dir),
        ]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        with Path(job.log_path).open("ab") as log:
            log.write(("CMD: " + " ".join(cmd) + "\n").encode("utf-8"))
            proc = subprocess.Popen(cmd, cwd=str(BACKEND_DIR), env=env, stdout=log, stderr=log)
            while proc.poll() is None:
                output_dir = Path(job.out_dir)
                stages = [
                    ("element_variation/element_background_statistics.csv", 35, "正在计算背景值和异常阈值"),
                    ("element_variation/merged_anomaly_segments.csv", 58, "正在识别和合并异常段"),
                    ("element_correlation/correlation_pairs.csv", 70, "正在计算元素相关系数"),
                    ("element_correlation/r_type_cluster_groups.csv", 82, "正在执行 R 型聚类"),
                    ("figures", 90, "正在生成中文图件"),
                ]
                progress, stage = 18, "正在融合坐标、深度和地质分段"
                for relative, candidate, label in stages:
                    if (output_dir / relative).exists():
                        progress, stage = candidate, label
                current = db.get(GeochemMiningJob, job_id)
                if current and (current.progress != progress or current.stage != stage):
                    current.progress = progress
                    current.stage = stage
                    db.commit()
                time.sleep(0.8)
            code = proc.returncode
        if code != 0:
            raise RuntimeError(f"算法子进程退出码为 {code}，请查看任务日志")

        # Correlation remains independently runnable, but when a completed
        # variation task has the exact same filters we attach its formal
        # anomaly evidence before publishing the final report and summary.
        job = db.get(GeochemMiningJob, job_id)
        if job and job.algorithm_mode == "correlation" and job.source_variation_job_id:
            current_params = _json_dict(job.params_json)
            linked = db.get(GeochemMiningJob, job.source_variation_job_id)
            if linked:
                from ..algorithms.geochem_mining.correlation import build_cluster_overlap
                from ..algorithms.geochem_mining.data_pipeline import markdown_table
                from ..algorithms.geochem_mining.figures import configure_style
                from ..algorithms.geochem_mining.runner import _task_report_pdf, build_summary

                output_dir = Path(job.out_dir)
                clusters = pd.read_csv(output_dir / "element_correlation/r_type_cluster_groups.csv")
                overlap = build_cluster_overlap(clusters, Path(linked.out_dir) / "element_variation")
                overlap.to_csv(output_dir / "element_correlation/cluster_anomaly_overlap.csv", index=False, encoding="utf-8-sig")
                report_path = output_dir / "element_correlation/correlation_report.md"
                report = report_path.read_text(encoding="utf-8-sig")
                report += f"\n\n## 自动关联的变化规律任务\n\n- 任务编号：`{linked.id}`\n- 筛选条件：与当前相关性任务完全一致\n\n"
                report += markdown_table(overlap, ["cluster_id", "elements", "assays_with_two_or_more_members_anomalous", "co_anomaly_ratio", "dominant_holes", "dominant_section_geobody_keys"], 30)
                report_path.write_text(report, encoding="utf-8")
                existing_summary_path = output_dir / "summary.json"
                existing_summary = json.loads(existing_summary_path.read_text(encoding="utf-8")) if existing_summary_path.exists() else {}
                data_quality = existing_summary.get("data_quality") or {}
                current_params["input_summary"] = {
                    "assay_count": data_quality.get("assay_count", 0),
                    "valid_interval_count": data_quality.get("valid_interval_count", 0),
                    "invalid_interval_count": data_quality.get("invalid_interval_count", 0),
                    "chemical_hole_count": data_quality.get("chemical_hole_count", 0),
                    "detected_element_count": data_quality.get("detected_element_count", 0),
                    "coordinate_quality": data_quality.get("coordinate_quality", "unavailable"),
                    "section_match_rate": data_quality.get("section_match_rate", 0.0),
                }
                summary = build_summary(output_dir, current_params)
                summary["correlation"]["linked_variation_job_id"] = linked.id
                with _REPORT_RENDER_LOCK:
                    configure_style()
                    _task_report_pdf(output_dir, summary)
                (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        summary_path = Path(job.out_dir) / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        job = db.get(GeochemMiningJob, job_id)
        finished_at = datetime.utcnow()
        params_snapshot = _json_dict(job.params_json)
        summary["task"] = {
            "job_id": job.id,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": finished_at.isoformat(),
        }
        summary["parameters"] = params_snapshot
        summary["provenance"] = {
            **(summary.get("provenance") or {}),
            "rule_set_id": job.rule_set_id,
            "source_variation_job_id": job.source_variation_job_id,
            "evidence_mode": params_snapshot.get("evidence_mode"),
            "analysis_data_sha256": params_snapshot.get("analysis_data_sha256"),
        }
        output_dir = Path(job.out_dir)
        source_report = output_dir / ("element_variation/variation_report.md" if job.algorithm_mode == "variation" else "element_correlation/correlation_report.md")
        report_body = source_report.read_text(encoding="utf-8-sig") if source_report.exists() else ""
        filters = params_snapshot.get("filters") or {}
        report_header = (
            f"# 任务分析报告\n\n"
            f"- 任务编号：`{job.id}`\n"
            f"- 模型编号：`{job.model_id}`\n"
            f"- 算法模式：`{job.algorithm_mode}`\n"
            f"- 结果结构版本：`{summary.get('result_schema_version', '历史版本')}`\n"
            f"- 算法版本：`{json.dumps(summary.get('algorithm_versions') or {}, ensure_ascii=False)}`\n"
            f"- 创建时间（UTC）：`{job.created_at.isoformat() if job.created_at else ''}`\n"
            f"- 完成时间（UTC）：`{finished_at.isoformat()}`\n"
            f"- 元素：`{','.join(filters.get('selected_elements') or []) or '全部'}`\n"
            f"- 钻孔：`{','.join(filters.get('hole_ids') or []) or '全部'}`\n"
            f"- 深度：`{filters.get('depth_min') if filters.get('depth_min') is not None else '最小'}` 至 `{filters.get('depth_max') if filters.get('depth_max') is not None else '最大'}` m\n"
            f"- 地质分段：`{','.join(filters.get('geobody_keys') or []) or '全部'}`\n\n"
            f"## 可复现参数快照\n\n```json\n{json.dumps(params_snapshot, ensure_ascii=False, indent=2, default=str)}\n```\n\n"
        )
        (output_dir / "任务分析报告.md").write_text(report_header + report_body, encoding="utf-8")
        from ..algorithms.geochem_mining.figures import configure_style
        from ..algorithms.geochem_mining.runner import _task_report_pdf
        with _REPORT_RENDER_LOCK:
            configure_style()
            _task_report_pdf(output_dir, summary)
        summary["outputs"] = sorted(
            str(path.relative_to(output_dir)).replace("\\", "/")
            for path in output_dir.rglob("*") if path.is_file() and path.name != "summary.json"
        )
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if job.algorithm_mode in {"variation", "both"} and job.rule_set_id:
            statistics_path = output_dir / "element_variation" / "element_background_statistics.csv"
            if statistics_path.exists():
                statistics = pd.read_csv(statistics_path)
                db.query(GeochemBackgroundProfile).filter(
                    GeochemBackgroundProfile.variation_job_id == job.id
                ).delete(synchronize_session=False)
                filters = params_snapshot.get("filters") or {}
                scope_signature = json.dumps(
                    {
                        "model_id": job.model_id,
                        "scope": (params_snapshot.get("variation") or {}).get("background_scope", "model"),
                        "selected_elements": sorted(filters.get("selected_elements") or []),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                for row in statistics.to_dict("records"):
                    background = row.get("background_mean")
                    threshold = row.get("threshold_T_auto")
                    if pd.isna(background) or pd.isna(threshold):
                        continue
                    db.add(
                        GeochemBackgroundProfile(
                            model_id=job.model_id,
                            rule_set_id=job.rule_set_id,
                            variation_job_id=job.id,
                            element=str(row["element"]),
                            method=str(row.get("background_method") or "unknown"),
                            scope_signature=scope_signature,
                            background_value=float(background),
                            statistical_threshold=float(threshold),
                            statistics_json=json.dumps(row, ensure_ascii=False, default=str),
                        )
                    )
        job.status = "success"
        job.progress = 100
        job.stage = "分析完成"
        job.summary_json = json.dumps(summary, ensure_ascii=False)
        job.finished_at = finished_at
        job.error = None
        db.commit()
    except Exception as exc:
        job = db.get(GeochemMiningJob, job_id)
        if job:
            job.status = "failed"
            job.progress = 100
            job.stage = "分析失败"
            job.error = str(exc)
            job.finished_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


def start_job(db: Session, model_id: int, params: dict[str, Any]) -> str:
    available_options(db, model_id)
    job = create_job(db, model_id, params)
    threading.Thread(target=_run_job, args=(job.id,), daemon=True).start()
    return job.id


def result_frame(job: GeochemMiningJob, relative_file: str) -> pd.DataFrame:
    path = safe_output_path(job, relative_file)
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def safe_output_path(job: GeochemMiningJob, relative_file: str) -> Path:
    if ".." in relative_file or relative_file.startswith(("/", "\\")):
        raise ValueError("invalid file")
    root = Path(job.out_dir).resolve()
    target = (root / relative_file).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("invalid file")
    return target


def paginate(frame: pd.DataFrame, page: int, page_size: int) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = min(500, max(1, int(page_size)))
    total = len(frame)
    start = (page - 1) * page_size
    sample = frame.iloc[start : start + page_size].astype(object)
    sample = sample.where(pd.notna(sample), None)
    return {"items": sample.to_dict("records"), "total": total, "page": page, "page_size": page_size}
