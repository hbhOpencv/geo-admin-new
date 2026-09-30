# 加入约束_CDT_model_domain_boundary_平底.py
# -*- coding: utf-8 -*-
"""
CDT（Constrained Delaunay Triangulation） + model.stl(2D domain) + boundary(3D修正)
并实现：每个孔只把最低层补到统一最小Z，使地质体底面平。

依赖：
pip install numpy pandas shapely trimesh matplotlib openpyxl triangle
(可选) pip install scipy
"""

import os
import argparse
import json
import hashlib
from dataclasses import dataclass
from collections import Counter
from typing import List, Tuple

import numpy as np
import pandas as pd
import trimesh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

from shapely.geometry import LineString, Polygon, Point, MultiPoint
from shapely.ops import unary_union
from shapely.prepared import prep

import triangle as tr


def _is_garbled_text(s: str) -> bool:
    """Heuristic: collapse obvious encoding-garbled lithology strings."""
    s = (s or "").strip()
    if not s:
        return True
    if "\ufffd" in s or "�" in s:
        return True
    q = s.count("?")
    if q >= 2 and q / max(len(s), 1) > 0.2:
        return True
    return False


def _normalize_lith_name(name: str) -> str:
    name = (name or "").strip()
    name = " ".join(name.split())
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

# optional scipy
try:
    from scipy.spatial import Delaunay as SciDelaunay
    from scipy.spatial import cKDTree
    _HAVE_SCIPY = True
except Exception:
    _HAVE_SCIPY = False
    SciDelaunay = None
    cKDTree = None
    from matplotlib.tri import Triangulation


# =========================
# Data structures
# =========================
@dataclass
class Borehole:
    code: str
    x: float
    y: float
    collar_z: float
    max_depth: float
    segs: list  # list[(d0, d1, desc)]


# =========================
# Helpers
# =========================
def _hash_color(label: str) -> np.ndarray:
    if label is None:
        label = ""
    h = hashlib.md5(str(label).encode("utf-8")).digest()
    r = int(h[0]) // 2 + 80
    g = int(h[1]) // 2 + 80
    b = int(h[2]) // 2 + 80
    return np.array([r, g, b, 255], dtype=np.uint8)




def _rgba_to_hex(rgba: np.ndarray) -> str:
    r, g, b, a = [int(x) for x in list(rgba)]
    return f"#{r:02X}{g:02X}{b:02X}{a:02X}"


def save_lithology_color_map(labels: List[str], outdir: str):
    """Save deterministic lithology -> color mapping used by Algorithm A."""
    uniq = []
    seen = set()
    for label in labels:
        label = _normalize_lith_name(label)
        if label in seen:
            continue
        seen.add(label)
        uniq.append(label)
    uniq.sort()

    rows = []
    for label in uniq:
        rgba = _hash_color(label)
        rows.append({
            "岩性名称": label,
            "R": int(rgba[0]),
            "G": int(rgba[1]),
            "B": int(rgba[2]),
            "A": int(rgba[3]),
            "HEX": _rgba_to_hex(rgba),
        })

    csv_path = os.path.join(outdir, "lithology_color_map.csv")
    json_path = os.path.join(outdir, "lithology_color_map.json")
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    return csv_path, json_path

def _find_desc_at_depth(segs, d_mid: float):
    for d0, d1, desc in segs:
        if d_mid >= d0 and d_mid < d1:
            return desc
    return None


def _prism_mesh_from_vertices(v6: np.ndarray, face_rgba: np.ndarray) -> trimesh.Trimesh:
    faces = np.array([
        [0, 1, 2],  # top
        [5, 4, 3],  # bottom
        [0, 1, 4], [0, 4, 3],
        [1, 2, 5], [1, 5, 4],
        [2, 0, 3], [2, 3, 5],
    ], dtype=np.int64)
    m = trimesh.Trimesh(vertices=v6, faces=faces, process=False)
    m.visual.face_colors = np.tile(face_rgba, (len(faces), 1))
    return m


