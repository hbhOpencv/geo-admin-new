# -*- coding: utf-8 -*-
"""
Algorithm B (paper workflow, voxel + interpolation):

Inputs:
  --borehole_csv  : 钻孔分层 CSV（系统从DB导出）
  --model_stl     : model.stl（用于体素化）
  --fault_stl     : (optional) 断层 STL（用于条件模拟/断层带约束）
  --outdir        : 输出目录

Outputs (written into outdir):
  voxels.csv                     体素中心点
  virtual_points.csv             虚拟孔点
  virtual_boreholes.csv          虚拟孔分层
  real_plus_virtual.csv          实孔+虚拟孔分层（样本）
  voxels_ikrig.csv               指示克里金结果（pred_code + p_*）
  voxels_final.csv               最终结果（pred_code_final 或 pred_code）
  result_B.ply                   最终PLY（点云着色）
  view_B_XY.png / view_B_XZ.png / view_B_YZ.png  三视图
"""
from __future__ import annotations

import argparse
import math
import os
import colorsys
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd
import trimesh

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Patch

from scipy.spatial import Delaunay, cKDTree


# ------------------------
# helpers
# ------------------------
def log(msg: str):
    print(msg, flush=True)


def load_mesh(stl_path: str) -> trimesh.Trimesh:
    mesh = trimesh.load(stl_path)
    if isinstance(mesh, trimesh.Scene):
        mesh = trimesh.util.concatenate([g for g in mesh.geometry.values()])
    if mesh.is_empty:
        raise ValueError("model_stl is empty")
    return mesh


def safe_int(v) -> Optional[int]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    # keep leading number (e.g. "3 砂岩")
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


def sample_points_in_triangle(v0, v1, v2, n, rng):
    r1 = rng.random(n)
    r2 = rng.random(n)
    s1 = np.sqrt(r1)
    p = (1 - s1)[:, None] * v0 + (s1 * (1 - r2))[:, None] * v1 + (s1 * r2)[:, None] * v2
    return p


def min_dist_to_points(p, P):
    if len(P) == 0:
        return np.inf
    d = np.sqrt(((P - p) ** 2).sum(axis=1))
    return float(d.min())


def build_layers_by_hole(df, col_id, col_ztop, col_zbot, col_code):
    layers = {}
    for hid, g in df.groupby(col_id):
        zmin = np.minimum(g[col_ztop].to_numpy(float), g[col_zbot].to_numpy(float))
        zmax = np.maximum(g[col_ztop].to_numpy(float), g[col_zbot].to_numpy(float))
        codes = g[col_code].to_numpy(int)
        order = np.argsort(zmin)
        layers[hid] = pd.DataFrame({"zmin": zmin[order], "zmax": zmax[order], "code": codes[order]})
    return layers


def lithology_at_z(layers_table: pd.DataFrame, z: float):
    hit = layers_table[(layers_table["zmin"] <= z) & (z <= layers_table["zmax"])]
    if len(hit) == 0:
        return None
    return int(hit.iloc[0]["code"])


def choose_code_idw(codes3, dists3, power=2.0):
    score = {}
    for c, d in zip(codes3, dists3):
        if c is None:
            continue
        w = 1.0 / max(d, 1e-9) ** power
        score[c] = score.get(c, 0.0) + w
    if not score:
        return None
    best = max(score.items(), key=lambda kv: kv[1])[0]
    best_score = score[best]
    tied = [k for k, v in score.items() if abs(v - best_score) < 1e-12]
    if len(tied) == 1:
        return int(best)
    idx_sorted = np.argsort(dists3)
    for idx in idx_sorted:
        if codes3[idx] is not None and int(codes3[idx]) in tied:
            return int(codes3[idx])
    return int(best)


def merge_segments(segs):
    if not segs:
        return []
    segs = sorted(segs, key=lambda t: (t[0], t[1]))
    merged = [list(segs[0])]
    for z0, z1, code in segs[1:]:
        last = merged[-1]
        if code == last[2] and abs(z0 - last[1]) < 1e-9:
            last[1] = z1
        else:
            merged.append([z0, z1, code])
    return [tuple(x) for x in merged]


# ------------------------
# variogram / kriging (gamma form)
# ------------------------
def gamma_spherical(h, a, c0, c):
    h = np.asarray(h, dtype=float)
    hr = h / np.maximum(a, 1e-12)
    g = np.empty_like(hr, dtype=float)
    inside = hr <= 1.0
    g[inside] = c0 + c * (1.5 * hr[inside] - 0.5 * (hr[inside] ** 3))
    g[~inside] = c0 + c
    return g


def ok_weights(nei_xyz, target_xyz, a, c0, c, diag_eps=1e-8):
    n = nei_xyz.shape[0]
    diff = nei_xyz[:, None, :] - nei_xyz[None, :, :]
    dij = np.sqrt((diff ** 2).sum(axis=2))
    Gamma = gamma_spherical(dij, a=a, c0=c0, c=c) + np.eye(n) * diag_eps

    d0 = np.sqrt(((nei_xyz - target_xyz[None, :]) ** 2).sum(axis=1))
    gamma0 = gamma_spherical(d0, a=a, c0=c0, c=c)

    A = np.zeros((n + 1, n + 1), dtype=float)
    A[:n, :n] = Gamma
    A[:n, n] = 1.0
    A[n, :n] = 1.0
    b = np.zeros(n + 1, dtype=float)
    b[:n] = gamma0
    b[n] = 1.0

    try:
        sol = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        sol = np.linalg.lstsq(A, b, rcond=None)[0]
    return sol[:n]


