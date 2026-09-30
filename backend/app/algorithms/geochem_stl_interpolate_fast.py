import argparse
import json
import re
import struct
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree


DEFAULT_ELEMENTS = ["Fe", "Ca", "Si", "Cu", "Pb", "Zn", "As", "Sb", "W", "Sn"]


def normalize_hole_code(value):
    if not isinstance(value, str):
        return None
    match = re.search(r"(\d+(?:-\d+)?)$", value.strip())
    if not match:
        return None
    parts = match.group(1).split("-")
    try:
        parts[0] = str(int(parts[0]))
    except ValueError:
        return None
    return "-".join(parts)


def load_binary_stl_vertices(path):
    with Path(path).open("rb") as handle:
        handle.read(80)
        triangle_count = struct.unpack("<I", handle.read(4))[0]
        raw = np.fromfile(handle, dtype=np.dtype([
            ("normal", "<f4", (3,)),
            ("vertices", "<f4", (3, 3)),
            ("attr", "<u2"),
        ]), count=triangle_count)
    vertices = raw["vertices"].astype(np.float64)
    return vertices


def triangle_z_at_xy(vertices, x, y, eps=1e-10):
    x1 = vertices[:, 0, 0]
    y1 = vertices[:, 0, 1]
    z1 = vertices[:, 0, 2]
    x2 = vertices[:, 1, 0]
    y2 = vertices[:, 1, 1]
    z2 = vertices[:, 1, 2]
    x3 = vertices[:, 2, 0]
    y3 = vertices[:, 2, 1]
    z3 = vertices[:, 2, 2]

    denom = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    valid = np.abs(denom) > eps
    a = np.empty_like(denom)
    b = np.empty_like(denom)
    a.fill(np.nan)
    b.fill(np.nan)
    a[valid] = ((y2[valid] - y3[valid]) * (x - x3[valid]) + (x3[valid] - x2[valid]) * (y - y3[valid])) / denom[valid]
    b[valid] = ((y3[valid] - y1[valid]) * (x - x3[valid]) + (x1[valid] - x3[valid]) * (y - y3[valid])) / denom[valid]
    c = 1.0 - a - b
    inside = valid & (a >= -eps) & (b >= -eps) & (c >= -eps)
    return a[inside] * z1[inside] + b[inside] * z2[inside] + c[inside] * z3[inside]


def unique_sorted(values, tolerance):
    if values.size == 0:
        return values
    values = np.sort(values)
    keep = np.ones(values.shape[0], dtype=bool)
    keep[1:] = np.abs(values[1:] - values[:-1]) > tolerance
    return values[keep]


def build_xy_bins(vertices, bounds_min, cell_size):
    tri_min = vertices.min(axis=1)
    tri_max = vertices.max(axis=1)
    ix0 = np.floor((tri_min[:, 0] - bounds_min[0]) / cell_size).astype(np.int32)
    ix1 = np.floor((tri_max[:, 0] - bounds_min[0]) / cell_size).astype(np.int32)
    iy0 = np.floor((tri_min[:, 1] - bounds_min[1]) / cell_size).astype(np.int32)
    iy1 = np.floor((tri_max[:, 1] - bounds_min[1]) / cell_size).astype(np.int32)
    bins = {}
    for index in range(vertices.shape[0]):
        for ix in range(ix0[index], ix1[index] + 1):
            for iy in range(iy0[index], iy1[index] + 1):
                bins.setdefault((int(ix), int(iy)), []).append(index)
    return bins


