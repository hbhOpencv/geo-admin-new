"""算法二：化学元素相关性挖掘算法。

核心是 Pearson 相关系数与 R 型层次聚类。Apriori、SVM、体素预测
不属于本模块，也不会在这里实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial import Delaunay, QhullError, cKDTree
from scipy.spatial.distance import squareform
from scipy.stats import fisher_exact, spearmanr

from .data_pipeline import (
    DEFAULT_DB_PATH,
    ensure_dir,
    load_geochem_dataset,
    markdown_table,
    valid_intervals,
    write_csv,
    write_text,
)
from .professional_rules import COMMON_METAL_ELEMENTS


@dataclass(frozen=True)
class CorrelationConfig:
    minimum_positive_count: int = 30
    minimum_pair_common_count: int = 100
    strong_correlation_threshold: float = 0.50
    focus_elements: tuple[str, ...] = ()
    target_elements: tuple[str, ...] = ()
    same_hole_gap_m: float = 2.0
    cross_hole_distance_m: float = 300.0
    minimum_lift: float = 1.2
    rule_set_status: str = "draft"


def build_correlation_quality(
    assays: pd.DataFrame, elements: list[str], config: CorrelationConfig
) -> pd.DataFrame:
    rows = []
    focus_elements = set(config.focus_elements or COMMON_METAL_ELEMENTS)
    for element in elements:
        values = pd.to_numeric(assays[element], errors="coerce")
        present = int(values.notna().sum())
        zero = int((values == 0).sum())
        positive = int((values > 0).sum())
        candidate = positive >= config.minimum_positive_count
        rows.append(
            {
                "element": element,
                "field_present_count": present,
                "missing_count": len(assays) - present,
                "zero_count": zero,
                "positive_count": positive,
                "is_focus_metal": element in focus_elements,
                "zero_ratio_of_present": zero / present if present else np.nan,
                "candidate_for_pairwise_correlation": candidate,
                "used_in_r_type_cluster": False,
                "exclusion_reason": "" if candidate else f"positive_count < {config.minimum_positive_count}",
                "zero_value_policy": "zero_or_below_detection_unknown_excluded_from_main_correlation",
            }
        )
    return pd.DataFrame(rows).sort_values("element").reset_index(drop=True)


def _pairwise_correlations(
    assays: pd.DataFrame,
    candidates: list[str],
    minimum_common_count: int,
) -> pd.DataFrame:
    rows = []
    for i, first in enumerate(candidates):
        first_values = pd.to_numeric(assays[first], errors="coerce")
        for second in candidates[i + 1 :]:
            second_values = pd.to_numeric(assays[second], errors="coerce")
            mask = (first_values > 0) & (second_values > 0)
            common = pd.DataFrame({"first": first_values[mask], "second": second_values[mask]}).dropna()
            common_count = len(common)
            if common_count < minimum_common_count:
                rows.append(
                    {
                        "element_a": first,
                        "element_b": second,
                        "n_common": common_count,
                        "pearson_raw_r": np.nan,
                        "pearson_log_r": np.nan,
                        "pearson_r": np.nan,
                        "spearman_r": np.nan,
                        "primary_correlation_scale": "log_positive_values",
                        "pair_status": "insufficient_common_positive_values",
                    }
                )
                continue

            pearson_r = float(common["first"].corr(common["second"], method="pearson"))
            pearson_log_r = float(
                np.log(common["first"]).corr(np.log(common["second"]), method="pearson")
            )
            spearman_r = float(spearmanr(common["first"], common["second"], nan_policy="omit").statistic)
            status = (
                "ok"
                if np.isfinite(pearson_log_r) and np.isfinite(spearman_r)
                else "constant_or_invalid"
            )
            rows.append(
                {
                    "element_a": first,
                    "element_b": second,
                    "n_common": common_count,
                    "pearson_raw_r": pearson_r,
                    "pearson_log_r": pearson_log_r,
                    "pearson_r": pearson_log_r,
                    "spearman_r": spearman_r,
                    "primary_correlation_scale": "log_positive_values",
                    "pair_status": status,
                }
            )
    return pd.DataFrame(rows)


def _correlation_level(value: float, threshold: float) -> str:
    if not np.isfinite(value):
        return "不可判定"
    if value >= 0.70:
        return "强正相关"
    if value >= threshold:
        return "较强正相关"
    if value >= 0.30:
        return "中等正相关"
    if value <= -0.70:
        return "强负相关"
    if value <= -threshold:
        return "较强负相关"
    if value <= -0.30:
        return "中等负相关"
    return "弱相关"


def build_correlation_matrices(
    pairwise: pd.DataFrame, elements: list[str], strong_threshold: float
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    pearson = pd.DataFrame(np.nan, index=elements, columns=elements, dtype=float)
    spearman = pd.DataFrame(np.nan, index=elements, columns=elements, dtype=float)
    # pandas 3 may expose a read-only ndarray through .values.
    for element in elements:
        pearson.loc[element, element] = 1.0
        spearman.loc[element, element] = 1.0
    report_rows = []
    for _, row in pairwise.iterrows():
        first, second = row["element_a"], row["element_b"]
        pearson.loc[first, second] = pearson.loc[second, first] = row["pearson_r"]
        spearman.loc[first, second] = spearman.loc[second, first] = row["spearman_r"]
        pearson_r = row["pearson_r"]
        spearman_r = row["spearman_r"]
        pearson_strong = np.isfinite(pearson_r) and abs(pearson_r) >= strong_threshold
        spearman_strong = np.isfinite(spearman_r) and abs(spearman_r) >= strong_threshold
        same_direction = np.sign(pearson_r) == np.sign(spearman_r)
        if pearson_strong and spearman_strong and same_direction:
            stability = "Pearson与Spearman均较强且方向一致"
        elif pearson_strong and spearman_strong:
            stability = "Pearson与Spearman方向不一致，需复核"
        elif pearson_strong:
            stability = "Pearson较强但Spearman未达阈值，需警惕极端值影响"
        else:
            stability = "未达到较强相关阈值"
        report_rows.append(
            {
                **row.to_dict(),
                "correlation_level": _correlation_level(pearson_r, strong_threshold),
                "stability_note": stability,
            }
        )
    return pearson, spearman, pd.DataFrame(report_rows)


def annotate_focus_results(
    pairs: pd.DataFrame,
    clusters: pd.DataFrame,
    focus_elements: tuple[str, ...] | list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """标记常见金属优先结果，但不删除其他元素的计算结果。"""

    focus = set(focus_elements or COMMON_METAL_ELEMENTS)
    pairs = pairs.copy()
    if not pairs.empty:
        pairs["focus_element_count"] = pairs.apply(
            lambda row: int(row["element_a"] in focus) + int(row["element_b"] in focus), axis=1
        )
        pairs["contains_focus_metal"] = pairs["focus_element_count"] > 0
        pairs["focus_members"] = pairs.apply(
            lambda row: ",".join(element for element in (row["element_a"], row["element_b"]) if element in focus),
            axis=1,
        )
        pairs = pairs.assign(_abs_r=pd.to_numeric(pairs["pearson_r"], errors="coerce").abs()).sort_values(
            ["focus_element_count", "_abs_r", "n_common"], ascending=[False, False, False]
        ).drop(columns="_abs_r").reset_index(drop=True)

    clusters = clusters.copy()
    if not clusters.empty:
        members = clusters["elements"].fillna("").astype(str).str.split(",")
        clusters["focus_members"] = members.apply(lambda values: ",".join(value for value in values if value in focus))
        clusters["focus_element_count"] = members.apply(lambda values: sum(value in focus for value in values))
        clusters["contains_focus_metal"] = clusters["focus_element_count"] > 0
        clusters = clusters.sort_values(
            ["contains_focus_metal", "focus_element_count", "element_count", "mean_internal_pearson_r"],
            ascending=[False, False, False, False],
        ).reset_index(drop=True)
    return pairs, clusters


def _best_complete_correlation_set(
    pearson: pd.DataFrame, quality: pd.DataFrame
) -> list[str]:
    """找出一个所有元素对都拥有有效相关系数的最大贪心集合。

    聚类不能对缺失相关系数随意补 0；因此将不完整元素对排除并在质量表中说明。
    """

    candidates = quality.loc[
        quality["candidate_for_pairwise_correlation"], ["element", "positive_count"]
    ].sort_values("positive_count", ascending=False)
    elements = candidates["element"].tolist()
    counts = candidates.set_index("element")["positive_count"].to_dict()
    best: list[str] = []
    best_score = -1
    for seed in elements:
        selected = [seed]
        for element in elements:
            if element == seed:
                continue
            if all(np.isfinite(pearson.loc[element, existing]) for existing in selected):
                selected.append(element)
        score = sum(counts[element] for element in selected)
        if len(selected) > len(best) or (len(selected) == len(best) and score > best_score):
            best, best_score = selected, score
    return sorted(best)


def r_type_cluster(
    pearson: pd.DataFrame, strong_threshold: float
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """以 distance=1-r、average linkage 形成 R 型层次聚类。"""

    elements = list(pearson.index)
    if len(elements) < 2:
        empty_groups = pd.DataFrame(columns=["cluster_id", "elements", "element_count", "is_multielement_combination", "mean_internal_pearson_r"])
        empty_merges = pd.DataFrame(columns=["merge_step", "left_node", "right_node", "distance", "new_cluster_size"])
        return empty_groups, empty_merges, pd.DataFrame()

    distance = 1.0 - pearson
    for element in distance.index:
        distance.loc[element, element] = 0.0
    condensed = squareform(distance.values, checks=True)
    linkage_matrix = linkage(condensed, method="average")
    labels = fcluster(linkage_matrix, t=1.0 - strong_threshold, criterion="distance")

    cluster_rows = []
    for cluster_id in sorted(set(labels)):
        members = [element for element, label in zip(elements, labels) if label == cluster_id]
        if len(members) < 2:
            mean_r = np.nan
        else:
            sub = pearson.loc[members, members].to_numpy(dtype=float)
            upper = sub[np.triu_indices_from(sub, k=1)]
            mean_r = float(np.mean(upper))
        cluster_rows.append(
            {
                "cluster_id": int(cluster_id),
                "elements": ",".join(members),
                "element_count": len(members),
                "is_multielement_combination": len(members) >= 2,
                "mean_internal_pearson_r": mean_r,
                "cut_distance": 1.0 - strong_threshold,
                "candidate_rule": f"Pearson r >= {strong_threshold:.2f}",
            }
        )

    names: dict[int, str] = {index: element for index, element in enumerate(elements)}
    sizes: dict[int, int] = {index: 1 for index in range(len(elements))}
    merge_rows = []
    for step, (left, right, distance_value, count) in enumerate(linkage_matrix, start=1):
        left_id, right_id = int(left), int(right)
        new_id = len(elements) + step - 1
        names[new_id] = f"cluster_{step}"
        sizes[new_id] = int(count)
        merge_rows.append(
            {
                "merge_step": step,
                "left_node": names[left_id],
                "right_node": names[right_id],
                "distance": float(distance_value),
                "new_cluster_size": int(count),
            }
        )
    linkage_frame = pd.DataFrame(linkage_matrix, columns=["left_id", "right_id", "distance", "cluster_size"])
    return pd.DataFrame(cluster_rows), pd.DataFrame(merge_rows), linkage_frame


def enrich_cluster_statistics(
    clusters: pd.DataFrame,
    pearson: pd.DataFrame,
    spearman: pd.DataFrame,
    strong_threshold: float,
) -> pd.DataFrame:
    """Add transparent stability evidence to each R-type cluster."""
    if clusters.empty:
        return clusters
    rows = []
    for _, cluster in clusters.iterrows():
        members = [item for item in str(cluster["elements"]).split(",") if item]
        row = cluster.to_dict()
        if len(members) < 2:
            row.update({
                "min_internal_pearson_r": np.nan,
                "max_internal_pearson_r": np.nan,
                "mean_internal_spearman_r": np.nan,
                "stable_pair_ratio": np.nan,
                "confidence_level": "单元素",
                "confidence_reason": "未形成多元素组合",
            })
            rows.append(row)
            continue
        p = pearson.loc[members, members].to_numpy(dtype=float)
        s = spearman.loc[members, members].to_numpy(dtype=float)
        idx = np.triu_indices_from(p, k=1)
        p_values, s_values = p[idx], s[idx]
        valid = np.isfinite(p_values) & np.isfinite(s_values)
        stable = valid & (p_values >= strong_threshold) & (s_values >= strong_threshold)
        stable_ratio = float(stable.sum() / valid.sum()) if valid.any() else np.nan
        mean_p = float(np.nanmean(p_values))
        min_p = float(np.nanmin(p_values))
        if np.isfinite(stable_ratio) and stable_ratio >= 0.8 and min_p >= strong_threshold:
            confidence, reason = "高", "组内相关均达到阈值，且Pearson与Spearman高度一致"
        elif np.isfinite(stable_ratio) and stable_ratio >= 0.5:
            confidence, reason = "中", "多数元素对在Pearson与Spearman中保持一致"
        else:
            confidence, reason = "低", "组内相关或两种相关系数的一致性有限"
        row.update({
            "mean_internal_pearson_r": mean_p,
            "min_internal_pearson_r": min_p,
            "max_internal_pearson_r": float(np.nanmax(p_values)),
            "mean_internal_spearman_r": float(np.nanmean(s_values)),
            "stable_pair_ratio": stable_ratio,
            "confidence_level": confidence,
            "confidence_reason": reason,
        })
        rows.append(row)
    return pd.DataFrame(rows)


def build_cluster_geology_summary(clusters: pd.DataFrame, assays: pd.DataFrame) -> pd.DataFrame:
    """Summarise where cluster members jointly show high values.

    This is deliberately labelled as joint high values, not an anomaly result;
    formal anomalies come from a linked variation task.
    """
    columns = [
        "cluster_id", "elements", "joint_high_interval_count", "joint_high_ratio",
        "dominant_holes", "dominant_depth_range", "dominant_section_geobody_keys",
        "dominant_stratum_codes", "dominant_lithologies", "dominant_weathering",
        "spatial_evidence_status",
    ]
    rows = []
    total = len(assays)
    for _, cluster in clusters.loc[clusters["is_multielement_combination"]].iterrows():
        members = [item for item in str(cluster["elements"]).split(",") if item in assays.columns]
        high_flags = pd.DataFrame(index=assays.index)
        for member in members:
            values = pd.to_numeric(assays[member], errors="coerce")
            positive = values[values > 0]
            threshold = float(positive.quantile(0.75)) if len(positive) else np.nan
            high_flags[member] = values >= threshold if np.isfinite(threshold) else False
        joint = high_flags.sum(axis=1) >= 2 if len(high_flags.columns) >= 2 else pd.Series(False, index=assays.index)
        selected = assays.loc[joint]
        def top_values(column: str) -> str:
            if column not in selected or selected.empty:
                return ""
            values = selected[column].replace("", np.nan).dropna().astype(str)
            return ",".join(values.value_counts().head(3).index.tolist())
        if selected.empty:
            depth_range = ""
        else:
            depth_range = f"{selected['from_depth'].min():.2f}-{selected['to_depth'].max():.2f}m"
        rows.append({
            "cluster_id": int(cluster["cluster_id"]),
            "elements": str(cluster["elements"]),
            "joint_high_interval_count": int(joint.sum()),
            "joint_high_ratio": float(joint.mean()) if total else np.nan,
            "dominant_holes": top_values("hole_id"),
            "dominant_depth_range": depth_range,
            "dominant_section_geobody_keys": top_values("section_geobody_key"),
            "dominant_stratum_codes": top_values("stratum_code"),
            "dominant_lithologies": top_values("lithology"),
            "dominant_weathering": top_values("weathering"),
            "spatial_evidence_status": "joint_upper_quartile_values" if not selected.empty else "no_joint_high_values",
        })
    return pd.DataFrame(rows, columns=columns)


def build_threshold_sensitivity(
    pearson: pd.DataFrame,
    thresholds: tuple[float, ...] = (0.40, 0.50, 0.60, 0.70),
) -> pd.DataFrame:
    """Re-cut the same R-type tree at several transparent r thresholds."""
    columns = [
        "correlation_threshold", "cut_distance", "cluster_count",
        "multielement_cluster_count", "multielement_groups",
    ]
    if pearson.empty or len(pearson) < 2:
        return pd.DataFrame(columns=columns)
    rows = []
    for threshold in sorted(set(float(value) for value in thresholds)):
        groups, _, _ = r_type_cluster(pearson, threshold)
        multi = groups.loc[groups["is_multielement_combination"]]
        rows.append({
            "correlation_threshold": threshold,
            "cut_distance": 1.0 - threshold,
            "cluster_count": len(groups),
            "multielement_cluster_count": len(multi),
            "multielement_groups": "; ".join(multi["elements"].astype(str).tolist()),
        })
    return pd.DataFrame(rows, columns=columns)


def build_cluster_overlap(
    clusters: pd.DataFrame, variation_output_dir: Path
) -> pd.DataFrame:
    columns = [
        "cluster_id", "elements", "variation_status", "anomaly_record_count", "assays_with_any_anomaly",
        "assays_with_two_or_more_members_anomalous", "co_anomaly_ratio", "dominant_holes",
        "dominant_section_geobody_keys",
    ]
    anomaly_file = variation_output_dir / "anomaly_intervals.csv"
    if not anomaly_file.exists():
        rows = []
        for _, cluster in clusters.loc[clusters["is_multielement_combination"]].iterrows():
            rows.append(
                {
                    "cluster_id": int(cluster["cluster_id"]),
                    "elements": str(cluster["elements"]),
                    "variation_status": "variation_algorithm_not_run",
                    "anomaly_record_count": 0,
                    "assays_with_any_anomaly": 0,
                    "assays_with_two_or_more_members_anomalous": 0,
                    "co_anomaly_ratio": np.nan,
                    "dominant_holes": "",
                    "dominant_section_geobody_keys": "",
                }
            )
        return pd.DataFrame(rows, columns=columns)

    anomalies = pd.read_csv(anomaly_file, encoding="utf-8-sig")
    rows = []
    for _, cluster in clusters.loc[clusters["is_multielement_combination"]].iterrows():
        members = [member for member in str(cluster["elements"]).split(",") if member]
        selected = anomalies.loc[anomalies["element"].isin(members)].copy()
        if selected.empty:
            rows.append(
                {
                    "cluster_id": cluster["cluster_id"],
                    "elements": cluster["elements"],
                    "variation_status": "no_anomaly_overlap",
                    "anomaly_record_count": 0,
                    "assays_with_any_anomaly": 0,
                    "assays_with_two_or_more_members_anomalous": 0,
                    "co_anomaly_ratio": np.nan,
                    "dominant_holes": "",
                    "dominant_section_geobody_keys": "",
                }
            )
            continue
        per_assay = selected.groupby("assay_id")["element"].nunique()
        any_count = int(len(per_assay))
        co_anomaly_ids = per_assay.loc[per_assay >= 2].index
        two_plus = int(len(co_anomaly_ids))
        co_anomaly_intervals = selected.loc[selected["assay_id"].isin(co_anomaly_ids)].drop_duplicates("assay_id")
        holes = co_anomaly_intervals["hole_id"].value_counts().head(3).index.astype(str).tolist()
        section_keys = (
            co_anomaly_intervals["section_geobody_key"]
            .replace("", np.nan)
            .dropna()
            .value_counts()
            .head(3)
            .index.astype(str)
            .tolist()
        )
        rows.append(
            {
                "cluster_id": cluster["cluster_id"],
                "elements": cluster["elements"],
                "variation_status": "ok",
                "anomaly_record_count": len(selected),
                "assays_with_any_anomaly": any_count,
                "assays_with_two_or_more_members_anomalous": two_plus,
                "co_anomaly_ratio": two_plus / any_count if any_count else np.nan,
                "dominant_holes": ",".join(holes),
                "dominant_section_geobody_keys": ",".join(section_keys),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _boolean_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(False, index=frame.index)
    return frame[column].astype(str).str.lower().isin(["true", "1", "yes"])


def build_target_pair_coanomaly(
    assays: pd.DataFrame,
    elements: list[str],
    variation_output_dir: Path,
    config: CorrelationConfig,
) -> pd.DataFrame:
    """Compute target-centred same-assay co-anomaly with explicit denominators."""

    columns = [
        "target_element", "candidate_element", "variation_status", "n_common_positive",
        "n11_both_anomalous", "n10_target_only", "n01_candidate_only", "n00_neither",
        "target_anomaly_count", "candidate_anomaly_count", "co_anomaly_count",
        "target_statistical_anomaly_count", "candidate_statistical_anomaly_count",
        "statistical_co_anomaly_count", "professional_co_anomaly_count",
        "support", "p_candidate_given_target", "jaccard", "lift",
        "odds_ratio_corrected", "fisher_p", "bh_q", "fdr_pass_0_10",
        "dominant_holes",
        "dominant_section_geobody_keys",
    ]
    anomaly_file = variation_output_dir / "anomaly_intervals.csv"
    if not anomaly_file.exists():
        return pd.DataFrame(columns=columns)
    anomalies = pd.read_csv(anomaly_file, encoding="utf-8-sig")
    if anomalies.empty:
        return pd.DataFrame(columns=columns)
    focus = [
        element for element in (config.target_elements or elements)
        if element in elements
    ]
    if not focus:
        focus = list(elements)
    anomaly_ids = {
        element: set(group["assay_id"].tolist())
        for element, group in anomalies.groupby("element")
    }
    statistical = anomalies.loc[_boolean_series(anomalies, "meets_statistical_threshold")]
    professional = anomalies.loc[
        _boolean_series(anomalies, "meets_boundary_grade")
        | _boolean_series(anomalies, "meets_industrial_grade")
    ]
    statistical_ids = {
        element: set(group["assay_id"].tolist()) for element, group in statistical.groupby("element")
    }
    professional_ids = {
        element: set(group["assay_id"].tolist()) for element, group in professional.groupby("element")
    }
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for target in focus:
        target_values = pd.to_numeric(assays[target], errors="coerce")
        for candidate in elements:
            if candidate == target:
                continue
            key = tuple(sorted((target, candidate)))
            if candidate in focus and key in seen:
                continue
            seen.add(key)
            candidate_values = pd.to_numeric(assays[candidate], errors="coerce")
            denominator_ids = set(
                assays.loc[(target_values > 0) & (candidate_values > 0), "assay_id"].tolist()
            )
            if not denominator_ids:
                continue
            target_ids = anomaly_ids.get(target, set()) & denominator_ids
            candidate_ids = anomaly_ids.get(candidate, set()) & denominator_ids
            both = target_ids & candidate_ids
            union = target_ids | candidate_ids
            n = len(denominator_ids)
            p_target = len(target_ids) / n
            p_candidate = len(candidate_ids) / n
            p_both = len(both) / n
            n11 = len(both)
            n10 = len(target_ids - candidate_ids)
            n01 = len(candidate_ids - target_ids)
            n00 = max(0, n - n11 - n10 - n01)
            # Haldane-Anscombe correction keeps the odds ratio finite when a
            # cell is zero.  Fisher's exact test itself uses the exact counts.
            odds_ratio_corrected = (
                (n11 + 0.5) * (n00 + 0.5)
                / ((n10 + 0.5) * (n01 + 0.5))
            )
            fisher_p = float(
                fisher_exact([[n11, n10], [n01, n00]], alternative="greater").pvalue
            )
            selected = anomalies.loc[anomalies["assay_id"].isin(both)].drop_duplicates("assay_id")

            def dominant(column: str) -> str:
                if column not in selected:
                    return ""
                values = selected[column].replace("", np.nan).dropna().astype(str)
                return ",".join(values.value_counts().head(3).index.tolist())

            rows.append(
                {
                    "target_element": target,
                    "candidate_element": candidate,
                    "variation_status": "bound_variation",
                    "n_common_positive": n,
                    "n11_both_anomalous": n11,
                    "n10_target_only": n10,
                    "n01_candidate_only": n01,
                    "n00_neither": n00,
                    "target_anomaly_count": len(target_ids),
                    "candidate_anomaly_count": len(candidate_ids),
                    "co_anomaly_count": n11,
                    "target_statistical_anomaly_count": len(
                        statistical_ids.get(target, set()) & denominator_ids
                    ),
                    "candidate_statistical_anomaly_count": len(
                        statistical_ids.get(candidate, set()) & denominator_ids
                    ),
                    "statistical_co_anomaly_count": len(
                        statistical_ids.get(target, set())
                        & statistical_ids.get(candidate, set())
                        & denominator_ids
                    ),
                    "professional_co_anomaly_count": len(
                        professional_ids.get(target, set())
                        & professional_ids.get(candidate, set())
                        & denominator_ids
                    ),
                    "support": n11 / n,
                    "p_candidate_given_target": n11 / len(target_ids) if target_ids else np.nan,
                    "jaccard": len(both) / len(union) if union else np.nan,
                    "lift": p_both / (p_target * p_candidate) if p_target > 0 and p_candidate > 0 else np.nan,
                    "odds_ratio_corrected": odds_ratio_corrected,
                    "fisher_p": fisher_p,
                    "bh_q": np.nan,
                    "fdr_pass_0_10": False,
                    "dominant_holes": dominant("hole_id"),
                    "dominant_section_geobody_keys": dominant("section_geobody_key"),
                }
            )
    result = pd.DataFrame(rows, columns=columns)
    if result.empty:
        return result
    # Benjamini-Hochberg correction is applied independently inside each
    # target-centred search, exactly matching the business question.
    for _, index in result.groupby("target_element").groups.items():
        indices = list(index)
        p_values = pd.to_numeric(result.loc[indices, "fisher_p"], errors="coerce")
        valid = p_values.dropna().sort_values()
        if valid.empty:
            continue
        m = len(valid)
        raw_q = pd.Series(
            [float(value) * m / rank for rank, value in enumerate(valid, start=1)],
            index=valid.index,
            dtype=float,
        )
        adjusted = raw_q.iloc[::-1].cummin().iloc[::-1].clip(upper=1.0)
        result.loc[adjusted.index, "bh_q"] = adjusted
    result["fdr_pass_0_10"] = pd.to_numeric(result["bh_q"], errors="coerce") <= 0.10
    return result.sort_values(
        ["fdr_pass_0_10", "co_anomaly_count", "lift", "jaccard"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)


def build_same_hole_coanomaly(
    pair_evidence: pd.DataFrame,
    variation_output_dir: Path,
    gap_m: float,
) -> pd.DataFrame:
    """Find co-anomalous segments in the same hole, allowing a configured gap."""

    columns = [
        "target_element", "candidate_element", "hole_id", "target_segment_from",
        "target_segment_to", "candidate_segment_from", "candidate_segment_to",
        "overlap_length_m", "gap_m", "same_section_geobody", "section_geobody_keys",
    ]
    segment_file = variation_output_dir / "merged_anomaly_segments.csv"
    if pair_evidence.empty or not segment_file.exists():
        return pd.DataFrame(columns=columns)
    segments = pd.read_csv(segment_file, encoding="utf-8-sig")
    rows: list[dict] = []
    candidate_pairs = pair_evidence.loc[pair_evidence["co_anomaly_count"] > 0]
    for _, pair in candidate_pairs.iterrows():
        target = segments.loc[segments["element"] == pair["target_element"]]
        candidate = segments.loc[segments["element"] == pair["candidate_element"]]
        for hole_id in sorted(set(target["hole_id"]) & set(candidate["hole_id"])):
            left = target.loc[target["hole_id"] == hole_id]
            right = candidate.loc[candidate["hole_id"] == hole_id]
            for _, a in left.iterrows():
                for _, b in right.iterrows():
                    overlap = min(a["segment_to_depth"], b["segment_to_depth"]) - max(
                        a["segment_from_depth"], b["segment_from_depth"]
                    )
                    gap = max(0.0, -float(overlap))
                    if gap > gap_m:
                        continue
                    a_keys = set(str(a.get("section_geobody_keys") or "").split(",")) - {""}
                    b_keys = set(str(b.get("section_geobody_keys") or "").split(",")) - {""}
                    rows.append(
                        {
                            "target_element": pair["target_element"],
                            "candidate_element": pair["candidate_element"],
                            "hole_id": hole_id,
                            "target_segment_from": a["segment_from_depth"],
                            "target_segment_to": a["segment_to_depth"],
                            "candidate_segment_from": b["segment_from_depth"],
                            "candidate_segment_to": b["segment_to_depth"],
                            "overlap_length_m": max(0.0, float(overlap)),
                            "gap_m": gap,
                            "same_section_geobody": bool(a_keys & b_keys),
                            "section_geobody_keys": ",".join(sorted(a_keys & b_keys)),
                        }
                    )
    return pd.DataFrame(rows, columns=columns)


def build_cross_hole_coanomaly(
    pair_evidence: pd.DataFrame,
    variation_output_dir: Path,
    max_distance_m: float,
) -> pd.DataFrame:
    """Summarise approximate 3D proximity across different holes."""

    columns = [
        "target_element", "candidate_element", "target_segment_count",
        "candidate_segment_count", "cross_hole_proximity_count", "minimum_distance_m",
        "median_distance_m", "same_geobody_proximity_count", "coordinate_quality",
        "topology_method", "topology_edge_count",
    ]
    segment_file = variation_output_dir / "merged_anomaly_segments.csv"
    if pair_evidence.empty or not segment_file.exists():
        return pd.DataFrame(columns=columns)
    segments = pd.read_csv(segment_file, encoding="utf-8-sig")
    coordinate_columns = ["x_approx", "y_approx", "z_approx_mid"]
    segments = segments.dropna(subset=coordinate_columns)
    hole_xy = (
        segments.groupby("hole_id")[["x_approx", "y_approx"]]
        .median()
        .sort_index()
    )
    topology_edges: set[tuple[str, str]] = set()
    topology_method = "insufficient_holes"
    if len(hole_xy) >= 2:
        holes = [str(value) for value in hole_xy.index]
        points = hole_xy.to_numpy(dtype=float)
        if len(holes) >= 3:
            try:
                triangulation = Delaunay(points)
                for simplex in triangulation.simplices:
                    for left, right in ((0, 1), (1, 2), (2, 0)):
                        topology_edges.add(
                            tuple(sorted((holes[int(simplex[left])], holes[int(simplex[right])])))
                        )
                topology_method = "delaunay_collar_xy"
            except QhullError:
                pass
        if not topology_edges:
            for index, hole in enumerate(holes):
                distances = np.linalg.norm(points - points[index], axis=1)
                for neighbour in np.argsort(distances)[1 : min(3, len(holes))]:
                    topology_edges.add(tuple(sorted((hole, holes[int(neighbour)]))))
            topology_method = "two_nearest_collar_xy"
    rows: list[dict] = []
    candidates = pair_evidence.loc[
        (pair_evidence["co_anomaly_count"] > 0)
        & (pd.to_numeric(pair_evidence["lift"], errors="coerce") >= 1.0)
    ]
    for _, pair in candidates.iterrows():
        target = segments.loc[segments["element"] == pair["target_element"]]
        candidate = segments.loc[segments["element"] == pair["candidate_element"]]
        distances: list[float] = []
        same_geobody = 0
        if not target.empty and not candidate.empty:
            tree = cKDTree(candidate[coordinate_columns].to_numpy(dtype=float))
            for _, target_row in target.iterrows():
                neighbours = tree.query_ball_point(
                    target_row[coordinate_columns].to_numpy(dtype=float),
                    r=max_distance_m,
                )
                for index in neighbours:
                    candidate_row = candidate.iloc[index]
                    target_hole = str(target_row["hole_id"])
                    candidate_hole = str(candidate_row["hole_id"])
                    if target_hole == candidate_hole:
                        continue
                    if tuple(sorted((target_hole, candidate_hole))) not in topology_edges:
                        continue
                    distance = float(
                        np.linalg.norm(
                            target_row[coordinate_columns].to_numpy(dtype=float)
                            - candidate_row[coordinate_columns].to_numpy(dtype=float)
                        )
                    )
                    distances.append(distance)
                    left_keys = set(str(target_row.get("section_geobody_keys") or "").split(",")) - {""}
                    right_keys = set(str(candidate_row.get("section_geobody_keys") or "").split(",")) - {""}
                    same_geobody += int(bool(left_keys & right_keys))
        rows.append(
            {
                "target_element": pair["target_element"],
                "candidate_element": pair["candidate_element"],
                "target_segment_count": len(target),
                "candidate_segment_count": len(candidate),
                "cross_hole_proximity_count": len(distances),
                "minimum_distance_m": min(distances) if distances else np.nan,
                "median_distance_m": float(np.median(distances)) if distances else np.nan,
                "same_geobody_proximity_count": same_geobody,
                "coordinate_quality": "approximate_vertical",
                "topology_method": topology_method,
                "topology_edge_count": len(topology_edges),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def build_candidate_combination_cards(
    pairs: pd.DataFrame,
    coanomaly: pd.DataFrame,
    same_hole: pd.DataFrame,
    cross_hole: pd.DataFrame,
    config: CorrelationConfig,
) -> pd.DataFrame:
    """Create pair-sized candidate cards so larger clusters gain no count advantage."""

    columns = [
        "target_element", "candidate_element", "pearson_log_r", "pearson_raw_r",
        "spearman_r", "n_common", "co_anomaly_count", "support",
        "p_candidate_given_target", "jaccard", "lift", "odds_ratio_corrected",
        "fisher_p", "bh_q", "fdr_pass_0_10",
        "same_hole_event_count", "supporting_drillhole_count",
        "cross_hole_proximity_count", "same_geobody_proximity_count",
        "spatial_grade", "action_status", "spatial_action_allowed",
        "evidence_tier", "formal_candidate",
        "recommended_for_review", "reference_status",
        "contains_focus_metal", "focus_element_count", "focus_members",
        "rule_set_status", "evidence_reason", "limitations",
    ]
    if coanomaly.empty:
        return pd.DataFrame(columns=columns)
    correlation = pairs.copy()
    focus = set(config.focus_elements or COMMON_METAL_ELEMENTS)
    if "focus_element_count" not in correlation:
        correlation["focus_element_count"] = correlation.apply(
            lambda row: int(row["element_a"] in focus) + int(row["element_b"] in focus),
            axis=1,
        )
    if "contains_focus_metal" not in correlation:
        correlation["contains_focus_metal"] = correlation["focus_element_count"] > 0
    if "focus_members" not in correlation:
        correlation["focus_members"] = correlation.apply(
            lambda row: ",".join(
                element for element in (row["element_a"], row["element_b"])
                if element in focus
            ),
            axis=1,
        )
    direct = correlation.rename(
        columns={"element_a": "target_element", "element_b": "candidate_element"}
    )
    reverse = correlation.rename(
        columns={"element_b": "target_element", "element_a": "candidate_element"}
    )
    correlation = pd.concat([direct, reverse], ignore_index=True)
    cards = coanomaly.merge(
        correlation[
            [
                "target_element", "candidate_element", "pearson_log_r",
                "pearson_raw_r", "spearman_r", "n_common", "pair_status",
                "contains_focus_metal", "focus_element_count", "focus_members",
            ]
        ],
        on=["target_element", "candidate_element"],
        how="left",
    )
    if same_hole.empty:
        cards["same_hole_event_count"] = 0
        cards["supporting_drillhole_count"] = 0
    else:
        counts = (
            same_hole.groupby(["target_element", "candidate_element"])
            .size()
            .rename("same_hole_event_count")
        )
        cards = cards.merge(counts, on=["target_element", "candidate_element"], how="left")
        cards["same_hole_event_count"] = cards["same_hole_event_count"].fillna(0).astype(int)
        hole_counts = (
            same_hole.groupby(["target_element", "candidate_element"])["hole_id"]
            .nunique()
            .rename("supporting_drillhole_count")
        )
        cards = cards.merge(hole_counts, on=["target_element", "candidate_element"], how="left")
        cards["supporting_drillhole_count"] = (
            cards["supporting_drillhole_count"].fillna(0).astype(int)
        )
    if cross_hole.empty:
        cards["cross_hole_proximity_count"] = 0
        cards["same_geobody_proximity_count"] = 0
    else:
        cards = cards.merge(
            cross_hole[
                [
                    "target_element", "candidate_element", "cross_hole_proximity_count",
                    "same_geobody_proximity_count",
                ]
            ],
            on=["target_element", "candidate_element"],
            how="left",
        )
        for column in ("cross_hole_proximity_count", "same_geobody_proximity_count"):
            cards[column] = cards[column].fillna(0).astype(int)

    def tier(row: pd.Series) -> tuple[str, bool, str]:
        co = int(row.get("co_anomaly_count") or 0)
        lift = float(row.get("lift")) if pd.notna(row.get("lift")) else np.nan
        stable = (
            pd.notna(row.get("pearson_log_r"))
            and pd.notna(row.get("spearman_r"))
            and abs(float(row["pearson_log_r"])) >= config.strong_correlation_threshold
            and abs(float(row["spearman_r"])) >= config.strong_correlation_threshold
            and np.sign(float(row["pearson_log_r"])) == np.sign(float(row["spearman_r"]))
        )
        same_hole_count = int(row.get("same_hole_event_count") or 0)
        cross_hole_count = int(row.get("cross_hole_proximity_count") or 0)
        # Same-hole co-anomaly is useful screening evidence, but it cannot by
        # itself establish spatial repeatability. A grade therefore requires
        # support from at least two different holes.
        if (
            co >= 3
            and np.isfinite(lift)
            and lift >= config.minimum_lift
            and stable
            and cross_hole_count > 0
        ):
            if config.rule_set_status == "confirmed":
                return "A", True, "共异常、提升度、两种相关系数与空间证据一致，且规则版本已确认"
            return "A", False, "A 级试算证据成立，但规则版本仍为草稿，不能发布为正式候选"
        if co > 0 and np.isfinite(lift) and lift >= config.minimum_lift:
            if same_hole_count > 0 and cross_hole_count == 0:
                return "B", False, "有共异常和同孔证据，但缺少跨孔重复，不能评为 A 级"
            return "B", False, "有共异常和提升度证据，但稳定性或跨孔空间证据不足"
        return "C", False, "仅统计相关或共异常证据不足"

    rated = cards.apply(tier, axis=1)
    cards["evidence_tier"] = [item[0] for item in rated]
    cards["formal_candidate"] = [item[1] for item in rated]
    cards["recommended_for_review"] = cards["evidence_tier"].isin(["A", "B"])
    cards["reference_status"] = cards["evidence_tier"].map(
        {"A": "高优先找矿参考", "B": "中优先找矿参考", "C": "统计线索"}
    )
    cards["rule_set_status"] = config.rule_set_status
    cards["evidence_reason"] = [item[2] for item in rated]
    cards["spatial_grade"] = np.select(
        [
            cards["cross_hole_proximity_count"] > 0,
            cards["same_hole_event_count"] > 0,
        ],
        ["cross_hole", "same_hole"],
        default="statistical_only",
    )
    cards["action_status"] = cards["spatial_grade"].map(
        {
            "cross_hole": "跨孔支持，可进入空间验证",
            "same_hole": "仅同孔待验证",
            "statistical_only": "仅统计线索",
        }
    )
    cards["spatial_action_allowed"] = cards["spatial_grade"].eq("cross_hole")
    cards["limitations"] = cards.apply(
        lambda row: ";".join(
            item
            for item in (
                "缺少跨孔重复" if row["spatial_grade"] != "cross_hole" else "",
                "FDR未通过探索阈值" if not bool(row.get("fdr_pass_0_10")) else "",
                "规则仍为草稿" if config.rule_set_status != "confirmed" else "",
            )
            if item
        ),
        axis=1,
    )
    return cards[columns].sort_values(
        [
            "spatial_action_allowed", "supporting_drillhole_count",
            "co_anomaly_count", "contains_focus_metal",
            "fdr_pass_0_10", "lift", "spearman_r",
        ],
        ascending=[False, False, False, False, False, False, False],
    ).reset_index(drop=True)


def _correlation_report(
    total_elements: int,
    quality: pd.DataFrame,
    pairs: pd.DataFrame,
    cluster_elements: list[str],
    clusters: pd.DataFrame,
    overlap: pd.DataFrame,
    sensitivity: pd.DataFrame,
    config: CorrelationConfig,
) -> str:
    strong = pairs.loc[pairs["pearson_r"] >= config.strong_correlation_threshold].sort_values(
        ["focus_element_count", "pearson_r"] if "focus_element_count" in pairs else ["pearson_r"],
        ascending=False,
    )
    combinations = clusters.loc[clusters["is_multielement_combination"]]
    return f"""# 化学元素相关性挖掘算法报告