def ik_predict(nei_codes, weights, classes):
    ind = (nei_codes[:, None] == classes[None, :]).astype(float)
    pk = (weights[:, None] * ind).sum(axis=0)
    pk = np.clip(pk, 0.0, 1.0)
    s = pk.sum()
    if s > 1e-12:
        pk = pk / s
    pred = int(classes[int(np.argmax(pk))])
    return pred, pk


# ------------------------
# conditional simulation (vote on probs + fault band hard constraint)
# ------------------------
def load_fault_kdtree(stl_path: str, voxel_hint: float, n_samples: Optional[int] = None):
    mesh = trimesh.load(stl_path)
    if isinstance(mesh, trimesh.Scene):
        mesh = trimesh.util.concatenate([g for g in mesh.geometry.values()])
    if mesh.is_empty:
        raise ValueError("fault_stl is empty")

    if n_samples is None:
        est = int(max(30_000, min(600_000, mesh.area / (voxel_hint ** 2) * 6.0)))
        n_samples = est

    pts, _ = trimesh.sample.sample_surface(mesh, n_samples)
    pts = np.asarray(pts, dtype=np.float64)
    tree = cKDTree(pts)
    return tree


def simulate_vote_chunk(
    P_lith: np.ndarray,
    pred_before: np.ndarray,
    rng: np.random.Generator,
    fault_dist: np.ndarray,
    fault_thickness: float,
    n_sim: int,
    vote_threshold: float,
    lith_codes: List[int],
    hard_fault_band: bool = True,
    fault_code: int = 12
) -> Tuple[np.ndarray, np.ndarray]:
    """
    方案2（动态岩性K）：
      - Step4 只输出 K 个岩性概率列（P_lith shape=(n,K)，列对应 lith_codes）
      - Step5 再额外加 1 列 fault（内部索引 fault_idx=K）
      - 返回 pred_final 为“真实岩性编码 or fault_code”
    """
    n = P_lith.shape[0]
    K = P_lith.shape[1]
    if len(lith_codes) != K:
        raise ValueError(f"lith_codes length {len(lith_codes)} != P_lith cols {K}")

    # categories: [岩性编码..., fault_code]
    categories = np.array(list(lith_codes) + [int(fault_code)], dtype=int)
    fault_idx = K

    # counts & probs
    counts = np.zeros((n, K + 1), dtype=np.uint16)
    P = np.zeros((n, K + 1), dtype=np.float64)
    P[:, :K] = P_lith

    in_fault = fault_dist <= fault_thickness

    if hard_fault_band and np.any(in_fault):
        # hard constraint: always fault class inside band
        counts[in_fault, fault_idx] = n_sim
        free_idx = np.where(~in_fault)[0]
    else:
        free_idx = np.arange(n, dtype=int)

    if len(free_idx) > 0:
        # outside band: fault prob = 0 (hard)
        P[free_idx, fault_idx] = 0.0
        # normalize
        ss = P[free_idx].sum(axis=1)
        P[free_idx] = P[free_idx] / np.maximum(ss[:, None], 1e-12)

        cdf = np.cumsum(P[free_idx], axis=1)
        cdf[:, -1] = 1.0

        for _ in range(n_sim):
            r = rng.random(len(free_idx), dtype=np.float64)
            idx = (r[:, None] > cdf).sum(axis=1).astype(np.int16)  # 0..K
            np.add.at(counts, (free_idx, idx), 1)

    winner_idx = np.argmax(counts, axis=1).astype(int)          # 0..K
    win_cnt = counts[np.arange(n), winner_idx].astype(int)

    min_votes = int(math.floor(n_sim * vote_threshold) + 1)

    pred_final = pred_before.copy()
    ok = win_cnt >= min_votes
    if np.any(ok):
        pred_final[ok] = categories[winner_idx[ok]]
    return pred_final, win_cnt


# ------------------------
# visualization (mode projection)
# ------------------------
def scan_minmax(csv_path: Path, usecols: List[str], chunksize: int = 200000):
    xmin = ymin = zmin = np.inf
    xmax = ymax = zmax = -np.inf
    total = 0
    for chunk in pd.read_csv(csv_path, chunksize=chunksize, usecols=usecols, encoding="utf-8-sig"):
        x = chunk[usecols[0]].to_numpy(float)
        y = chunk[usecols[1]].to_numpy(float)
        z = chunk[usecols[2]].to_numpy(float)
        xmin = min(xmin, float(np.min(x)))
        ymin = min(ymin, float(np.min(y)))
        zmin = min(zmin, float(np.min(z)))
        xmax = max(xmax, float(np.max(x)))
        ymax = max(ymax, float(np.max(y)))
        zmax = max(zmax, float(np.max(z)))
        total += len(chunk)
    return xmin, xmax, ymin, ymax, zmin, zmax, total


