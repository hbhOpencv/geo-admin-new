# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from collections import Counter

import pandas as pd
import subprocess

from sqlalchemy.orm import Session

from ..models import GeologicalModel, Borehole, BoreholeSection, ReconstructJob, get_session
from ..utils import parse_origin_xyz
from .fault_trace_extractor import extract_fault_traces_to_csv


# -------------------------
# Lithology parsing helpers
# -------------------------

# Match patterns like: "24.8-78.5: 碎石土" (supports Chinese colon and en dash)
_SEGMENT_RE = re.compile(
    r"(?P<d0>\d+(?:\.\d+)?)\s*[\-–—]\s*(?P<d1>\d+(?:\.\d+)?)\s*[:：]\s*(?P<name>.*?)"
    r"(?=(\d+(?:\.\d+)?)\s*[\-–—]\s*(\d+(?:\.\d+)?)\s*[:：]|$)",
    re.S,
)


def _is_garbled_text(s: str) -> bool:
    """Heuristic: treat obvious encoding-garbled strings as "one lithology"."""
    s = (s or "").strip()
    if not s:
        return True
    # Unicode replacement char
    if "\ufffd" in s or "�" in s:
        return True
    # lots of question marks
    q = s.count("?")
    if q >= 2 and q / max(len(s), 1) > 0.2:
        return True
    return False