## 运行范围

- 数据库记录的元素数：{total_elements}
- 通过正值样本数准入的元素数：{int(quality['candidate_for_pairwise_correlation'].sum())}
- 实际进入 R 型聚类的元素数：{len(cluster_elements)}
- 有效元素对数：{int((pairs['pair_status'] == 'ok').sum()) if not pairs.empty else 0}
- R 型多元素组合数：{len(combinations)}

## 方法说明

本算法以 Pearson 相关系数和 R 型层次聚类为主。0 值因缺少检出限说明，不进入主相关性计算；每个元素对仅使用双方同时为正值的化验区间，且必须满足 `n_common >= {config.minimum_pair_common_count}`。Spearman 相关系数作为稳健性对照。

结果展示优先列出常见金属及找矿关注元素，但保留其他通过数据质量检查的元素，避免因预设名单漏掉项目区特有组合。这里的“优先”只改变阅读顺序，不改变相关系数和聚类结果。

R 型聚类使用 `distance = 1 - r` 与 average linkage；`r >= {config.strong_correlation_threshold:.2f}` 用于识别候选紧密组合。相关性只代表协同变化，不代表因果关系。

## 元素准入与数据质量摘要

{markdown_table(quality, ['element', 'positive_count', 'zero_count', 'candidate_for_pairwise_correlation', 'used_in_r_type_cluster', 'exclusion_reason'], 15)}