def _prism_vertices_depth(b0: Borehole, b1: Borehole, b2: Borehole, d0: float, d1: float) -> np.ndarray:
    """常规：顶/底都用 collar_z - depth（depth 为孔深）"""
    top = np.array([
        [b0.x, b0.y, (b0.collar_z - d0)],
        [b1.x, b1.y, (b1.collar_z - d0)],
        [b2.x, b2.y, (b2.collar_z - d0)],
    ], dtype=float)
    bot = np.array([
        [b0.x, b0.y, (b0.collar_z - d1)],
        [b1.x, b1.y, (b1.collar_z - d1)],
        [b2.x, b2.y, (b2.collar_z - d1)],
    ], dtype=float)
    return np.vstack([top, bot])


def _prism_vertices_z(
    b0: Borehole, b1: Borehole, b2: Borehole,
    zt0: float, zt1: float, zt2: float,
    zb0: float, zb1: float, zb2: float
) -> np.ndarray:
    """按给定的顶/底绝对Z构造三棱柱（用于底部补平）"""
    top = np.array([
        [b0.x, b0.y, float(zt0)],
        [b1.x, b1.y, float(zt1)],
        [b2.x, b2.y, float(zt2)],
    ], dtype=float)
    bot = np.array([
        [b0.x, b0.y, float(zb0)],
        [b1.x, b1.y, float(zb1)],
        [b2.x, b2.y, float(zb2)],
    ], dtype=float)
    return np.vstack([top, bot])