def generate_voxel_csv(stl_vertices, output_path, cell_size, z_cell_size, max_voxels, chunk_rows=200000):
    points = stl_vertices.reshape(-1, 3)
    bounds_min = points.min(axis=0)
    bounds_max = points.max(axis=0)
    nx = int(np.ceil((bounds_max[0] - bounds_min[0]) / cell_size))
    ny = int(np.ceil((bounds_max[1] - bounds_min[1]) / cell_size))
    nz = int(np.ceil((bounds_max[2] - bounds_min[2]) / z_cell_size))
    bins = build_xy_bins(stl_vertices, bounds_min, cell_size)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    inside_columns = 0
    rows = []
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("x,y,z\n")
        for ix in range(nx):
            x = bounds_min[0] + (ix + 0.5) * cell_size
            for iy in range(ny):
                tri_indexes = bins.get((ix, iy))
                if not tri_indexes:
                    continue
                y = bounds_min[1] + (iy + 0.5) * cell_size
                crossings = triangle_z_at_xy(stl_vertices[np.array(tri_indexes, dtype=np.int32)], x, y)
                crossings = unique_sorted(crossings, max(z_cell_size * 0.02, 0.01))
                if crossings.size < 2:
                    continue
                inside_columns += 1
                for start, end in zip(crossings[0::2], crossings[1::2]):
                    if end <= start:
                        continue
                    first = max(0, int(np.ceil((start - bounds_min[2]) / z_cell_size - 0.5)))
                    last = min(nz - 1, int(np.floor((end - bounds_min[2]) / z_cell_size - 0.5)))
                    if last < first:
                        continue
                    z_values = bounds_min[2] + (np.arange(first, last + 1, dtype=np.float64) + 0.5) * z_cell_size
                    valid = z_values[(z_values >= start) & (z_values <= end)]
                    for z in valid:
                        rows.append(f"{x:.6f},{y:.6f},{z:.6f}\n")
                    total += int(valid.size)
                    if total > max_voxels:
                        raise RuntimeError(f"Voxel count exceeded {max_voxels}.")
                    if len(rows) >= chunk_rows:
                        handle.writelines(rows)
                        rows.clear()
        if rows:
            handle.writelines(rows)
    return {
        "bounds_min": bounds_min.tolist(),
        "bounds_max": bounds_max.tolist(),
        "triangle_count": int(stl_vertices.shape[0]),
        "nx": nx,
        "ny": ny,
        "nz": nz,
        "inside_columns": inside_columns,
        "voxel_count": total,
    }