def _normalize_lith_name(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"\s+", " ", name)
    name = name.strip(" ,;；，。\t\r\n")
    if len(name) >= 2:
        quote_pairs = [('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’")]
        changed = True
        while changed and len(name) >= 2:
            changed = False
            for left, right in quote_pairs:
                if name.startswith(left) and name.endswith(right):
                    name = name[1:-1].strip()
                    changed = True
            if changed:
                name = name.strip(" ,;；，。\t\r\n")
    if not name:
        return "未知岩性"
    if _is_garbled_text(name):
        return "乱码岩性"
    return name


def _parse_desc_segments(desc: str) -> List[Tuple[float, float, str]]:
    """Parse a Description that contains multiple segments like '0-6.8: xxx 6.8-9: yyy ...'."""
    text = (desc or "")
    if len(text) < 6:
        return []
    if (":" not in text) and ("：" not in text):
        return []
    if ("-" not in text) and ("–" not in text) and ("—" not in text):
        return []

    segs: List[Tuple[float, float, str]] = []
    for m in _SEGMENT_RE.finditer(text):
        try:
            d0 = float(m.group("d0"))
            d1 = float(m.group("d1"))
        except Exception:
            continue
        if d1 <= d0:
            continue
        name = _normalize_lith_name(m.group("name"))
        segs.append((d0, d1, name))

    if not segs:
        return []

    # sort and merge adjacent segments with the same name
    segs.sort(key=lambda x: (x[0], x[1]))
    merged: List[Tuple[float, float, str]] = []
    for d0, d1, name in segs:
        if not merged:
            merged.append((d0, d1, name))
            continue
        pd0, pd1, pname = merged[-1]
        if abs(d0 - pd1) < 1e-6 and name == pname:
            merged[-1] = (pd0, d1, pname)
        else:
            merged.append((d0, d1, name))
    return merged


def _estimate_xy(bh: Borehole, secs: List[BoreholeSection]) -> Tuple[Optional[float], Optional[float]]:
    x = bh.Easting if bh.Easting is not None else bh.x
    y = bh.Northing if bh.Northing is not None else bh.y
    if x is None or y is None:
        for sec in secs:
            xt, yt, _ = parse_origin_xyz(getattr(sec, "顶坐标", None))
            xb, yb, _ = parse_origin_xyz(getattr(sec, "底坐标", None))
            if x is None and xt is not None:
                x = xt
            if y is None and yt is not None:
                y = yt
            if x is None and xb is not None:
                x = xb
            if y is None and yb is not None:
                y = yb
            if x is not None and y is not None:
                break
    return (float(x) if x is not None else None, float(y) if y is not None else None)


def _estimate_collar_z(bh: Borehole, secs: List[BoreholeSection]) -> Optional[float]:
    """Estimate collar elevation (Z at depth=0). Prefer borehole Elevation; else infer from section coordinates."""
    if bh.Elevation is not None:
        try:
            return float(bh.Elevation)
        except Exception:
            pass

    cands: List[float] = []
    for sec in secs:
        # use top coord + Depth, or bottom coord + Bottom
        xt, yt, zt = parse_origin_xyz(getattr(sec, "顶坐标", None))
        xb, yb, zb = parse_origin_xyz(getattr(sec, "底坐标", None))
        d0 = getattr(sec, "Depth", None)
        d1 = getattr(sec, "Bottom", None)
        if zt is not None and d0 is not None:
            try:
                cands.append(float(zt) + float(d0))
            except Exception:
                pass
        if zb is not None and d1 is not None:
            try:
                cands.append(float(zb) + float(d1))
            except Exception:
                pass
    if not cands:
        return None
    # robust median
    try:
        s = pd.Series(cands)
        return float(s.median())
    except Exception:
        return float(sorted(cands)[len(cands) // 2])


def _expand_sections(secs: List[BoreholeSection]) -> List[Tuple[float, float, str]]:
    """Expand BoreholeSection rows into (d0, d1, lith_name) segments.

    - If Description contains a list of segments, parse and expand.
    - Else use Depth/Bottom as a single segment.
    - Lithology name is normalized; garbled strings collapse to '乱码岩性'.
    """
    out: List[Tuple[float, float, str]] = []
    for sec in secs:
        desc = getattr(sec, "Description", None)
        segs = _parse_desc_segments(str(desc or ""))
        if segs:
            out.extend(segs)
            continue

        d0 = float(sec.Depth) if sec.Depth is not None else None
        d1 = float(sec.Bottom) if sec.Bottom is not None else d0
        if d0 is None or d1 is None:
            continue
        if d1 < d0:
            d0, d1 = d1, d0
        name = _normalize_lith_name(str(desc or ""))
        out.append((float(d0), float(d1), name))

    # sort & merge adjacent with same name
    out.sort(key=lambda x: (x[0], x[1]))
    merged: List[Tuple[float, float, str]] = []
    for d0, d1, name in out:
        if not merged:
            merged.append((d0, d1, name))
            continue
        pd0, pd1, pname = merged[-1]
        if abs(d0 - pd1) < 1e-6 and name == pname:
            merged[-1] = (pd0, d1, pname)
        else:
            merged.append((d0, d1, name))
    return merged


def _storage_root() -> Path:
    # backend/
    backend_dir = Path(__file__).resolve().parents[2]
    root = backend_dir / "storage" / "reconstruct"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _alg_script() -> Path:
    # backend/app/algorithms/reconstruct_cdt_flatbottom.py
    return Path(__file__).resolve().parents[1] / "algorithms" / "reconstruct_cdt_flatbottom.py"


def _alg_script_b() -> Path:
    # backend/app/algorithms/reconstruct_voxel_b.py
    return Path(__file__).resolve().parents[1] / "algorithms" / "reconstruct_voxel_b.py"


def _safe_filename(name: str) -> str:
    name = os.path.basename(name)
    # keep simple ascii for windows paths; fall back to generic
    if not name:
        return "file"
    return name


def export_model_sections_to_excel(db: Session, model_id: int, excel_path: Path) -> int:
    """Export model's borehole sections to an Excel file required by the algorithm.

    Required columns:
      钻孔编号, 起始孔深, 终止孔深, X坐标, Y坐标, Z坐标
    Optional:
      分层描述
    """
    model = db.get(GeologicalModel, model_id)
    if not model:
        raise ValueError(f"Model {model_id} not found")

    rows = (
        db.query(BoreholeSection, Borehole)
        .join(Borehole, BoreholeSection.borehole_id == Borehole.id)
        .filter(Borehole.model_id == model_id)
        .order_by(Borehole.Borehole.asc(), BoreholeSection.Depth.asc().nullsfirst())
        .all()
    )
    if not rows:
        raise ValueError("该模型下没有钻孔分层数据（borehole_sections 为空），无法重建。")

    # group by borehole so we can robustly infer collar_z and also expand Description segment lists
    by_bh: Dict[int, Tuple[Borehole, List[BoreholeSection]]] = {}
    for sec, bh in rows:
        by_bh.setdefault(bh.id, (bh, []))[1].append(sec)

    out: List[Dict[str, Any]] = []
    for _, (bh, secs) in by_bh.items():
        borehole_code = (bh.Borehole or "").strip()
        if not borehole_code:
            continue

        x, y = _estimate_xy(bh, secs)
        collar_z = _estimate_collar_z(bh, secs)
        if x is None or y is None or collar_z is None:
            # cannot export this borehole
            continue

        segs = _expand_sections(secs)
        for d0, d1, name in segs:
            # algorithm expects Z at segment top such that collar_z ~= z + depth
            z_top = float(collar_z) - float(d0)
            out.append(
                {
                    "钻孔编号": borehole_code,
                    "起始孔深": float(d0),
                    "终止孔深": float(d1),
                    "X坐标": float(x),
                    "Y坐标": float(y),
                    "Z坐标": float(z_top),
                    "分层描述": name,
                }
            )

    if not out:
        raise ValueError("导出 Excel 失败：无法从分层数据解析出有效的 X/Y/Z 坐标。请检查 顶坐标 或 boreholes 的坐标字段。")

    df = pd.DataFrame(out)
    excel_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(excel_path, index=False)
    return len(df)



def export_model_sections_to_layer_csv(db: Session, model_id: int, csv_path: Path) -> Tuple[int, int]:
    """Export model's borehole sections to a CSV required by Algorithm B.

    Columns (Chinese to match your paper scripts):
      钻孔ID, X坐标(m), Y坐标(m),
      岩性段顶深Z(m), 岩性段底深Z(m), 岩性段中点Z(m),
      岩性编码
    """
    model = db.get(GeologicalModel, model_id)
    if not model:
        raise ValueError(f"Model {model_id} not found")

    rows = (
        db.query(BoreholeSection, Borehole)
        .join(Borehole, BoreholeSection.borehole_id == Borehole.id)
        .filter(Borehole.model_id == model_id)
        .order_by(Borehole.Borehole.asc(), BoreholeSection.Depth.asc().nullsfirst())
        .all()
    )
    if not rows:
        raise ValueError("该模型下没有钻孔分层数据（borehole_sections 为空），无法运行算法B。")

    # group by borehole so we can infer collar_z and expand segment lists
    by_bh: Dict[int, Tuple[Borehole, List[BoreholeSection]]] = {}
    for sec, bh in rows:
        by_bh.setdefault(bh.id, (bh, []))[1].append(sec)

    # Collect all segments with lithology name (normalized). We'll map to dynamic integer codes per model.
    seg_records: List[Tuple[str, float, float, float, float, str]] = []
    # tuple: (borehole_id, x, y, d0, d1, lith_name)
    thickness_counter: Counter = Counter()

    for _, (bh, secs) in by_bh.items():
        borehole_id = (bh.Borehole or "").strip()
        if not borehole_id:
            continue

        x, y = _estimate_xy(bh, secs)
        collar_z = _estimate_collar_z(bh, secs)
        if x is None or y is None or collar_z is None:
            continue

        segs = _expand_sections(secs)
        for d0, d1, name in segs:
            if d1 <= d0:
                continue
            seg_records.append((borehole_id, float(x), float(y), float(d0), float(d1), name))
            thickness_counter[name] += float(d1 - d0)

    if not seg_records:
        raise ValueError(
            "无法从数据库解析出算法B所需的分层数据：请确认 borehole_sections 至少包含 Depth/Bottom，且 boreholes 或 顶/底坐标 能解析 X/Y/Z。"
        )

    # Dynamic lithology mapping per model:
    # - garbled strings already normalized to '乱码岩性'
    # - stable order by thickness(desc) then name
    items = list(thickness_counter.items())
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    name_to_code = {name: i for i, (name, _) in enumerate(items)}
    n_lith = len(name_to_code)
    fault_code = int(n_lith)  # reserve one extra code for fault class

    # Build per-borehole collar_z cache for Z computation
    out: List[Dict[str, Any]] = []
    collar_cache: Dict[str, float] = {}
    for _, (bh, secs) in by_bh.items():
        bid = (bh.Borehole or "").strip()
        if not bid:
            continue
        cz = _estimate_collar_z(bh, secs)
        if cz is not None:
            collar_cache[bid] = float(cz)

    for borehole_id, x, y, d0, d1, name in seg_records:
        cz = collar_cache.get(borehole_id)
        if cz is None:
            continue
        zt = float(cz) - float(d0)
        zb = float(cz) - float(d1)
        zmid = 0.5 * (zt + zb)
        out.append(
            {
                "钻孔ID": borehole_id,
                "X坐标(m)": float(x),
                "Y坐标(m)": float(y),
                "岩性段顶深Z(m)": float(zt),
                "岩性段底深Z(m)": float(zb),
                "岩性段中点Z(m)": float(zmid),
                "岩性编码": int(name_to_code.get(name, 0)),
            }
        )

    df = pd.DataFrame(out)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # Also write a mapping file for debugging / report
    map_rows = [{"岩性编码": i, "岩性名称": name, "厚度权重": float(w)} for i, (name, w) in enumerate(items)]
    map_df = pd.DataFrame(map_rows)
    map_df.to_csv(csv_path.parent / "lithology_map.csv", index=False, encoding="utf-8-sig")
    (csv_path.parent / "lithology_map.json").write_text(
        json.dumps({"fault_code": fault_code, "mapping": {k: int(v) for k, v in name_to_code.items()}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return len(df), fault_code


def _parse_int_prefix(s: str) -> Optional[int]:
    s = (s or "").strip()
    if not s:
        return None
    num = ""
    for ch in s:
        if ch.isdigit() or (ch == "-" and not num):
            num += ch
        else:
            break
    try:
        return int(num) if num not in ("", "-") else None
    except Exception:
        return None


def create_job(
    db: Session,
    model_id: int,
    params: Dict[str, Any] | None = None,
) -> ReconstructJob:
    job_id = uuid.uuid4().hex
    job_dir = _storage_root() / job_id
    inputs_dir = job_dir / "inputs"
    outputs_dir = job_dir / "outputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    log_path = job_dir / "run.log"

    job = ReconstructJob(
        id=job_id,
        model_id=model_id,
        status="queued",
        params_json=json.dumps(params or {}, ensure_ascii=False),
        out_dir=str(outputs_dir),
        log_path=str(log_path),
        created_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _run_job_in_thread(job_id: str, excel_path: Path, fault_csv: Path, model_stl: Path, params: Dict[str, Any]):
    # New DB session in worker thread
    db = get_session()
    try:
        job: ReconstructJob | None = db.get(ReconstructJob, job_id)
        if not job:
            return

        job.status = "running"
        job.started_at = datetime.utcnow()
        db.commit()

        out_dir = Path(job.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Build command
        cmd = [
            sys.executable,
            "-u",
            str(_alg_script()),
            "--excel",
            str(excel_path),
            "--fault_csv",
            str(fault_csv),
            "--model_stl",
            str(model_stl),
            "--outdir",
            str(out_dir),
        ]

        # pass params
        def add_arg(flag: str, value: Any):
            if value is None or value == "":
                return
            cmd.extend([flag, str(value)])

        add_arg("--snap_tol", params.get("snap_tol", 0.1))
        add_arg("--model_face_step", params.get("model_face_step", 5))
        add_arg("--domain_simplify", params.get("domain_simplify", 70.0))
        add_arg("--domain_buffer", params.get("domain_buffer", 100.0))
        add_arg("--fault_step", params.get("fault_step", 2))
        add_arg("--fault_simplify", params.get("fault_simplify", 2.0))
        add_arg("--cdt_q", params.get("cdt_q", 5.0))
        add_arg("--triangle_extra", params.get("triangle_extra", ""))
        add_arg("--tri_max_show", params.get("tri_max_show", 1500))
        add_arg("--clip", params.get("clip", "sample"))
        add_arg("--clip_threshold", params.get("clip_threshold", 0.7))
        add_arg("--z_min", params.get("z_min", None))

        # Run (binary log + force UTF-8 for Windows)
        log_path = Path(job.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        with open(log_path, "ab") as f:
            f.write(("CMD: " + " ".join(cmd) + "\n").encode("utf-8", errors="ignore"))
            p = subprocess.Popen(
                cmd,
                stdout=f,
                stderr=f,
                env=env,
                cwd=str(Path(__file__).resolve().parents[2]),  # backend/
            )
            ret = p.wait()

        job.finished_at = datetime.utcnow()
        if ret == 0:
            job.status = "success"
            job.error = None
        else:
            job.status = "failed"
            job.error = f"process exited with code {ret}"
        db.commit()
    except Exception as e:
        try:
            job = db.get(ReconstructJob, job_id)
            if job:
                job.status = "failed"
                job.finished_at = datetime.utcnow()
                job.error = str(e)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def start_reconstruct_job(
    db: Session,
    model_id: int,
    fault_stl_upload_name: str,
    fault_stl_content: bytes,
    model_upload_name: str,
    model_content: bytes,
    params: Dict[str, Any] | None = None,
) -> str:
    """Create a job, export excel from DB, save uploads, and run algorithm in background thread."""
    params = params or {}
    job = create_job(db, model_id, params=params)
    job_dir = _storage_root() / job.id
    inputs_dir = job_dir / "inputs"
    outputs_dir = Path(job.out_dir)

    # Save uploads
    fault_stl_path = inputs_dir / _safe_filename(fault_stl_upload_name or "fault.stl")
    fault_csv_path = inputs_dir / "fault_traces.csv"
    model_path = inputs_dir / _safe_filename(model_upload_name or "model.stl")
    fault_stl_path.write_bytes(fault_stl_content)
    model_path.write_bytes(model_content)

    # Extract fault traces CSV from uploaded fault STL
    extract_fault_traces_to_csv(
        fault_stl_path=fault_stl_path,
        out_csv=fault_csv_path,
        simplify_tol=float(params.get("fault_simplify", 2.0)),
    )

    # Export excel from DB
    excel_path = inputs_dir / "boreholes.xlsx"
    export_model_sections_to_excel(db, model_id, excel_path)

    # kick off thread
    t = threading.Thread(
        target=_run_job_in_thread,
        args=(job.id, excel_path, fault_csv_path, model_path, params),
        daemon=True,
    )
    t.start()
    return job.id



def _run_job_b_in_thread(job_id: str, borehole_csv: Path, model_stl: Path, fault_stl: Optional[Path], params: Dict[str, Any]):
    """
    Worker thread for Algorithm B.
    """
    db = get_session()
    try:
        job: ReconstructJob | None = db.get(ReconstructJob, job_id)
        if not job:
            return

        job.status = "running"
        job.started_at = datetime.utcnow()
        db.commit()

        out_dir = Path(job.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        log_path = Path(job.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Build command
        cmd = [
            sys.executable,
            "-u",
            str(_alg_script_b()),
            "--borehole_csv",
            str(borehole_csv),
            "--model_stl",
            str(model_stl),
            "--outdir",
            str(out_dir),
        ]

        # optional fault
        use_condsim = int(params.get("use_condsim", 0))
        if use_condsim == 1 and fault_stl and fault_stl.exists():
            cmd.extend(["--fault_stl", str(fault_stl)])
        else:
            # ensure script sees empty
            cmd.extend(["--fault_stl", ""])

        # pass params
        def add_arg(flag: str, value: Any):
            if value is None or value == "":
                return
            cmd.extend([flag, str(value)])

        add_arg("--voxel_size", params.get("voxel_size"))
        add_arg("--n_virtual_per_tri", params.get("n_virtual_per_tri"))
        add_arg("--min_dist_to_real", params.get("min_dist_to_real"))
        add_arg("--min_dist_to_virtual", params.get("min_dist_to_virtual"))
        add_arg("--idw_power", params.get("idw_power", 2.0))

        add_arg("--n_neighbors", params.get("n_neighbors"))
        add_arg("--search_radius", params.get("search_radius"))

        add_arg("--range_a", params.get("range_a"))
        add_arg("--nugget_c0", params.get("nugget_c0"))
        add_arg("--sill_c", params.get("sill_c"))

        add_arg("--use_condsim", use_condsim)
        add_arg("--n_sim", params.get("n_sim"))
        add_arg("--vote_threshold", params.get("vote_threshold", 0.51))
        add_arg("--fault_thickness", params.get("fault_thickness"))
        add_arg("--max_points_ply", params.get("max_points_ply", 0))

        # dynamic fault class code (defaults to max(code)+1 if omitted in script)
        add_arg("--fault_code", params.get("fault_code", None))

        add_arg("--seed", params.get("seed", 202501))

        # Force UTF-8 in subprocess on Windows
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        with open(log_path, "ab") as f:
            f.write(("CMD: " + " ".join(cmd) + "\n").encode("utf-8", errors="ignore"))
            p = subprocess.Popen(cmd, stdout=f, stderr=f, env=env)
            rc = p.wait()

        job.finished_at = datetime.utcnow()
        if rc == 0:
            job.status = "success"
            job.error = None
        else:
            job.status = "failed"
            job.error = f"process exited with code {rc}"

        db.commit()

    except Exception as e:
        try:
            job = db.get(ReconstructJob, job_id)
            if job:
                job.status = "failed"
                job.error = str(e)
                job.finished_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def start_reconstruct_b_job(
    db: Session,
    model_id: int,
    model_upload_name: str,
    model_content: bytes,
    fault_upload_name: Optional[str] = None,
    fault_content: Optional[bytes] = None,
    params: Dict[str, Any] | None = None,
) -> str:
    """Create a job, export borehole CSV from DB, save uploads, and run Algorithm B in background thread."""
    params = params or {}
    params = {**params, "method": "B"}  # mark in params_json

    job = create_job(db, model_id, params=params)
    job_dir = _storage_root() / job.id
    inputs_dir = job_dir / "inputs"
    outputs_dir = job_dir / "outputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    # save uploads
    model_path = inputs_dir / _safe_filename(model_upload_name or "model.stl")
    model_path.write_bytes(model_content)

    fault_path: Optional[Path] = None
    if fault_upload_name and fault_content:
        fault_path = inputs_dir / _safe_filename(fault_upload_name or "fault.stl")
        fault_path.write_bytes(fault_content)

    # Export borehole layers csv from DB
    borehole_csv = inputs_dir / "borehole_layers.csv"
    n_rows, fault_code = export_model_sections_to_layer_csv(db, model_id, borehole_csv)

    # For dynamic lithology classes, reserve the next integer as fault_code.
    # Persist into params_json so前端/日志可见。
    params["fault_code"] = int(fault_code)
    params["n_lithologies"] = int(fault_code)
    params["n_layer_rows"] = int(n_rows)
    try:
        job.params_json = json.dumps(params, ensure_ascii=False)
        db.commit()
    except Exception:
        pass

    # kick off thread
    t = threading.Thread(
        target=_run_job_b_in_thread,
        args=(job.id, borehole_csv, model_path, fault_path, params),
        daemon=True,
    )
    t.start()
    return job.id


def list_outputs(job: ReconstructJob) -> List[str]:
    out_dir = Path(job.out_dir)
    if not out_dir.exists():
        return []
    files: List[str] = []
    for p in out_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".ply", ".stl", ".png", ".csv", ".json"}:
            # return relative to out_dir
            files.append(str(p.relative_to(out_dir)))
    files.sort()
    return files


def read_log_tail(job: ReconstructJob, max_bytes: int = 4000) -> str:
    p = Path(job.log_path)
    if not p.exists():
        return ""
    try:
        data = p.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""
