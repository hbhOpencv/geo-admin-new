# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import trimesh
from shapely.geometry import LineString


def _load_mesh_any(path: str | Path) -> trimesh.Trimesh:
    mesh = trimesh.load(str(path), force="mesh")
    if isinstance(mesh, trimesh.Scene):
        geoms = [g for g in mesh.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not geoms:
            raise ValueError(f"加载失败：{path} 中没有可用 mesh")
        mesh = trimesh.util.concatenate(geoms)

    try:
        mesh.remove_unreferenced_vertices()
    except Exception:
        pass
    try:
        mesh.process(validate=True)
    except Exception:
        try:
            mesh.process()
        except Exception:
            pass
    return mesh


def _polyline_len_xy(poly: np.ndarray) -> float:
    if poly is None or len(poly) < 2:
        return 0.0
    d = np.diff(poly[:, :2], axis=0)
    return float(np.sum(np.sqrt(np.sum(d * d, axis=1))))


def extract_fault_traces_to_csv(
    fault_stl_path: str | Path,
    out_csv: str | Path,
    n_levels: int = 25,
    z_low_q: float = 0.6,
    z_high_q: float = 0.98,
    simplify_tol: float = 2.0,
    min_len: float = 50.0,
) -> int:
    """
    Extract multiple disconnected fault traces (XY polylines) from fault STL and save CSV.
    CSV columns: fault_id, order, x, y, z
    Returns row count.
    """
    fault_mesh = _load_mesh_any(fault_stl_path)
    parts = fault_mesh.split(only_watertight=False)

    rows: List[dict] = []
    fault_id = 0

    for part in parts:
        if part is None or len(part.vertices) < 10 or len(part.faces) < 1:
            continue

        z = part.vertices[:, 2].astype(float)
        if not np.isfinite(z).any():
            continue

        z0 = float(np.quantile(z, z_low_q))
        z1 = float(np.quantile(z, z_high_q))
        if not np.isfinite(z0) or not np.isfinite(z1) or z1 <= z0:
            continue

        levels = np.linspace(z0, z1, max(int(n_levels), 5))
        best_poly = None
        best_len = 0.0
        best_z = None

        for zi in levels:
            sec = part.section(
                plane_origin=[0.0, 0.0, float(zi)],
                plane_normal=[0.0, 0.0, 1.0],
            )
            if sec is None or not sec.discrete:
                continue

            for poly in sec.discrete:
                if poly is None or len(poly) < 2:
                    continue
                L = _polyline_len_xy(poly)
                if L > best_len:
                    best_len = L
                    best_poly = poly
                    best_z = float(zi)

        if best_poly is None or best_len < float(min_len):
            continue

        ls = LineString(best_poly[:, :2])
        if simplify_tol and simplify_tol > 0:
            ls = ls.simplify(float(simplify_tol), preserve_topology=True)

        coords = np.array(ls.coords, dtype=float)
        if len(coords) < 2:
            continue

        fault_id += 1
        for order, (x, y) in enumerate(coords):
            rows.append(
                {
                    "fault_id": fault_id,
                    "order": int(order),
                    "x": float(x),
                    "y": float(y),
                    "z": float(best_z) if best_z is not None else np.nan,
                }
            )

    if not rows:
        raise ValueError("未从断层STL提取到有效断层线，请检查模型或调低提取阈值。")

    out_path = Path(out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["fault_id", "order", "x", "y", "z"]).to_csv(
        out_path, index=False, encoding="utf-8-sig"
    )
    return len(rows)
