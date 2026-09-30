import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from scipy.stats import spearmanr

try:
    # Package import used by the API service and tests.
    from .geochem_stl_interpolate_fast import load_samples
except ImportError:
    # Direct-script execution used by the reconstruction worker.
    from geochem_stl_interpolate_fast import load_samples


def load_lithology_names(path):
    if not path or not Path(path).exists():
        return {}
    frame = pd.read_csv(path, encoding="utf-8-sig")
    code_col = "岩性编码"
    name_col = "岩性名称"
    if code_col not in frame.columns or name_col not in frame.columns:
        return {}
    return {
        int(row[code_col]): str(row[name_col]).strip()
        for _, row in frame.iterrows()
        if pd.notna(row[code_col])
    }


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def load_lithology_group_mapping(
    config_path,
    lithology_map_path,
    source_constraint_job_id,
    fault_code=None,
):
    """Load one confirmed, source-bound mapping without guessing by name."""

    config_path = Path(config_path)
    lithology_map_path = Path(lithology_map_path)
    if not config_path.exists():
        raise FileNotFoundError(f"岩性归并配置不存在: {config_path}")
    if not lithology_map_path.exists():
        raise FileNotFoundError(f"岩性映射文件不存在: {lithology_map_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("status") != "confirmed":
        raise ValueError("岩性归并配置未确认，禁止启用")
    if str(config.get("source_constraint_job_id") or "") != str(source_constraint_job_id):
        raise ValueError("岩性归并配置绑定的算法B任务不匹配")
    actual_map_sha256 = _file_sha256(lithology_map_path)
    expected_map_sha256 = str(config.get("source_lithology_map_sha256") or "").upper()
    if not expected_map_sha256 or expected_map_sha256 != actual_map_sha256:
        raise ValueError("岩性归并配置绑定的 lithology_map.csv 哈希不匹配")
    if config.get("unmapped_policy") != "keep_original":
        raise ValueError("岩性归并配置只允许 unmapped_policy=keep_original")
    if config.get("fault_policy") != "never_merge":
        raise ValueError("岩性归并配置只允许 fault_policy=never_merge")

    frame = pd.read_csv(lithology_map_path, encoding="utf-8-sig")
    required = {"岩性编码", "岩性名称"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"岩性映射缺少字段: {', '.join(sorted(missing))}")
    frame = frame.dropna(subset=["岩性编码", "岩性名称"]).copy()
    frame["岩性编码"] = pd.to_numeric(frame["岩性编码"], errors="raise").astype(int)
    frame["岩性名称"] = frame["岩性名称"].astype(str).str.strip()
    duplicate_names = frame.loc[frame["岩性名称"].duplicated(keep=False), "岩性名称"].unique()
    if len(duplicate_names):
        raise ValueError("lithology_map.csv 存在重复岩性名称，不能做精确映射")
    name_to_code = dict(zip(frame["岩性名称"], frame["岩性编码"]))

    code_to_group = {}
    group_names = {}
    claimed_names = set()
    for group in config.get("groups") or []:
        group_id = int(group["group_id"])
        group_name = str(group.get("group_name") or "").strip()
        if group_id >= 0:
            raise ValueError("归并 group_id 必须为负数，避免覆盖原始岩性编码")
        if not group_name:
            raise ValueError("归并组名称不能为空")
        if group_id in group_names:
            raise ValueError(f"归并 group_id 重复: {group_id}")
        members = [str(name).strip() for name in group.get("member_names") or []]
        if not members:
            raise ValueError(f"归并组 {group_name} 没有成员")
        for member_name in members:
            if member_name in claimed_names:
                raise ValueError(f"岩性 {member_name} 被配置到多个归并组")
            if member_name not in name_to_code:
                raise ValueError(f"归并成员不在当前 lithology_map.csv 中: {member_name}")
            original_code = int(name_to_code[member_name])
            if fault_code is not None and original_code == int(fault_code):
                raise ValueError("断层编码不能进入普通岩性归并组")
            claimed_names.add(member_name)
            code_to_group[original_code] = group_id
        group_names[group_id] = group_name

    metadata = {
        "mapping_version": str(config.get("mapping_version") or ""),
        "mapping_sha256": _file_sha256(config_path),
        "source_constraint_job_id": str(source_constraint_job_id),
        "source_lithology_map_sha256": actual_map_sha256,
    }
    return code_to_group, group_names, metadata


def apply_lithology_groups(samples, code_to_group, lithology_names, group_names):
    """Add auditable effective groups while preserving every original code/name."""

    result = samples.copy()
    original = pd.to_numeric(result["lithology_code"], errors="coerce").astype("Int64")
    result["original_lithology_code"] = original
    result["original_lithology_name"] = original.map(lithology_names).fillna("未知岩性")
    effective = original.map(code_to_group).fillna(original).astype("Int64")
    result["geochem_group_code"] = effective
    result["geochem_group_name"] = effective.map(group_names).fillna(
        result["original_lithology_name"]
    )
    return result


def assign_sample_lithology(samples, layers_csv):
    layers = pd.read_csv(layers_csv, encoding="utf-8-sig")
    required = {"钻孔ID", "岩性段顶深Z(m)", "岩性段底深Z(m)", "岩性段中点Z(m)", "岩性编码"}
    missing = required - set(layers.columns)
    if missing:
        raise ValueError(f"岩性分段缺少字段: {', '.join(sorted(missing))}")

    for col in ["岩性段顶深Z(m)", "岩性段底深Z(m)", "岩性段中点Z(m)", "岩性编码"]:
        layers[col] = pd.to_numeric(layers[col], errors="coerce")
    layers = layers.dropna(subset=["钻孔ID", "岩性段顶深Z(m)", "岩性段底深Z(m)", "岩性编码"])
    by_hole = {str(hole).strip(): group.copy() for hole, group in layers.groupby("钻孔ID")}

    codes = []
    methods = []
    for row in samples.itertuples(index=False):
        hole = str(getattr(row, "hole_id")).strip()
        z = float(getattr(row, "z"))
        group = by_hole.get(hole)
        if group is None or group.empty:
            codes.append(np.nan)
            methods.append("unmatched_hole")
            continue
        top = group["岩性段顶深Z(m)"].to_numpy(dtype=float)
        bottom = group["岩性段底深Z(m)"].to_numpy(dtype=float)
        inside = (z <= np.maximum(top, bottom) + 1e-6) & (z >= np.minimum(top, bottom) - 1e-6)
        if inside.any():
            candidates = group.loc[inside]
            mid = pd.to_numeric(candidates["岩性段中点Z(m)"], errors="coerce")
            if mid.notna().any():
                index = (mid - z).abs().idxmin()
            else:
                index = candidates.index[0]
            codes.append(int(group.loc[index, "岩性编码"]))
            methods.append("interval_match")
        else:
            codes.append(np.nan)
            methods.append("outside_layers")

    result = samples.copy()
    result["lithology_code"] = pd.Series(codes, dtype="Float64")
    result["lithology_match_method"] = methods
    return result


def _distinct_count(values, valid):
    marked = np.where(valid, values, -1)
    ordered = np.sort(marked, axis=1)
    count = (ordered[:, 0] >= 0).astype(np.int16)
    if ordered.shape[1] > 1:
        count += np.sum((ordered[:, 1:] >= 0) & (ordered[:, 1:] != ordered[:, :-1]), axis=1).astype(np.int16)
    return count


def _select_balanced_neighbours(
    distances,
    indexes,
    source,
    target_group_code,
    nearest,
    max_samples_per_hole,
    preferred_distinct_holes,
    compatible_lithology_weight,
):
    """Select a deterministic multi-hole neighbourhood in ellipsoid space."""

    distances = np.atleast_2d(np.asarray(distances, dtype=np.float64))
    indexes = np.atleast_2d(np.asarray(indexes, dtype=np.int64))
    row_count = distances.shape[0]
    selected_distances = np.full((row_count, int(nearest)), np.inf, dtype=np.float64)
    selected_indexes = np.full((row_count, int(nearest)), -1, dtype=np.int64)
    group_weight = max(float(compatible_lithology_weight), 1.0e-6)
    for row in range(row_count):
        candidates = []
        for distance, index in zip(distances[row], indexes[row]):
            if not np.isfinite(distance) or index < 0 or index >= len(source["values"]):
                continue
            same_group = int(source["group_codes"][index]) == int(target_group_code)
            adjusted = float(distance) / (1.0 if same_group else group_weight)
            candidates.append((adjusted, float(distance), int(index)))
        candidates.sort(key=lambda item: (item[0], item[2]))
        chosen: list[tuple[float, int]] = []
        chosen_indexes: set[int] = set()
        hole_counts: dict[int, int] = {}
        chosen_holes: set[int] = set()

        # First give nearby independent holes one representative each.  This
        # prevents dense vertical sampling in one hole from creating columns.
        for _, distance, index in candidates:
            hole = int(source["holes"][index])
            if hole in chosen_holes:
                continue
            chosen.append((distance, index))
            chosen_indexes.add(index)
            chosen_holes.add(hole)
            hole_counts[hole] = 1
            if len(chosen_holes) >= int(preferred_distinct_holes) or len(chosen) >= int(nearest):
                break

        for _, distance, index in candidates:
            if len(chosen) >= int(nearest):
                break
            if index in chosen_indexes:
                continue
            hole = int(source["holes"][index])
            if hole_counts.get(hole, 0) >= int(max_samples_per_hole):
                continue
            chosen.append((distance, index))
            chosen_indexes.add(index)
            chosen_holes.add(hole)
            hole_counts[hole] = hole_counts.get(hole, 0) + 1
        for column, (distance, index) in enumerate(chosen):
            selected_distances[row, column] = distance
            selected_indexes[row, column] = index
    return selected_distances, selected_indexes


def _build_hole_trees(transformed_points, holes):
    """Build one small search tree per drill hole for vectorised balancing."""

    transformed_points = np.asarray(transformed_points, dtype=np.float64)
    holes = np.asarray(holes, dtype=np.int32)
    trees = []
    for hole in np.unique(holes):
        global_indexes = np.flatnonzero(holes == hole)
        if len(global_indexes):
            trees.append((global_indexes, cKDTree(transformed_points[global_indexes])))
    return trees


def _query_hole_balanced_neighbours(
    query_points,
    source,
    target_group_code,
    nearest,
    max_samples_per_hole,
    compatible_lithology_weight,
):
    """Query per hole and rank candidates without a per-voxel Python loop."""

    candidate_distances = []
    candidate_indexes = []
    for global_indexes, tree in source["hole_trees"]:
        count = min(int(max_samples_per_hole), len(global_indexes))
        distances, local_indexes = tree.query(query_points, k=count, workers=-1)
        distances = np.asarray(distances, dtype=np.float64)
        local_indexes = np.asarray(local_indexes, dtype=np.int64)
        if count == 1:
            distances = distances[:, None]
            local_indexes = local_indexes[:, None]
        candidate_distances.append(distances)
        candidate_indexes.append(global_indexes[local_indexes])

    if not candidate_distances:
        row_count = len(query_points)
        return (
            np.full((row_count, int(nearest)), np.inf, dtype=np.float64),
            np.full((row_count, int(nearest)), -1, dtype=np.int64),
        )

    distances = np.concatenate(candidate_distances, axis=1)
    indexes = np.concatenate(candidate_indexes, axis=1)
    same_group = source["group_codes"][indexes] == int(target_group_code)
    group_weight = max(float(compatible_lithology_weight), 1.0e-6)
    adjusted = distances / np.where(same_group, 1.0, group_weight)
    width = min(int(nearest), adjusted.shape[1])
    order = np.argsort(adjusted, axis=1, kind="stable")[:, :width]
    selected_distances = np.take_along_axis(distances, order, axis=1)
    selected_indexes = np.take_along_axis(indexes, order, axis=1)
    if width < int(nearest):
        missing = int(nearest) - width
        selected_distances = np.pad(
            selected_distances, ((0, 0), (0, missing)), constant_values=np.inf
        )
        selected_indexes = np.pad(
            selected_indexes, ((0, 0), (0, missing)), constant_values=-1
        )
    return selected_distances, selected_indexes


def _idw_estimates(neighbour_distances, neighbour_indexes, source, power):
    distances = np.asarray(neighbour_distances, dtype=np.float64)
    indexes = np.asarray(neighbour_indexes, dtype=np.int64)
    if distances.ndim == 1:
        distances = distances[:, None]
        indexes = indexes[:, None]
    valid = np.isfinite(distances) & (indexes >= 0) & (indexes < len(source["values"]))
    safe_indexes = np.where(valid, indexes, 0)
    values = source["residual_values"][safe_indexes]
    exact = valid & (distances <= 1.0e-12)
    weights = np.where(
        valid & ~exact,
        1.0 / np.maximum(distances, 1.0e-12) ** float(power),
        0.0,
    )
    totals = weights.sum(axis=1)
    weights = np.divide(
        weights,
        totals[:, None],
        out=np.zeros_like(weights),
        where=totals[:, None] > 0,
    )
    estimates = (weights * values).sum(axis=1)
    estimates[totals <= 0] = 0.0
    exact_rows = exact.any(axis=1)
    if exact_rows.any():
        exact_columns = np.argmax(exact[exact_rows], axis=1)
        weights[exact_rows] = 0.0
        weights[exact_rows, exact_columns] = 1.0
        estimates[exact_rows] = values[exact_rows, exact_columns]
    return estimates, valid, safe_indexes, weights


def _structural_axes(azimuth_deg, plunge_deg):
    azimuth = np.deg2rad(float(azimuth_deg))
    plunge = np.deg2rad(float(plunge_deg))
    major = np.array(
        [np.sin(azimuth) * np.cos(plunge), np.cos(azimuth) * np.cos(plunge), -np.sin(plunge)],
        dtype=np.float64,
    )
    intermediate = np.array([np.cos(azimuth), -np.sin(azimuth), 0.0], dtype=np.float64)
    minor = np.cross(major, intermediate)
    axes = np.vstack([major, intermediate, minor])
    axes /= np.linalg.norm(axes, axis=1)[:, None]
    return axes


def derive_anisotropy_transform(samples, radii, azimuth_deg=80.1, plunge_deg=7.7):
    """Build the user-visible geological search ellipsoid, never a PCA proxy."""

    points = samples[["x", "y", "z"]].to_numpy(dtype=np.float64)
    center = np.nanmedian(points, axis=0)
    requested = np.asarray(radii, dtype=np.float64)
    if requested.shape != (3,) or np.any(~np.isfinite(requested)):
        raise ValueError("椭球三个轴半径必须是有限数")
    defaults = np.array([2400.0, 1200.0, 600.0], dtype=np.float64)
    resolved = np.where(requested > 0, requested, defaults)
    if np.any(resolved <= 0):
        raise ValueError("椭球三个轴半径必须大于零")
    return center, _structural_axes(azimuth_deg, plunge_deg), resolved


def _residual_fade(nearest_distance, full_radius, fade_radius):
    nearest_distance = np.asarray(nearest_distance, dtype=np.float64)
    result = np.ones_like(nearest_distance)
    result[~np.isfinite(nearest_distance)] = 0.0
    result[nearest_distance >= float(fade_radius)] = 0.0
    middle = (
        np.isfinite(nearest_distance)
        & (nearest_distance > float(full_radius))
        & (nearest_distance < float(fade_radius))
    )
    position = (nearest_distance[middle] - float(full_radius)) / max(
        float(fade_radius) - float(full_radius), 1.0e-9
    )
    result[middle] = 1.0 - (3.0 * position**2 - 2.0 * position**3)
    return result


def composite_samples(samples, element, interval=15.0):
    """Composite dense down-hole intervals to one robust sample per support."""

    valid = samples.copy()
    valid[element] = pd.to_numeric(valid[element], errors="coerce")
    valid = valid.loc[valid[element] > 0].copy()
    midpoint = (
        pd.to_numeric(valid["from_depth"], errors="coerce")
        + pd.to_numeric(valid["to_depth"], errors="coerce")
    ) / 2.0
    valid["_composite_bin"] = np.floor(midpoint / float(interval)).astype("Int64")
    rows = []
    group_columns = ["hole_id", "geochem_group_code", "_composite_bin"]
    for _, group in valid.dropna(subset=["_composite_bin"]).groupby(group_columns, sort=False):
        row = group.iloc[0].copy()
        row["from_depth"] = float(pd.to_numeric(group["from_depth"], errors="coerce").min())
        row["to_depth"] = float(pd.to_numeric(group["to_depth"], errors="coerce").max())
        for coordinate in ("x", "y", "z"):
            row[coordinate] = float(pd.to_numeric(group[coordinate], errors="coerce").median())
        row[element] = float(np.exp(np.log(group[element].to_numpy(dtype=float)).mean()))
        row["composite_source_count"] = int(len(group))
        rows.append(row.drop(labels=["_composite_bin"]))
    return pd.DataFrame(rows).reset_index(drop=True)


def transform_anisotropic(points, center, axes, radii):
    return ((np.asarray(points, dtype=np.float64) - center) @ axes.T) / radii


def interpolate_constrained_csv(
    voxel_csv,
    samples,
    element,
    output_csv,
    lithology_names=None,
    nearest=48,
    power=1.6,
    search_radius=None,
    fault_code=None,
    fault_thickness=30.0,
    chunk_size=2048,
    transform="log",
    statistical_threshold=None,
    boundary_grade=None,
    industrial_grade=None,
    validation_quality="medium",
    lithology_group_mapping=None,
    group_names=None,
    mapping_metadata=None,
    anisotropy_radii=(2400.0, 1200.0, 600.0),
    azimuth=80.1,
    plunge=7.7,
    max_samples_per_hole=8,
    preferred_distinct_holes=6,
    compatible_lithology_weight=0.70,
    residual_full_radius=0.65,
    residual_fade_radius=1.5,
):
    lithology_names = lithology_names or {}
    lithology_group_mapping = lithology_group_mapping or {}
    group_names = group_names or {}
    mapping_metadata = mapping_metadata or {}
    valid_samples = samples.dropna(subset=[element, "lithology_code", "x", "y", "z"]).copy()
    valid_samples[element] = pd.to_numeric(valid_samples[element], errors="coerce")
    valid_samples = valid_samples.loc[valid_samples[element] > 0].copy()
    valid_samples["lithology_code"] = valid_samples["lithology_code"].astype(int)
    if "geochem_group_code" not in valid_samples.columns:
        valid_samples = apply_lithology_groups(
            valid_samples, lithology_group_mapping, lithology_names, group_names
        )
    valid_samples["geochem_group_code"] = valid_samples["geochem_group_code"].astype(int)
    valid_samples["hole_code"] = pd.factorize(valid_samples["hole_id"].astype(str))[0]

    anisotropy_center, anisotropy_axes, anisotropy_radii = derive_anisotropy_transform(
        valid_samples, anisotropy_radii, azimuth, plunge
    )
    all_points = valid_samples[["x", "y", "z"]].to_numpy(dtype=np.float64)
    all_transformed_points = transform_anisotropic(
        all_points, anisotropy_center, anisotropy_axes, anisotropy_radii
    )
    all_raw_values = valid_samples[element].to_numpy(dtype=np.float64)
    all_transformed_values = np.log(all_raw_values) if transform == "log" else all_raw_values
    group_trends = {
        int(code): float(np.nanmedian(values))
        for code, values in valid_samples.assign(_transformed=all_transformed_values)
        .groupby("geochem_group_code")["_transformed"]
    }
    global_trend = float(np.nanmedian(all_transformed_values))
    global_group = {
        "tree": cKDTree(all_transformed_points),
        "hole_trees": _build_hole_trees(
            all_transformed_points, valid_samples["hole_code"].to_numpy(dtype=np.int32)
        ),
        "points": all_points,
        "transformed_points": all_transformed_points,
        "values": all_raw_values,
        "transformed_values": all_transformed_values,
        # Use one continuous regional baseline. Lithology remains a soft
        # neighbour weight and a hard spatial/fault domain, but must not create
        # abrupt colour walls where Algorithm-B lithology codes change.
        "residual_values": all_transformed_values - global_trend,
        "holes": valid_samples["hole_code"].to_numpy(dtype=np.int32),
        "group_codes": valid_samples["geochem_group_code"].to_numpy(dtype=np.int32),
        "indicator_statistical": (
            (all_raw_values >= float(statistical_threshold)).astype(float)
            if statistical_threshold is not None else None
        ),
        "indicator_boundary": (
            (all_raw_values >= float(boundary_grade)).astype(float)
            if boundary_grade is not None else None
        ),
        "indicator_industrial": (
            (all_raw_values >= float(industrial_grade)).astype(float)
            if industrial_grade is not None else None
        ),
    }
    groups = {}
    for code, group in valid_samples.groupby("geochem_group_code"):
        points = group[["x", "y", "z"]].to_numpy(dtype=np.float64)
        transformed_points = transform_anisotropic(
            points, anisotropy_center, anisotropy_axes, anisotropy_radii
        )
        raw_values = group[element].to_numpy(dtype=np.float64)
        transformed_values = np.log(raw_values) if transform == "log" else raw_values
        trend = group_trends.get(int(code), global_trend)
        groups[int(code)] = {
            "tree": cKDTree(transformed_points),
            "hole_trees": _build_hole_trees(
                transformed_points, group["hole_code"].to_numpy(dtype=np.int32)
            ),
            "points": points,
            "transformed_points": transformed_points,
            "values": raw_values,
            "transformed_values": transformed_values,
            "residual_values": transformed_values - trend,
            "holes": group["hole_code"].to_numpy(dtype=np.int32),
            "group_codes": np.full(len(group), int(code), dtype=np.int32),
            "indicator_statistical": (
                (raw_values >= float(statistical_threshold)).astype(float)
                if statistical_threshold is not None else None
            ),
            "indicator_boundary": (
                (raw_values >= float(boundary_grade)).astype(float)
                if boundary_grade is not None else None
            ),
            "indicator_industrial": (
                (raw_values >= float(industrial_grade)).astype(float)
                if industrial_grade is not None else None
            ),
        }

    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    first = True
    stats = {
        "rows": 0,
        "interpolated": 0,
        "fault_masked": 0,
        "no_lithology_samples": 0,
        "outside_search_radius": 0,
        "geology_extrapolated": 0,
        "transform": transform,
        "validation_quality": validation_quality,
        "sample_lithology_counts": {
            str(int(code)): int(count)
            for code, count in valid_samples["lithology_code"].value_counts().sort_index().items()
        },
        "sample_group_counts": {str(k): int(len(v["values"])) for k, v in groups.items()},
        "merged_group_voxels": 0,
        "raw_code_voxels": 0,
        "unmapped_lithology_voxels": 0,
        "mapping_version": mapping_metadata.get("mapping_version"),
        "mapping_sha256": mapping_metadata.get("mapping_sha256"),
        "source_constraint_job_id": mapping_metadata.get("source_constraint_job_id"),
        "anisotropy": {
            "source": "task_parameter_geological_axis_with_algorithm_b_lithology_domains",
            "center": anisotropy_center.tolist(),
            "axes": anisotropy_axes.tolist(),
            "radii_m": anisotropy_radii.tolist(),
            "azimuth_deg": float(azimuth),
            "plunge_deg": float(plunge),
            "residual_full_radius": float(residual_full_radius),
            "residual_fade_radius": float(residual_fade_radius),
        },
        "idw": {
            "power": float(power),
            "nearest": int(nearest),
            "max_samples_per_hole": int(max_samples_per_hole),
            "preferred_distinct_holes": int(preferred_distinct_holes),
            "compatible_lithology_weight": float(compatible_lithology_weight),
            "trend_model": "regional_log_median_plus_faded_geology_weighted_local_residual",
            "group_trends": {str(code): value for code, value in group_trends.items()},
            "global_trend": global_trend,
        },
    }

    usecols = ["X", "Y", "Z", "pred_code_final", "dist_fault"]
    for voxels in pd.read_csv(voxel_csv, chunksize=chunk_size, usecols=lambda col: col in usecols):
        voxels = voxels.rename(columns={"X": "x", "Y": "y", "Z": "z", "pred_code_final": "original_lithology_code"})
        voxels["original_lithology_code"] = pd.to_numeric(
            voxels["original_lithology_code"], errors="coerce"
        ).astype("Int64")
        result = voxels.copy()
        result["lithology_code"] = result["original_lithology_code"]
        result["original_lithology_name"] = result["original_lithology_code"].map(
            lithology_names
        ).fillna("未知岩性")
        result[element] = np.nan
        result[f"{element}_support_T"] = np.nan
        result[f"{element}_support_boundary"] = np.nan
        result[f"{element}_support_industrial"] = np.nan
        result[f"{element}_neighbors"] = 0
        result["distinct_hole_count"] = 0
        result["nearest_sample_distance"] = np.nan
        result["confidence_level"] = "no_data"
        result["support_tier"] = "no_data"
        result["lithology_name"] = result["original_lithology_name"]

        fault_mask = pd.Series(False, index=result.index)
        if fault_code is not None:
            fault_mask |= result["original_lithology_code"].eq(int(fault_code)).fillna(False)
        if "dist_fault" in result.columns and fault_thickness > 0:
            fault_mask |= pd.to_numeric(result["dist_fault"], errors="coerce").le(float(fault_thickness)).fillna(False)
        result.loc[fault_mask, "confidence_level"] = "fault_mask"
        stats["fault_masked"] += int(fault_mask.sum())

        mapped_group = result["original_lithology_code"].map(lithology_group_mapping)
        result["geochem_group_code"] = mapped_group.fillna(
            result["original_lithology_code"]
        ).astype("Int64")
        result["geochem_group_name"] = result["geochem_group_code"].map(group_names).fillna(
            result["original_lithology_name"]
        )
        non_fault = ~fault_mask
        stats["merged_group_voxels"] += int((non_fault & mapped_group.notna()).sum())
        stats["raw_code_voxels"] += int(
            (non_fault & mapped_group.isna() & result["original_lithology_code"].notna()).sum()
        )
        stats["unmapped_lithology_voxels"] += int(
            (non_fault & result["original_lithology_code"].isna()).sum()
        )

        active_codes = result.loc[~fault_mask, "geochem_group_code"].dropna().astype(int).unique()
        for code in active_codes:
            row_mask = (~fault_mask) & result["geochem_group_code"].eq(code).fillna(False)
            row_indexes = result.index[row_mask]
            same_lithology_group = groups.get(int(code))
            if not same_lithology_group:
                stats["no_lithology_samples"] += int(len(row_indexes))
            # The trend fills the full geological domain. Local residuals use
            # hole-balanced anisotropic IDW and fade continuously with distance.
            group = global_group
            points = result.loc[row_indexes, ["x", "y", "z"]].to_numpy(dtype=np.float64)
            query_points = transform_anisotropic(
                points, anisotropy_center, anisotropy_axes, anisotropy_radii
            )
            if group:
                distances, indexes = _query_hole_balanced_neighbours(
                    query_points,
                    group,
                    int(code),
                    int(nearest),
                    int(max_samples_per_hole),
                    float(compatible_lithology_weight),
                )
                residual, valid, safe_indexes, weights = _idw_estimates(
                    distances, indexes, group, float(power)
                )
                nearest_ellipsoid_distance = np.min(
                    np.where(valid, distances, np.inf), axis=1
                )
                nearest_ellipsoid_distance[~np.isfinite(nearest_ellipsoid_distance)] = np.nan
                fade = _residual_fade(
                    nearest_ellipsoid_distance,
                    float(residual_full_radius),
                    float(residual_fade_radius),
                )
                transformed = float(global_trend) + fade * residual
                estimates = np.exp(transformed) if transform == "log" else transformed
            else:
                estimates = np.full(len(points), np.nan, dtype=float)
                valid = np.zeros((len(points), 1), dtype=bool)
                safe_indexes = np.zeros((len(points), 1), dtype=np.int64)
                weights = np.zeros((len(points), 1), dtype=float)
                distances = np.full((len(points), 1), np.inf, dtype=float)

            supports = {}
            for name in ("statistical", "boundary", "industrial"):
                indicators = group[f"indicator_{name}"] if group else None
                if indicators is None:
                    supports[name] = np.full(len(points), np.nan)
                    continue
                neighbour_indicators = indicators[safe_indexes]
                support = (weights * neighbour_indicators).sum(axis=1)
                exact = valid & (distances <= 1e-12)
                exact_rows = exact.any(axis=1)
                if exact_rows.any():
                    exact_columns = np.argmax(exact[exact_rows], axis=1)
                    support[exact_rows] = neighbour_indicators[exact_rows, exact_columns]
                supports[name] = support

            counts = valid.sum(axis=1).astype(np.int16)
            holes = (
                _distinct_count(group["holes"][safe_indexes], valid)
                if group else np.zeros(len(points), dtype=np.int16)
            )
            nearest_distance = np.full(len(points), np.nan, dtype=float)
            primary_rows = counts > 0
            if primary_rows.any() and group:
                nearest_distance[primary_rows] = np.linalg.norm(
                    group["points"][safe_indexes[primary_rows, 0]] - points[primary_rows], axis=1
                )
            fallback_rows = (~np.isfinite(nearest_ellipsoid_distance)) | (
                nearest_ellipsoid_distance >= float(residual_fade_radius)
            )
            stats["outside_search_radius"] += int(fallback_rows.sum())
            stats["geology_extrapolated"] += int(fallback_rows.sum())
            confidence = np.full(len(points), "low", dtype=object)
            confidence[(counts >= 3) & (holes >= 1)] = "medium"
            confidence[(counts >= 6) & (holes >= 2) & (nearest_ellipsoid_distance <= 0.5)] = "high"
            confidence[counts == 0] = "no_data"
            confidence[fallback_rows] = "low"
            quality_rank = {"low": 1, "medium": 2, "high": 3}
            validation_rank = quality_rank.get(str(validation_quality), 2)
            if validation_rank < 3:
                confidence[confidence == "high"] = "medium" if validation_rank == 2 else "low"
            if validation_rank == 1:
                confidence[confidence == "medium"] = "low"

            result.loc[row_indexes, element] = estimates
            result.loc[row_indexes, f"{element}_support_T"] = supports["statistical"]
            result.loc[row_indexes, f"{element}_support_boundary"] = supports["boundary"]
            result.loc[row_indexes, f"{element}_support_industrial"] = supports["industrial"]
            result.loc[row_indexes, f"{element}_neighbors"] = counts
            result.loc[row_indexes, "distinct_hole_count"] = holes
            result.loc[row_indexes, "nearest_sample_distance"] = nearest_distance
            result.loc[row_indexes, "confidence_level"] = confidence
            support_tier = np.where(
                fallback_rows, "geology_trend", "ellipsoid_supported"
            )
            support_tier[counts == 0] = "no_data"
            result.loc[row_indexes, "support_tier"] = support_tier
            stats["interpolated"] += int(np.isfinite(estimates).sum())

        result.to_csv(output_csv, mode="w" if first else "a", header=first, index=False, encoding="utf-8-sig")
        first = False
        stats["rows"] += int(len(result))
    classified_rows = (
        stats["fault_masked"]
        + stats["merged_group_voxels"]
        + stats["raw_code_voxels"]
        + stats["unmapped_lithology_voxels"]
    )
    if classified_rows != stats["rows"]:
        raise RuntimeError(
            f"体素归并审计计数不闭合: classified={classified_rows}, rows={stats['rows']}"
        )
    return stats


def leave_one_hole_out_validation(
    samples,
    element,
    nearest=48,
    power=1.6,
    search_radius=1200.0,
    transforms=("raw", "log"),
    thresholds=None,
    anisotropy_radii=(2400.0, 1200.0, 600.0),
    azimuth=80.1,
    plunge=7.7,
    max_samples_per_hole=8,
    preferred_distinct_holes=6,
    residual_full_radius=0.65,
    residual_fade_radius=1.5,
):
    """Validate interpolation by withholding every hole in turn."""

    valid = samples.dropna(subset=[element, "lithology_code", "x", "y", "z", "hole_id"]).copy()
    valid[element] = pd.to_numeric(valid[element], errors="coerce")
    valid = valid.loc[valid[element] > 0].copy()
    rows = []
    group_column = "geochem_group_code" if "geochem_group_code" in valid.columns else "lithology_code"
    for transform in transforms:
        for code, lithology in valid.groupby(group_column):
            for hole_id, held_out in lithology.groupby("hole_id"):
                training = lithology.loc[lithology["hole_id"] != hole_id]
                if training.empty:
                    for _, sample in held_out.iterrows():
                        rows.append(
                            {
                                "transform": transform,
                                "lithology_code": code,
                                "hole_id": hole_id,
                                "observed": sample[element],
                                "predicted": np.nan,
                                "absolute_error": np.nan,
                                "absolute_relative_error": np.nan,
                                "absolute_log_error": np.nan,
                                "validation_status": "no_other_hole_in_lithology",
                            }
                        )
                    continue
                training_points = training[["x", "y", "z"]].to_numpy(dtype=float)
                held_out_points = held_out[["x", "y", "z"]].to_numpy(dtype=float)
                center, axes, radii = derive_anisotropy_transform(
                    training,
                    anisotropy_radii,
                    azimuth,
                    plunge,
                )
                transformed_training_points = transform_anisotropic(
                    training_points, center, axes, radii
                )
                transformed_held_out_points = transform_anisotropic(
                    held_out_points, center, axes, radii
                )
                train_values = training[element].to_numpy(dtype=float)
                transformed_values = np.log(train_values) if transform == "log" else train_values
                trend = float(np.nanmedian(transformed_values))
                validation_source = {
                    "values": train_values,
                    "transformed_values": transformed_values,
                    "residual_values": transformed_values - trend,
                    "transformed_points": transformed_training_points,
                    "holes": pd.factorize(training["hole_id"].astype(str))[0].astype(np.int32),
                    "group_codes": np.zeros(len(training_points), dtype=np.int32),
                }
                validation_source["hole_trees"] = _build_hole_trees(
                    transformed_training_points, validation_source["holes"]
                )
                distances, indexes = _query_hole_balanced_neighbours(
                    transformed_held_out_points,
                    validation_source,
                    0,
                    int(nearest),
                    int(max_samples_per_hole),
                    1.0,
                )
                residual, valid_neighbours, _, _ = _idw_estimates(
                    distances,
                    indexes,
                    validation_source,
                    float(power),
                )
                nearest_distance = np.min(
                    np.where(valid_neighbours, distances, np.inf), axis=1
                )
                fade = _residual_fade(
                    nearest_distance,
                    residual_full_radius,
                    residual_fade_radius,
                )
                estimates = trend + fade * residual
                predicted = np.exp(estimates) if transform == "log" else estimates
                for sample_index, (_, sample) in enumerate(held_out.iterrows()):
                    observed = float(sample[element])
                    estimate = float(predicted[sample_index])
                    available = np.isfinite(estimate)
                    error = estimate - observed if available else np.nan
                    rows.append(
                        {
                            "transform": transform,
                            "lithology_code": code,
                            "hole_id": hole_id,
                            "observed": observed,
                            "predicted": estimate,
                            "absolute_error": abs(error) if available else np.nan,
                            "absolute_relative_error": abs(error) / observed if available else np.nan,
                            "absolute_log_error": (
                                abs(np.log(estimate) - np.log(observed))
                                if available and estimate > 0 else np.nan
                            ),
                            "validation_status": "ok" if available else "outside_search_radius",
                        }
                    )
    details = pd.DataFrame(rows)
    summaries = []
    for transform in transforms:
        selected = details.loc[details["transform"] == transform]
        successful = selected.loc[selected["validation_status"] == "ok"]
        residual = successful["predicted"] - successful["observed"]
        log_residual = (
            np.log(successful["predicted"]) - np.log(successful["observed"])
            if len(successful) else pd.Series(dtype=float)
        )
        relative = successful["absolute_relative_error"]
        coverage = len(successful) / len(selected) if len(selected) else 0.0
        median_relative = float(relative.median()) if len(relative) else np.nan
        if coverage >= 0.8 and np.isfinite(median_relative) and median_relative <= 0.5:
            quality = "high"
        elif coverage >= 0.5 and np.isfinite(median_relative) and median_relative <= 1.0:
            quality = "medium"
        else:
            quality = "low"
        summary_row = {
                "transform": transform,
                "validation_sample_count": len(selected),
                "predicted_sample_count": len(successful),
                "coverage": coverage,
                "mae": float(successful["absolute_error"].mean()) if len(successful) else np.nan,
                "rmse": float(np.sqrt(np.mean(residual**2))) if len(successful) else np.nan,
                "log_mae": float(np.abs(log_residual).mean()) if len(successful) else np.nan,
                "log_rmse": float(np.sqrt(np.mean(log_residual**2))) if len(successful) else np.nan,
                "spearman_r": (
                    float(spearmanr(successful["observed"], successful["predicted"]).statistic)
                    if len(successful) >= 3 else np.nan
                ),
                "bias": float(residual.mean()) if len(successful) else np.nan,
                "median_absolute_relative_error": median_relative,
                "p90_absolute_relative_error": float(relative.quantile(0.9)) if len(relative) else np.nan,
                "validation_quality": quality,
            }
        for threshold_name, threshold in (thresholds or {}).items():
            if threshold is None or not np.isfinite(float(threshold)) or not len(successful):
                continue
            observed_positive = successful["observed"] >= float(threshold)
            predicted_positive = successful["predicted"] >= float(threshold)
            true_positive = int((observed_positive & predicted_positive).sum())
            false_positive = int((~observed_positive & predicted_positive).sum())
            false_negative = int((observed_positive & ~predicted_positive).sum())
            true_negative = int((~observed_positive & ~predicted_positive).sum())
            summary_row[f"{threshold_name}_recall"] = (
                true_positive / (true_positive + false_negative)
                if true_positive + false_negative else np.nan
            )
            summary_row[f"{threshold_name}_precision"] = (
                true_positive / (true_positive + false_positive)
                if true_positive + false_positive else np.nan
            )
            summary_row[f"{threshold_name}_accuracy"] = (
                (true_positive + true_negative) / len(successful)
            )
        summaries.append(summary_row)
    return details, pd.DataFrame(summaries)


def summarize_validation_by_lithology(details):
    """Expose error and coverage heterogeneity instead of hiding it in one score."""

    rows = []
    for (transform, lithology_code), group in details.groupby(["transform", "lithology_code"]):
        successful = group.loc[group["validation_status"] == "ok"]
        residual = successful["predicted"] - successful["observed"]
        rows.append(
            {
                "transform": transform,
                "lithology_code": lithology_code,
                "validation_sample_count": len(group),
                "predicted_sample_count": len(successful),
                "coverage": len(successful) / len(group) if len(group) else 0.0,
                "mae": float(successful["absolute_error"].mean()) if len(successful) else np.nan,
                "rmse": float(np.sqrt(np.mean(residual**2))) if len(successful) else np.nan,
                "log_mae": float(successful["absolute_log_error"].mean()) if len(successful) else np.nan,
                "spearman_r": (
                    float(spearmanr(successful["observed"], successful["predicted"]).statistic)
                    if len(successful) >= 3 else np.nan
                ),
            }
        )
    return pd.DataFrame(rows)


def extract_candidate_regions(
    voxel_csv,
    element,
    voxel_size,
    support_cutoff,
    thresholds,
    chunk_size=200000,
):
    """Connected candidate regions for each available evidence threshold."""

    output_columns = [
        "region_id", "element", "evidence_type", "threshold_value_ppm", "support_cutoff",
        "voxel_count", "volume_m3", "centroid_x", "centroid_y", "centroid_z",
        "min_x", "max_x", "min_y", "max_y", "min_z", "max_z",
        "mean_support", "max_support", "max_estimated_value",
        "high_confidence_voxel_count", "medium_confidence_voxel_count",
        "low_confidence_voxel_count", "coordinate_quality",
    ]
    region_frames = []
    for evidence_type, threshold_value in thresholds.items():
        if threshold_value is None:
            continue
        support_column = f"{element}_support_{evidence_type}"
        chunks = []
        try:
            iterator = pd.read_csv(
                voxel_csv,
                chunksize=chunk_size,
                usecols=["x", "y", "z", element, support_column, "confidence_level"],
            )
        except ValueError:
            continue
        for chunk in iterator:
            support = pd.to_numeric(chunk[support_column], errors="coerce")
            selected = chunk.loc[
                (support >= float(support_cutoff))
                & ~chunk["confidence_level"].isin(["no_data", "fault_mask"])
            ].copy()
            if not selected.empty:
                chunks.append(selected)
        if not chunks:
            continue
        candidates = pd.concat(chunks, ignore_index=True)
        coordinates = candidates[["x", "y", "z"]].to_numpy(dtype=float)
        origins = np.nanmin(coordinates, axis=0)
        indexes = np.rint((coordinates - origins) / float(voxel_size)).astype(np.int32)
        shape = tuple((np.max(indexes, axis=0) + 1).tolist())
        cell_count = int(np.prod(shape, dtype=np.int64))
        if cell_count > 50_000_000:
            # Preserve evidence without risking an out-of-memory crash.
            candidates["region_id"] = f"{evidence_type}-extent-too-large"
            labels_at_points = np.ones(len(candidates), dtype=np.int32)
        else:
            grid = np.zeros(shape, dtype=bool)
            grid[indexes[:, 0], indexes[:, 1], indexes[:, 2]] = True
            structure = np.zeros((3, 3, 3), dtype=np.int8)
            structure[1, 1, :] = 1
            structure[1, :, 1] = 1
            structure[:, 1, 1] = 1
            labels, _ = connected_component_label(grid, structure=structure)
            labels_at_points = labels[indexes[:, 0], indexes[:, 1], indexes[:, 2]]
        candidates["_component"] = labels_at_points
        for component, group in candidates.groupby("_component"):
            if int(component) <= 0:
                continue
            confidence_counts = group["confidence_level"].value_counts().to_dict()
            region_frames.append(
                {
                    "region_id": f"{evidence_type}-{int(component)}",
                    "element": element,
                    "evidence_type": evidence_type,
                    "threshold_value_ppm": threshold_value,
                    "support_cutoff": support_cutoff,
                    "voxel_count": len(group),
                    "volume_m3": len(group) * float(voxel_size) ** 3,
                    "centroid_x": float(group["x"].mean()),
                    "centroid_y": float(group["y"].mean()),
                    "centroid_z": float(group["z"].mean()),
                    "min_x": float(group["x"].min()),
                    "max_x": float(group["x"].max()),
                    "min_y": float(group["y"].min()),
                    "max_y": float(group["y"].max()),
                    "min_z": float(group["z"].min()),
                    "max_z": float(group["z"].max()),
                    "mean_support": float(group[support_column].mean()),
                    "max_support": float(group[support_column].max()),
                    "max_estimated_value": float(group[element].max()),
                    "high_confidence_voxel_count": int(confidence_counts.get("high", 0)),
                    "medium_confidence_voxel_count": int(confidence_counts.get("medium", 0)),
                    "low_confidence_voxel_count": int(confidence_counts.get("low", 0)),
                    "coordinate_quality": "voxel_grid_with_vertical_drillhole_assumption",
                }
            )
    return pd.DataFrame(region_frames, columns=output_columns)


def attach_region_sample_support(regions, samples, element, voxel_size, thresholds):
    """Attach auditable raw-assay support to each connected candidate region."""

    if regions.empty:
        for column in (
            "raw_sample_count",
            "supporting_hole_count",
            "supporting_holes",
            "nearest_raw_sample_distance",
            "raw_exceedance_count",
            "contains_raw_exceedance",
            "maximum_raw_sample_value",
        ):
            regions[column] = pd.Series(dtype=object)
        return regions

    valid_samples = samples.dropna(subset=["x", "y", "z", element, "hole_id"]).copy()
    valid_samples[element] = pd.to_numeric(valid_samples[element], errors="coerce")
    valid_samples = valid_samples.dropna(subset=[element])
    margin = float(voxel_size) * 0.5
    enriched_rows = []
    for region in regions.to_dict("records"):
        inside = valid_samples.loc[
            valid_samples["x"].between(region["min_x"] - margin, region["max_x"] + margin)
            & valid_samples["y"].between(region["min_y"] - margin, region["max_y"] + margin)
            & valid_samples["z"].between(region["min_z"] - margin, region["max_z"] + margin)
        ]
        if len(valid_samples):
            delta = valid_samples[["x", "y", "z"]].to_numpy(dtype=float) - np.array(
                [region["centroid_x"], region["centroid_y"], region["centroid_z"]],
                dtype=float,
            )
            nearest_distance = float(np.sqrt(np.square(delta).sum(axis=1)).min())
        else:
            nearest_distance = np.nan
        threshold = thresholds.get(region["evidence_type"])
        exceedance_count = (
            int((inside[element] >= float(threshold)).sum())
            if threshold is not None and np.isfinite(float(threshold)) else 0
        )
        holes = sorted(inside["hole_id"].astype(str).unique().tolist())
        enriched_rows.append(
            {
                **region,
                "raw_sample_count": int(len(inside)),
                "supporting_hole_count": len(holes),
                "supporting_holes": ",".join(holes),
                "nearest_raw_sample_distance": nearest_distance,
                "raw_exceedance_count": exceedance_count,
                "contains_raw_exceedance": bool(exceedance_count),
                "maximum_raw_sample_value": (
                    float(inside[element].max()) if len(inside) else np.nan
                ),
            }
        )
    return pd.DataFrame(enriched_rows)


def parse_args():
    parser = argparse.ArgumentParser(description="Geology-constrained anisotropic residual IDW.")
    parser.add_argument("--assay-csv", required=True)
    parser.add_argument("--collar-csv", required=True)
    parser.add_argument("--voxel-csv", required=True)
    parser.add_argument("--layers-csv", required=True)
    parser.add_argument("--lithology-map-csv", default="")
    parser.add_argument("--lithology-group-config", default="")
    parser.add_argument("--source-constraint-job-id", default="")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--element", required=True)
    parser.add_argument("--nearest", type=int, default=48)
    parser.add_argument("--power", type=float, default=1.6)
    parser.add_argument("--search-radius", type=float, default=1200.0)
    parser.add_argument("--major-radius", type=float, default=2400.0)
    parser.add_argument("--intermediate-radius", type=float, default=1200.0)
    parser.add_argument("--minor-radius", type=float, default=600.0)
    parser.add_argument("--azimuth", type=float, default=80.1)
    parser.add_argument("--plunge", type=float, default=7.7)
    parser.add_argument("--max-samples-per-hole", type=int, default=8)
    parser.add_argument("--preferred-distinct-holes", type=int, default=6)
    parser.add_argument("--compatible-lithology-weight", type=float, default=0.70)
    parser.add_argument("--residual-full-radius", type=float, default=0.65)
    parser.add_argument("--residual-fade-radius", type=float, default=1.5)
    parser.add_argument("--composite-interval", type=float, default=15.0)
    parser.add_argument("--fault-code", type=int, default=-1)
    parser.add_argument("--fault-thickness", type=float, default=30.0)
    parser.add_argument("--chunk-size", type=int, default=2048)
    parser.add_argument("--transform", choices=["raw", "log"], default="log")
    parser.add_argument("--statistical-threshold", type=float, default=None)
    parser.add_argument("--boundary-grade", type=float, default=None)
    parser.add_argument("--industrial-grade", type=float, default=None)
    parser.add_argument("--support-cutoff", type=float, default=0.5)
    parser.add_argument("--voxel-size", type=float, default=15.0)
    return parser.parse_args()


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    samples, assay_info = load_samples(args.assay_csv, [args.element], args.collar_csv)
    samples = assign_sample_lithology(samples, args.layers_csv)
    lithology_names = load_lithology_names(args.lithology_map_csv)
    lithology_group_mapping = {}
    group_names = {}
    mapping_metadata = {}
    if args.source_constraint_job_id:
        mapping_metadata["source_constraint_job_id"] = args.source_constraint_job_id
    if args.lithology_group_config:
        lithology_group_mapping, group_names, mapping_metadata = load_lithology_group_mapping(
            args.lithology_group_config,
            args.lithology_map_csv,
            args.source_constraint_job_id,
            fault_code=None if args.fault_code < 0 else args.fault_code,
        )
    samples = apply_lithology_groups(
        samples, lithology_group_mapping, lithology_names, group_names
    )
    samples["lithology_name"] = samples["original_lithology_name"]
    uncomposited_sample_count = int(len(samples))
    samples = composite_samples(samples, args.element, args.composite_interval)
    samples.to_csv(output_dir / "used_assay_points.csv", index=False, encoding="utf-8-sig")

    validation_details, validation_summary = leave_one_hole_out_validation(
        samples,
        args.element,
        nearest=args.nearest,
        power=args.power,
        search_radius=args.search_radius,
        anisotropy_radii=(args.major_radius, args.intermediate_radius, args.minor_radius),
        azimuth=args.azimuth,
        plunge=args.plunge,
        max_samples_per_hole=args.max_samples_per_hole,
        preferred_distinct_holes=args.preferred_distinct_holes,
        residual_full_radius=args.residual_full_radius,
        residual_fade_radius=args.residual_fade_radius,
        thresholds={
            "T": args.statistical_threshold,
            "boundary": args.boundary_grade,
            "industrial": args.industrial_grade,
        },
    )
    validation_by_lithology = summarize_validation_by_lithology(validation_details)
    validation_details.to_csv(
        output_dir / "loho_validation_details.csv", index=False, encoding="utf-8-sig"
    )
    validation_summary.to_csv(
        output_dir / "loho_validation_summary.csv", index=False, encoding="utf-8-sig"
    )
    validation_by_lithology.to_csv(
        output_dir / "loho_validation_by_lithology.csv",
        index=False,
        encoding="utf-8-sig",
    )
    selected_validation = validation_summary.loc[
        validation_summary["transform"] == args.transform
    ]
    validation_quality = (
        str(selected_validation.iloc[0]["validation_quality"])
        if not selected_validation.empty else "low"
    )

    output_csv = output_dir / "geochemical_voxels_15m.csv"
    interpolation = interpolate_constrained_csv(
        args.voxel_csv,
        samples,
        args.element,
        output_csv,
        lithology_names=lithology_names,
        nearest=args.nearest,
        power=args.power,
        search_radius=args.search_radius,
        fault_code=None if args.fault_code < 0 else args.fault_code,
        fault_thickness=args.fault_thickness,
        chunk_size=args.chunk_size,
        transform=args.transform,
        statistical_threshold=args.statistical_threshold,
        boundary_grade=args.boundary_grade,
        industrial_grade=args.industrial_grade,
        validation_quality=validation_quality,
        lithology_group_mapping=lithology_group_mapping,
        group_names=group_names,
        mapping_metadata=mapping_metadata,
        anisotropy_radii=(
            args.major_radius,
            args.intermediate_radius,
            args.minor_radius,
        ),
        azimuth=args.azimuth,
        plunge=args.plunge,
        max_samples_per_hole=args.max_samples_per_hole,
        preferred_distinct_holes=args.preferred_distinct_holes,
        compatible_lithology_weight=args.compatible_lithology_weight,
        residual_full_radius=args.residual_full_radius,
        residual_fade_radius=args.residual_fade_radius,
    )
    raw_values = pd.to_numeric(samples[args.element], errors="coerce")
    positive_raw_values = raw_values.loc[raw_values > 0]
    summary = {
        "method": "single-element Algorithm-B-domain-constrained anisotropic residual IDW",
        "algorithm_version": "geochem-anisotropic-idw-v1-trend-residual-full-field",
        "inputs": {
            "element": args.element,
            "elements": [args.element],
            "voxel_csv": str(Path(args.voxel_csv).resolve()),
            "layers_csv": str(Path(args.layers_csv).resolve()),
        },
        "parameters": {
            "nearest": args.nearest,
            "power": args.power,
            "search_radius": None,
            "anisotropy_radii_m": [
                args.major_radius,
                args.intermediate_radius,
                args.minor_radius,
            ],
            "anisotropy_axis_source": "task_parameter_geological_axis",
            "azimuth_deg": args.azimuth,
            "plunge_deg": args.plunge,
            "max_samples_per_hole": args.max_samples_per_hole,
            "preferred_distinct_holes": args.preferred_distinct_holes,
            "compatible_lithology_weight": args.compatible_lithology_weight,
            "residual_full_radius": args.residual_full_radius,
            "residual_fade_radius": args.residual_fade_radius,
            "composite_interval_m": args.composite_interval,
            "fault_code": None if args.fault_code < 0 else args.fault_code,
            "fault_thickness": args.fault_thickness,
            "voxel_size": args.voxel_size,
            "transform": args.transform,
            "statistical_threshold": args.statistical_threshold,
            "boundary_grade": args.boundary_grade,
            "industrial_grade": args.industrial_grade,
            "support_cutoff": args.support_cutoff,
            "lithology_group_config": (
                str(Path(args.lithology_group_config).resolve())
                if args.lithology_group_config else None
            ),
            "mapping_version": mapping_metadata.get("mapping_version"),
            "mapping_sha256": mapping_metadata.get("mapping_sha256"),
        },
        "assays": {
            **assay_info,
            "uncomposited_positive_sample_count": uncomposited_sample_count,
            "composite_sample_count": int(len(samples)),
            "matched_lithology": int(samples["lithology_code"].notna().sum()),
            "unmatched_lithology": int(samples["lithology_code"].isna().sum()),
        },
        "raw_sample_fidelity": {
            "positive_sample_count": int(len(positive_raw_values)),
            "minimum_positive": float(positive_raw_values.min()) if len(positive_raw_values) else None,
            "maximum_positive": float(positive_raw_values.max()) if len(positive_raw_values) else None,
            "raw_points_are_separate_output": True,
        },
        "validation": (
            validation_summary.astype(object).where(pd.notna(validation_summary), None).to_dict("records")
        ),
        "validation_by_lithology": (
            validation_by_lithology.astype(object)
            .where(pd.notna(validation_by_lithology), None)
            .to_dict("records")
        ),
        "selected_validation_quality": validation_quality,
        "lithology_grouping": {
            "enabled": bool(args.lithology_group_config),
            **mapping_metadata,
            "group_count": len(group_names),
            "mapped_original_code_count": len(lithology_group_mapping),
        },
        "voxels": {"voxel_count": interpolation["rows"], "voxel_size": args.voxel_size},
        "interpolation": interpolation,
        "outputs": {
            "geochemical_voxels_csv": str(output_csv.resolve()),
            "used_assay_points_csv": str((output_dir / "used_assay_points.csv").resolve()),
            "loho_validation_details_csv": str((output_dir / "loho_validation_details.csv").resolve()),
            "loho_validation_summary_csv": str((output_dir / "loho_validation_summary.csv").resolve()),
            "loho_validation_by_lithology_csv": str(
                (output_dir / "loho_validation_by_lithology.csv").resolve()
            ),
        },
        "limitations": [
            "化验样点优先使用项目钻孔分段坐标形成的三维孔迹；仅缺少有效孔迹时垂直回退。",
            "Fault handling uses a hard distance-band mask rather than exact fault-side topology.",
            "椭球轴向来自任务参数；80.1°/7.7°仅为当前缺少确认产状时的临时默认值。",
            "远离钻孔区域回归合并岩性组趋势值，以保证地质体全域有值；这些体素不得解释为已验证矿体。",
            "A draft rule set must not be described as expert-confirmed.",
        ],
    }
    (output_dir / "model_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"samples": len(samples), "interpolation": interpolation}, ensure_ascii=False))


if __name__ == "__main__":
    main()