def plot_discrete_grid(a_vals, b_vals, grid_codes, title, xlabel, ylabel, out_png, grid_step=4, figsize=(40, 3.5)):
    valid = grid_codes[grid_codes != -9999]
    classes = np.unique(valid)
    classes.sort()
    n = len(classes)

    colors = plt.cm.gray(np.linspace(0.20, 0.85, max(n, 2)))
    cmap = ListedColormap(colors[:n])
    bounds = np.arange(n + 1) - 0.5
    norm = BoundaryNorm(bounds, cmap.N)

    mapping = {c: i for i, c in enumerate(classes)}
    show = np.full_like(grid_codes, fill_value=np.nan, dtype=float)
    for c, idx in mapping.items():
        show[grid_codes == c] = idx

    da = np.min(np.diff(a_vals)) if len(a_vals) > 1 else 1.0
    db = np.min(np.diff(b_vals)) if len(b_vals) > 1 else 1.0
    extent = [a_vals.min() - da / 2, a_vals.max() + da / 2, b_vals.min() - db / 2, b_vals.max() + db / 2]

    fig, ax = plt.subplots(figsize=figsize, dpi=300)
    ax.imshow(show, origin="lower", extent=extent, cmap=cmap, norm=norm,
              interpolation="nearest", aspect="auto")

    if len(a_vals) > 1 and len(b_vals) > 1:
        ax.set_xticks(a_vals[::grid_step], minor=True)
        ax.set_yticks(b_vals[::grid_step], minor=True)
        ax.grid(which="minor", linewidth=0.3, alpha=0.35)

    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.ticklabel_format(style="plain", axis="both", useOffset=False)

    # ---- legend: 动态，不再写死 12 ----
    show_n = min(len(classes), 30)  # 类别太多会把图挤爆，最多展示30个
    legend_patches = []
    for c in classes[:show_n]:
        idx = mapping[c]
        legend_patches.append(
            Patch(facecolor=cmap.colors[idx], edgecolor="k", label=f"code {int(c)}")
        )
    if legend_patches:
        ax.legend(handles=legend_patches, loc="upper right", fontsize=8, framealpha=0.9)

    plt.tight_layout()
    plt.savefig(out_png, dpi=300, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    log(f"saved: {out_png}")


def project_mode(csv_path: Path, voxel_size: float, code_col: str, out_xy: Path, out_xz: Path, out_yz: Path):
    usecols = ["X", "Y", "Z", code_col]
    xmin, xmax, ymin, ymax, zmin, zmax, total = scan_minmax(csv_path, usecols[:3])
    log(f"final voxels rows = {total:,}  range X[{xmin},{xmax}] Y[{ymin},{ymax}] Z[{zmin},{zmax}]")

    counts_xy: Dict[Tuple[int, int], Dict[int, int]] = {}
    counts_xz: Dict[Tuple[int, int], Dict[int, int]] = {}
    counts_yz: Dict[Tuple[int, int], Dict[int, int]] = {}

    chunksize = 250000
    processed = 0
    for chunk in pd.read_csv(csv_path, chunksize=chunksize, usecols=usecols, encoding="utf-8-sig"):
        x = chunk["X"].to_numpy(float)
        y = chunk["Y"].to_numpy(float)
        z = chunk["Z"].to_numpy(float)
        c = chunk[code_col].to_numpy(int)

        ix = np.rint((x - xmin) / voxel_size).astype(int)
        iy = np.rint((y - ymin) / voxel_size).astype(int)
        iz = np.rint((z - zmin) / voxel_size).astype(int)

        for a, b, code in zip(ix, iy, c):
            key = (int(a), int(b))
            d = counts_xy.get(key)
            if d is None:
                counts_xy[key] = {int(code): 1}
            else:
                d[int(code)] = d.get(int(code), 0) + 1

        for a, b, code in zip(ix, iz, c):
            key = (int(a), int(b))
            d = counts_xz.get(key)
            if d is None:
                counts_xz[key] = {int(code): 1}
            else:
                d[int(code)] = d.get(int(code), 0) + 1

        for a, b, code in zip(iy, iz, c):
            key = (int(a), int(b))
            d = counts_yz.get(key)
            if d is None:
                counts_yz[key] = {int(code): 1}
            else:
                d[int(code)] = d.get(int(code), 0) + 1

        processed += len(chunk)
        if processed % (chunksize * 4) == 0:
            log(f"processed rows: {processed:,}")

    def dict_to_grid(counts):
        keys = np.array(list(counts.keys()), dtype=int)
        a_idx = keys[:, 0]
        b_idx = keys[:, 1]
        a_vals = np.unique(a_idx); a_vals.sort()
        b_vals = np.unique(b_idx); b_vals.sort()
        ai = {v: i for i, v in enumerate(a_vals)}
        bi = {v: i for i, v in enumerate(b_vals)}
        grid = np.full((len(b_vals), len(a_vals)), -9999, dtype=int)
        for (a, b), d in counts.items():
            mode_code = max(d.items(), key=lambda kv: kv[1])[0]
            grid[bi[b], ai[a]] = int(mode_code)
        return a_vals, b_vals, grid

    ax, ay, gxy = dict_to_grid(counts_xy)
    ax_real = xmin + ax.astype(float) * voxel_size
    ay_real = ymin + ay.astype(float) * voxel_size
    plot_discrete_grid(ax_real, ay_real, gxy, "Algorithm B - XY", "X (m)", "Y (m)", str(out_xy), grid_step=2, figsize=(12, 10))

    ax, az, gxz = dict_to_grid(counts_xz)
    ax_real = xmin + ax.astype(float) * voxel_size
    az_real = zmin + az.astype(float) * voxel_size
    plot_discrete_grid(ax_real, az_real, gxz, "Algorithm B - XZ", "X (m)", "Z (m)", str(out_xz), grid_step=4, figsize=(40, 4.5))

    ay, az, gyz = dict_to_grid(counts_yz)
    ay_real = ymin + ay.astype(float) * voxel_size
    az_real = zmin + az.astype(float) * voxel_size
    plot_discrete_grid(ay_real, az_real, gyz, "Algorithm B - YZ", "Y (m)", "Z (m)", str(out_yz), grid_step=4, figsize=(40, 4.5))


# ------------------------
# ply export (colored point cloud)
# ------------------------
def _color_from_index(i: int) -> Tuple[int, int, int, int]:
    # deterministic distinct-ish colors by HSV
    h = (i * 0.61803398875) % 1.0
    s = 0.65
    v = 0.95
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255), 255


