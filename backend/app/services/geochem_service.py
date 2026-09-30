# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import numpy as np
from sqlalchemy.orm import Session

from ..models import (
    Borehole,
    BoreholeSection,
    ChemicalAssay,
    ChemicalBorehole,
    GeochemMiningJob,
    GeochemReconstructJob,
    GeochemRuleSet,
    ReconstructJob,
    get_session,
)
from ..algorithms.geochem_mining.professional_rules import (
    MINING_DISPLAY_LEVEL_COLORS,
    RELIABILITY_LEVEL_COLORS,
    build_threshold_profile,
    classify_mining_level,
    continuous_mining_color,
    continuous_relative_color,
    professional_display_label,
)
from ..algorithms.geochem_mining.data_pipeline import dataset_sha256, load_geochem_dataset
from ..algorithms.geochem_mining.variation import expert_2sigma_background, robust_log_mad_background
from .geochem_rule_service import ensure_default_rule_set, runtime_rule_config
from ..utils import parse_origin_xyz


DEFAULT_ELEMENTS = ["Fe", "Ca", "Si", "Cu", "Pb", "Zn", "As", "Sb", "W", "Sn"]


def process_failure_message(code: int) -> str:
    unsigned = int(code) & 0xFFFFFFFF
    hexadecimal = f"0x{unsigned:08X}"
    if unsigned == 0xC0000142:
        return (
            "三维计算程序启动失败：Windows 运行库或 DLL 初始化失败"
            f"（{hexadecimal}），尚未进入插值计算"
        )
    return f"三维计算进程异常退出（{hexadecimal}，十进制 {code}）"


def _storage_root() -> Path:
    root = Path(__file__).resolve().parents[2] / "storage" / "geochem"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _algorithm_script() -> Path:
    return Path(__file__).resolve().parents[1] / "algorithms" / "geochem_stl_interpolate_fast.py"


def _constrained_algorithm_script() -> Path:
    # The active constrained path is the geology-aware residual IDW model.
    return Path(__file__).resolve().parents[1] / "algorithms" / "geochem_geology_constrained.py"


LITHOLOGY_GROUPS_V2 = (
    (-1001, "花岗质岩组", ("花岗岩", "黑云母二长花岗岩", "二长花岗岩", "黑云母花岗岩", "正长花岗岩", "正厂花岗岩", "花岗细晶岩", "花岗闪长岩")),
    (-1002, "闪长质岩组", ("闪长岩", "英云闪长岩", "细-中粒英云闪长岩")),
    (-1003, "板岩组", ("板岩",)),
    (-1004, "碳酸盐岩组", ("灰岩", "细晶灰岩")),
    (-1005, "砂岩组", ("细粒长石石英砂岩", "石英砂岩", "砂岩")),
    (-1006, "松散覆盖层组", ("碎石土", "块石土", "粗角砾土", "角砾土", "粉质粘土", "卵石土", "细角砾土", "灰褐色细角砾土", "细砂")),
    (-1007, "石英特殊岩性组", ("石英",)),
)
FAULT_OR_FRACTURE_NAMES = {"压碎岩", "断层角砾土", "断层破碎带", "破碎带", "断层泥", "断层角砾岩", "断层"}