## 较强正相关元素对

{markdown_table(strong, ['element_a', 'element_b', 'focus_members', 'n_common', 'pearson_r', 'spearman_r', 'correlation_level', 'stability_note'], 15)}

## R 型元素组合及统计稳定性

{markdown_table(combinations, ['cluster_id', 'elements', 'focus_members', 'element_count', 'mean_internal_pearson_r', 'min_internal_pearson_r', 'mean_internal_spearman_r', 'stable_pair_ratio', 'confidence_level', 'confidence_reason'], 15)}

## 聚类阈值敏感性

{markdown_table(sensitivity, ['correlation_threshold', 'cluster_count', 'multielement_cluster_count', 'multielement_groups'], 10)}

## 组合与算法一异常段的重合情况

{markdown_table(overlap, ['cluster_id', 'elements', 'variation_status', 'assays_with_two_or_more_members_anomalous', 'co_anomaly_ratio', 'dominant_holes'], 15)}

## 使用限制

1. 进入聚类的是通过有效正值和成对共同样本数检查的元素子集，不强行对所有 41 种元素聚类。
2. 元素组合由当前数据自动计算，不能照搬其他地区的固定组合。
3. 本报告不使用 Apriori、SVM、CLR 或三维体素预测；这些属于后续扩展。
"""


def run_correlation_algorithm(
    db_path: Path | str = DEFAULT_DB_PATH,
    model_id: int | None = None,
    output_dir: Path | str | None = None,
    variation_output_dir: Path | str | None = None,
    config: CorrelationConfig = CorrelationConfig(),
    filters: dict | None = None,
) -> dict[str, Path]:
    """运行算法二并写入离线结果。"""

    root_output = Path(__file__).resolve().parent / "outputs"
    output_dir = Path(output_dir) if output_dir else root_output / "element_correlation"
    variation_output_dir = (
        Path(variation_output_dir) if variation_output_dir else root_output / "element_variation"
    )
    ensure_dir(output_dir)
    dataset = load_geochem_dataset(db_path, model_id=model_id, **(filters or {}))
    assays = valid_intervals(dataset.assays)
    quality = build_correlation_quality(assays, dataset.elements, config)
    candidates = quality.loc[quality["candidate_for_pairwise_correlation"], "element"].tolist()
    pairwise = _pairwise_correlations(assays, candidates, config.minimum_pair_common_count)
    pearson_all, spearman_all, pairs = build_correlation_matrices(
        pairwise, candidates, config.strong_correlation_threshold
    )
    cluster_elements = _best_complete_correlation_set(pearson_all, quality) if candidates else []

    quality.loc[quality["element"].isin(cluster_elements), "used_in_r_type_cluster"] = True
    quality.loc[
        quality["candidate_for_pairwise_correlation"] & ~quality["element"].isin(cluster_elements),
        "exclusion_reason",
    ] = quality.loc[
        quality["candidate_for_pairwise_correlation"] & ~quality["element"].isin(cluster_elements),
        "exclusion_reason",
    ].replace("", "insufficient_complete_pairwise_matrix_for_r_type_cluster")

    pearson_cluster = pearson_all.loc[cluster_elements, cluster_elements] if cluster_elements else pd.DataFrame()
    spearman_cluster = spearman_all.loc[cluster_elements, cluster_elements] if cluster_elements else pd.DataFrame()
    clusters, merges, linkage_frame = r_type_cluster(pearson_cluster, config.strong_correlation_threshold)
    clusters = enrich_cluster_statistics(
        clusters, pearson_cluster, spearman_cluster, config.strong_correlation_threshold
    )
    pairs, clusters = annotate_focus_results(pairs, clusters, config.focus_elements)
    geology_summary = build_cluster_geology_summary(clusters, assays)
    sensitivity = build_threshold_sensitivity(
        pearson_cluster,
        tuple(sorted({0.40, 0.50, 0.60, 0.70, float(config.strong_correlation_threshold)})),
    )
    overlap = build_cluster_overlap(clusters, variation_output_dir)
    target_pair_coanomaly = build_target_pair_coanomaly(
        assays, candidates, variation_output_dir, config
    )
    same_hole_coanomaly = build_same_hole_coanomaly(
        target_pair_coanomaly, variation_output_dir, config.same_hole_gap_m
    )
    cross_hole_coanomaly = build_cross_hole_coanomaly(
        target_pair_coanomaly, variation_output_dir, config.cross_hole_distance_m
    )
    candidate_cards = build_candidate_combination_cards(
        pairs,
        target_pair_coanomaly,
        same_hole_coanomaly,
        cross_hole_coanomaly,
        config,
    )

    outputs = {
        "correlation_data_quality": write_csv(quality, output_dir / "correlation_data_quality.csv"),
        "pairwise_correlation": write_csv(pairs, output_dir / "correlation_pairs.csv"),
        "pearson_correlation_matrix": write_csv(pearson_cluster.reset_index(names="element"), output_dir / "pearson_correlation_matrix.csv"),
        "spearman_correlation_matrix": write_csv(spearman_cluster.reset_index(names="element"), output_dir / "spearman_correlation_matrix.csv"),
        "r_type_cluster_groups": write_csv(clusters, output_dir / "r_type_cluster_groups.csv"),
        "r_type_cluster_merges": write_csv(merges, output_dir / "r_type_cluster_merges.csv"),
        "r_type_linkage": write_csv(linkage_frame, output_dir / "r_type_linkage.csv"),
        "cluster_geology_summary": write_csv(geology_summary, output_dir / "cluster_geology_summary.csv"),
        "cluster_threshold_sensitivity": write_csv(sensitivity, output_dir / "cluster_threshold_sensitivity.csv"),
        "cluster_anomaly_overlap": write_csv(overlap, output_dir / "cluster_anomaly_overlap.csv"),
        "target_pair_coanomaly": write_csv(
            target_pair_coanomaly, output_dir / "target_pair_coanomaly.csv"
        ),
        "same_hole_coanomaly": write_csv(
            same_hole_coanomaly, output_dir / "same_hole_coanomaly.csv"
        ),
        "cross_hole_coanomaly": write_csv(
            cross_hole_coanomaly, output_dir / "cross_hole_coanomaly.csv"
        ),
        "candidate_combination_cards": write_csv(
            candidate_cards, output_dir / "candidate_combination_cards.csv"
        ),
    }
    evidence_note = f"""

## 目标元素共异常证据（正式结论优先）

相关系数采用正值浓度的对数 Pearson 作为主尺度，同时保留原值 Pearson 和
Spearman。候选组合按元素对逐一比较，避免大组合天然获得更多命中次数。

{markdown_table(candidate_cards, ['target_element', 'candidate_element', 'pearson_log_r', 'pearson_raw_r', 'spearman_r', 'co_anomaly_count', 'jaccard', 'lift', 'same_hole_event_count', 'cross_hole_proximity_count', 'evidence_tier', 'formal_candidate'], 25)}

其中 A 级同时要求共异常、提升度、Pearson/Spearman 稳定性和空间证据；
B 级仍需复核，C 级仅作统计线索。跨孔坐标基于垂直孔近似，不能替代实测孔斜轨迹。
"""
    outputs["correlation_report"] = write_text(
        _correlation_report(
            len(dataset.elements), quality, pairs, cluster_elements, clusters, overlap, sensitivity, config
        ) + evidence_note,
        output_dir / "correlation_report.md",
    )
    return outputs