def _load_mesh_any(path: str) -> trimesh.Trimesh:
    m = trimesh.load(path, force="mesh")
    if isinstance(m, trimesh.Scene):
        geoms = [g for g in m.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not geoms:
            raise ValueError(f"加载失败：{path} 里没有 mesh。")
        m = trimesh.util.concatenate(geoms)
    try:
        m.remove_unreferenced_vertices()
    except Exception:
        pass
    try:
        m.process(validate=True)
    except Exception:
        try:
            m.process()
        except Exception:
            pass
    return m


# =========================
# Boundary clip (3D 修正)
# =========================
def _inside_ratio(boundary: trimesh.Trimesh, v6: np.ndarray, mode: str = "sample") -> float:
    """
    centroid: 1点
    sample: 9点（cen + top_c + bot_c + 6 vertices）
    """
    mode = str(mode).lower().strip()
    if mode == "centroid":
        p = v6.mean(axis=0, keepdims=True)
        inside = boundary.contains(p)
        return 1.0 if bool(inside[0]) else 0.0

    top_c = v6[0:3].mean(axis=0)
    bot_c = v6[3:6].mean(axis=0)
    cen = v6.mean(axis=0)
    pts = np.vstack([cen, top_c, bot_c, v6])
    inside = boundary.contains(pts)
    return float(np.count_nonzero(inside)) / float(len(inside))


def _point_to_fault_distance_2d(x: float, y: float, fault_polys: List[np.ndarray] | None) -> float:
    if not fault_polys:
        return float("inf")
    p = Point(float(x), float(y))
    dmin = float("inf")
    for poly in fault_polys:
        if len(poly) < 2:
            continue
        ls = LineString(poly)
        d = float(p.distance(ls))
        if d < dmin:
            dmin = d
    return dmin


def _triangle_cross_fault(v2_tri: np.ndarray, fault_polys: List[np.ndarray] | None) -> bool:
    if not fault_polys:
        return False
    tri_poly = Polygon(v2_tri)
    if (not tri_poly.is_valid) or tri_poly.area <= 0:
        return False
    for poly in fault_polys:
        if len(poly) < 2:
            continue
        ls = LineString(poly)
        if tri_poly.crosses(ls):
            return True
        if tri_poly.intersects(ls):
            inter = tri_poly.intersection(ls)
            if not inter.is_empty and getattr(inter, "length", 0.0) > 1e-6:
                return True
    return False


# =========================
# Load boreholes
# =========================
def load_boreholes(excel_path: str) -> dict[str, Borehole]:
    df = pd.read_excel(excel_path)
    if "Unnamed: 0" in df.columns:
        df = df.drop(columns=["Unnamed: 0"])
    df.columns = [str(c).strip() for c in df.columns]

    for c in ["起始孔深", "终止孔深", "X坐标", "Y坐标", "Z坐标"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["钻孔编号", "起始孔深", "终止孔深", "X坐标", "Y坐标", "Z坐标"])

    boreholes: dict[str, Borehole] = {}
    for code, g in df.groupby("钻孔编号"):
        g = g.sort_values("起始孔深", ascending=True)
        x = float(g["X坐标"].iloc[0])
        y = float(g["Y坐标"].iloc[0])

        collar_vals = (g["Z坐标"] + g["起始孔深"]).to_numpy(dtype=float)
        collar_z = float(np.nanmedian(collar_vals))
        max_depth = float(np.nanmax(g["终止孔深"].to_numpy(dtype=float)))

        segs = []
        for _, r in g.iterrows():
            d0 = float(r["起始孔深"])
            d1 = float(r["终止孔深"])
            if not np.isfinite(d0) or not np.isfinite(d1) or d1 <= d0:
                continue
            desc = "" if pd.isna(r.get("分层描述", "")) else str(r.get("分层描述", ""))
            desc = _normalize_lith_name(desc)
            segs.append((d0, d1, desc))

        boreholes[str(code).strip()] = Borehole(
            code=str(code).strip(),
            x=x, y=y,
            collar_z=collar_z,
            max_depth=max_depth,
            segs=segs
        )

    if len(boreholes) < 3:
        raise RuntimeError("钻孔数量不足3，无法三角剖分。")
    return boreholes


# =========================
# 关键：只补最低层，使每孔底部到达 Z_MIN
# =========================
def align_bottom_extend_last_layer(boreholes: dict[str, Borehole], z_min: float):
    """
    对每个孔：
      目标深度 D_target = collar_z - z_min
      - 截断所有分层到 D_target
      - 若孔浅：把最后一层延伸到 D_target（只补最低层）
      - 更新 max_depth = D_target
    """
    z_min = float(z_min)

    for b in boreholes.values():
        d_target = float(b.collar_z) - z_min
        if not np.isfinite(d_target) or d_target <= 0:
            continue

        b.segs = sorted(b.segs, key=lambda x: float(x[0]))
        old_max = float(b.max_depth) if np.isfinite(b.max_depth) else 0.0

        # 1) 截断到 d_target
        new_segs = []
        for d0, d1, desc in b.segs:
            d0 = float(d0); d1 = float(d1)
            if d1 <= 0 or d0 >= d_target:
                continue
            d1c = min(d1, d_target)
            if d1c > d0 + 1e-9:
                new_segs.append((d0, d1c, desc))

        # 2) 若孔浅：补最低层到 d_target
        if old_max < d_target - 1e-6:
            if new_segs:
                last_desc = new_segs[-1][2]
                last_end = float(new_segs[-1][1])
            elif b.segs:
                last_desc = b.segs[-1][2]
                last_end = min(float(b.segs[-1][1]), d_target)
            else:
                last_desc = ""
                last_end = 0.0

            start = max(old_max, last_end)
            if d_target > start + 1e-6:
                new_segs.append((start, d_target, last_desc))

        b.segs = new_segs
        b.max_depth = d_target


# =========================
# Load fault polylines
# =========================
def load_fault_polylines(csv_path: str, simplify_tol: float = 0.0, step: int = 1) -> List[np.ndarray]:
    df = pd.read_csv(csv_path)
    cols = {c.lower(): c for c in df.columns}
    xcol = cols.get("x")
    ycol = cols.get("y")
    ocol = cols.get("order", None)
    fidcol = cols.get("fault_id", None)

    if xcol is None or ycol is None:
        raise ValueError("fault_csv 必须包含 x,y 列（可选 fault_id, order）。")
    if ocol is None:
        df["_order"] = np.arange(len(df), dtype=int)
        ocol = "_order"

    polylines = []
    if fidcol is None:
        g = df.sort_values(ocol)
        polylines.append(g[[xcol, ycol]].to_numpy(float))
    else:
        for _, g in df.groupby(fidcol):
            g = g.sort_values(ocol)
            polylines.append(g[[xcol, ycol]].to_numpy(float))

    out = []
    step = max(int(step), 1)
    for xy in polylines:
        if len(xy) < 2:
            continue
        xy2 = xy[::step].copy()
        if (xy2[-1] != xy[-1]).any():
            xy2 = np.vstack([xy2, xy[-1]])
        if simplify_tol and simplify_tol > 0 and len(xy2) >= 3:
            ls = LineString(xy2).simplify(float(simplify_tol), preserve_topology=True)
            xy2 = np.array(ls.coords, dtype=float)
        if len(xy2) >= 2:
            out.append(xy2)
    return out


# =========================
# Domain polygon from model.stl projection
# =========================
def domain_from_projected_faces(
    model_stl: str,
    face_step: int = 5,
    simplify_tol: float = 70.0,
    buffer_tol: float = 100.0,
) -> Polygon:
    m = _load_mesh_any(model_stl)
    V = m.vertices
    F = m.faces

    face_step = max(int(face_step), 1)
    polys = []
    for i in range(0, len(F), face_step):
        tri = V[F[i]][:, :2]
        p = Polygon([(float(tri[0, 0]), float(tri[0, 1])),
                     (float(tri[1, 0]), float(tri[1, 1])),
                     (float(tri[2, 0]), float(tri[2, 1]))])
        if p.area > 0:
            polys.append(p)

    if not polys:
        dom = MultiPoint(V[:, :2]).convex_hull
    else:
        chunk = 2000
        parts = [unary_union(polys[j:j + chunk]) for j in range(0, len(polys), chunk)]
        dom = unary_union(parts)

    dom = dom.buffer(0)
    if buffer_tol and buffer_tol != 0:
        dom = dom.buffer(float(buffer_tol)).buffer(-float(buffer_tol))
    if simplify_tol and simplify_tol > 0:
        dom = dom.simplify(float(simplify_tol), preserve_topology=True)

    if getattr(dom, "geom_type", "") == "MultiPolygon":
        dom = max(list(dom.geoms), key=lambda g: g.area)

    if getattr(dom, "geom_type", "") != "Polygon":
        raise RuntimeError("model_stl 投影未得到 Polygon domain，请调 face_step/buffer/simplify。")

    return dom


# =========================
# Build PSLG: vertices + segments
# =========================
def build_pslg(
    bore_pts: np.ndarray,
    fault_polys: List[np.ndarray],
    domain_poly: Polygon,
    snap_tol: float = 0.1,
) -> Tuple[np.ndarray, np.ndarray]:
    verts: List[List[float]] = []
    grid = {}

    def key_xy(x, y):
        return (int(round(float(x) / snap_tol)), int(round(float(y) / snap_tol)))

    def add_point(x, y):
        k = key_xy(x, y)
        if k in grid:
            return grid[k]
        idx = len(verts)
        verts.append([float(x), float(y)])
        grid[k] = idx
        return idx

    # boreholes first
    for x, y in bore_pts:
        add_point(x, y)

    segments: List[List[int]] = []

    # fault segments
    for poly in fault_polys:
        prev = None
        for x, y in poly:
            i = add_point(x, y)
            if prev is not None and i != prev:
                segments.append([prev, i])
            prev = i

    # domain boundary segments (exterior + holes)
    def add_ring(ring_coords: np.ndarray):
        idxs = [add_point(x, y) for x, y in ring_coords]
        for a, b in zip(idxs[:-1], idxs[1:]):
            if a != b:
                segments.append([a, b])

    ext = np.array(domain_poly.exterior.coords, dtype=float)
    add_ring(ext)

    for interior in domain_poly.interiors:
        hole = np.array(interior.coords, dtype=float)
        add_ring(hole)

    return np.array(verts, float), np.array(segments, np.int32)


# =========================
# CDT triangulation
# =========================
def run_cdt(vertices_xy: np.ndarray, segments: np.ndarray, quality_q: float = 0.0, extra_opts: str = ""):
    A = {
        "vertices": vertices_xy.astype(float),
        "segments": segments.astype(np.int32) if len(segments) else np.zeros((0, 2), np.int32),
    }
    opt = "pQ" + (f"q{float(quality_q):g}" if quality_q and quality_q > 0 else "")
    if extra_opts:
        opt += str(extra_opts)
    B = tr.triangulate(A, opt)
    V2 = np.asarray(B.get("vertices", vertices_xy), float)
    T = np.asarray(B.get("triangles", np.zeros((0, 3), np.int32)), np.int32)
    return V2, T


def filter_triangles_by_domain_centroid(V2: np.ndarray, T: np.ndarray, domain_poly: Polygon):
    if T is None or len(T) == 0:
        return T
    kept = []
    Pdom = prep(domain_poly)
    for tri in T:
        p = V2[tri]
        cx = float(p[:, 0].mean())
        cy = float(p[:, 1].mean())
        if Pdom.contains(Point(cx, cy)):
            kept.append(tri)
    return np.array(kept, np.int32)

# =========================
# Create boreholes for ALL CDT vertices (including steiner)
# =========================
def create_vertex_boreholes(V2: np.ndarray, boreholes: dict[str, Borehole], base_codes: List[str]):
    base_pts = np.array([[boreholes[c].x, boreholes[c].y] for c in base_codes], float)

    if _HAVE_SCIPY:
        tree = cKDTree(base_pts)
        def nearest_bh_idx(xy):
            _, j = tree.query(xy, k=1)
            return int(j)
    else:
        def nearest_bh_idx(xy):
            d2 = np.sum((base_pts - xy[None, :])**2, axis=1)
            return int(np.argmin(d2))

    all_bhs = {}
    codes_for_vertices = []

    for i in range(len(V2)):
        xy = V2[i]
        j = nearest_bh_idx(xy)
        near_code = base_codes[j]
        b = boreholes[near_code]
        code = f"V_{i}"
        codes_for_vertices.append(code)
        all_bhs[code] = Borehole(
            code=code,
            x=float(xy[0]),
            y=float(xy[1]),
            collar_z=float(b.collar_z),
            max_depth=float(b.max_depth),
            segs=list(b.segs)
        )

    return all_bhs, codes_for_vertices


# =========================
# Build 3D mesh with boundary clip + 平底补层
# =========================
def build_mesh(
    boreholes: dict[str, Borehole],
    codes_for_vertices: List[str],
    triangles: np.ndarray,
    boundary: trimesh.Trimesh | None,
    clip: str,
    clip_threshold: float,
    z_min: float,
    fault_polys: List[np.ndarray] | None = None,
    fault_band: float = 0.0,
    fault_label: str = "断层破碎带",
    bottom_eps: float = 1e-3
):
    """
    bottom_eps: 判断是否需要补底的小阈值
    """
    meshes = []
    n_prisms = 0
    n_kept = 0

    z_min = float(z_min)
    clip = str(clip).lower().strip()
    clip_threshold = float(clip_threshold)

    for tri in triangles:
        c0, c1, c2 = codes_for_vertices[int(tri[0])], codes_for_vertices[int(tri[1])], codes_for_vertices[int(tri[2])]
        b0, b1, b2 = boreholes[c0], boreholes[c1], boreholes[c2]
        tri_xy = np.array([[b0.x, b0.y], [b1.x, b1.y], [b2.x, b2.y]], dtype=float)
        cross_fault = _triangle_cross_fault(tri_xy, fault_polys)

        common_max = min(b0.max_depth, b1.max_depth, b2.max_depth)
        if not np.isfinite(common_max) or common_max <= 0:
            continue

        bounds = set([0.0, float(common_max)])
        for d0, d1, _ in b0.segs: bounds.add(float(d0)); bounds.add(float(d1))
        for d0, d1, _ in b1.segs: bounds.add(float(d0)); bounds.add(float(d1))
        for d0, d1, _ in b2.segs: bounds.add(float(d0)); bounds.add(float(d1))

        bounds = sorted([d for d in bounds if np.isfinite(d) and 0.0 <= d <= common_max])
        clean = []
        for d in bounds:
            if not clean or abs(d - clean[-1]) > 1e-6:
                clean.append(d)
        bounds = clean

        # ---------- 常规 prism（到 common_max） ----------
        for i in range(len(bounds) - 1):
            d0, d1 = bounds[i], bounds[i + 1]
            if d1 <= d0:
                continue

            d_mid = 0.5 * (d0 + d1)
            desc0 = _find_desc_at_depth(b0.segs, d_mid)
            desc1 = _find_desc_at_depth(b1.segs, d_mid)
            desc2 = _find_desc_at_depth(b2.segs, d_mid)
            if desc0 is None or desc1 is None or desc2 is None:
                continue

            label = Counter([desc0, desc1, desc2]).most_common(1)[0][0]
            v6 = _prism_vertices_depth(b0, b1, b2, d0, d1)
            cx = float(v6[:, 0].mean())
            cy = float(v6[:, 1].mean())
            if cross_fault or _point_to_fault_distance_2d(cx, cy, fault_polys) <= float(fault_band):
                label = fault_label

            n_prisms += 1
            if boundary is not None and clip != "none":
                ratio = _inside_ratio(boundary, v6, mode=clip)
                if ratio < clip_threshold:
                    continue

            color = _hash_color(label)
            meshes.append(_prism_mesh_from_vertices(v6, color))
            n_kept += 1

        # ---------- 底部补层：把三角形底面补到 z_min（只补最低层） ----------
        # 该三角形在 common_max 深度处各顶点的底Z
        zt0 = float(b0.collar_z - common_max)
        zt1 = float(b1.collar_z - common_max)
        zt2 = float(b2.collar_z - common_max)

        # 如果三角形的任一顶点仍高于 z_min，则补一个到 z_min 的 prism
        if max(zt0, zt1, zt2) > z_min + bottom_eps:
            # 选一个代表性的中点深度来取 label（仍然使用“最低层”）
            # 用 z_mid 在 [z_min, max(zt)] 的中点
            z_mid = 0.5 * (z_min + max(zt0, zt1, zt2))

            def depth_from_z(b: Borehole, z: float) -> float:
                d = float(b.collar_z - z)
                # clamp to [0, max_depth)
                return max(0.0, min(d, float(b.max_depth) - 1e-6))

            dmid0 = depth_from_z(b0, z_mid)
            dmid1 = depth_from_z(b1, z_mid)
            dmid2 = depth_from_z(b2, z_mid)

            desc0 = _find_desc_at_depth(b0.segs, dmid0)
            desc1 = _find_desc_at_depth(b1.segs, dmid1)
            desc2 = _find_desc_at_depth(b2.segs, dmid2)

            # 若某孔在该深度无描述，退化用其最底层描述
            if desc0 is None and b0.segs: desc0 = b0.segs[-1][2]
            if desc1 is None and b1.segs: desc1 = b1.segs[-1][2]
            if desc2 is None and b2.segs: desc2 = b2.segs[-1][2]
            if desc0 is None: desc0 = ""
            if desc1 is None: desc1 = ""
            if desc2 is None: desc2 = ""

            label = Counter([desc0, desc1, desc2]).most_common(1)[0][0]

            v6_fill = _prism_vertices_z(
                b0, b1, b2,
                zt0, zt1, zt2,          # 顶面为当前三角形底（可能倾斜）
                z_min, z_min, z_min     # 底面强制平底
            )
            cx_fill = float(v6_fill[:, 0].mean())
            cy_fill = float(v6_fill[:, 1].mean())
            if cross_fault or _point_to_fault_distance_2d(cx_fill, cy_fill, fault_polys) <= float(fault_band):
                label = fault_label

            n_prisms += 1
            if boundary is not None and clip != "none":
                ratio = _inside_ratio(boundary, v6_fill, mode=clip)
                if ratio >= clip_threshold:
                    meshes.append(_prism_mesh_from_vertices(v6_fill, _hash_color(label)))
                    n_kept += 1
            else:
                meshes.append(_prism_mesh_from_vertices(v6_fill, _hash_color(label)))
                n_kept += 1

    if not meshes:
        raise RuntimeError("没有生成任何三棱柱：clip_threshold 可能过严或 triangles 为空。")

    return trimesh.util.concatenate(meshes), n_prisms, n_kept


# =========================
# Plotting
# =========================
def plot_tris(out_png: str, pts: np.ndarray, tris: np.ndarray, title: str,
              domain_poly: Polygon | None = None,
              fault_polys: List[np.ndarray] | None = None,
              bore_pts: np.ndarray | None = None,
              tri_max_show: int = 2000):
    fig = plt.figure(figsize=(16, 12))
    ax = plt.gca()

    ntri = 0 if tris is None else len(tris)
    if tris is not None and len(tris) > 0:
        show = tris
        if len(show) > tri_max_show:
            idx = np.random.choice(len(show), size=tri_max_show, replace=False)
            show = show[idx]
        import matplotlib.tri as mtri
        tri_obj = mtri.Triangulation(pts[:, 0], pts[:, 1], show)
        ax.triplot(tri_obj, linewidth=0.35, alpha=0.9)

    if domain_poly is not None:
        x, y = domain_poly.exterior.xy
        ax.plot(x, y, "k-", linewidth=2.0)
        for interior in domain_poly.interiors:
            hx, hy = interior.xy
            ax.plot(hx, hy, "k-", linewidth=1.2)

    if fault_polys:
        for poly in fault_polys:
            ax.plot(poly[:, 0], poly[:, 1], "r-", linewidth=2.0, alpha=0.9)

    if bore_pts is not None:
        ax.scatter(bore_pts[:, 0], bore_pts[:, 1], s=45, c="navy")

    ax.set_title(f"{title}\n(triangles={ntri})")
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.grid(True, linestyle="--", alpha=0.25)
    ax.set_aspect("auto")

    if domain_poly is not None:
        minx, miny, maxx, maxy = domain_poly.bounds
        padx = 0.03 * (maxx - minx + 1e-9)
        pady = 0.03 * (maxy - miny + 1e-9)
        ax.set_xlim(minx - padx, maxx + padx)
        ax.set_ylim(miny - pady, maxy + pady)

    plt.tight_layout()
    plt.savefig(out_png, dpi=350)
    plt.close(fig)


# =========================
# Meshlab local export
# =========================
def export_local(mesh: trimesh.Trimesh, out_ply: str, out_stl: str):
    m_local = mesh.copy()
    origin = m_local.vertices.min(axis=0)
    m_local.vertices = m_local.vertices - origin
    m_local.export(out_ply)
    m_local.export(out_stl)
    return origin



# =========================
# Main
# =========================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", required=True)
    ap.add_argument("--fault_csv", required=True)
    ap.add_argument("--model_stl", required=True)
    ap.add_argument("--outdir", default="out_cdt_flatbottom")

    ap.add_argument("--snap_tol", type=float, default=0.1)
    ap.add_argument("--model_face_step", type=int, default=5)
    ap.add_argument("--domain_simplify", type=float, default=70.0)
    ap.add_argument("--domain_buffer", type=float, default=100.0)

    ap.add_argument("--fault_step", type=int, default=2)
    ap.add_argument("--fault_simplify", type=float, default=2.0)

    ap.add_argument("--cdt_q", type=float, default=5.0)
    ap.add_argument("--triangle_extra", default="")
    ap.add_argument("--tri_max_show", type=int, default=1500)

    # 3D 修正参数
    ap.add_argument("--clip", choices=["none", "centroid", "sample"], default="sample")
    ap.add_argument("--clip_threshold", type=float, default=0.7)
    ap.add_argument("--fault_band", type=float, default=20.0, help="fault influence band width in XY plane")
    ap.add_argument("--fault_label", default="断层", help="label used for prisms in fault band")

    # ???????????? model.stl ? zmin
    ap.add_argument("--z_min", type=float, default=None, help="????Z?????? model.stl.bounds[0,2]")

    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    # load boreholes
    boreholes = load_boreholes(args.excel)
    base_codes = sorted(boreholes.keys())
    bore_pts = np.array([[boreholes[c].x, boreholes[c].y] for c in base_codes], dtype=float)

    # use model.stl as 3D clip boundary
    boundary = _load_mesh_any(args.model_stl)
    print("model boundary watertight/is_volume:", boundary.is_watertight, boundary.is_volume)
    if not boundary.is_watertight:
        print("?? model.stl ?? watertight?contains ????????")
    # choose z_min
    z_min = float(args.z_min) if args.z_min is not None else float(boundary.bounds[0, 2])
    print(f"[FlatBottom] z_min = {z_min:.3f}")

    # ✅ 只补最低层，使每孔底部到 z_min
    align_bottom_extend_last_layer(boreholes, z_min)

    # domain from model.stl
    domain = domain_from_projected_faces(
        args.model_stl,
        face_step=int(args.model_face_step),
        simplify_tol=float(args.domain_simplify),
        buffer_tol=float(args.domain_buffer),
    )

    # fault polylines
    fault_polys = load_fault_polylines(
        args.fault_csv,
        simplify_tol=float(args.fault_simplify),
        step=int(args.fault_step)
    )

    # reference Delaunay (optional)
    if _HAVE_SCIPY and SciDelaunay is not None:
        raw_tris = SciDelaunay(bore_pts).simplices
    else:
        tri_obj = Triangulation(bore_pts[:, 0], bore_pts[:, 1])
        raw_tris = tri_obj.triangles

    plot_tris(
        os.path.join(args.outdir, "01_raw_delaunay_boreholes.png"),
        bore_pts, raw_tris,
        "01 Borehole Delaunay (reference)",
        domain_poly=domain, fault_polys=fault_polys, bore_pts=bore_pts,
        tri_max_show=99999
    )

    # PSLG + CDT
    V, S = build_pslg(bore_pts, fault_polys, domain, snap_tol=float(args.snap_tol))
    V2, T = run_cdt(V, S, quality_q=float(args.cdt_q), extra_opts=str(args.triangle_extra))
    T_in = filter_triangles_by_domain_centroid(V2, T, domain)

    plot_tris(
        os.path.join(args.outdir, "02_cdt_constrained.png"),
        V2, T_in,
        f"02 CDT constrained (q={float(args.cdt_q):g})",
        domain_poly=domain, fault_polys=fault_polys, bore_pts=bore_pts,
        tri_max_show=int(args.tri_max_show)
    )

    # build boreholes for vertices (steiner included) - inherit adjusted segs/max_depth
    all_bhs, codes_for_vertices = create_vertex_boreholes(V2, boreholes, base_codes)

    # build 3D mesh with boundary clip + flat bottom fill
    mesh, n_prisms, n_kept = build_mesh(
        boreholes=all_bhs,
        codes_for_vertices=codes_for_vertices,
        triangles=T_in,
        boundary=boundary,
        clip=str(args.clip),
        clip_threshold=float(args.clip_threshold),
        z_min=z_min,
        fault_polys=fault_polys,
        fault_band=float(args.fault_band),
        fault_label=str(args.fault_label)
    )

    tag = f"cdt_q{float(args.cdt_q):g}_clip{args.clip}_th{float(args.clip_threshold):.2f}_zmin{z_min:.1f}".replace(".", "_")
    out_ply = os.path.join(args.outdir, f"strata_{tag}.ply")
    out_stl = os.path.join(args.outdir, f"strata_{tag}.stl")
    mesh.export(out_ply)
    mesh.export(out_stl)

    out_ply_local = os.path.join(args.outdir, f"strata_{tag}_LOCAL.ply")
    out_stl_local = os.path.join(args.outdir, f"strata_{tag}_LOCAL.stl")
    origin = export_local(mesh, out_ply_local, out_stl_local)

    used_labels = []
    for c in base_codes:
        for _, _, desc in boreholes[c].segs:
            used_labels.append(desc)
    if fault_polys:
        used_labels.append(str(args.fault_label))
    map_csv, map_json = save_lithology_color_map(used_labels, args.outdir)

    print("OK ✅")
    print(f"- boreholes: {len(base_codes)}")
    print(f"- fault polylines: {len(fault_polys)}")
    print(f"- fault band: {float(args.fault_band):.2f}, fault label: {args.fault_label}")
    print(f"- PSLG vertices: {len(V)} segments: {len(S)}")
    print(f"- CDT vertices: {len(V2)} triangles(in domain): {len(T_in)}")
    print(f"- prisms generated/kept: {n_prisms}/{n_kept} (clip={args.clip}, th={float(args.clip_threshold):.2f})")
    print(f"- out: {out_ply}")
    print(f"- lithology color map: {map_csv}")
    print(f"- LOCAL for Meshlab: {out_ply_local}")
    print(f"  local origin offset: {origin}")


if __name__ == "__main__":
    main()