def _write_lithology_group_config(
    lithology_map_csv: Path,
    source_constraint_job_id: str,
    output_path: Path,
) -> Path:
    """Bind the confirmed grouping decision to the exact Algorithm-B map."""

    frame = pd.read_csv(lithology_map_csv, encoding="utf-8-sig")
    available = {
        str(value).strip()
        for value in frame.get("岩性名称", pd.Series(dtype=str)).dropna().tolist()
    }
    groups = []
    for group_id, group_name, candidates in LITHOLOGY_GROUPS_V2:
        members = [name for name in candidates if name in available]
        if not members:
            continue
        if any(name in FAULT_OR_FRACTURE_NAMES for name in members):
            raise ValueError(f"断层或破碎类岩性禁止进入普通归并组: {group_name}")
        groups.append({
            "group_id": group_id,
            "group_name": group_name,
            "member_names": members,
            "basis": "依据2026-08-09汇报要求，按地质语义合并相近岩性；断层及破碎类对象始终隔离",
            "allow_cross_member_interpolation": True,
        })
    payload = {
        "schema_version": 2,
        "mapping_version": "geochem-lithology-groups-v2",
        "status": "confirmed",
        "source_constraint_job_id": source_constraint_job_id,
        "source_lithology_map_sha256": hashlib.sha256(lithology_map_csv.read_bytes()).hexdigest().upper(),
        "confirmed_by": "用户确认按2026-08-09汇报优化方案执行",
        "confirmed_at": datetime.now().astimezone().isoformat(),
        "groups": groups,
        "unmapped_policy": "keep_original",
        "fault_policy": "never_merge",
        "unmapped_names": sorted(available - {name for group in groups for name in group["member_names"]}),
        "notes": [
            "乱码或无法判定的岩性保持独立，不按关键词猜测归并。",
            "断层、断层泥、断层破碎带、破碎带、压碎岩和断层角砾类不进入普通岩性组。",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def _voxel_csv_path(out_dir: Path) -> Path:
    for name in ("geochemical_voxels_15m.csv", "geochemical_voxels_20m.csv"):
        path = out_dir / name
        if path.exists():
            return path
    return out_dir / "geochemical_voxels_15m.csv"


def _safe_filename(name: str) -> str:
    return os.path.basename(name or "model.stl") or "model.stl"


def _load_elements_json(value: str | None) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def available_elements(db: Session, model_id: int) -> List[str]:
    seen: set[str] = set()
    rows = (
        db.query(ChemicalAssay.elements_json)
        .filter(ChemicalAssay.model_id == model_id)
        .limit(10000)
        .all()
    )
    for (payload,) in rows:
        for key in _load_elements_json(payload):
            seen.add(str(key))
    preferred = [name for name in DEFAULT_ELEMENTS if name in seen]
    rest = sorted(seen - set(preferred))
    return preferred + rest


def available_constraint_jobs(db: Session, model_id: int) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    jobs = (
        db.query(ReconstructJob)
        .filter(ReconstructJob.model_id == model_id, ReconstructJob.status == "success")
        .order_by(ReconstructJob.created_at.desc())
        .all()
    )
    for job in jobs:
        try:
            params = json.loads(job.params_json or "{}")
        except Exception:
            params = {}
        if str(params.get("method", "")).upper() != "B":
            continue
        out_dir = Path(job.out_dir or "")
        voxel_csv = out_dir / "voxels_final.csv"
        layers_csv = out_dir.parent / "inputs" / "borehole_layers.csv"
        lithology_map_csv = out_dir.parent / "inputs" / "lithology_map.csv"
        if not voxel_csv.exists() or not layers_csv.exists() or not lithology_map_csv.exists():
            continue
        try:
            layer_frame = pd.read_csv(layers_csv, encoding="utf-8-sig", usecols=["钻孔ID"])
            borehole_count = int(layer_frame["钻孔ID"].nunique())
        except Exception:
            borehole_count = None
        result.append(
            {
                "id": job.id,
                "created_at": job.created_at,
                "voxel_size": float(params.get("voxel_size", 15.0)),
                "fault_thickness": float(params.get("fault_thickness", 30.0)),
                "fault_code": int(params.get("fault_code", -1)),
                "lithology_count": int(params.get("n_lithologies", 0)),
                "borehole_count": borehole_count,
                "is_local": True,
            }
        )
    return result


def create_job(db: Session, model_id: int, params: Dict[str, Any]) -> GeochemReconstructJob:
    job_id = uuid.uuid4().hex
    job_dir = _storage_root() / job_id
    (job_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (job_dir / "outputs").mkdir(parents=True, exist_ok=True)

    job = GeochemReconstructJob(
        id=job_id,
        model_id=model_id,
        status="queued",
        params_json=json.dumps(params, ensure_ascii=False),
        out_dir=str(job_dir / "outputs"),
        log_path=str(job_dir / "run.log"),
        rule_set_id=params.get("rule_set_id"),
        source_variation_job_id=params.get("source_variation_job_id"),
        source_correlation_job_id=params.get("source_correlation_job_id"),
        workflow_id=params.get("workflow_id"),
        selected_clue_id=params.get("selected_clue_id"),
        dataset_hash=params.get("analysis_data_sha256"),
        algorithm_version=params.get("algorithm_version", "geochem-reconstruct-v2"),
        algorithm_profile=params.get("algorithm_profile", "baseline"),
        validation_status="pending",
        created_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def export_assay_csv(db: Session, model_id: int, elements: List[str], assay_csv: Path, collar_csv: Path) -> Dict[str, Any]:
    rows = (
        db.query(ChemicalAssay, ChemicalBorehole)
        .join(ChemicalBorehole, ChemicalAssay.chemical_borehole_id == ChemicalBorehole.id)
        .filter(ChemicalAssay.model_id == model_id)
        .order_by(ChemicalAssay.hole_id.asc(), ChemicalAssay.from_depth.asc(), ChemicalAssay.id.asc())
        .all()
    )
    if not rows:
        raise ValueError("该模型下没有化学钻孔样品数据，请先在“化学钻孔”中导入 CSV。")

    drillhole_paths = _project_drillhole_paths(db, model_id)
    records: List[Dict[str, Any]] = []
    collars: Dict[str, Dict[str, Any]] = {}
    for assay, borehole in rows:
        element_values = _load_elements_json(assay.elements_json)
        record: Dict[str, Any] = {
            "assay_id": assay.id,
            "hole_id": assay.hole_id,
            "from_depth": assay.from_depth,
            "to_depth": assay.to_depth,
            "collar_x": borehole.collar_x,
            "collar_y": borehole.collar_y,
            "collar_z": borehole.collar_z,
        }
        path_info = _find_drillhole_path(drillhole_paths, str(assay.hole_id))
        for prefix, depth in (
            ("sample_from", assay.from_depth),
            ("sample_to", assay.to_depth),
            ("sample", (float(assay.from_depth) + float(assay.to_depth)) / 2.0),
        ):
            point = _point_on_drillhole_path(
                path_info,
                float(depth),
                fallback=(borehole.collar_x, borehole.collar_y, borehole.collar_z),
            )
            record[f"{prefix}_x"], record[f"{prefix}_y"], record[f"{prefix}_z"] = point
        record["trajectory_quality"] = (
            path_info.get("quality") if path_info else "vertical_fallback"
        )
        for element in elements:
            record[element] = element_values.get(element)
        records.append(record)
        if assay.hole_id not in collars:
            collars[assay.hole_id] = {
                "hole_id": assay.hole_id,
                "hole_depth": borehole.depth_max,
                "collar_x": borehole.collar_x,
                "collar_y": borehole.collar_y,
                "collar_z": borehole.collar_z,
            }

    assay_csv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records).to_csv(assay_csv, index=False, encoding="utf-8-sig")
    pd.DataFrame(list(collars.values())).to_csv(collar_csv, index=False, encoding="utf-8-sig")
    return {
        "assay_rows": len(records),
        "hole_count": len(collars),
        "trajectory_hole_count": len({row["hole_id"] for row in records if row["trajectory_quality"] != "vertical_fallback"}),
        "elements": elements,
        "assay_csv_sha256": hashlib.sha256(assay_csv.read_bytes()).hexdigest(),
        "collar_csv_sha256": hashlib.sha256(collar_csv.read_bytes()).hexdigest(),
    }


def _normalized_hole_suffix(value: str) -> str:
    match = re.search(r"(\d+(?:-\d+)?)$", str(value or "").strip())
    if not match:
        return str(value or "").strip().lower()
    parts = match.group(1).split("-")
    parts[0] = str(int(parts[0]))
    return "-".join(parts)


def _project_drillhole_paths(db: Session, model_id: int) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    holes = db.query(Borehole).filter(Borehole.model_id == model_id).all()
    for hole in holes:
        points: list[list[float]] = []
        sections = (
            db.query(BoreholeSection)
            .filter(BoreholeSection.borehole_id == hole.id)
            .order_by(BoreholeSection.id.asc())
            .all()
        )
        for section in sections:
            for raw in (section.顶坐标, section.底坐标):
                point = _section_point(raw)
                if point and (not points or np.linalg.norm(np.asarray(point) - np.asarray(points[-1])) > 1e-6):
                    points.append(point)
        if len(points) < 2 and None not in (hole.x, hole.y, hole.z):
            points = [
                [float(hole.x), float(hole.y), float(hole.z)],
                [float(hole.x), float(hole.y), float(hole.z) - float(hole.Holedepth or 0.0)],
            ]
            quality = "vertical_fallback"
        else:
            quality = "section_coordinate_polyline"
        if len(points) < 2:
            continue
        segment_lengths = np.linalg.norm(np.diff(np.asarray(points, dtype=float), axis=0), axis=1)
        cumulative = np.concatenate(([0.0], np.cumsum(segment_lengths)))
        info = {
            "points": points,
            "cumulative": cumulative.tolist(),
            "path_length": float(cumulative[-1]),
            "hole_depth": float(hole.Holedepth or cumulative[-1]),
            "quality": quality,
        }
        result[str(hole.Borehole).strip().lower()] = info
        result[f"__suffix__:{_normalized_hole_suffix(hole.Borehole)}"] = info
    return result


def _find_drillhole_path(paths: dict[str, dict[str, Any]], hole_id: str) -> dict[str, Any] | None:
    return paths.get(hole_id.strip().lower()) or paths.get(f"__suffix__:{_normalized_hole_suffix(hole_id)}")


def _point_on_drillhole_path(
    path_info: dict[str, Any] | None,
    depth: float,
    *,
    fallback: tuple[Any, Any, Any],
) -> tuple[float | None, float | None, float | None]:
    if path_info and path_info.get("path_length", 0.0) > 0:
        points = np.asarray(path_info["points"], dtype=float)
        cumulative = np.asarray(path_info["cumulative"], dtype=float)
        hole_depth = max(float(path_info.get("hole_depth") or cumulative[-1]), 1e-9)
        target = np.clip(float(depth) / hole_depth, 0.0, 1.0) * cumulative[-1]
        segment = min(int(np.searchsorted(cumulative, target, side="right") - 1), len(points) - 2)
        segment = max(segment, 0)
        span = max(cumulative[segment + 1] - cumulative[segment], 1e-9)
        fraction = (target - cumulative[segment]) / span
        point = points[segment] + fraction * (points[segment + 1] - points[segment])
        return tuple(float(value) for value in point)
    x, y, z = fallback
    if None in (x, y, z):
        return None, None, None
    return float(x), float(y), float(z) - float(depth)


def list_outputs(job: GeochemReconstructJob) -> List[str]:
    out_dir = Path(job.out_dir or "")
    if not out_dir.exists():
        return []
    return sorted(str(path.relative_to(out_dir)).replace("\\", "/") for path in out_dir.rglob("*") if path.is_file())


def read_log_tail(job: GeochemReconstructJob, max_chars: int = 8000) -> str:
    path = Path(job.log_path or "")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-max_chars:]


ELEMENT_BASE_COLORS: Dict[str, tuple[int, int, int]] = {
    "Cu": (230, 126, 34),
    "Fe": (192, 57, 43),
    "Zn": (45, 117, 182),
    "Pb": (126, 87, 194),
    "As": (196, 151, 18),
    "Au": (212, 160, 23),
    "Ag": (91, 119, 142),
    "Ca": (46, 139, 87),
    "Si": (27, 153, 139),
    "W": (121, 85, 72),
    "Sn": (87, 111, 127),
}


def element_base_color(element: str) -> tuple[int, int, int]:
    if element in ELEMENT_BASE_COLORS:
        return ELEMENT_BASE_COLORS[element]
    seed = sum((index + 1) * ord(char) for index, char in enumerate(str(element)))
    return (70 + seed % 140, 70 + (seed // 7) % 140, 70 + (seed // 17) % 140)


def _color_ramp(value: float, low: float, high: float, element: str = "") -> tuple[int, int, int]:
    if not math.isfinite(value) or high <= low:
        t = 0.0
    else:
        t = max(0.0, min(1.0, (value - low) / (high - low)))
    base = element_base_color(element)
    light = tuple(int(245 * 0.72 + channel * 0.28) for channel in base)
    dark = tuple(int(channel * 0.72) for channel in base)
    return tuple(int(light[i] + (dark[i] - light[i]) * t) for i in range(3))


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _support_color(value: float) -> tuple[int, int, int]:
    value = max(0.0, min(1.0, float(value)))
    if value <= 0.5:
        factor = value / 0.5
        return (255, int(255 - 55 * factor), int(240 - 190 * factor))
    factor = (value - 0.5) / 0.5
    return (255, int(200 - 150 * factor), int(50 - 30 * factor))


def _json_safe_mapping(values: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: (None if isinstance(value, (float, np.floating)) and not math.isfinite(float(value)) else value)
        for key, value in values.items()
    }


def generate_element_ply(
    csv_path: Path,
    out_ply: Path,
    element: str,
    max_points: int,
    seed: int = 202501,
    display_mode: str = "concentration",
    threshold_profile: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    supported_modes = {
        "concentration",
        "statistical_anomaly",
        "support_T",
        "support_boundary",
        "support_industrial",
        "confidence",
    }
    if display_mode not in supported_modes:
        raise ValueError(f"不支持的三维显示模式：{display_mode}")
    if display_mode in {"concentration", "statistical_anomaly"} and not threshold_profile:
        raise ValueError("六级浓度着色缺少背景值与品位阈值配置")

    scalar_column = (
        f"{element}_{display_mode}" if display_mode.startswith("support_") else element
    )
    usecols = [
        "x", "y", "z", element, scalar_column, "confidence_level",
        "distinct_hole_count", "support_tier",
    ]
    total = 0
    valid = 0
    values: List[pd.DataFrame] = []
    for chunk in pd.read_csv(csv_path, chunksize=200000, usecols=lambda col: col in usecols):
        if element not in chunk.columns and display_mode != "confidence":
            raise ValueError(f"结果中没有元素列 {element}")
        if element in chunk.columns:
            chunk[element] = pd.to_numeric(chunk[element], errors="coerce")
        if scalar_column not in chunk.columns and display_mode.startswith("support_"):
            raise ValueError(f"结果中没有支持度列 {scalar_column}")
        if scalar_column in chunk.columns:
            chunk[scalar_column] = pd.to_numeric(chunk[scalar_column], errors="coerce")
        chunk = chunk.dropna(subset=["x", "y", "z"])
        total += len(chunk)
        valid += int(chunk[scalar_column].notna().sum()) if scalar_column in chunk else len(chunk)
        if display_mode in {"concentration", "statistical_anomaly"} or display_mode.startswith("support_"):
            chunk = chunk.dropna(subset=[scalar_column])
        if len(chunk):
            values.append(chunk)
    if not values:
        raise ValueError(f"元素 {element} 没有可用于生成 PLY 的有效结果。")
    df = pd.concat(values, ignore_index=True)
    # The prospecting view should show interpretable values, not fill most of
    # the screen with no-data/fault-mask grey. Those causes remain available in
    # the separate confidence view.
    if display_mode in {"statistical_anomaly", "mining_anomaly", "anomaly_body"} and "confidence_level" in df.columns:
        confidence = df["confidence_level"].astype(str).str.lower()
        df = df.loc[~confidence.isin({"no_data", "fault_mask"})].copy()
        if df.empty and display_mode != "statistical_anomaly":
            raise ValueError(f"元素 {element} 没有可用于找矿潜力显示的可靠体素。")
    if display_mode == "statistical_anomaly":
        threshold = pd.to_numeric(
            (threshold_profile or {}).get("threshold_T_auto"), errors="coerce"
        )
        if pd.isna(threshold) or float(threshold) <= 0:
            raise ValueError(f"元素 {element} 缺少统计异常体提取所需的异常下限。")
        df = df.loc[pd.to_numeric(df[element], errors="coerce") >= float(threshold)].copy()
    if display_mode == "anomaly_body":
        background = float((threshold_profile or {}).get("background_mean") or math.nan)
        cutoffs = (threshold_profile or {}).get("relative_ratio_cutoffs") or [1.25, 1.5, 2.0]
        # The anomaly-body layer deliberately excludes the broad background and
        # weak-enrichment shell.  The complete field remains available as a
        # separate layer, so this tighter threshold does not discard data.
        lower = background * float(cutoffs[-1]) if math.isfinite(background) else math.nan
        if not math.isfinite(lower):
            raise ValueError(f"元素 {element} 缺少异常体提取所需的背景值。")
        df = df.loc[pd.to_numeric(df[element], errors="coerce") >= lower].copy()
        if df.empty:
            raise ValueError(f"元素 {element} 没有达到相对富集下限的连续异常体素。")
        support_rule = "strong_relative_enrichment"
        if "distinct_hole_count" in df.columns:
            multi_hole = df.loc[
                pd.to_numeric(df["distinct_hole_count"], errors="coerce").fillna(0) >= 2
            ].copy()
            # Keep the stricter multi-hole body when it is large enough to be
            # spatially interpretable.  Sparse elements retain their one-hole
            # exploratory bodies instead of disappearing from the viewer.
            if len(multi_hole) >= 500:
                df = multi_hole
                support_rule = "strong_relative_enrichment_and_at_least_two_drillholes"
            else:
                support_rule = "strong_relative_enrichment_single_hole_exploratory"
    if max_points > 0 and len(df) > max_points:
        df = df.sample(n=max_points, random_state=seed)
    finite_values = (
        pd.to_numeric(df[scalar_column], errors="coerce").dropna()
        if scalar_column in df else pd.Series(dtype=float)
    )
    configured_low = pd.to_numeric((threshold_profile or {}).get("display_low_ppm"), errors="coerce")
    configured_high = pd.to_numeric((threshold_profile or {}).get("display_high_ppm"), errors="coerce")
    low = (
        float(configured_low)
        if pd.notna(configured_low) and float(configured_low) > 0
        else float(finite_values.quantile(0.05)) if len(finite_values) else math.nan
    )
    high = (
        float(configured_high)
        if pd.notna(configured_high) and float(configured_high) > 0
        else float(finite_values.quantile(0.98)) if len(finite_values) else math.nan
    )
    level_counts: Dict[str, int] = {}
    confidence_counts: Dict[str, int] = {}

    out_ply.parent.mkdir(parents=True, exist_ok=True)
    with out_ply.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("ply\n")
        handle.write("format ascii 1.0\n")
        handle.write(f"comment element={element} display_mode={display_mode} low_p05={low} high_p98={high}\n")
        handle.write(f"element vertex {len(df)}\n")
        handle.write("property float x\nproperty float y\nproperty float z\n")
        handle.write("property uchar red\nproperty uchar green\nproperty uchar blue\n")
        handle.write("end_header\n")
        for record in df.to_dict("records"):
            x, y, z = float(record["x"]), float(record["y"]), float(record["z"])
            value = record.get(element)
            if display_mode in {"concentration", "statistical_anomaly"}:
                confidence = str(record.get("confidence_level") or "").lower()
                confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
                classification = classify_mining_level(value, threshold_profile or {})
                level = classification["display_level"]
                level_counts[level] = level_counts.get(level, 0) + 1
                if display_mode == "statistical_anomaly":
                    r, g, b = continuous_mining_color(value, threshold_profile or {})
                else:
                    # Professional classification remains recorded in the legend;
                    # the default field uses the same palette over its robust log
                    # range so background-dominated elements remain legible.
                    r, g, b = continuous_relative_color(value, low, high)
            elif display_mode.startswith("support_"):
                r, g, b = _support_color(float(record[scalar_column]))
            else:
                confidence = str(record.get("confidence_level") or "no_data").lower()
                confidence_colors = {
                    "high": (24, 145, 92),
                    "medium": (238, 166, 35),
                    "low": (126, 139, 156),
                    "no_data": (215, 220, 226),
                    "fault_mask": (55, 63, 73),
                }
                r, g, b = confidence_colors.get(confidence, confidence_colors["no_data"])
                level_counts[confidence] = level_counts.get(confidence, 0) + 1
            handle.write(f"{x:.6f} {y:.6f} {z:.6f} {r} {g} {b}\n")
    base = element_base_color(element)
    result = {
        "element": element,
        "display_mode": display_mode,
        "scalar_column": scalar_column,
        "source_rows": int(total),
        "valid_rows": int(valid),
        "ply_points": int(len(df)),
        "color_low_p05": low if math.isfinite(low) else None,
        "color_low_p02": low if math.isfinite(low) else None,
        "color_high_p98": high if math.isfinite(high) else None,
        "base_color_rgb": list(base),
        "base_color_hex": "#%02X%02X%02X" % base,
        "color_encoding": (
            "relative_continuous_log_p05_p98 within the same six project-wide colours; "
            "actual ppm values and professional thresholds remain separately reported; "
            f"rule_status={(threshold_profile or {}).get('rule_set_status', 'draft')}"
            if display_mode == "concentration"
            else "threshold_T_auto clipped anomaly geometry with professional threshold colours"
            if display_mode == "statistical_anomaly"
            else "threshold-neighbourhood support from 0 to 1"
            if display_mode.startswith("support_")
            else "validated coverage confidence and explicit blank reasons"
        ),
        "level_counts": level_counts,
        "confidence_counts": confidence_counts if display_mode in {"concentration", "statistical_anomaly"} else {},
        "legend": [
            {"level": professional_display_label(level), "color": color}
            for level, color in MINING_DISPLAY_LEVEL_COLORS.items()
        ] if display_mode in {"concentration", "statistical_anomaly"} else [],
        "reliability_legend": [
            {"level": level, "color": color}
            for level, color in RELIABILITY_LEVEL_COLORS.items()
        ] if display_mode in {"concentration", "statistical_anomaly"} else [],
        "output": str(out_ply.resolve()),
        "output_name": out_ply.name,
    }
    if display_mode in {"statistical_anomaly", "mining_anomaly", "anomaly_body"}:
        reliable_count = int(confidence_counts.get("high", 0)) + int(
            confidence_counts.get("medium", 0)
        )
        result["recommended_visibility"] = reliable_count > 0
    if display_mode == "anomaly_body":
        result["anomaly_support_rule"] = support_rule
    if threshold_profile:
        result["threshold_profile"] = _json_safe_mapping(dict(threshold_profile))
    return result


def compute_element_stats(csv_path: Path, elements: List[str]) -> Dict[str, Dict[str, Any]]:
    stats: Dict[str, Dict[str, Any]] = {}
    if not csv_path.exists():
        return stats

    for element in elements:
        values: List[np.ndarray] = []
        for chunk in pd.read_csv(csv_path, chunksize=200000, usecols=lambda col: col == element):
            if element not in chunk.columns:
                break
            series = pd.to_numeric(chunk[element], errors="coerce").dropna()
            if len(series):
                values.append(series.to_numpy(dtype=float))

        if not values:
            continue

        arr = np.concatenate(values)
        if arr.size == 0:
            continue
        p95 = float(np.quantile(arr, 0.95))
        stats[element] = {
            "valid_count": int(arr.size),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "mean": float(np.mean(arr)),
            "p95": p95,
            "high_value_voxel_count": int(np.sum(arr >= p95)),
        }
    return stats


def compute_threshold_profiles(
    assay_csv: Path,
    elements: List[str],
    industrial_grades_ppm: Dict[str, Any] | None = None,
    rule_config: Dict[str, Any] | None = None,
) -> Dict[str, Dict[str, Any]]:
    """用原始化验值建立找矿分级，避免拿插值体素反推背景值。"""

    profiles: Dict[str, Dict[str, Any]] = {}
    if not assay_csv.exists():
        return profiles
    frame = pd.read_csv(assay_csv, encoding="utf-8-sig", usecols=lambda column: column in set(elements))
    rule_config = rule_config or {}
    for element in elements:
        if element not in frame:
            continue
        values = pd.to_numeric(frame[element], errors="coerce")
        background_method = str(rule_config.get("background_method") or "log_mad")
        calculator = expert_2sigma_background if background_method == "expert_2sigma" else robust_log_mad_background
        background = calculator(
            values,
            minimum_count=int(rule_config.get("minimum_positive_count", 30)),
        )
        profile = build_threshold_profile(
            element,
            values[values > 0],
            background.get("background_mean"),
            background.get("threshold_T_auto"),
            {
                **(rule_config.get("industrial_grades_ppm") or {}),
                **(industrial_grades_ppm or {}),
            },
            boundary_grades_ppm=rule_config.get("boundary_grades_ppm") or {},
            relative_ratio_cutoffs=rule_config.get("relative_ratio_cutoffs"),
            rule_set_id=rule_config.get("id"),
            rule_set_status=rule_config.get("status", "draft"),
        )
        profile.update({
            "background_used_count": background.get("background_used_count", 0),
            "background_iterations": background.get("background_iterations", 0),
            "removed_high_count": background.get("removed_high_count", 0),
            "background_distribution": background.get("distribution"),
            "normality_p_value": background.get("normality_p_value"),
            "threshold_formula": background.get("threshold_formula"),
            "outlier_cutoff": background.get("outlier_cutoff"),
            "outlier_cutoff_scale": background.get("outlier_cutoff_scale"),
            "background_method": background_method,
            "profile_source": f"original_assay_values_{background_method}",
        })
        profiles[element] = _json_safe_mapping(profile)
    return profiles


def resolve_threshold_profiles(
    assay_csv: Path,
    elements: List[str],
    params: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    variation_dir = Path(str(params.get("variation_output_dir") or ""))
    statistics_path = variation_dir / "element_background_statistics.csv"
    if params.get("source_variation_job_id"):
        if not statistics_path.exists():
            raise ValueError("绑定的变化规律任务缺少背景统计文件")
        statistics = pd.read_csv(statistics_path, encoding="utf-8-sig")
        profiles: Dict[str, Dict[str, Any]] = {}
        for element in elements:
            selected = statistics.loc[statistics["element"] == element]
            if selected.empty:
                raise ValueError(f"绑定的变化规律任务不包含元素 {element}")
            stored = selected.iloc[0].to_dict()
            rule_config = params.get("rule_set") or {}
            source_values = pd.read_csv(
                assay_csv,
                encoding="utf-8-sig",
                usecols=lambda column: column == element,
            )[element]
            profile = build_threshold_profile(
                element,
                pd.to_numeric(source_values, errors="coerce"),
                stored.get("background_mean"),
                stored.get("threshold_T_auto"),
                rule_config.get("industrial_grades_ppm") or {},
                boundary_grades_ppm=rule_config.get("boundary_grades_ppm") or {},
                relative_ratio_cutoffs=rule_config.get("relative_ratio_cutoffs"),
                rule_set_id=rule_config.get("id"),
                rule_set_status=rule_config.get("status", "draft"),
            )
            for key in ("background_used_count", "background_iterations", "removed_high_count"):
                profile[key] = stored.get(key)
            profile["profile_source"] = "bound_variation_background_with_current_confirmed_grades"
            profile["source_variation_job_id"] = params["source_variation_job_id"]
            profiles[element] = _json_safe_mapping(profile)
        return profiles
    return compute_threshold_profiles(
        assay_csv,
        elements,
        params.get("industrial_grades_ppm") or {},
        params.get("rule_set") or {},
    )


def prepare_interpolation_assay_csv(
    assay_csv: Path,
    output_csv: Path,
    element: str,
    profile: Dict[str, Any],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply the expert 3-sigma sample screen before a comparison interpolation."""

    frame = pd.read_csv(assay_csv, encoding="utf-8-sig")
    values = pd.to_numeric(frame.get(element), errors="coerce")
    method = str(profile.get("background_method") or params.get("background_method") or "log_mad")
    cutoff = pd.to_numeric(profile.get("outlier_cutoff"), errors="coerce")
    if method != "expert_2sigma" or pd.isna(cutoff) or float(cutoff) <= 0:
        frame.to_csv(output_csv, index=False, encoding="utf-8-sig")
        return {
            "method": method,
            "input_sample_count": int(len(frame)),
            "used_sample_count": int(len(frame)),
            "removed_sample_count": 0,
            "cutoff_ppm": None,
            "source": str(assay_csv.name),
        }
    keep = values.isna() | (values <= float(cutoff))
    filtered = frame.loc[keep].copy()
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    filtered.to_csv(output_csv, index=False, encoding="utf-8-sig")
    return {
        "method": method,
        "input_sample_count": int(len(frame)),
        "used_sample_count": int(len(filtered)),
        "removed_sample_count": int((~keep).sum()),
        "cutoff_ppm": float(cutoff),
        "cutoff_scale": profile.get("outlier_cutoff_scale"),
        "source": str(assay_csv.name),
        "output": str(output_csv.name),
    }


def update_preview_ply(
    db: Session,
    job: GeochemReconstructJob,
    element: str,
    display_mode: str = "concentration",
) -> GeochemReconstructJob:
    element = str(element or "").strip()
    if not element:
        raise ValueError("preview element is required")
    if job.status != "success":
        raise ValueError("only successful jobs can switch preview element")

    out_dir = Path(job.out_dir or "")
    csv_path = _voxel_csv_path(out_dir)
    if not csv_path.exists():
        raise ValueError("geochemical voxel CSV not found")

    params = json.loads(job.params_json or "{}")
    elements = [str(x).strip() for x in params.get("elements", []) if str(x).strip()]
    if elements and element not in elements:
        raise ValueError(f"element {element} was not included in this job")
    if display_mode != "concentration":
        raise ValueError("主场景只提供完整浓度场，不提供连续异常体或其他派生预览。")

    summary: Dict[str, Any]
    try:
        summary = json.loads(job.summary_json or "{}")
        if not isinstance(summary, dict):
            summary = {}
    except Exception:
        summary = {}

    profiles = summary.get("threshold_profiles") or resolve_threshold_profiles(
        out_dir.parent / "inputs" / "assay_intervals_merged.csv",
        elements or [element],
        params,
    )
    threshold_profile = profiles.get(element)
    output_name = f"geochem_{element}_full_field.ply"
    ply_info = generate_element_ply(
        csv_path,
        out_dir / output_name,
        element,
        int(params.get("max_points_ply", 600000)),
        display_mode=display_mode,
        threshold_profile=threshold_profile,
    )
    summary["preview_ply"] = ply_info
    # The active preview is the complete concentration field itself, not an
    # independently extracted anomaly body.
    summary["full_field_ply"] = ply_info
    summary["active_preview_element"] = element
    summary["active_preview_mode"] = display_mode
    summary["threshold_profiles"] = profiles
    raw_points_csv = out_dir / "used_assay_points.csv"
    if raw_points_csv.exists():
        raw_output_name = f"raw_assay_{element}.ply"
        raw_profile = {
            **(threshold_profile or {}),
            "display_low_ppm": ply_info.get("color_low_p05"),
            "display_high_ppm": ply_info.get("color_high_p98"),
        }
        raw_ply_info = generate_element_ply(
            raw_points_csv,
            out_dir / raw_output_name,
            element,
            0,
            display_mode=display_mode,
            threshold_profile=raw_profile,
        )
        summary["raw_sample_ply"] = raw_ply_info
    summary.pop("raw_sample_anomaly_ply", None)
    if elements and not summary.get("element_stats"):
        summary["element_stats"] = compute_element_stats(csv_path, elements)

    (out_dir / "model_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    job.params_json = json.dumps({**params, "preview_element": element, "preview_mode": display_mode}, ensure_ascii=False)
    job.summary_json = json.dumps(summary, ensure_ascii=False)
    db.commit()
    db.refresh(job)
    return job


def ensure_summary_metadata(db: Session, job: GeochemReconstructJob) -> GeochemReconstructJob:
    if job.status != "success":
        return job

    out_dir = Path(job.out_dir or "")
    csv_path = _voxel_csv_path(out_dir)
    if not csv_path.exists():
        return job

    params = json.loads(job.params_json or "{}")
    elements = [str(x).strip() for x in params.get("elements", []) if str(x).strip()]
    if not elements:
        return job

    try:
        summary = json.loads(job.summary_json or "{}")
        if not isinstance(summary, dict):
            summary = {}
    except Exception:
        summary = {}

    changed = False
    if not summary.get("element_stats"):
        summary["element_stats"] = compute_element_stats(csv_path, elements)
        changed = True
    if not summary.get("threshold_profiles"):
        summary["threshold_profiles"] = resolve_threshold_profiles(
            out_dir.parent / "inputs" / "assay_intervals_merged.csv",
            elements,
            params,
        )
        changed = True

    if changed:
        (out_dir / "model_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        job.summary_json = json.dumps(summary, ensure_ascii=False)
        db.commit()
        db.refresh(job)
    return job


def _constraint_paths(db: Session, model_id: int, constraint_job_id: str) -> Dict[str, Any]:
    source = db.get(ReconstructJob, constraint_job_id)
    if not source or source.model_id != model_id or source.status != "success":
        raise ValueError("所选已有地质模型约束不存在、未成功或不属于当前模型。")
    try:
        source_params = json.loads(source.params_json or "{}")
    except Exception:
        source_params = {}
    if str(source_params.get("method", "")).upper() != "B":
        raise ValueError("所选结果不具备当前三维插值所需的岩性体素约束。")
    source_out = Path(source.out_dir or "")
    source_inputs = source_out.parent / "inputs"
    input_stls = list(source_inputs.glob("*.stl"))
    model_stl = next(
        (path for path in input_stls if "fault" not in path.name.lower() and "断层" not in path.name),
        None,
    )
    fault_stl = next(
        (path for path in input_stls if "fault" in path.name.lower() or "断层" in path.name),
        None,
    )
    paths = {
        "job": source,
        "params": source_params,
        "voxel_csv": source_out / "voxels_final.csv",
        "layers_csv": source_out.parent / "inputs" / "borehole_layers.csv",
        "lithology_map_csv": source_out.parent / "inputs" / "lithology_map.csv",
        "model_stl": model_stl,
        "fault_stl": fault_stl,
    }
    missing = [path.name for key, path in paths.items() if key.endswith("_csv") and not path.exists()]
    if missing:
        raise ValueError(f"已有地质模型约束结果不完整，缺少: {', '.join(missing)}")
    return paths


def _section_point(value: str | None) -> list[float] | None:
    x, y, z = parse_origin_xyz(value)
    if x is None or y is None or z is None:
        return None
    return [float(x), float(y), float(z)]


def _export_scene_drillholes(db: Session, model_id: int, output_path: Path) -> dict[str, Any]:
    chemical_ids = {
        str(value)
        for value, in db.query(ChemicalBorehole.hole_id)
        .filter(ChemicalBorehole.model_id == model_id)
        .all()
    }
    holes = (
        db.query(Borehole)
        .filter(Borehole.model_id == model_id)
        .order_by(Borehole.Borehole.asc())
        .all()
    )
    rows: list[dict[str, Any]] = []
    for hole in holes:
        points: list[list[float]] = []
        sections = (
            db.query(BoreholeSection)
            .filter(BoreholeSection.borehole_id == hole.id)
            .order_by(BoreholeSection.id.asc())
            .all()
        )
        for section in sections:
            for value in (section.顶坐标, section.底坐标):
                point = _section_point(value)
                if point and (not points or point != points[-1]):
                    points.append(point)
        if len(points) < 2 and hole.x is not None and hole.y is not None and hole.z is not None:
            depth = float(hole.Holedepth or 0)
            points = [
                [float(hole.x), float(hole.y), float(hole.z)],
                [float(hole.x), float(hole.y), float(hole.z) - depth],
            ]
        if len(points) >= 2:
            rows.append(
                {
                    "hole_id": str(hole.Borehole),
                    "has_chemical_assays": str(hole.Borehole) in chemical_ids,
                    "trajectory_quality": "section_coordinates" if sections else "vertical_fallback",
                    "points": points,
                }
            )
    payload = {
        "project_drillhole_count": len(rows),
        "chemical_drillhole_count": sum(bool(row["has_chemical_assays"]) for row in rows),
        "drillholes": rows,
        "limitations": [
            "无化验钻孔仅作空间参照，不参与化学统计或插值。",
            "缺少测斜轨迹的钻孔使用已有分段坐标或垂直回退，并在单孔属性中标明。",
        ],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _export_scene_assays(
    assay_csv: Path,
    element: str,
    profile: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    frame = pd.read_csv(assay_csv, encoding="utf-8-sig")
    records: list[dict[str, Any]] = []
    for row in frame.to_dict("records"):
        value = pd.to_numeric(row.get(element), errors="coerce")
        # A zero / empty assay is not an observed positive chemical interval.
        # Excluding it makes the scene count match the actual six chemical
        # drillholes for the current element rather than counting the all-zero
        # reference hole as a chemical observation.
        if pd.isna(value) or float(value) <= 0:
            continue
        classification = classify_mining_level(float(value), profile)
        if classification["display_level"] not in MINING_DISPLAY_LEVEL_COLORS:
            continue
        ratio = classification.get("value_to_background_ratio")
        if isinstance(ratio, (float, np.floating)) and not math.isfinite(float(ratio)):
            ratio = None
        records.append(
            {
                "source_interval_id": row.get("assay_id"),
                "hole_id": str(row.get("hole_id") or ""),
                "from_depth": row.get("from_depth"),
                "to_depth": row.get("to_depth"),
                "x": row.get("sample_x"),
                "y": row.get("sample_y"),
                "z": row.get("sample_z"),
                "x_from": row.get("sample_from_x"),
                "y_from": row.get("sample_from_y"),
                "z_from": row.get("sample_from_z"),
                "x_to": row.get("sample_to_x"),
                "y_to": row.get("sample_to_y"),
                "z_to": row.get("sample_to_z"),
                "trajectory_quality": row.get("trajectory_quality"),
                "value_ppm": float(value),
                "display_level": classification["display_level"],
                "display_color": classification["display_color_hex"],
                "background_ratio": ratio,
            }
        )
    payload = {
        "element": element,
        "interval_count": len(records),
        "hole_count": len({item["hole_id"] for item in records}),
        "level_counts": {
            level: sum(1 for item in records if item["display_level"] == level)
            for level in MINING_DISPLAY_LEVEL_COLORS
        },
        "threshold_profile": _json_safe_mapping(dict(profile)),
        "intervals": records,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _scene_bounds_and_origin(stl_path: Path | None) -> tuple[list[float] | None, list[float]]:
    if stl_path and stl_path.exists():
        try:
            import trimesh

            mesh = trimesh.load(stl_path, force="mesh")
            bounds = np.asarray(mesh.bounds, dtype=float)
            return bounds.tolist(), bounds.mean(axis=0).tolist()
        except Exception:
            pass
    return None, [0.0, 0.0, 0.0]


def build_scene_manifest(
    db: Session,
    job: GeochemReconstructJob,
    *,
    geobody_stl: Path | None,
    fault_stl: Path | None,
    assay_csv: Path,
    preview_element: str,
    threshold_profile: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Create the small, stable scene contract consumed by the V2 viewer."""

    out_dir = Path(job.out_dir)
    scene_dir = out_dir / "scene"
    scene_dir.mkdir(parents=True, exist_ok=True)
    layers: dict[str, Any] = {}
    if geobody_stl and geobody_stl.exists():
        target = scene_dir / "geobody.stl"
        if geobody_stl.resolve() != target.resolve():
            shutil.copy2(geobody_stl, target)
        layers["geobody"] = {
            "file": "scene/geobody.stl",
            "format": "stl",
            "visible": True,
            "opacity": 0.22,
            "color": "#7AA6C2",
        }
    if fault_stl and fault_stl.exists():
        target = scene_dir / "fault.stl"
        if fault_stl.resolve() != target.resolve():
            shutil.copy2(fault_stl, target)
        layers["fault"] = {
            "file": "scene/fault.stl",
            "format": "stl",
            "visible": True,
            "opacity": 0.42,
            "color": "#F59E0B",
            "constraint_mode": "algorithm_b_fault_code_and_distance_mask",
            "constraint_reason": "沿用所绑定算法B任务的断层编码和断层影响距离。",
        }
    drillholes = _export_scene_drillholes(db, job.model_id, scene_dir / "drillholes.json")
    assays = _export_scene_assays(
        assay_csv,
        preview_element,
        threshold_profile,
        scene_dir / "assay_intervals.json",
    )
    ply = (summary.get("preview_ply") or {}).get("output_name")
    full_field_ply = (summary.get("full_field_ply") or {}).get("output_name")
    comparison_info = summary.get("comparison_anomaly_ply") or {}
    comparison_ply = comparison_info.get("output_name")
    layers["drillholes"] = {
        "file": "scene/drillholes.json",
        "format": "json",
        "visible": True,
        "project_count": drillholes["project_drillhole_count"],
        "chemical_count": drillholes["chemical_drillhole_count"],
    }
    layers["assay_intervals"] = {
        "file": "scene/assay_intervals.json",
        "format": "json",
        "visible": True,
        "count": assays["interval_count"],
        "hole_count": assays["hole_count"],
    }
    if comparison_ply:
        layers["statistical_anomaly_layer"] = {
            "file": comparison_ply,
            "format": "ply",
            "visible": False,
            "element": preview_element,
            "display_mode": "statistical_anomaly",
            "label": f"{preview_element} 统计异常体",
            "threshold_ppm": threshold_profile.get("threshold_T_auto"),
            "voxel_count": comparison_info.get("ply_points", 0),
            "color_encoding": "professional_threshold_palette",
            "meaning": "仅显示达到本方法统计异常下限的体素；完整浓度场仍作为主图层显示。",
        }
    if ply:
        validation_passed = bool((summary.get("candidate_gate") or {}).get("validation_passed"))
        preview_info = summary.get("preview_ply") or {}
        layers["element_layer"] = {
            "file": ply,
            "format": "ply",
            "visible": True,
            "element": preview_element,
            "opacity_encoding": "reliability",
            "color_encoding": "continuous_shared_six_level_palette",
            "display_mode": "relative_continuous_p05_p98",
            "display_low_ppm": preview_info.get("color_low_p05"),
            "display_high_ppm": preview_info.get("color_high_p98"),
            "status": "validated_reference" if validation_passed else "exploratory_validation_limited",
            "validation_passed": validation_passed,
        }
        if full_field_ply:
            layers["element_layer"] = {
                **layers["element_layer"],
                "file": full_field_ply,
                "visible": True,
                "meaning": "地质体内完整单元素浓度场；当前按该元素P05-P98连续拉伸公共六色锚点，用于显示内部相对空间变化，不等同于矿产品位等级；可靠性另行记录。",
            }
        if comparison_ply:
            layers["element_layer"] = {
                **layers["element_layer"],
                "file": full_field_ply or ply,
                "visible": True,
                "display_mode": "concentration",
                "label": f"{preview_element} 完整浓度场",
                "meaning": "按本方法实际插值输入生成的整个地质体单元素浓度场；统计异常体作为可选叠加层。",
            }
    bounds, origin = _scene_bounds_and_origin(geobody_stl)
    manifest = {
        "schema_version": "geochem-scene-v2",
        "job_id": job.id,
        "workflow_id": job.workflow_id,
        "selected_clue_id": job.selected_clue_id,
        "element": preview_element,
        "algorithm_profile": job.algorithm_profile or "anisotropic_idw_trend_residual_full_field",
        "algorithm_basis": "算法B地质体与断层约束 + 已确认岩性归并 + 结构轴旋转搜索椭球 + 多钻孔均衡log-IDW残差 + 岩性趋势全域浓度场",
        "coordinate_origin": origin,
        "bounds": bounds,
        "layers": layers,
        "legend": [
            {
                "level": professional_display_label(level),
                "color": color,
                "measured_interval_count": assays["level_counts"].get(level, 0),
            }
            for level, color in MINING_DISPLAY_LEVEL_COLORS.items()
        ],
        "reliability_encoding": {
            "channel": "support_tier_and_confidence_fields",
            "meaning": "椭球内支持与跨域低可信外推分别记录；完整场有值不等于高可信。",
        },
        "background_definition": {
            "value_ppm": threshold_profile.get("background_mean"),
            "method": threshold_profile.get("background_method") or threshold_profile.get("profile_source"),
            "meaning": "背景值表示本项目该元素在未见明显富集条件下的基准含量，用于计算相对富集倍数；它不是零值，也不是矿体边界。",
        },
        "limitations": [
            "三维结果是找矿空间参考，不等同于矿体。",
            "未提供确认产状时采用任务记录的临时结构轴参数，不表述为已确认矿体走向。",
            "远区回归合并岩性组趋势以消除无意义空洞，低支持区不表述为已验证矿体。",
        ],
    }
    manifest_path = out_dir / "scene_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    job.scene_manifest_path = str(manifest_path)
    return manifest


def normalize_scene_display_labels(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize only user-facing scene legend labels, including old jobs."""
    legend = payload.get("legend")
    if not isinstance(legend, list):
        return payload
    for item in legend:
        if isinstance(item, dict) and "level" in item:
            item["level"] = professional_display_label(item.get("level"))
    return payload


def _run_job(
    job_id: str,
    stl_path: Optional[Path] = None,
    fault_stl_path: Optional[Path] = None,
) -> None:
    db = get_session()
    job: Optional[GeochemReconstructJob] = None
    try:
        job = db.get(GeochemReconstructJob, job_id)
        if not job:
            return
        job.status = "running"
        job.started_at = datetime.utcnow()
        db.commit()

        params = json.loads(job.params_json or "{}")
        elements = [str(x).strip() for x in params.get("elements", DEFAULT_ELEMENTS) if str(x).strip()]
        out_dir = Path(job.out_dir)
        inputs_dir = out_dir.parent / "inputs"
        assay_csv = inputs_dir / "assay_intervals_merged.csv"
        collar_csv = inputs_dir / "selected_collar_from_geo_admin.csv"
        export_info = export_assay_csv(db, job.model_id, elements, assay_csv, collar_csv)
        threshold_profiles = resolve_threshold_profiles(assay_csv, elements, params)
        active_profile = threshold_profiles.get(elements[0]) or {}
        interpolation_assay_csv = assay_csv
        interpolation_filter = {
            "method": active_profile.get("background_method"),
            "input_sample_count": None,
            "used_sample_count": None,
            "removed_sample_count": 0,
        }
        if str(active_profile.get("background_method") or "") == "expert_2sigma":
            interpolation_assay_csv = inputs_dir / "assay_intervals_expert_filtered.csv"
            interpolation_filter = prepare_interpolation_assay_csv(
                assay_csv,
                interpolation_assay_csv,
                elements[0],
                active_profile,
                params,
            )

        constraint_job_id = str(params.get("constraint_job_id", "") or "").strip()
        if constraint_job_id:
            constraint = _constraint_paths(db, job.model_id, constraint_job_id)
            stl_path = stl_path or constraint.get("model_stl")
            fault_stl_path = fault_stl_path or constraint.get("fault_stl")
            source_params = constraint["params"]
            cmd = [
                sys.executable,
                "-u",
                str(_constrained_algorithm_script()),
                "--assay-csv",
                str(interpolation_assay_csv),
                "--collar-csv",
                str(collar_csv),
                "--voxel-csv",
                str(constraint["voxel_csv"]),
                "--layers-csv",
                str(constraint["layers_csv"]),
                "--lithology-map-csv",
                str(constraint["lithology_map_csv"]),
                "--output-dir",
                str(out_dir),
                "--element",
                elements[0],
                "--nearest",
                str(params.get("nearest", 48)),
                "--power",
                str(params.get("power", 1.6)),
                "--search-radius",
                str(params.get("search_radius", source_params.get("search_radius", 1200.0))),
                "--major-radius",
                str(params.get("major_radius", 2400.0)),
                "--intermediate-radius",
                str(params.get("intermediate_radius", 1200.0)),
                "--minor-radius",
                str(params.get("minor_radius", 600.0)),
                "--azimuth",
                str(params.get("azimuth", 80.1)),
                "--plunge",
                str(params.get("plunge", 7.7)),
                "--max-samples-per-hole",
                str(params.get("max_samples_per_hole", 8)),
                "--preferred-distinct-holes",
                str(params.get("preferred_distinct_holes", 6)),
                "--compatible-lithology-weight",
                str(params.get("compatible_lithology_weight", 0.70)),
                "--residual-full-radius",
                str(params.get("residual_full_radius", 0.65)),
                "--residual-fade-radius",
                str(params.get("residual_fade_radius", 1.5)),
                "--composite-interval",
                str(params.get("composite_interval", 15.0)),
                "--fault-code",
                str(source_params.get("fault_code", -1)),
                "--fault-thickness",
                str(params.get("fault_thickness", source_params.get("fault_thickness", 30.0))),
                "--chunk-size",
                str(params.get("chunk_size", 50000)),
                "--transform",
                str(params.get("transform", "log")),
                "--support-cutoff",
                str(params.get("support_probability_cutoff", 0.5)),
                "--voxel-size",
                str(source_params.get("voxel_size", 15.0)),
            ]
            for option, key in (
                ("--statistical-threshold", "threshold_T_auto"),
                ("--boundary-grade", "boundary_grade_ppm"),
                ("--industrial-grade", "industrial_grade_ppm"),
            ):
                value = active_profile.get(key)
                if value is not None and not pd.isna(value):
                    cmd.extend([option, str(value)])
            cmd.extend(["--source-constraint-job-id", constraint_job_id])
            if bool(params.get("lithology_grouping_enabled", False)):
                grouping_path = _write_lithology_group_config(
                    Path(constraint["lithology_map_csv"]),
                    constraint_job_id,
                    inputs_dir / "geochem_lithology_groups.confirmed.json",
                )
                cmd.extend(
                    [
                        "--lithology-group-config",
                        str(grouping_path),
                    ]
                )
        else:
            if stl_path is None:
                raise ValueError("普通克里金模式需要地质体 STL。")
            cmd = [
                sys.executable,
                "-u",
                str(_algorithm_script()),
                "--assay-csv",
                str(interpolation_assay_csv),
                "--collar-csv",
                str(collar_csv),
                "--stl",
                str(stl_path),
                "--output-dir",
                str(out_dir),
                "--elements",
                ",".join(elements),
                "--cell-size",
                str(params.get("cell_size", 20.0)),
                "--z-cell-size",
                str(params.get("z_cell_size", 20.0)),
                "--nearest",
                str(params.get("nearest", 12)),
                "--power",
                str(params.get("power", 2.0)),
                "--transform",
                str(params.get("transform", "log")),
                "--search-radius",
                str(params.get("search_radius", 1200.0)),
                "--max-voxels",
                str(params.get("max_voxels", 5000000)),
                "--chunk-size",
                str(params.get("chunk_size", 200000)),
                "--voxelizer",
                str(params.get("voxelizer", "ray")),
            ]
            zero_fill = str(params.get("zero_fill_elements", "") or "").strip()
            if zero_fill:
                cmd.extend(["--zero-fill-elements", zero_fill])

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        log_path = Path(job.log_path)
        with log_path.open("ab") as log:
            log.write(("CMD: " + " ".join(cmd) + "\n").encode("utf-8", errors="ignore"))
            code = -1
            for attempt in (1, 2):
                log.write(f"PROCESS_ATTEMPT: {attempt}\n".encode("utf-8"))
                proc = subprocess.Popen(
                    cmd,
                    stdout=log,
                    stderr=log,
                    env=env,
                    cwd=str(Path(__file__).resolve().parents[2]),
                )
                code = proc.wait()
                if code == 0 or (int(code) & 0xFFFFFFFF) != 0xC0000142 or attempt == 2:
                    break
                log.write(
                    "RUNTIME_RETRY: Windows DLL 初始化失败，0.5 秒后仅重试一次。\n".encode("utf-8")
                )
                log.flush()
                time.sleep(0.5)

        if code != 0:
            raise RuntimeError(process_failure_message(code))

        # The senior baseline script does not perform validation itself.
        # Run the same leave-one-drillhole-out protocol for both baseline and
        # enhanced profiles so enhancements can never be selected merely
        # because their picture looks smoother.
        if not (out_dir / "loho_validation_summary.csv").exists():
            from ..algorithms.geochem_geology_constrained import (
                leave_one_hole_out_validation,
            )

            used_samples = pd.read_csv(out_dir / "used_assay_points.csv", encoding="utf-8-sig")
            used_samples["lithology_code"] = 1
            thresholds = {
                "statistical": active_profile.get("threshold_T_auto"),
                "boundary": active_profile.get("boundary_grade_ppm"),
                "industrial": active_profile.get("industrial_grade_ppm"),
            }
            details, validation = leave_one_hole_out_validation(
                used_samples,
                elements[0],
                nearest=int(params.get("nearest", 12)),
                search_radius=float(params.get("search_radius", 1200.0)),
                thresholds=thresholds,
            )
            details.to_csv(
                out_dir / "loho_validation_details.csv",
                index=False,
                encoding="utf-8-sig",
            )
            validation.to_csv(
                out_dir / "loho_validation_summary.csv",
                index=False,
                encoding="utf-8-sig",
            )

        summary_path = out_dir / "model_summary.json"
        summary: Dict[str, Any] = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
        summary["database_export"] = export_info
        summary["interpolation_input"] = interpolation_filter
        summary["interpolation_input"]["file"] = interpolation_assay_csv.name
        if constraint_job_id:
            summary["constraint_source"] = {
                "job_id": constraint_job_id,
                "method": "B",
                "voxel_size": float(constraint["params"].get("voxel_size", 15.0)),
                "fault_thickness": float(params.get("fault_thickness", constraint["params"].get("fault_thickness", 30.0))),
                "lithology_count": int(constraint["params"].get("n_lithologies", 0)),
            }
        voxel_csv = _voxel_csv_path(out_dir)
        summary["element_stats"] = compute_element_stats(voxel_csv, elements)
        summary["threshold_profiles"] = threshold_profiles
        summary["provenance"] = {
            **(summary.get("provenance") or {}),
            "rule_set_id": params.get("rule_set_id"),
            "rule_set_status": (params.get("rule_set") or {}).get("status"),
            "source_variation_job_id": params.get("source_variation_job_id"),
            "threshold_profile_source": active_profile.get("profile_source"),
            "analysis_data_sha256": params.get("analysis_data_sha256"),
            "random_seed": params.get("random_seed"),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "interpolation_sample_policy": (
                "expert_3sigma_filtered_samples"
                if interpolation_filter.get("removed_sample_count")
                else "all_exported_assay_samples"
            ),
        }
        preview_element = str(params.get("preview_element") or (elements[0] if elements else "")).strip()
        preview_mode = "concentration"
        if preview_element:
            full_field_info = generate_element_ply(
                voxel_csv,
                out_dir / f"geochem_{preview_element}_full_field.ply",
                preview_element,
                int(params.get("max_points_ply", 600000)),
                display_mode="concentration",
                threshold_profile=threshold_profiles.get(preview_element),
            )
            summary["full_field_ply"] = full_field_info
            summary.pop("anomaly_body_ply", None)
            ply_info = full_field_info
            summary["preview_ply"] = ply_info
            summary["active_preview_element"] = preview_element
            summary["active_preview_mode"] = preview_mode
            if params.get("direct_background_comparison"):
                comparison_info = generate_element_ply(
                    voxel_csv,
                    out_dir / f"geochem_{preview_element}_statistical_anomaly.ply",
                    preview_element,
                    int(params.get("max_points_ply", 600000)),
                    display_mode="statistical_anomaly",
                    threshold_profile=threshold_profiles.get(preview_element),
                )
                summary["comparison_anomaly_ply"] = comparison_info
            raw_points_csv = out_dir / "used_assay_points.csv"
            if raw_points_csv.exists():
                raw_profile = {
                    **(threshold_profiles.get(preview_element) or {}),
                    "display_low_ppm": full_field_info.get("color_low_p05"),
                    "display_high_ppm": full_field_info.get("color_high_p98"),
                }
                raw_ply_info = generate_element_ply(
                    raw_points_csv,
                    out_dir / f"raw_assay_{preview_element}.ply",
                    preview_element,
                    0,
                    display_mode="concentration",
                    threshold_profile=raw_profile,
                )
                summary["raw_sample_ply"] = raw_ply_info
                summary.pop("raw_sample_anomaly_ply", None)
        validation_path = out_dir / "loho_validation_summary.csv"
        validation_rows: list[dict[str, Any]] = []
        validation_quality = "low"
        if validation_path.exists():
            validation_frame = pd.read_csv(validation_path)
            validation_rows = (
                validation_frame.astype(object)
                .where(pd.notna(validation_frame), None)
                .to_dict("records")
            )
            selected_validation = validation_frame.loc[
                validation_frame["transform"] == str(params.get("transform", "log"))
            ]
            if not selected_validation.empty:
                validation_quality = str(
                    selected_validation.iloc[0].get("validation_quality") or "low"
                )
        summary["validation"] = validation_rows
        summary["selected_validation_quality"] = validation_quality
        summary["candidate_gate"] = {
            "validation_passed": validation_quality in {"high", "medium"},
            "minimum_supporting_drillholes": 2,
            "minimum_display_level": "统计正异常",
            "candidate_body_allowed": validation_quality in {"high", "medium"},
            "failure_message": (
                None
                if validation_quality in {"high", "medium"}
                else "当前留一钻孔验证未通过门禁，只显示原始化验段和探索性插值，不形成可信异常体。"
            ),
        }
        manifest = build_scene_manifest(
            db,
            job,
            geobody_stl=stl_path,
            fault_stl=fault_stl_path,
            assay_csv=assay_csv,
            preview_element=preview_element,
            threshold_profile=threshold_profiles.get(preview_element) or {},
            summary=summary,
        )
        summary["scene_manifest"] = manifest
        (out_dir / "model_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        job = db.get(GeochemReconstructJob, job_id)
        if job:
            job.summary_json = json.dumps(summary, ensure_ascii=False)
            job.status = "success"
            job.validation_status = (
                "passed" if validation_quality in {"high", "medium"} else "limited"
            )
            job.finished_at = datetime.utcnow()
            job.error = None
            db.commit()
    except Exception as exc:
        if job is None:
            job = db.get(GeochemReconstructJob, job_id)
        if job:
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = datetime.utcnow()
            try:
                with Path(job.log_path).open("a", encoding="utf-8") as log:
                    log.write(f"ERROR: {exc}\n")
            except Exception:
                pass
            db.commit()
    finally:
        db.close()


def start_geochem_job(
    db: Session,
    model_id: int,
    params: Dict[str, Any],
    stl_upload_name: str = "",
    stl_content: bytes = b"",
    fault_stl_upload_name: str = "",
    fault_stl_content: bytes = b"",
) -> str:
    elements = [str(x).strip() for x in params.get("elements", []) if str(x).strip()]
    if not elements:
        elements = [x for x in DEFAULT_ELEMENTS if x in available_elements(db, model_id)] or available_elements(db, model_id)[:10]
    if not elements:
        raise ValueError("该模型下没有可用化学元素数据。")
    if len(elements) != 1:
        raise ValueError("每个任务只能选择一种元素；不同元素必须独立重建，不能累加。")
    constraint_job_id = str(params.get("constraint_job_id", "") or "").strip()
    if constraint_job_id:
        constraint = _constraint_paths(db, model_id, constraint_job_id)
    elif not stl_content:
        raise ValueError("请选择已有地质模型约束，或为普通克里金上传地质体 STL。")
    rule_set_id = str(params.get("rule_set_id") or "").strip()
    rule_set = db.get(GeochemRuleSet, rule_set_id) if rule_set_id else ensure_default_rule_set(db, model_id)
    if rule_set is None or rule_set.model_id != model_id:
        raise ValueError("所选规则集不存在或不属于当前模型")
    source_variation_job_id = str(params.get("source_variation_job_id") or "").strip()
    source_correlation_job_id = str(params.get("source_correlation_job_id") or "").strip()
    variation_output_dir = ""
    database_path = Path(__file__).resolve().parents[2] / "geology_norm.db"
    analysis_data_sha256 = dataset_sha256(
        load_geochem_dataset(
            database_path,
            model_id=model_id,
        )
    )
    direct_background_comparison = bool(params.get("direct_background_comparison"))
    if not source_variation_job_id and not direct_background_comparison:
        raise ValueError("化学元素三维核查必须显式绑定一个已成功完成的变化规律任务")
    if source_variation_job_id:
        source = db.get(GeochemMiningJob, source_variation_job_id)
        if (
            source is None
            or source.model_id != model_id
            or source.status != "success"
            or source.algorithm_mode not in {"variation", "both"}
        ):
            raise ValueError("所选变化规律任务不存在、未成功或不属于当前模型")
        if source.rule_set_id != rule_set.id:
            raise ValueError("三维重建与变化规律任务必须使用同一规则集")
        try:
            source_params = json.loads(source.params_json or "{}")
        except json.JSONDecodeError:
            source_params = {}
        source_analysis_data_sha256 = source_params.get("analysis_data_sha256")
        if not source_analysis_data_sha256:
            try:
                source_summary = json.loads(source.summary_json or "{}")
            except json.JSONDecodeError:
                source_summary = {}
            source_analysis_data_sha256 = (
                source_summary.get("provenance") or {}
            ).get("analysis_data_sha256")
        if not source_analysis_data_sha256:
            raise ValueError("所选变化规律任务缺少分析数据快照哈希，请先用当前版本重新运行变化规律任务")
        if source_analysis_data_sha256 != analysis_data_sha256:
            raise ValueError("化验或地质关联数据已在变化规律任务完成后发生变化，请重新运行变化规律任务后再做三维核查")
        variation_output_dir = str(Path(source.out_dir) / "element_variation")
        statistics_path = Path(variation_output_dir) / "element_background_statistics.csv"
        if not statistics_path.exists():
            raise ValueError("所选变化规律任务缺少元素背景统计产物")
        source_elements = set(
            pd.read_csv(
                statistics_path,
                encoding="utf-8-sig",
                usecols=["element"],
            )["element"].astype(str)
        )
        missing_elements = [element for element in elements if element not in source_elements]
        if missing_elements:
            raise ValueError(
                "所选变化规律任务未包含当前三维元素：" + ", ".join(missing_elements)
            )
    if source_correlation_job_id:
        correlation_source = db.get(GeochemMiningJob, source_correlation_job_id)
        if (
            correlation_source is None
            or correlation_source.model_id != model_id
            or correlation_source.status != "success"
            or correlation_source.algorithm_mode not in {"correlation", "both"}
        ):
            raise ValueError("所选相关性任务不存在、未成功或不属于当前模型")
        if correlation_source.rule_set_id != rule_set.id:
            raise ValueError("三维重建与相关性任务必须使用同一规则集")
        try:
            correlation_params = json.loads(correlation_source.params_json or "{}")
        except json.JSONDecodeError:
            correlation_params = {}
        linked_variation_id = str(
            correlation_params.get("source_variation_job_id") or ""
        ).strip()
        if linked_variation_id and linked_variation_id != source_variation_job_id:
            raise ValueError("三维重建绑定的变化规律任务与相关性任务来源不一致")
        correlation_snapshot = correlation_params.get("analysis_data_sha256")
        if correlation_snapshot and correlation_snapshot != analysis_data_sha256:
            raise ValueError("化验或地质关联数据已在相关性任务完成后发生变化，请重新运行相关性分析")
        candidate_elements = {
            str(item).strip()
            for item in params.get("candidate_elements", [])
            if str(item).strip()
        }
        if candidate_elements and elements[0] not in candidate_elements:
            raise ValueError("当前三维元素不属于所选相关性候选组合")
    runtime_rules = runtime_rule_config(rule_set)
    if direct_background_comparison:
        runtime_rules["background_method"] = str(
            params.get("background_method_override") or "log_mad"
        )
        runtime_rules["status"] = "comparison"
    params = {
        **params,
        "elements": elements,
        "preview_element": elements[0],
        "preview_mode": "concentration",
        "transform": str(params.get("transform") or "log"),
        "rule_set_id": rule_set.id,
        "rule_set": runtime_rules,
        "source_variation_job_id": source_variation_job_id or None,
        "source_correlation_job_id": source_correlation_job_id or None,
        "variation_output_dir": variation_output_dir,
        "analysis_data_sha256": analysis_data_sha256,
        "random_seed": 202501,
        "algorithm_version": str(
            params.get("algorithm_version") or "geochem-anisotropic-idw-v1-trend-residual-full-field"
        ),
        "algorithm_profile": str(
            params.get("algorithm_profile")
            or "algorithm_b_domain_grouped_anisotropic_idw_full_field"
        ),
        "support_probability_cutoff": float(
            params.get("support_probability_cutoff")
            or rule_set.support_probability_cutoff
        ),
    }
    job = create_job(db, model_id, params)
    stl_path: Optional[Path] = None
    if stl_content:
        stl_path = _storage_root() / job.id / "inputs" / _safe_filename(stl_upload_name)
        stl_path.write_bytes(stl_content)
    fault_stl_path: Optional[Path] = None
    if fault_stl_content:
        fault_stl_path = (
            _storage_root()
            / job.id
            / "inputs"
            / _safe_filename(fault_stl_upload_name or "fault.stl")
        )
        fault_stl_path.write_bytes(fault_stl_content)
    thread = threading.Thread(
        target=_run_job,
        args=(job.id, stl_path, fault_stl_path),
        daemon=True,
    )
    thread.start()
    return job.id