def color_table_from_codes(codes: List[int], fault_code: int = 12) -> Dict[int, Tuple[int, int, int, int]]:
    """
    动态颜色表：根据当前数据里出现的编码生成颜色映射。
    fault_code 固定黑色。
    """
    base = [
        (31, 119, 180), (255, 127, 14), (44, 160, 44), (214, 39, 40),
        (148, 103, 189), (140, 86, 75), (227, 119, 194), (127, 127, 127),
        (188, 189, 34), (23, 190, 207), (255, 152, 150), (197, 176, 213),
        (174, 199, 232), (255, 187, 120), (152, 223, 138), (255, 152, 150),
        (197, 176, 213), (196, 156, 148), (247, 182, 210), (199, 199, 199),
    ]
    tab: Dict[int, Tuple[int, int, int, int]] = {}
    codes_sorted = sorted(set(int(c) for c in codes))
    lith = [c for c in codes_sorted if c != int(fault_code)]

    for i, c in enumerate(lith):
        if i < len(base):
            r, g, b = base[i]
            tab[c] = (r, g, b, 255)
        else:
            tab[c] = _color_from_index(i)

    tab[int(fault_code)] = (190, 24, 24, 255)  # fault
    return tab




def _rgba_to_hex_tuple(rgba: Tuple[int, int, int, int]) -> str:
    r, g, b, a = [int(x) for x in rgba]
    return f"#{r:02X}{g:02X}{b:02X}{a:02X}"


def save_lithology_color_map_b(codes: List[int], outdir: Path, fault_code: int = 12, borehole_csv: Optional[Path] = None):
    """Save code/name -> RGBA mapping used by Algorithm B."""
    tab = color_table_from_codes(codes, fault_code=int(fault_code))

    code_to_name: Dict[int, str] = {}
    if borehole_csv is not None:
        try:
            cand_csv = borehole_csv.parent / 'lithology_map.csv'
            if cand_csv.exists():
                mdf = pd.read_csv(cand_csv, encoding='utf-8-sig')
                if '岩性编码' in mdf.columns and '岩性名称' in mdf.columns:
                    for _, row in mdf.iterrows():
                        try:
                            code_to_name[int(row['岩性编码'])] = str(row['岩性名称'])
                        except Exception:
                            pass
        except Exception:
            pass

    rows = []
    for code in sorted(set(int(c) for c in codes)):
        rgba = tab.get(int(code), (200, 200, 200, 255))
        rows.append({
            '岩性编码': int(code),
            '岩性名称': '断层' if int(code) == int(fault_code) else code_to_name.get(int(code), ''),
            '是否断层': 1 if int(code) == int(fault_code) else 0,
            'R': int(rgba[0]),
            'G': int(rgba[1]),
            'B': int(rgba[2]),
            'A': int(rgba[3]),
            'HEX': _rgba_to_hex_tuple(rgba),
        })

    csv_path = outdir / 'lithology_color_map.csv'
    json_path = outdir / 'lithology_color_map.json'
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding='utf-8-sig')
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    log(f'saved: {csv_path}')
    return csv_path, json_path

