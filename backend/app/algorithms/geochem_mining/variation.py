"""Algorithm 1: auditable geochemical variation and anomaly segmentation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import normaltest

from .data_pipeline import (
    DEFAULT_DB_PATH,
    GeochemDataset,
    ensure_dir,
    load_geochem_dataset,
    markdown_table,
    valid_intervals,
    write_csv,
    write_text,
)
from .professional_rules import (
    DEFAULT_RELATIVE_RATIO_CUTOFFS,
    MINING_LEVEL_RANK,
    build_threshold_profile,
    classify_mining_level,
)


@dataclass(frozen=True)
class VariationConfig:
    minimum_positive_count: int = 30
    minimum_group_positive_count: int = 3
    depth_bin_size_m: int = 100
    background_method: str = "log_mad"
    background_scope: str = "model"
    merge_gap_m: float = 0.5
    minimum_segment_length_m: float = 0.0
    industrial_grades_ppm: dict[str, float] = field(default_factory=dict)
    boundary_grades_ppm: dict[str, float] = field(default_factory=dict)
    element_rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    relative_ratio_cutoffs: list[float] = field(
        default_factory=lambda: list(DEFAULT_RELATIVE_RATIO_CUTOFFS)
    )
    rule_set_id: str | None = None
    rule_set_status: str = "draft"


def _sample_std(values: pd.Series) -> float:
    return float(values.std(ddof=1)) if len(values) > 1 else np.nan


def _positive_values(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    return numeric[numeric > 0].astype(float)


def iterative_upper_background(values: pd.Series, minimum_count: int) -> dict[str, float | int | str]:
    """Legacy raw-scale mean+3SD clipping, retained for sensitivity comparison."""

    current = _positive_values(values)
    removed = 0
    iterations = 0
    while len(current) >= minimum_count:
        mean = float(current.mean())
        std = _sample_std(current)
        if not np.isfinite(std) or std <= 0:
            break
        keep = current <= mean + 3.0 * std
        removed_now = int((~keep).sum())
        if removed_now == 0:
            break
        current = current.loc[keep]
        removed += removed_now
        iterations += 1
    if len(current) < minimum_count:
        return {
            "background_mean": np.nan,
            "background_std": np.nan,
            "threshold_T_auto": np.nan,
            "background_used_count": int(len(current)),
            "removed_high_count": removed,
            "background_iterations": iterations,
            "background_method": "iterative_upper",
        }
    mean = float(current.mean())
    std = _sample_std(current)
    return {
        "background_mean": mean,
        "background_std": std,
        "threshold_T_auto": mean + 2.0 * std,
        "background_used_count": int(len(current)),
        "removed_high_count": removed,
        "background_iterations": iterations,
        "background_method": "iterative_upper",
    }


def robust_log_mad_background(values: pd.Series, minimum_count: int) -> dict[str, float | int | str]:
    """Robust background on log concentrations using median and scaled MAD."""

    positive = _positive_values(values)
    if len(positive) < minimum_count:
        return {
            "background_mean": np.nan,
            "background_std": np.nan,
            "threshold_T_auto": np.nan,
            "background_used_count": int(len(positive)),
            "removed_high_count": 0,
            "background_iterations": 0,
            "background_method": "log_mad",
        }
    logs = np.log(positive.to_numpy(dtype=float))
    median_log = float(np.median(logs))
    mad = float(np.median(np.abs(logs - median_log)))
    robust_sigma = 1.4826 * mad
    if not np.isfinite(robust_sigma) or robust_sigma <= 0:
        robust_sigma = float(np.std(logs, ddof=1)) if len(logs) > 1 else 0.0
    keep = logs <= median_log + 3.0 * robust_sigma if robust_sigma > 0 else np.ones(len(logs), dtype=bool)
    retained = logs[keep]
    if len(retained) < minimum_count:
        retained = logs
        keep = np.ones(len(logs), dtype=bool)
    center = float(np.median(retained))
    retained_mad = float(np.median(np.abs(retained - center)))
    sigma = 1.4826 * retained_mad
    if not np.isfinite(sigma) or sigma <= 0:
        sigma = float(np.std(retained, ddof=1)) if len(retained) > 1 else 0.0
    background = float(np.exp(center))
    threshold = float(np.exp(center + 2.0 * sigma))
    # Approximate original-scale spread, used only as a descriptive field.
    spread = float(background * np.sqrt(max(0.0, np.exp(sigma * sigma) - 1.0)))
    return {
        "background_mean": background,
        "background_std": spread,
        "threshold_T_auto": threshold,
        "background_used_count": int(len(retained)),
        "removed_high_count": int((~keep).sum()),
        "background_iterations": 1,
        "background_method": "log_mad",
        "log_median": center,
        "log_mad_sigma": sigma,
    }


def expert_2sigma_background(values: pd.Series, minimum_count: int) -> dict[str, float | int | str]:
    """Expert rule: clip 3-sigma outliers, select normal/log-normal scale, use X+2σ."""

    positive = _positive_values(values)
    empty = {
        "background_mean": np.nan,
        "background_std": np.nan,
        "threshold_T_auto": np.nan,
        "background_used_count": int(len(positive)),
        "removed_high_count": 0,
        "background_iterations": 0,
        "background_method": "expert_2sigma",
        "distribution": "insufficient_data",
        "normality_p_value": np.nan,
        "threshold_formula": "X + 2σ",
    }
    if len(positive) < minimum_count:
        return empty

    candidates: list[dict[str, Any]] = []
    raw = positive.to_numpy(dtype=float)
    for distribution, transformed in (("normal", raw), ("lognormal", np.log(raw))):
        current = transformed.copy()
        removed = 0
        iterations = 0
        while len(current) >= minimum_count:
            mean = float(np.mean(current))
            std = float(np.std(current, ddof=1)) if len(current) > 1 else np.nan
            if not np.isfinite(std) or std <= 0:
                break
            keep = current <= mean + 3.0 * std
            removed_now = int((~keep).sum())
            if removed_now == 0:
                break
            if int(keep.sum()) < minimum_count:
                break
            current = current[keep]
            removed += removed_now
            iterations += 1
        if len(current) < minimum_count:
            continue
        mean = float(np.mean(current))
        std = float(np.std(current, ddof=1)) if len(current) > 1 else np.nan
        try:
            p_value = float(normaltest(current).pvalue) if len(current) >= 8 else np.nan
        except Exception:
            p_value = np.nan
        candidates.append({
            "distribution": distribution,
            "mean": mean,
            "std": std,
            "used_count": int(len(current)),
            "removed": removed,
            "iterations": iterations,
            "p_value": p_value,
        })
    if not candidates:
        return empty
    passing = [item for item in candidates if np.isfinite(item["p_value"]) and item["p_value"] >= 0.05]
    pool = passing or candidates
    selected = max(pool, key=lambda item: item["p_value"] if np.isfinite(item["p_value"]) else -1.0)
    mean = float(selected["mean"])
    std = float(selected["std"])
    threshold = mean + 2.0 * std
    if selected["distribution"] == "lognormal":
        background = float(np.exp(mean))
        threshold = float(np.exp(threshold))
        background_std = float(background * np.sqrt(max(0.0, np.exp(std * std) - 1.0)))
    else:
        background = mean
        background_std = std
    return {
        "background_mean": background,
        "background_std": background_std,
        "threshold_T_auto": threshold,
        "background_used_count": int(selected["used_count"]),
        "removed_high_count": int(selected["removed"]),
        "background_iterations": int(selected["iterations"]),
        "background_method": "expert_2sigma",
        "distribution": selected["distribution"],
        "normality_p_value": selected["p_value"],
        "threshold_formula": "exp(log(X) + 2σ_log)" if selected["distribution"] == "lognormal" else "X + 2σ",
        "outlier_cutoff": (
            float(np.exp(mean + 3.0 * std))
            if selected["distribution"] == "lognormal"
            else float(mean + 3.0 * std)
        ),
        "outlier_cutoff_scale": selected["distribution"],
    }


def _background(values: pd.Series, minimum_count: int, method: str) -> dict[str, Any]:
    if method == "iterative_upper":
        return iterative_upper_background(values, minimum_count)
    if method == "log_mad":
        return robust_log_mad_background(values, minimum_count)
    if method == "expert_2sigma":
        return expert_2sigma_background(values, minimum_count)
    raise ValueError(f"Unsupported background method: {method}")


def _cv_class(value: float) -> str:
    if not np.isfinite(value):
        return "不可判定"
    if value > 1.0:
        return "强分异"
    if value > 0.75:
        return "分异"
    if value > 0.45:
        return "弱分异"
    return "较均匀"


def _enrichment_class(value: float) -> str:
    if not np.isfinite(value):
        return "不可判定"
    if value >= 4.0:
        return "强相对富集"
    if value >= 2.0:
        return "中等相对富集"
    if value >= 1.25:
        return "相对富集"
    if value >= 0.8:
        return "背景水平"
    return "相对贫化"


def _anomaly_level(value: float, threshold: float) -> str:
    if not np.isfinite(value) or not np.isfinite(threshold) or threshold <= 0:
        return "不可判定"
    if value < threshold:
        return "非统计异常"
    if value < 2.0 * threshold:
        return "统计异常外带"
    if value < 4.0 * threshold:
        return "统计异常中带"
    return "统计异常内带"


def build_variation_quality(
    assays: pd.DataFrame,
    elements: list[str],
    minimum_positive_count: int,
    element_rules: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    total = len(assays)
    for element in elements:
        rule = (element_rules or {}).get(element, {})
        values = pd.to_numeric(assays[element], errors="coerce")
        present = int(values.notna().sum())
        positive = int((values > 0).sum())
        eligible = positive >= minimum_positive_count
        rows.append(
            {
                "element": element,
                "element_role": rule.get("role", "other"),
                "unit": rule.get("unit", "ppm"),
                "detection_limit": rule.get("detection_limit"),
                "detection_limit_status": rule.get("detection_limit_status", "pending"),
                "applicable_sample_medium": rule.get(
                    "applicable_sample_medium", "drill_core_assay"
                ),
                "valid_interval_count": total,
                "field_present_count": present,
                "missing_count": total - present,
                "zero_count": int((values == 0).sum()),
                "negative_count": int((values < 0).sum()),
                "positive_count": positive,
                "zero_ratio_of_present": int((values == 0).sum()) / present if present else np.nan,
                "used_for_variation": eligible,
                "exclusion_reason": "" if eligible else f"positive_count < {minimum_positive_count}",
                "zero_value_policy": (
                    "below_detection_limit_excluded_from_main_statistics"
                    if rule.get("detection_limit_status") == "confirmed"
                    and rule.get("detection_limit") is not None
                    else "zero_or_below_detection_unknown_excluded_from_main_statistics"
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("element").reset_index(drop=True)


def build_background_statistics(
    assays: pd.DataFrame,
    quality: pd.DataFrame,
    config: VariationConfig,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, quality_row in quality.iterrows():
        element = str(quality_row["element"])
        positive = _positive_values(assays[element])
        raw_mean = float(positive.mean()) if len(positive) else np.nan
        raw_std = _sample_std(positive)
        primary = _background(positive, config.minimum_positive_count, config.background_method)
        legacy = iterative_upper_background(positive, config.minimum_positive_count)
        robust = robust_log_mad_background(positive, config.minimum_positive_count)
        bg_mean = float(primary["background_mean"])
        bg_std = float(primary["background_std"])
        profile = build_threshold_profile(
            element,
            positive,
            bg_mean,
            primary["threshold_T_auto"],
            config.industrial_grades_ppm,
            boundary_grades_ppm=config.boundary_grades_ppm,
            relative_ratio_cutoffs=config.relative_ratio_cutoffs,
            rule_set_id=config.rule_set_id,
            rule_set_status=config.rule_set_status,
        )
        rows.append(
            {
                "element": element,
                "used_for_variation": bool(quality_row["used_for_variation"]),
                "positive_count": int(quality_row["positive_count"]),
                "raw_min_positive": float(positive.min()) if len(positive) else np.nan,
                "raw_p02_positive": float(positive.quantile(0.02)) if len(positive) else np.nan,
                "raw_median_positive": float(positive.median()) if len(positive) else np.nan,
                "raw_p98_positive": float(positive.quantile(0.98)) if len(positive) else np.nan,
                "raw_max_positive": float(positive.max()) if len(positive) else np.nan,
                "raw_mean_positive": raw_mean,
                "raw_std_positive": raw_std,
                "CV_raw": raw_std / raw_mean if np.isfinite(raw_std) and raw_mean > 0 else np.nan,
                "CV_raw_class": _cv_class(raw_std / raw_mean if np.isfinite(raw_std) and raw_mean > 0 else np.nan),
                "background_mean": bg_mean,
                "background_std": bg_std,
                "CV_bg": bg_std / bg_mean if np.isfinite(bg_std) and bg_mean > 0 else np.nan,
                "threshold_T_auto": primary["threshold_T_auto"],
                "T_review": np.nan,
                "threshold_source": "auto_frozen_model_background",
                "background_scope": config.background_scope,
                "background_method": config.background_method,
                "background_used_count": primary["background_used_count"],
                "removed_high_count": primary["removed_high_count"],
                "background_iterations": primary["background_iterations"],
                "background_distribution": primary.get("distribution"),
                "normality_p_value": primary.get("normality_p_value"),
                "threshold_formula": primary.get("threshold_formula"),
                "legacy_background_mean": legacy["background_mean"],
                "legacy_threshold_T": legacy["threshold_T_auto"],
                "robust_background_mean": robust["background_mean"],
                "robust_threshold_T": robust["threshold_T_auto"],
                "threshold_method_ratio_robust_to_legacy": (
                    float(robust["threshold_T_auto"]) / float(legacy["threshold_T_auto"])
                    if np.isfinite(float(robust["threshold_T_auto"]))
                    and np.isfinite(float(legacy["threshold_T_auto"]))
                    and float(legacy["threshold_T_auto"]) > 0
                    else np.nan
                ),
                **{
                    key: profile[key]
                    for key in (
                        "statistical_threshold_to_background_ratio",
                        "relative_band_cut_1",
                        "relative_band_cut_2",
                        "relative_band_cut_3",
                        "relative_band_method",
                        "relative_band_status",
                        "boundary_grade_ppm",
                        "boundary_grade_source",
                        "industrial_grade_ppm",
                        "industrial_grade_source",
                        "rule_set_id",
                        "rule_set_status",
                    )
                },
            }
        )
    return pd.DataFrame(rows).sort_values("element").reset_index(drop=True)


def _group_enrichment(
    assays: pd.DataFrame,
    elements: list[str],
    group_column: str,
    reference_means: dict[str, float],
    minimum_group_positive_count: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    valid_groups = assays.loc[assays[group_column].notna() & (assays[group_column] != "")]
    for group_value, group in valid_groups.groupby(group_column, dropna=False):
        for element in elements:
            positive = _positive_values(group[element])
            count = int(len(positive))
            mean = float(positive.mean()) if count else np.nan
            reference = reference_means.get(element, np.nan)
            ratio = (
                mean / reference
                if count >= minimum_group_positive_count and np.isfinite(reference) and reference > 0
                else np.nan
            )
            rows.append(
                {
                    group_column: group_value,
                    "element": element,
                    "positive_count": count,
                    "group_mean_positive": mean,
                    "project_reference_mean_positive": reference,
                    "project_background_mean": reference,
                    "E_internal": ratio,
                    "relative_background_ratio": ratio,
                    "relative_deviation": ratio - 1.0 if np.isfinite(ratio) else np.nan,
                    "E_internal_class": _enrichment_class(ratio),
                    "interpretation_ready": count >= minimum_group_positive_count,
                }
            )
    return pd.DataFrame(rows)


def _evidence_channel(statistical: bool, boundary: bool, industrial: bool) -> str:
    professional = boundary or industrial
    if statistical and professional:
        return "statistical_and_professional"
    if professional:
        return "professional_only"
    return "statistical_only"


def build_anomaly_intervals(assays: pd.DataFrame, statistics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, statistic in statistics.iterrows():
        element = str(statistic["element"])
        threshold = float(statistic["threshold_T_auto"])
        if not np.isfinite(threshold) or threshold <= 0:
            continue
        profile = statistic.to_dict()
        profile["relative_ratio_cutoffs"] = [
            profile.get(f"relative_band_cut_{index}") for index in range(1, 4)
        ]
        values = pd.to_numeric(assays[element], errors="coerce")
        boundary = pd.to_numeric(pd.Series([profile.get("boundary_grade_ppm")]), errors="coerce").iloc[0]
        industrial = pd.to_numeric(pd.Series([profile.get("industrial_grade_ppm")]), errors="coerce").iloc[0]
        # T, 2T and 4T are inclusive lower bounds. Keep the interval selection
        # consistent with classify_mining_level and with the displayed legend.
        mask = values >= threshold
        if np.isfinite(boundary):
            mask |= values >= float(boundary)
        if np.isfinite(industrial):
            mask |= values >= float(industrial)
        for index, row in assays.loc[mask].iterrows():
            value = float(values.loc[index])
            mining = classify_mining_level(value, profile)
            rows.append(
                {
                    "assay_id": row["assay_id"],
                    "hole_id": row["hole_id"],
                    "from_depth": row["from_depth"],
                    "to_depth": row["to_depth"],
                    "mid_depth": row["mid_depth"],
                    "interval_length_m": float(row["to_depth"]) - float(row["from_depth"]),
                    "element": element,
                    "value": value,
                    "threshold_T_auto": threshold,
                    "threshold_source": statistic.get("threshold_source", "auto"),
                    "value_to_threshold_ratio": value / threshold,
                    "background_mean": profile.get("background_mean"),
                    "value_to_background_ratio": mining["value_to_background_ratio"],
                    "relative_deviation": mining["relative_deviation"],
                    "anomaly_level": _anomaly_level(value, threshold),
                    **mining,
                    "evidence_channel": _evidence_channel(
                        mining["meets_statistical_threshold"],
                        mining["meets_boundary_grade"],
                        mining["meets_industrial_grade"],
                    ),
                    "boundary_grade_ppm": profile.get("boundary_grade_ppm"),
                    "industrial_grade_ppm": profile.get("industrial_grade_ppm"),
                    "rule_set_id": profile.get("rule_set_id"),
                    "rule_set_status": profile.get("rule_set_status"),
                    "x_approx": row["x_approx"],
                    "y_approx": row["y_approx"],
                    "z_approx": row["z_approx"],
                    "coordinate_quality": row["coordinate_quality"],
                    "section_match_status": row["section_match_status"],
                    "section_geobody_key": row["section_geobody_key"],
                    "stratum_code": row.get("stratum_code", ""),
                    "lithology": row.get("lithology", ""),
                    "weathering": row.get("weathering", ""),
                    "engineering_grade": row.get("engineering_grade", ""),
                    "geobody_property_status": row["geobody_property_status"],
                    "data_quality_flags": row["data_quality_flags"],
                }
            )
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(["element", "hole_id", "from_depth", "to_depth"]).reset_index(drop=True)
    return result


def merge_anomaly_segments(
    anomalies: pd.DataFrame,
    merge_gap_m: float = 0.0,
    minimum_segment_length_m: float = 0.0,
) -> pd.DataFrame:
    columns = [
        "hole_id", "element", "segment_from_depth", "segment_to_depth", "segment_length_m",
        "interval_count", "assay_ids", "max_value", "length_weighted_mean_value",
        "threshold_T_auto", "max_value_to_threshold_ratio", "max_value_to_background_ratio",
        "highest_anomaly_level", "highest_mining_level", "highest_statistical_anomaly_level",
        "evidence_channels", "statistical_interval_count", "professional_interval_count",
        "boundary_grade_ppm", "industrial_grade_ppm", "meets_boundary_grade",
        "meets_industrial_grade", "display_color_hex", "rule_set_id", "rule_set_status",
        "x_approx", "y_approx", "z_approx_mid", "coordinate_quality",
        "section_geobody_keys", "stratum_codes", "lithologies", "weathering_classes",
        "geobody_property_statuses",
    ]
    if anomalies.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    for (_, _), group in anomalies.groupby(["hole_id", "element"], dropna=False):
        current: list[dict[str, Any]] = []
        current_end = -np.inf
        for record in group.sort_values(["from_depth", "to_depth"]).to_dict("records"):
            if current and float(record["from_depth"]) > current_end + float(merge_gap_m) + 1e-9:
                rows.append(_summarise_segment(current))
                current = []
            current.append(record)
            current_end = max(current_end, float(record["to_depth"]))
        if current:
            rows.append(_summarise_segment(current))
    result = pd.DataFrame(rows, columns=columns)
    if minimum_segment_length_m > 0:
        result = result.loc[result["segment_length_m"] >= minimum_segment_length_m]
    return result.sort_values(["element", "hole_id", "segment_from_depth"]).reset_index(drop=True)


def _summarise_segment(records: list[dict[str, Any]]) -> dict[str, Any]:
    first = records[0]
    start = min(float(record["from_depth"]) for record in records)
    end = max(float(record["to_depth"]) for record in records)
    mining_levels = [str(record.get("mining_level") or record.get("anomaly_level", "不可判定")) for record in records]
    statistical_rank = {"非统计异常": 0, "统计异常外带": 1, "统计异常中带": 2, "统计异常内带": 3}
    statistical_levels = [str(record.get("anomaly_level", "不可判定")) for record in records]
    highest = max(mining_levels, key=lambda value: MINING_LEVEL_RANK.get(value, 0))
    highest_record = max(
        records,
        key=lambda record: MINING_LEVEL_RANK.get(
            str(record.get("mining_level") or record.get("anomaly_level", "")), 0
        ),
    )
    lengths = np.asarray(
        [max(0.0, float(record["to_depth"]) - float(record["from_depth"])) for record in records]
    )
    values = np.asarray([float(record["value"]) for record in records])
    weighted_mean = float(np.average(values, weights=lengths)) if lengths.sum() > 0 else float(values.mean())
    collar_elevations = [
        float(record["z_approx"]) + (float(record["from_depth"]) + float(record["to_depth"])) / 2.0
        for record in records
        if np.isfinite(record.get("z_approx", np.nan))
    ]
    z_mid = float(np.mean(collar_elevations)) - (start + end) / 2.0 if collar_elevations else np.nan
    joined = lambda key: ",".join(sorted({str(record.get(key)) for record in records if record.get(key)}))
    return {
        "hole_id": first["hole_id"],
        "element": first["element"],
        "segment_from_depth": start,
        "segment_to_depth": end,
        "segment_length_m": end - start,
        "interval_count": len(records),
        "assay_ids": ",".join(str(record["assay_id"]) for record in records),
        "max_value": float(values.max()),
        "length_weighted_mean_value": weighted_mean,
        "threshold_T_auto": first["threshold_T_auto"],
        "max_value_to_threshold_ratio": max(float(record["value_to_threshold_ratio"]) for record in records),
        "max_value_to_background_ratio": max(
            (float(record["value_to_background_ratio"]) for record in records if np.isfinite(record.get("value_to_background_ratio", np.nan))),
            default=np.nan,
        ),
        "highest_anomaly_level": highest,
        "highest_mining_level": highest,
        "highest_statistical_anomaly_level": max(
            statistical_levels, key=lambda value: statistical_rank.get(value, 0)
        ),
        "evidence_channels": joined("evidence_channel"),
        "statistical_interval_count": sum(bool(record.get("meets_statistical_threshold")) for record in records),
        "professional_interval_count": sum(
            bool(record.get("meets_boundary_grade") or record.get("meets_industrial_grade"))
            for record in records
        ),
        "boundary_grade_ppm": first.get("boundary_grade_ppm", np.nan),
        "industrial_grade_ppm": first.get("industrial_grade_ppm", np.nan),
        "meets_boundary_grade": any(bool(record.get("meets_boundary_grade")) for record in records),
        "meets_industrial_grade": any(bool(record.get("meets_industrial_grade")) for record in records),
        "display_color_hex": highest_record.get("display_color_hex", ""),
        "rule_set_id": first.get("rule_set_id"),
        "rule_set_status": first.get("rule_set_status"),
        "x_approx": first.get("x_approx"),
        "y_approx": first.get("y_approx"),
        "z_approx_mid": z_mid,
        "coordinate_quality": first.get("coordinate_quality"),
        "section_geobody_keys": joined("section_geobody_key"),
        "stratum_codes": joined("stratum_code"),
        "lithologies": joined("lithology"),
        "weathering_classes": joined("weathering"),
        "geobody_property_statuses": joined("geobody_property_status"),
    }


def build_depth_variation(
    assays: pd.DataFrame,
    elements: list[str],
    reference_means: dict[str, float],
    config: VariationConfig,
) -> pd.DataFrame:
    working = assays.copy()
    start = np.floor(working["mid_depth"] / config.depth_bin_size_m) * config.depth_bin_size_m
    working["depth_bin"] = start.map(
        lambda value: "" if not np.isfinite(value) else f"{int(value)}-{int(value + config.depth_bin_size_m)}m"
    )
    return _group_enrichment(
        working, elements, "depth_bin", reference_means, config.minimum_group_positive_count
    )


def _variation_report(
    dataset: GeochemDataset,
    baseline_dataset: GeochemDataset,
    quality: pd.DataFrame,
    statistics: pd.DataFrame,
    anomalies: pd.DataFrame,
    segments: pd.DataFrame,
    config: VariationConfig,
) -> str:
    statistical = int(anomalies["meets_statistical_threshold"].sum()) if not anomalies.empty else 0
    boundary = int(anomalies["meets_boundary_grade"].sum()) if not anomalies.empty else 0
    industrial = int(anomalies["meets_industrial_grade"].sum()) if not anomalies.empty else 0
    return f"""# 化学元素变化规律分析报告