def generate_voxel_csv_trimesh_fill(stl_path, output_path, cell_size, max_voxels, chunk_rows=200000):
    import trimesh

    mesh = trimesh.load(stl_path)
    if isinstance(mesh, trimesh.Scene):
        mesh = trimesh.util.concatenate(tuple(mesh.geometry.values()))
    voxels = mesh.voxelized(pitch=cell_size).fill()
    points = voxels.points.astype(np.float64)
    if len(points) > max_voxels:
        raise RuntimeError(f"Voxel count exceeded {max_voxels}.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("x,y,z\n")
        for start in range(0, len(points), chunk_rows):
            chunk = points[start : start + chunk_rows]
            lines = (f"{x:.6f},{y:.6f},{z:.6f}\n" for x, y, z in chunk)
            handle.writelines(lines)

    return {
        "bounds_min": points.min(axis=0).tolist(),
        "bounds_max": points.max(axis=0).tolist(),
        "triangle_count": int(len(mesh.faces)),
        "is_watertight": bool(mesh.is_watertight),
        "voxel_count": int(len(points)),
        "voxelizer": "trimesh.voxelized(pitch).fill()",
    }


def load_collar_depths(collar_csv):
    if not collar_csv or not Path(collar_csv).exists():
        return {}
    collars = pd.read_csv(collar_csv, encoding="utf-8-sig")
    depths = {}
    for _, row in collars.iterrows():
        hole_id = str(row.get("hole_id", "")).strip()
        depth = pd.to_numeric(row.get("hole_depth"), errors="coerce")
        if not hole_id or pd.isna(depth):
            continue
        depths[hole_id] = float(depth)
        code = normalize_hole_code(hole_id)
        if code:
            depths[f"__code__:{code}"] = float(depth)
    return depths


def lookup_hole_depth(depths, hole_id):
    if hole_id in depths:
        return depths[hole_id]
    code = normalize_hole_code(hole_id)
    if code and f"__code__:{code}" in depths:
        return depths[f"__code__:{code}"]
    return np.nan


def load_samples(assay_csv, elements, collar_csv=None, max_depth_tolerance=5.0, zero_fill_elements=None):
    zero_fill_elements = set(zero_fill_elements or [])
    df = pd.read_csv(assay_csv, encoding="utf-8-sig")
    df["from_depth"] = pd.to_numeric(df["from_depth"], errors="coerce")
    df["to_depth"] = pd.to_numeric(df["to_depth"], errors="coerce")
    df["collar_x"] = pd.to_numeric(df["collar_x"], errors="coerce")
    df["collar_y"] = pd.to_numeric(df["collar_y"], errors="coerce")
    df["collar_z"] = pd.to_numeric(df["collar_z"], errors="coerce")
    for element in elements:
        if element in df.columns:
            df[element] = pd.to_numeric(df[element], errors="coerce")
    zero_fill_counts = {}
    for element in zero_fill_elements:
        if element in df.columns:
            missing = int(df[element].isna().sum())
            df[element] = df[element].fillna(0.0)
            zero_fill_counts[element] = missing
    valid = (
        df["from_depth"].notna()
        & df["to_depth"].notna()
        & (df["to_depth"] > df["from_depth"])
        & df["collar_x"].notna()
        & df["collar_y"].notna()
        & df["collar_z"].notna()
    )
    bad_or_missing = int((~valid).sum())
    depths = load_collar_depths(collar_csv)
    beyond_depth = 0
    if depths:
        hole_depths = df["hole_id"].map(lambda value: lookup_hole_depth(depths, str(value).strip()))
        depth_valid = hole_depths.isna() | (df["to_depth"] <= hole_depths + max_depth_tolerance)
        beyond_depth = int((valid & ~depth_valid).sum())
        valid = valid & depth_valid
    samples = df.loc[valid].copy()
    for axis in ("x", "y", "z"):
        spatial_column = f"sample_{axis}"
        if spatial_column in samples:
            spatial = pd.to_numeric(samples[spatial_column], errors="coerce")
        else:
            spatial = pd.Series(np.nan, index=samples.index)
        fallback = (
            samples[f"collar_{axis}"]
            if axis in {"x", "y"}
            else samples["collar_z"] - (samples["from_depth"] + samples["to_depth"]) / 2.0
        )
        samples[axis] = spatial.fillna(fallback)
    samples = samples[["hole_id", "from_depth", "to_depth", "x", "y", "z"] + elements]
    return samples, {
        "rows": int(len(df)),
        "kept": int(len(samples)),
        "bad_or_missing_interval": bad_or_missing,
        "beyond_collar_depth": beyond_depth,
        "max_depth_tolerance": max_depth_tolerance,
        "zero_fill_elements": sorted(zero_fill_elements),
        "zero_fill_missing_value_counts": zero_fill_counts,
    }


def interpolate_csv(
    voxel_csv,
    samples,
    elements,
    output_csv,
    nearest=12,
    power=2.0,
    chunk_size=200000,
    transform="log",
    search_radius=1200.0,
):
    output_csv = Path(output_csv)
    first = True
    stats = {"chunks": 0, "rows": 0}
    for voxels in pd.read_csv(voxel_csv, chunksize=chunk_size):
        points = voxels[["x", "y", "z"]].to_numpy(dtype=np.float64)
        result = voxels.copy()
        for element in elements:
            element_samples = samples[samples[element].notna()].copy()
            if transform == "log":
                element_samples = element_samples.loc[element_samples[element] > 0]
            if element_samples.empty:
                result[element] = np.nan
                result[f"{element}_neighbors"] = 0
                result[f"{element}_supporting_holes"] = 0
                result[f"{element}_nearest_distance"] = np.nan
                result["confidence_level"] = "no_data"
                continue
            sample_points = element_samples[["x", "y", "z"]].to_numpy(dtype=np.float64)
            values = element_samples[element].to_numpy(dtype=np.float64)
            transformed_values = np.log(values) if transform == "log" else values
            hole_codes, _ = pd.factorize(element_samples["hole_id"].astype(str), sort=True)
            k = min(nearest, sample_points.shape[0])
            tree = cKDTree(sample_points)
            distances, indexes = tree.query(
                points,
                k=k,
                workers=-1,
                distance_upper_bound=float(search_radius),
            )
            distances = np.atleast_2d(distances)
            indexes = np.atleast_2d(indexes)
            if distances.shape[0] != points.shape[0]:
                distances = distances.T
                indexes = indexes.T
            available = np.isfinite(distances) & (indexes < len(element_samples))
            safe_indexes = np.where(available, indexes, 0)
            exact = available & (distances <= 1e-12)
            weights = np.zeros_like(distances, dtype=np.float64)
            non_exact = available & ~exact
            weights[non_exact] = 1.0 / np.power(distances[non_exact], power)
            neighbor_values = transformed_values[safe_indexes]
            weighted = np.sum(np.where(np.isinf(weights), 0.0, weights) * neighbor_values, axis=1)
            totals = np.sum(np.where(np.isinf(weights), 0.0, weights), axis=1)
            estimated = np.divide(
                weighted,
                totals,
                out=np.full(points.shape[0], np.nan, dtype=float),
                where=totals > 0,
            )
            exact_rows = exact.any(axis=1)
            if exact_rows.any():
                exact_col = np.argmax(exact[exact_rows], axis=1)
                estimated[exact_rows] = neighbor_values[exact_rows, exact_col]
            if transform == "log":
                estimated = np.exp(estimated)
            neighbour_counts = available.sum(axis=1).astype(int)
            nearest_distance = np.where(
                available.any(axis=1),
                np.min(np.where(available, distances, np.inf), axis=1),
                np.nan,
            )
            neighbour_holes = hole_codes[safe_indexes]
            supporting_holes = np.zeros(points.shape[0], dtype=int)
            # There are usually only a handful of chemical drillholes.  Counting
            # support hole-by-hole keeps this vectorised for million-voxel jobs;
            # the previous Python loop made the 20 m baseline needlessly slow.
            for hole_code in np.unique(hole_codes):
                supporting_holes += np.any(
                    available & (neighbour_holes == hole_code),
                    axis=1,
                ).astype(int)
            confidence = np.full(points.shape[0], "low", dtype=object)
            confidence[neighbour_counts == 0] = "no_data"
            confidence[
                (supporting_holes >= 2)
                & (nearest_distance <= float(search_radius) * 2.0 / 3.0)
            ] = "medium"
            confidence[
                (supporting_holes >= 3)
                & (nearest_distance <= float(search_radius) / 3.0)
            ] = "high"
            result[element] = estimated
            result[f"{element}_neighbors"] = neighbour_counts
            result[f"{element}_supporting_holes"] = supporting_holes
            result[f"{element}_nearest_distance"] = nearest_distance
            result["confidence_level"] = confidence
        result.to_csv(output_csv, mode="w" if first else "a", header=first, index=False, encoding="utf-8-sig")
        first = False
        stats["chunks"] += 1
        stats["rows"] += int(len(result))
    return stats


def parse_args():
    parser = argparse.ArgumentParser(description="Fast STL-constrained geochemical interpolation with numpy/scipy.")
    parser.add_argument("--assay-csv", default="outputs/selected_assays/assay_intervals_merged.csv")
    parser.add_argument("--collar-csv", default="outputs/selected_collar_from_geo_admin.csv")
    parser.add_argument("--stl", default="地质体.stl")
    parser.add_argument("--output-dir", default="outputs/geochem_model_20m")
    parser.add_argument("--elements", default=",".join(DEFAULT_ELEMENTS))
    parser.add_argument("--cell-size", type=float, default=20.0)
    parser.add_argument("--z-cell-size", type=float, default=20.0)
    parser.add_argument("--nearest", type=int, default=12)
    parser.add_argument("--power", type=float, default=2.0)
    parser.add_argument("--transform", choices=["raw", "log"], default="log")
    parser.add_argument("--search-radius", type=float, default=1200.0)
    parser.add_argument("--max-voxels", type=int, default=5000000)
    parser.add_argument("--chunk-size", type=int, default=200000)
    parser.add_argument("--voxelizer", choices=["ray", "trimesh-fill"], default="ray")
    parser.add_argument(
        "--zero-fill-elements",
        default="",
        help="Comma-separated elements whose missing assay values should be treated as 0 before interpolation.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    elements = [item.strip() for item in args.elements.split(",") if item.strip()]
    zero_fill_elements = [item.strip() for item in args.zero_fill_elements.split(",") if item.strip()]

    voxel_points_csv = output_dir / "voxel_points_20m.csv"
    if args.voxelizer == "trimesh-fill":
        voxel_info = generate_voxel_csv_trimesh_fill(
            args.stl,
            voxel_points_csv,
            args.cell_size,
            args.max_voxels,
            args.chunk_size,
        )
    else:
        stl_vertices = load_binary_stl_vertices(args.stl)
        voxel_info = generate_voxel_csv(
            stl_vertices,
            voxel_points_csv,
            args.cell_size,
            args.z_cell_size,
            args.max_voxels,
            args.chunk_size,
        )
    samples, assay_info = load_samples(args.assay_csv, elements, args.collar_csv, zero_fill_elements=zero_fill_elements)
    used_samples_csv = output_dir / "used_assay_points.csv"
    samples.to_csv(used_samples_csv, index=False, encoding="utf-8-sig")
    output_csv = output_dir / "geochemical_voxels_20m.csv"
    interpolation_info = interpolate_csv(
        voxel_points_csv,
        samples,
        elements,
        output_csv,
        args.nearest,
        args.power,
        args.chunk_size,
        args.transform,
        args.search_radius,
    )
    summary = {
        "method": "STL-constrained 20m voxelization plus cKDTree IDW interpolation",
        "assumptions": [
            "Drillholes are treated as vertical because no survey/trajectory file is available.",
            "The STL is treated as a closed geological solid.",
            "CSV collar_x/collar_y/collar_z are used as sample collar coordinates.",
        ],
        "inputs": {
            "assay_csv": str(Path(args.assay_csv).resolve()),
            "collar_csv": str(Path(args.collar_csv).resolve()) if args.collar_csv else None,
            "stl": str(Path(args.stl).resolve()),
            "elements": elements,
        },
        "parameters": {
            "cell_size": args.cell_size,
            "z_cell_size": args.z_cell_size,
            "nearest": args.nearest,
            "power": args.power,
            "transform": args.transform,
            "search_radius": args.search_radius,
            "chunk_size": args.chunk_size,
            "voxelizer": args.voxelizer,
            "zero_fill_elements": zero_fill_elements,
        },
        "assays": assay_info,
        "voxels": voxel_info,
        "interpolation": interpolation_info,
        "outputs": {
            "voxel_points_csv": str(voxel_points_csv.resolve()),
            "geochemical_voxels_csv": str(output_csv.resolve()),
            "used_assay_points_csv": str(used_samples_csv.resolve()),
        },
    }
    (output_dir / "model_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Samples used: {len(samples)}")
    print(f"Voxels generated: {voxel_info['voxel_count']}")
    print(f"Output: {output_csv.resolve()}")


if __name__ == "__main__":
    main()