def export_ply_pointcloud(csv_path: Path, code_col: str, out_ply: Path, max_points: int = 0, seed: int = 202501, fault_code: int = 12):
    # read all points (can be big) -> optionally downsample
    df = pd.read_csv(csv_path, usecols=["X", "Y", "Z", code_col], encoding="utf-8-sig")
    pts = df[["X", "Y", "Z"]].to_numpy(np.float32)
    codes = df[code_col].to_numpy(int)

    n = len(pts)
    if max_points and n > max_points:
        rng = np.random.default_rng(seed)
        idx = rng.choice(n, size=max_points, replace=False)
        pts = pts[idx]
        codes = codes[idx]
        log(f"PLY downsample: {n:,} -> {len(pts):,}")

    uniq_codes = np.unique(codes).astype(int).tolist()
    tab = color_table_from_codes(uniq_codes, fault_code=int(fault_code))

    colors = np.zeros((len(pts), 4), dtype=np.uint8)
    for i, c in enumerate(codes):
        colors[i] = tab.get(int(c), (200, 200, 200, 255))

    pc = trimesh.points.PointCloud(pts, colors=colors)
    pc.export(out_ply)
    log(f"saved: {out_ply}")


def fill_invalid_codes_nearest(csv_path: Path, code_col: str, invalid_code: int = -1) -> int:
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    if code_col not in df.columns:
        return 0

    code_series = pd.to_numeric(df[code_col], errors="coerce")
    invalid_mask = code_series.isna() | (code_series.astype("Int64") == int(invalid_code))
    if not bool(invalid_mask.any()):
        return 0

    valid_mask = ~invalid_mask
    if not bool(valid_mask.any()):
        log(f"[fill-invalid] no valid codes found in {csv_path.name}, skip filling")
        return 0

    valid_pts = df.loc[valid_mask, ["X", "Y", "Z"]].to_numpy(np.float64)
    invalid_pts = df.loc[invalid_mask, ["X", "Y", "Z"]].to_numpy(np.float64)
    valid_codes = code_series.loc[valid_mask].astype(int).to_numpy()

    tree = cKDTree(valid_pts)
    _, nn_idx = tree.query(invalid_pts, k=1, workers=-1)
    filled_codes = valid_codes[np.asarray(nn_idx, dtype=int)]

    df.loc[invalid_mask, code_col] = filled_codes
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    filled_count = int(invalid_mask.sum())
    log(f"[fill-invalid] filled {filled_count} invalid `{code_col}` values by nearest neighbor")
    return filled_count