## 口径与范围

- 展示/筛选区间：{len(valid_intervals(dataset.assays))}
- 冻结背景区间：{len(valid_intervals(baseline_dataset.assays))}
- 背景范围：`{config.background_scope}`；主方法：`{config.background_method}`
- 规则集：`{config.rule_set_id or '未绑定'}`（`{config.rule_set_status}`）
- 统计异常记录：{statistical}
- 达边界品位记录：{boundary}
- 达最低工业品位记录：{industrial}
- 关注记录并集：{len(anomalies)}；合并异常段：{len(segments)}

统计异常、边界品位和最低工业品位是三个独立证据通道。关注记录是三者并集，
不能把并集数量表述为“统计异常数量”。筛选只改变展示样本；当背景范围为
`model` 时，背景值始终由当前模型的全孔有效区间计算。

## 背景方法敏感性

{markdown_table(statistics, ['element', 'background_method', 'background_mean', 'threshold_T_auto', 'legacy_threshold_T', 'robust_threshold_T', 'threshold_method_ratio_robust_to_legacy'], 20)}

## 数据质量

{markdown_table(quality, ['element', 'positive_count', 'zero_count', 'missing_count', 'used_for_variation', 'exclusion_reason'], 20)}

## 重点异常段

{markdown_table(segments.sort_values('max_value_to_background_ratio', ascending=False) if not segments.empty else segments, ['element', 'hole_id', 'segment_from_depth', 'segment_to_depth', 'segment_length_m', 'highest_mining_level', 'evidence_channels', 'max_value_to_background_ratio'], 20)}

## 限制

1. `z_approx` 基于垂直钻孔假设，不等同于实测轨迹。
2. 零值在检出限语义未知时不进入主背景统计。
3. 最低工业品位未确认的元素保持待定，程序不会自行补值。
4. 规则集为 `draft` 时，颜色和候选区只能作为算法草案，不能写成已确认结论。
"""


def run_variation_algorithm(
    db_path: Path | str = DEFAULT_DB_PATH,
    model_id: int | None = None,
    output_dir: Path | str | None = None,
    config: VariationConfig = VariationConfig(),
    filters: dict | None = None,
) -> dict[str, Path]:
    output_dir = Path(output_dir) if output_dir else ROOT_OUTPUT_DIR / "element_variation"
    ensure_dir(output_dir)
    filters = filters or {}
    dataset = load_geochem_dataset(db_path, model_id=model_id, **filters)
    baseline_filters = filters if config.background_scope == "selection" else {
        "selected_elements": filters.get("selected_elements")
    }
    baseline_dataset = load_geochem_dataset(db_path, model_id=model_id, **baseline_filters)
    assays = valid_intervals(dataset.assays)
    baseline_assays = valid_intervals(baseline_dataset.assays)

    quality = build_variation_quality(
        baseline_assays,
        baseline_dataset.elements,
        config.minimum_positive_count,
        config.element_rules,
    )
    selection_quality = build_variation_quality(
        assays,
        dataset.elements,
        config.minimum_positive_count,
        config.element_rules,
    )
    statistics = build_background_statistics(baseline_assays, quality, config)
    used_elements = statistics.loc[statistics["used_for_variation"], "element"].tolist()
    reference_means = statistics.set_index("element")["background_mean"].to_dict()
    anomalies = build_anomaly_intervals(
        assays, statistics.loc[statistics["used_for_variation"]]
    )
    statistical_anomalies = (
        anomalies.loc[anomalies["meets_statistical_threshold"]].copy()
        if not anomalies.empty else anomalies.copy()
    )
    professional_anomalies = (
        anomalies.loc[anomalies["meets_boundary_grade"] | anomalies["meets_industrial_grade"]].copy()
        if not anomalies.empty else anomalies.copy()
    )
    segments = merge_anomaly_segments(
        anomalies,
        merge_gap_m=config.merge_gap_m,
        minimum_segment_length_m=config.minimum_segment_length_m,
    )
    by_hole = _group_enrichment(
        assays, used_elements, "hole_id", reference_means, config.minimum_group_positive_count
    )
    by_section = _group_enrichment(
        assays.loc[assays["section_match_status"] == "unique_match"],
        used_elements,
        "section_geobody_key",
        reference_means,
        config.minimum_group_positive_count,
    )
    by_depth = build_depth_variation(assays, used_elements, reference_means, config)

    outputs = {
        "fused_assay_dataset": write_csv(dataset.assays, output_dir / "fused_assay_dataset.csv"),
        "baseline_assay_dataset": write_csv(
            baseline_dataset.assays, output_dir / "baseline_assay_dataset.csv"
        ),
        "invalid_interval_audit": write_csv(dataset.invalid_intervals, output_dir / "invalid_interval_audit.csv"),
        "section_match_audit": write_csv(dataset.section_match_audit, output_dir / "section_match_audit.csv"),
        "variation_data_quality": write_csv(quality, output_dir / "variation_data_quality.csv"),
        "selection_data_quality": write_csv(selection_quality, output_dir / "selection_data_quality.csv"),
        "element_background_statistics": write_csv(statistics, output_dir / "element_background_statistics.csv"),
        "background_method_comparison": write_csv(
            statistics[
                [
                    "element", "background_method", "background_distribution", "normality_p_value",
                    "threshold_formula", "background_mean", "threshold_T_auto",
                    "legacy_background_mean", "legacy_threshold_T",
                    "robust_background_mean", "robust_threshold_T",
                    "threshold_method_ratio_robust_to_legacy",
                ]
            ],
            output_dir / "background_method_comparison.csv",
        ),
        "hole_element_variation": write_csv(by_hole, output_dir / "hole_element_variation.csv"),
        "section_element_enrichment": write_csv(by_section, output_dir / "section_element_enrichment.csv"),
        "depth_element_variation": write_csv(by_depth, output_dir / "depth_element_variation.csv"),
        "anomaly_intervals": write_csv(anomalies, output_dir / "anomaly_intervals.csv"),
        "attention_intervals": write_csv(anomalies, output_dir / "attention_intervals.csv"),
        "statistical_anomaly_intervals": write_csv(
            statistical_anomalies, output_dir / "statistical_anomaly_intervals.csv"
        ),
        "professional_grade_intervals": write_csv(
            professional_anomalies, output_dir / "professional_grade_intervals.csv"
        ),
        "merged_anomaly_segments": write_csv(segments, output_dir / "merged_anomaly_segments.csv"),
    }
    outputs["variation_report"] = write_text(
        _variation_report(
            dataset, baseline_dataset, quality, statistics, anomalies, segments, config
        ),
        output_dir / "variation_report.md",
    )
    return outputs


ROOT_OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