# ------------------------
# main
# ------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--borehole_csv", required=True)
    ap.add_argument("--model_stl", required=True)
    ap.add_argument("--fault_stl", default="")
    ap.add_argument("--outdir", required=True)

    ap.add_argument("--voxel_size", type=float, default=15.0)
    ap.add_argument("--n_virtual_per_tri", type=int, default=5)
    ap.add_argument("--min_dist_to_real", type=float, default=80.0)
    ap.add_argument("--min_dist_to_virtual", type=float, default=40.0)
    ap.add_argument("--idw_power", type=float, default=2.0)

    ap.add_argument("--n_neighbors", type=int, default=12)
    ap.add_argument("--search_radius", type=float, default=1200.0)

    ap.add_argument("--range_a", type=float, default=944.0)
    ap.add_argument("--nugget_c0", type=float, default=0.227)
    ap.add_argument("--sill_c", type=float, default=0.021)

    ap.add_argument("--use_condsim", type=int, default=0)
    ap.add_argument("--n_sim", type=int, default=100)
    ap.add_argument("--vote_threshold", type=float, default=0.51)
    ap.add_argument("--fault_thickness", type=float, default=30.0)
    ap.add_argument("--max_points_ply", type=int, default=0)
    ap.add_argument("--skip_ply", type=int, default=0)
    ap.add_argument("--skip_views", type=int, default=0)
    ap.add_argument("--skip_color_map", type=int, default=0)

    ap.add_argument("--seed", type=int, default=202501)

    # fault_code 不参与 IK，只在 Step5 hard band 中加入
    # default=-1: auto = max(岩性编码)+1 （适配动态岩性数量）
    ap.add_argument("--fault_code", type=int, default=-1)

    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(args.seed)

    # ---- load real borehole layers ----
    real = pd.read_csv(args.borehole_csv, encoding="utf-8-sig")
    # expected columns
    COL_ID = "钻孔ID"
    COL_X = "X坐标(m)"
    COL_Y = "Y坐标(m)"
    COL_ZTOP = "岩性段顶深Z(m)"
    COL_ZBOT = "岩性段底深Z(m)"
    COL_ZMID = "岩性段中点Z(m)"
    COL_CODE = "岩性编码"
    for c in [COL_ID, COL_X, COL_Y, COL_ZTOP, COL_ZBOT, COL_ZMID, COL_CODE]:
        if c not in real.columns:
            raise ValueError(f"borehole_csv missing col: {c}")

    # decide fault_code (auto if -1)
    FAULT_CODE = int(args.fault_code)
    if FAULT_CODE < 0:
        try:
            FAULT_CODE = int(np.max(real[COL_CODE].to_numpy(dtype=int))) + 1
        except Exception:
            FAULT_CODE = 999999

    # ---- voxelize model ----
    log("=== Step1: voxelize model ===")
    mesh = load_mesh(args.model_stl)
    vox = mesh.voxelized(pitch=float(args.voxel_size)).fill()
    pts = np.asarray(vox.points, dtype=np.float64)
    voxels_csv = outdir / "voxels.csv"
    pd.DataFrame(pts, columns=["X", "Y", "Z"]).to_csv(voxels_csv, index=False, encoding="utf-8-sig")
    log(f"voxels: {len(pts):,}  saved: {voxels_csv}")

    # ---- virtual points (optional) ----
    virt_points_csv = outdir / "virtual_points.csv"
    if args.n_virtual_per_tri > 0:
        log("=== Step2: virtual points ===")
        boreholes = real.groupby(COL_ID, as_index=False).first()[[COL_ID, COL_X, COL_Y]]
        coords = boreholes[[COL_X, COL_Y]].to_numpy(float)
        ids = boreholes[COL_ID].astype(str).tolist()
        tri = Delaunay(coords)
        simplices = tri.simplices

        real_pts = coords.copy()
        virt_pts_arr = np.empty((0, 2), dtype=float)
        virt_rows = []
        for t_idx, (a, b, c) in enumerate(simplices):
            v0, v1, v2 = coords[a], coords[b], coords[c]
            candidates = sample_points_in_triangle(v0, v1, v2, args.n_virtual_per_tri * 10, rng)
            kept = 0
            for p in candidates:
                if min_dist_to_points(p, real_pts) < args.min_dist_to_real:
                    continue
                if min_dist_to_points(p, virt_pts_arr) < args.min_dist_to_virtual:
                    continue
                virt_pts_arr = np.vstack([virt_pts_arr, p[None, :]])
                virt_rows.append({
                    "虚拟孔ID": f"VIRT_{len(virt_rows) + 1:04d}",
                    "X坐标(m)": float(p[0]),
                    "Y坐标(m)": float(p[1]),
                    "所属三角形": int(t_idx)
                })
                kept += 1
                if kept >= args.n_virtual_per_tri:
                    break

        pd.DataFrame(virt_rows).to_csv(virt_points_csv, index=False, encoding="utf-8-sig")
        log(f"virtual points: {len(virt_rows):,}  saved: {virt_points_csv}")
    else:
        pd.DataFrame(columns=["虚拟孔ID", "X坐标(m)", "Y坐标(m)", "所属三角形"]).to_csv(
            virt_points_csv, index=False, encoding="utf-8-sig"
        )
        log("skip virtual points (n_virtual_per_tri=0)")

    # ---- virtual boreholes layers ----
    log("=== Step3: virtual boreholes layers ===")
    virt_df = pd.read_csv(virt_points_csv, encoding="utf-8-sig")
    boreholes = real.groupby(COL_ID, as_index=False).first()[[COL_ID, COL_X, COL_Y]]
    ids = boreholes[COL_ID].astype(str).tolist()
    coords = boreholes[[COL_X, COL_Y]].to_numpy(float)
    tri = Delaunay(coords)
    simplices = tri.simplices

    layers_by_hole = build_layers_by_hole(real, COL_ID, COL_ZTOP, COL_ZBOT, COL_CODE)

    out_rows = []
    for _, r in virt_df.iterrows():
        vid = str(r.get("虚拟孔ID", ""))
        if not vid:
            continue
        x = float(r[COL_X])
        y = float(r[COL_Y])
        t_idx = int(r["所属三角形"])
        if t_idx < 0 or t_idx >= len(simplices):
            continue
        a, b, c = simplices[t_idx]
        h3 = [ids[a], ids[b], ids[c]]
        d3 = [
            float(np.hypot(coords[a, 0] - x, coords[a, 1] - y)),
            float(np.hypot(coords[b, 0] - x, coords[b, 1] - y)),
            float(np.hypot(coords[c, 0] - x, coords[c, 1] - y)),
        ]

        breakpoints = []
        for hid in h3:
            tab = layers_by_hole[hid]
            breakpoints.extend(tab["zmin"].tolist())
            breakpoints.extend(tab["zmax"].tolist())
        bp = np.unique(np.array(breakpoints, dtype=float))
        bp.sort()
        if len(bp) < 2:
            continue

        segs = []
        for z0, z1 in zip(bp[:-1], bp[1:]):
            if z1 <= z0:
                continue
            zmid = 0.5 * (z0 + z1)
            c3 = [
                lithology_at_z(layers_by_hole[h3[0]], zmid),
                lithology_at_z(layers_by_hole[h3[1]], zmid),
                lithology_at_z(layers_by_hole[h3[2]], zmid),
            ]
            code = choose_code_idw(c3, d3, power=float(args.idw_power))
            if code is None:
                continue
            segs.append((z0, z1, int(code)))
        segs = merge_segments(segs)
        for z0, z1, code in segs:
            out_rows.append({
                COL_ID: vid,
                COL_X: x,
                COL_Y: y,
                COL_ZTOP: float(z0),
                COL_ZBOT: float(z1),
                COL_ZMID: float(0.5 * (z0 + z1)),
                COL_CODE: int(code),
                "来源": "虚拟",
            })

    virt_boreholes_csv = outdir / "virtual_boreholes.csv"
    pd.DataFrame(out_rows).to_csv(virt_boreholes_csv, index=False, encoding="utf-8-sig")
    log(f"virtual borehole segments: {len(out_rows):,}  saved: {virt_boreholes_csv}")

    # ---- merge real + virtual ----
    real2 = real.copy()
    if "来源" not in real2.columns:
        real2["来源"] = "实测"
    merged = pd.concat([real2, pd.DataFrame(out_rows)], ignore_index=True)
    merged_csv = outdir / "real_plus_virtual.csv"
    merged.to_csv(merged_csv, index=False, encoding="utf-8-sig")
    log(f"merged samples rows: {len(merged):,}  saved: {merged_csv}")

    # ---- IKriging ----
    log("=== Step4: indicator kriging ===")
    sdf = merged.dropna(subset=[COL_X, COL_Y, COL_ZMID, COL_CODE]).copy()
    sdf[COL_CODE] = sdf[COL_CODE].astype(int)

    # 方案2：IK 不包含 fault_code
    sdf = sdf[sdf[COL_CODE] != FAULT_CODE].copy()

    sample_xyz = sdf[[COL_X, COL_Y, COL_ZMID]].to_numpy(float)
    sample_code = sdf[COL_CODE].astype(int).to_numpy()

    classes = np.unique(sample_code).astype(int)
    classes = classes[classes >= 0]  # 过滤掉 -1 等无效值
    classes.sort()

    if len(classes) < 2:
        raise ValueError(f"Not enough lithology classes for IK (after excluding fault_code={FAULT_CODE}): {classes.tolist()}")

    log(f"[IK] lithology classes(K={len(classes)}): {classes.tolist()}  (fault_code={FAULT_CODE} excluded)")

    tree = cKDTree(sample_xyz)

    # read voxels in chunks
    vox_df_iter = pd.read_csv(voxels_csv, chunksize=200000, encoding="utf-8-sig")
    out_ik = outdir / "voxels_ikrig.csv"
    # header
    out_cols = ["X", "Y", "Z", "pred_code"] + [f"p_{c}" for c in classes]
    pd.DataFrame(columns=out_cols).to_csv(out_ik, index=False, encoding="utf-8-sig")

    written = 0
    for chunk in vox_df_iter:
        vxyz = chunk[["X", "Y", "Z"]].to_numpy(float)
        dists, idxs = tree.query(
            vxyz,
            k=int(args.n_neighbors),
            distance_upper_bound=float(args.search_radius),
            workers=-1
        )
        if int(args.n_neighbors) == 1:
            dists = dists[:, None]
            idxs = idxs[:, None]

        pred_codes = np.empty(len(vxyz), dtype=int)
        prob_mat = np.zeros((len(vxyz), len(classes)), dtype=float)

        for i in range(len(vxyz)):
            valid = idxs[i] < sample_xyz.shape[0]
            nei_idx = idxs[i][valid]
            if len(nei_idx) < 3:
                if len(nei_idx) == 0:
                    pred_codes[i] = -1
                    continue
                nc = int(sample_code[nei_idx[0]])
                pred_codes[i] = nc
                j = np.where(classes == nc)[0]
                if len(j) > 0:
                    prob_mat[i, j[0]] = 1.0
                continue

            nei_xyz = sample_xyz[nei_idx]
            w = ok_weights(
                nei_xyz, vxyz[i],
                a=float(args.range_a),
                c0=float(args.nugget_c0),
                c=float(args.sill_c)
            )
            nei_codes = sample_code[nei_idx].astype(int)
            pred, pk = ik_predict(nei_codes, w, classes)
            pred_codes[i] = int(pred)
            prob_mat[i, :] = pk

        out_chunk = pd.DataFrame(vxyz, columns=["X", "Y", "Z"])
        out_chunk["pred_code"] = pred_codes
        for j, c in enumerate(classes):
            out_chunk[f"p_{int(c)}"] = prob_mat[:, j]
        out_chunk.to_csv(out_ik, mode="a", header=False, index=False, encoding="utf-8-sig")
        written += len(out_chunk)
        if written % 500000 == 0:
            log(f"kriging processed: {written:,}")

    log(f"kriging done. saved: {out_ik}")

    # ---- final (optional condsim) ----
    final_csv = outdir / "voxels_final.csv"
    if int(args.use_condsim) == 1 and args.fault_stl:
        log("=== Step5: conditional simulation (vote + hard fault band) ===")

        head = pd.read_csv(out_ik, nrows=5, encoding="utf-8-sig")
        p_cols = [c for c in head.columns if c.startswith("p_") and c[2:].lstrip("-").isdigit()]
        p_cols = sorted(p_cols, key=lambda s: int(s.split("_")[1]))

        if not p_cols:
            raise ValueError("No probability columns found in voxels_ikrig.csv (expected p_<code>...)")

        lith_codes = [int(s.split("_")[1]) for s in p_cols]  # 动态K
        K = len(lith_codes)
        code_to_j = {c: j for j, c in enumerate(lith_codes)}

        log(f"[Step5] lith_codes(K={K}) = {lith_codes} ; fault_code={FAULT_CODE}")

        fault_tree = load_fault_kdtree(args.fault_stl, voxel_hint=float(args.voxel_size))
        rng2 = np.random.default_rng(args.seed)

        # header
        out_cols = ["X", "Y", "Z", "pred_code", "pred_code_final", "vote_count", "dist_fault"]
        pd.DataFrame(columns=out_cols).to_csv(final_csv, index=False, encoding="utf-8-sig")

        processed = 0
        skipped_nonfinite = 0
        for chunk in pd.read_csv(out_ik, chunksize=200000, encoding="utf-8-sig"):
            xyz = chunk[["X", "Y", "Z"]].apply(pd.to_numeric, errors="coerce")
            finite_mask = np.isfinite(xyz.to_numpy(np.float64)).all(axis=1)
            if not bool(finite_mask.all()):
                skipped_nonfinite += int((~finite_mask).sum())
                chunk = chunk.loc[finite_mask].copy()
                xyz = xyz.loc[finite_mask]
            if len(chunk) == 0:
                continue

            pts3 = xyz.to_numpy(np.float64)
            dist_fault, _ = fault_tree.query(pts3, k=1, workers=-1)

            P = chunk[p_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(np.float64).copy()  # (n, K)
            P[P < 0] = 0.0
            s = P.sum(axis=1)
            bad = s <= 1e-12

            # bad rows: 用 pred_code 对应的列做 one-hot（若 pred_code 不在 lith_codes，则均匀分布兜底）
            if np.any(bad):
                pred = pd.to_numeric(chunk["pred_code"], errors="coerce").fillna(-1).to_numpy(int)
                P[bad] = 0.0
                bad_idx = np.where(bad)[0]
                for ii in bad_idx:
                    pc = int(pred[ii])
                    j = code_to_j.get(pc, None)
                    if j is not None:
                        P[ii, j] = 1.0
                    else:
                        P[ii, :] = 1.0 / max(K, 1)

                s = P.sum(axis=1)

            P = P / np.maximum(s[:, None], 1e-12)

            pred_before = pd.to_numeric(chunk["pred_code"], errors="coerce").fillna(-1).to_numpy(int)

            pred_final, win_cnt = simulate_vote_chunk(
                P_lith=P,
                pred_before=pred_before,
                rng=rng2,
                fault_dist=dist_fault,
                fault_thickness=float(args.fault_thickness),
                n_sim=int(args.n_sim),
                vote_threshold=float(args.vote_threshold),
                lith_codes=lith_codes,
                hard_fault_band=True,
                fault_code=FAULT_CODE,
            )

            outc = xyz.copy()
            outc["pred_code"] = pred_before
            outc["pred_code_final"] = pred_final
            outc["vote_count"] = win_cnt
            outc["dist_fault"] = dist_fault
            outc.to_csv(final_csv, mode="a", header=False, index=False, encoding="utf-8-sig")

            processed += len(outc)
            if processed % 500000 == 0:
                log(f"condsim processed: {processed:,}")

        if skipped_nonfinite:
            log(f"[Step5] skipped {skipped_nonfinite:,} rows with non-finite XYZ from voxels_ikrig.csv")

        code_col = "pred_code_final"
        log(f"condsim done. saved: {final_csv}")
    else:
        # no condsim: just use kriging pred_code
        dfk = pd.read_csv(out_ik, usecols=["X", "Y", "Z", "pred_code"], encoding="utf-8-sig")
        dfk.to_csv(final_csv, index=False, encoding="utf-8-sig")
        code_col = "pred_code"
        log(f"skip condsim. saved: {final_csv}")

    fill_invalid_codes_nearest(final_csv, code_col=code_col, invalid_code=-1)

    # ---- export PLY ----
    if int(args.skip_ply) != 1:
        log("=== Step6: export PLY ===")
        out_ply = outdir / "result_B.ply"
        export_ply_pointcloud(final_csv, code_col=code_col, out_ply=out_ply, max_points=int(args.max_points_ply), seed=int(args.seed), fault_code=FAULT_CODE)
    else:
        log("skip PLY export")

    if int(args.skip_color_map) != 1:
        color_codes_df = pd.read_csv(final_csv, usecols=[code_col], encoding="utf-8-sig")
        color_codes = color_codes_df[code_col].dropna().astype(int).tolist()
        save_lithology_color_map_b(color_codes, outdir=outdir, fault_code=FAULT_CODE, borehole_csv=Path(args.borehole_csv))
    else:
        log("skip color map export")

    # ---- 3-view png ----
    if int(args.skip_views) != 1:
        log("=== Step7: export 3-view PNG ===")
        out_xy = outdir / "view_B_XY.png"
        out_xz = outdir / "view_B_XZ.png"
        out_yz = outdir / "view_B_YZ.png"
        project_mode(final_csv, voxel_size=float(args.voxel_size), code_col=code_col, out_xy=out_xy, out_xz=out_xz, out_yz=out_yz)
    else:
        log("skip 3-view PNG export")

    log("DONE Algorithm B.")


if __name__ == "__main__":
    main()
