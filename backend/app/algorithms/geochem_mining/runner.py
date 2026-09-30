"""Command-line runner used by the background mining job service."""

from __future__ import annotations

import argparse
import hashlib
import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages

from .correlation import CorrelationConfig, run_correlation_algorithm
from .data_pipeline import dataset_sha256, load_geochem_dataset, valid_intervals
from .figures import (
    configure_style,
    plot_cluster_summary,
    plot_correlation_heatmap,
    plot_r_type_dendrogram,
    plot_variation_depth_segments,
    plot_variation_overview,
    write_figure_contract,
)
from .variation import VariationConfig, run_variation_algorithm

RESULT_SCHEMA_VERSION = "5.1"
VARIATION_ALGORITHM_VERSION = "3.1"
CORRELATION_ALGORITHM_VERSION = "2.0"


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _records(frame: pd.DataFrame, count: int = 10) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    clean = frame.head(count).astype(object)
    clean = clean.where(pd.notna(clean), None)
    return clean.to_dict("records")


def _relative_files(root: Path) -> list[str]:
    return sorted(
        str(path.relative_to(root)).replace("\\", "/")
        for path in root.rglob("*")
        if path.is_file() and path.name != "summary.json"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: Any) -> str:
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)


def _task_report_pdf(root: Path, summary: dict[str, Any]) -> Path:
    """Generate a compact Chinese task report using only installed matplotlib."""
    mode = summary["algorithm_mode"]
    title = "化学元素变化规律分析报告" if mode == "variation" else "化学元素相关性分析报告"
    target = root / ("变化规律分析报告.pdf" if mode == "variation" else "元素相关性分析报告.pdf")
    conclusions = summary.get("variation" if mode == "variation" else "correlation", {}).get("conclusions", [])
    quality = summary.get("data_quality", {})
    with PdfPages(target) as pdf:
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.text(0.08, 0.94, title, fontsize=18, fontweight="bold")
        fig.text(0.08, 0.905, f"模型编号：{summary.get('model_id')}    算法模式：{mode}", fontsize=10)
        task = summary.get("task") or {}
        params = summary.get("parameters") or {}
        filters = params.get("filters") or {}
        fig.text(0.08, 0.88, f"任务编号：{task.get('job_id', '未记录')}    结果版本：{summary.get('result_schema_version', '历史版本')}", fontsize=9)
        fig.text(0.08, 0.855, f"任务时间（UTC）：{task.get('created_at', '未记录')} 至 {task.get('finished_at', '未记录')}", fontsize=9)
        fig.text(0.08, 0.82, "输入筛选", fontsize=13, fontweight="bold")
        filter_lines = [
            f"元素：{','.join(filters.get('selected_elements') or []) or '全部'}；钻孔：{','.join(filters.get('hole_ids') or []) or '全部'}",
            f"深度：{filters.get('depth_min') if filters.get('depth_min') is not None else '最小'} 至 {filters.get('depth_max') if filters.get('depth_max') is not None else '最大'} m；地质分段：{','.join(filters.get('geobody_keys') or []) or '全部'}",
        ]
        y = 0.79
        for line in filter_lines:
            for wrapped in textwrap.wrap(line, width=68) or [""]:
                fig.text(0.09, y, wrapped, fontsize=9); y -= 0.022
        fig.text(0.08, y - 0.005, "数据与质量", fontsize=13, fontweight="bold")
        y -= 0.04
        quality_lines = [
            f"输入化验区间：{quality.get('assay_count', 0)}；有效区间：{quality.get('valid_interval_count', 0)}；无效区间：{quality.get('invalid_interval_count', 0)}",
            f"化学钻孔：{quality.get('chemical_hole_count', 0)}；检测元素：{quality.get('detected_element_count', 0)}；地质分段匹配率：{quality.get('section_match_rate', 0):.1%}",
            "空间坐标：按钻孔口工程坐标与垂直钻孔假定近似计算，不代表真实测斜轨迹。",
        ]
        for line in quality_lines:
            fig.text(0.09, y, line, fontsize=10); y -= 0.027
        fig.text(0.08, y - 0.01, "自动结论", fontsize=13, fontweight="bold"); y -= 0.05
        for index, conclusion in enumerate(conclusions[:8], 1):
            wrapped = textwrap.wrap(f"{index}. {_text(conclusion.get('text', conclusion))}", width=52) or [""]
            for line in wrapped:
                fig.text(0.09, y, line, fontsize=10); y -= 0.025
            y -= 0.008
        fig.text(0.08, 0.09, "说明：统计异常和相关关系均为候选规律，必须结合岩性、构造及工程地质资料解释。", fontsize=9, color="#555555")
        plt.axis("off"); pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)

        fig = plt.figure(figsize=(8.27, 11.69))
        fig.text(0.08, 0.94, "方法参数与可复现快照", fontsize=17, fontweight="bold")
        fig.text(0.08, 0.905, "以下内容记录本次任务实际使用的筛选条件和算法参数。", fontsize=9, color="#555555")
        parameter_text = json.dumps(params, ensure_ascii=False, indent=2, default=str)
        y = 0.865
        for raw_line in parameter_text.splitlines():
            wrapped = textwrap.wrap(raw_line, width=84, replace_whitespace=False, drop_whitespace=False) or [""]
            for line in wrapped:
                if y < 0.08:
                    break
                fig.text(0.07, y, line, fontsize=7.5, family="sans-serif"); y -= 0.016
            if y < 0.08:
                break
        fig.text(0.08, 0.055, "论文对应：算法一采用迭代背景值、异常下限、CV与内部富集；算法二采用Pearson/Spearman与R型层次聚类。", fontsize=8.5)
        plt.axis("off"); pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)

        figure_candidates = (
            [root / "figures/element_variation/variation_element_overview.png", root / "figures/element_variation/variation_anomaly_depth_segments.png"]
            if mode == "variation" else
            [root / "figures/element_correlation/correlation_clustered_heatmap.png", root / "figures/element_correlation/correlation_r_type_dendrogram.png", root / "figures/element_correlation/correlation_cluster_summary.png"]
        )
        for image_path in figure_candidates:
            if not image_path.exists():
                continue
            fig = plt.figure(figsize=(11.69, 8.27))
            image = plt.imread(image_path)
            plt.imshow(image); plt.axis("off"); plt.tight_layout()
            pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)
    return target


def _build_summary_legacy(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    variation_dir = root / "element_variation"
    correlation_dir = root / "element_correlation"
    fused = _read_csv(variation_dir / "fused_assay_dataset.csv")
    input_summary = params.get("input_summary") or {}
    if fused.empty and params["algorithm_mode"] == "correlation":
        quality = _read_csv(correlation_dir / "correlation_data_quality.csv")
        assay_count = int(input_summary.get("assay_count", 0))
        element_count = len(quality)
    else:
        assay_count = len(fused)
        element_columns = [
            col for col in fused.columns
            if col not in {
                "assay_id", "model_id", "chemical_borehole_id", "hole_id", "from_depth",
                "to_depth", "mid_depth", "interval_length", "unit", "created_at", "collar_x",
                "collar_y", "collar_z", "depth_min", "depth_max", "x_approx", "y_approx",
                "z_approx", "coordinate_quality", "section_match_status", "section_id",
                "section_depth", "section_bottom", "section_description", "section_geobody_key",
                "geobody_property_status", "geobody_key_exact", "stratum_code", "lithology",
                "weathering", "engineering_grade", "data_quality_flags",
            }
        ]
        element_count = int(input_summary.get("detected_element_count", len(element_columns)))

    invalid = _read_csv(variation_dir / "invalid_interval_audit.csv")
    stats = _read_csv(variation_dir / "element_background_statistics.csv")
    anomalies = _read_csv(variation_dir / "anomaly_intervals.csv")
    segments = _read_csv(variation_dir / "merged_anomaly_segments.csv")
    pairs = _read_csv(correlation_dir / "correlation_pairs.csv")
    clusters = _read_csv(correlation_dir / "r_type_cluster_groups.csv")
    overlap = _read_csv(correlation_dir / "cluster_anomaly_overlap.csv")
    geology_summary = _read_csv(correlation_dir / "cluster_geology_summary.csv")
    sensitivity = _read_csv(correlation_dir / "cluster_threshold_sensitivity.csv")
    section_enrichment = _read_csv(variation_dir / "section_element_enrichment.csv")

    top_elements = pd.DataFrame()
    if not stats.empty:
        anomaly_counts = anomalies.groupby("element").size().rename("anomaly_record_count") if not anomalies.empty else pd.Series(dtype=int)
        unique_counts = anomalies.groupby("element")["assay_id"].nunique().rename("unique_anomalous_assay_count") if not anomalies.empty else pd.Series(dtype=int)
        inner_counts = anomalies.loc[anomalies["anomaly_level"] == "统计异常内带"].groupby("element").size().rename("inner_anomaly_count") if not anomalies.empty else pd.Series(dtype=int)
        boundary_counts = anomalies.loc[anomalies.get("meets_boundary_grade", False).astype(str).str.lower().isin(["true", "1"])].groupby("element").size().rename("boundary_grade_count") if not anomalies.empty and "meets_boundary_grade" in anomalies else pd.Series(dtype=int)
        industrial_counts = anomalies.loc[anomalies.get("meets_industrial_grade", False).astype(str).str.lower().isin(["true", "1"])].groupby("element").size().rename("industrial_grade_count") if not anomalies.empty and "meets_industrial_grade" in anomalies else pd.Series(dtype=int)
        strong_relative_counts = anomalies.loc[anomalies.get("mining_level", "") == "强相对富集"].groupby("element").size().rename("strong_relative_anomaly_count") if not anomalies.empty and "mining_level" in anomalies else pd.Series(dtype=int)
        max_background_ratio = anomalies.groupby("element")["value_to_background_ratio"].max().rename("max_value_to_background_ratio") if not anomalies.empty and "value_to_background_ratio" in anomalies else pd.Series(dtype=float)
        top_elements = stats.merge(anomaly_counts, left_on="element", right_index=True, how="left")
        top_elements = top_elements.merge(unique_counts, left_on="element", right_index=True, how="left").merge(inner_counts, left_on="element", right_index=True, how="left")
        top_elements = top_elements.merge(boundary_counts, left_on="element", right_index=True, how="left")
        top_elements = top_elements.merge(industrial_counts, left_on="element", right_index=True, how="left")
        top_elements = top_elements.merge(strong_relative_counts, left_on="element", right_index=True, how="left")
        top_elements = top_elements.merge(max_background_ratio, left_on="element", right_index=True, how="left")
        for column in ("anomaly_record_count", "unique_anomalous_assay_count", "inner_anomaly_count", "boundary_grade_count", "industrial_grade_count", "strong_relative_anomaly_count"):
            top_elements[column] = top_elements[column].fillna(0).astype(int)
        top_elements["anomaly_rate"] = top_elements["unique_anomalous_assay_count"] / top_elements["positive_count"].replace(0, pd.NA)
        top_elements = top_elements.sort_values(
            [
                "inner_anomaly_count",
                "strong_relative_anomaly_count",
                "max_value_to_background_ratio",
                "anomaly_rate",
                "boundary_grade_count",
            ],
            ascending=False,
        )

    threshold = float((params.get("correlation") or {}).get("strong_correlation_threshold", 0.5))
    if not pairs.empty:
        pearson_values = pd.to_numeric(pairs["pearson_r"], errors="coerce")
        valid_pairs = pairs["pair_status"] == "ok"
        strong_pairs = pairs.loc[valid_pairs & (pearson_values.abs() >= threshold)].copy()
        positive_pairs = pairs.loc[valid_pairs & (pearson_values >= threshold)]
        negative_pairs = pairs.loc[valid_pairs & (pearson_values <= -threshold)]
    else:
        strong_pairs = positive_pairs = negative_pairs = pairs
    multiclusters = clusters.loc[clusters.get("is_multielement_combination", False).astype(str).str.lower().isin(["true", "1"])] if not clusters.empty else clusters

    coordinate_quality = input_summary.get("coordinate_quality", "unavailable")
    section_match_rate = float(input_summary.get("section_match_rate", 0.0))
    hole_count = int(input_summary.get("chemical_hole_count", 0))
    if not fused.empty:
        coordinate_quality = "approximate_vertical" if (fused["coordinate_quality"] == "approximate_vertical").any() else "unavailable"
        section_match_rate = float((fused["section_match_status"] == "unique_match").mean())
        hole_count = int(fused["hole_id"].nunique())

    level_counts = anomalies["anomaly_level"].value_counts().to_dict() if not anomalies.empty else {}
    mining_level_counts = anomalies["mining_level"].value_counts().to_dict() if not anomalies.empty and "mining_level" in anomalies else {}
    unique_anomalous_assays = int(anomalies["assay_id"].nunique()) if not anomalies.empty else 0
    anomalous_holes = int(anomalies["hole_id"].nunique()) if not anomalies.empty else 0
    segment_rank_column = "max_value_to_background_ratio" if "max_value_to_background_ratio" in segments else "max_value_to_threshold_ratio"
    top_segments = segments.sort_values(segment_rank_column, ascending=False) if not segments.empty else segments
    top_sections = section_enrichment.loc[
        section_enrichment.get("interpretation_ready", False).astype(str).str.lower().isin(["true", "1"])
    ].sort_values("E_internal", ascending=False) if not section_enrichment.empty else section_enrichment

    variation_conclusions: list[dict[str, Any]] = []
    for _, row in top_elements.head(3).iterrows():
        ratio = row.get("max_value_to_background_ratio")
        ratio_text = f"最高为背景的 {ratio:.2f} 倍" if pd.notna(ratio) else "背景倍数不可判定"
        variation_conclusions.append({
            "type": "element",
            "element": row["element"],
            "text": f"{row['element']} 识别 {int(row['unique_anomalous_assay_count'])} 个候选异常区间，{ratio_text}；达到边界品位 {int(row['boundary_grade_count'])} 条，达到最低工业品位 {int(row['industrial_grade_count'])} 条。",
        })
    for _, row in top_segments.head(3).iterrows():
        level = row.get("highest_mining_level") or row.get("highest_anomaly_level")
        ratio = row.get("max_value_to_background_ratio")
        ratio_text = f"背景的 {ratio:.2f} 倍" if pd.notna(ratio) else f"统计异常下限的 {row.get('max_value_to_threshold_ratio'):.2f} 倍"
        variation_conclusions.append({
            "type": "segment", "element": row.get("element"), "hole_id": row.get("hole_id"),
            "from_depth": row.get("segment_from_depth"), "to_depth": row.get("segment_to_depth"),
            "text": f"{row.get('element')} 在钻孔 {row.get('hole_id')} 的 {row.get('segment_from_depth'):.2f}-{row.get('segment_to_depth'):.2f} m 达到{level}，最大为{ratio_text}，地质分段为 {_text(row.get('section_geobody_keys')) or '未匹配'}。",
        })

    correlation_conclusions: list[dict[str, Any]] = []
    ordered_strong_pairs = (
        strong_pairs.assign(_abs_pearson=pd.to_numeric(strong_pairs["pearson_r"], errors="coerce").abs())
        .sort_values(
            (["focus_element_count", "_abs_pearson"] if "focus_element_count" in strong_pairs else ["_abs_pearson"]),
            ascending=False,
        )
        .drop(columns=["_abs_pearson"])
        if "pearson_r" in strong_pairs else strong_pairs
    )
    for _, row in ordered_strong_pairs.head(3).iterrows():
        correlation_conclusions.append({
            "type": "pair", "element_a": row["element_a"], "element_b": row["element_b"],
            "text": f"{row['element_a']}-{row['element_b']} 的Pearson r={row['pearson_r']:.3f}、Spearman r={row['spearman_r']:.3f}（{row['correlation_level']}），共同有效样本 {int(row['n_common'])} 条；{row['stability_note']}。",
        })
    ordered_multiclusters = (
        multiclusters.sort_values(
            (["focus_element_count", "mean_internal_pearson_r"] if "focus_element_count" in multiclusters else ["mean_internal_pearson_r"]),
            ascending=False,
        )
        if "mean_internal_pearson_r" in multiclusters else multiclusters
    )
    for _, row in ordered_multiclusters.head(3).iterrows():
        correlation_conclusions.append({
            "type": "cluster", "cluster_id": int(row["cluster_id"]), "elements": row["elements"],
            "text": f"组合 {row['elements']} 的组内平均Pearson r={row['mean_internal_pearson_r']:.3f}，统计可信度为{row.get('confidence_level', '待评估')}。",
        })
    if not overlap.empty and "co_anomaly_ratio" in overlap:
        overlap_ranked = overlap.assign(_ratio=pd.to_numeric(overlap["co_anomaly_ratio"], errors="coerce")).dropna(subset=["_ratio"]).sort_values("_ratio", ascending=False)
        if not overlap_ranked.empty:
            row = overlap_ranked.iloc[0]
            correlation_conclusions.append({
                "type": "overlap", "cluster_id": int(row["cluster_id"]), "elements": row["elements"],
                "text": f"组合 {row['elements']} 在 {int(row['assays_with_two_or_more_members_anomalous'])} 个化验区间中至少有两个成员共同异常，共现率为 {row['_ratio']:.1%}，主要钻孔为 {_text(row.get('dominant_holes')) or '未识别'}。",
            })

    return {
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "algorithm_versions": {
            "variation": VARIATION_ALGORITHM_VERSION,
            "correlation": CORRELATION_ALGORITHM_VERSION,
        },
        "model_id": params["model_id"],
        "algorithm_mode": params["algorithm_mode"],
        "data_quality": {
            "assay_count": assay_count,
            "valid_interval_count": int(input_summary.get("valid_interval_count", max(0, assay_count - len(invalid)))),
            "invalid_interval_count": int(input_summary.get("invalid_interval_count", len(invalid))),
            "chemical_hole_count": hole_count,
            "detected_element_count": element_count,
            "coordinate_quality": coordinate_quality,
            "section_match_rate": section_match_rate,
            "database_sha256": input_summary.get("database_sha256"),
        },
        "variation": {
            "used_element_count": int(stats["used_for_variation"].astype(str).str.lower().isin(["true", "1"]).sum()) if not stats.empty else 0,
            "anomaly_record_count": len(anomalies),
            "anomaly_interval_count": len(anomalies),
            "unique_anomalous_assay_count": unique_anomalous_assays,
            "anomalous_hole_count": anomalous_holes,
            "outer_anomaly_count": int(level_counts.get("统计异常外带", 0)),
            "middle_anomaly_count": int(level_counts.get("统计异常中带", 0)),
            "inner_anomaly_count": int(level_counts.get("统计异常内带", 0)),
            "weak_relative_anomaly_count": int(mining_level_counts.get("轻微相对富集", 0)),
            "moderate_relative_anomaly_count": int(mining_level_counts.get("相对富集", 0)),
            "strong_relative_anomaly_count": int(mining_level_counts.get("强相对富集", 0)),
            "level_counts": {str(key): int(value) for key, value in mining_level_counts.items()},
            "boundary_grade_count": int(mining_level_counts.get("最低边界品位", 0)),
            "industrial_grade_count": int(mining_level_counts.get("工业品位", 0)),
            "merged_segment_count": len(segments),
            "professional_thresholds": _records(stats[["element", "boundary_grade_ppm", "industrial_grade_ppm", "industrial_grade_source"]] if not stats.empty else stats, 100),
            "top_elements": _records(top_elements, 10),
            "top_segments": _records(top_segments, 10),
            "top_enriched_sections": _records(top_sections, 10),
            "conclusions": variation_conclusions,
        },
        "correlation": {
            "cluster_element_count": int(clusters["element_count"].sum()) if not clusters.empty else 0,
            "strong_pair_count": len(strong_pairs),
            "strong_positive_pair_count": len(positive_pairs),
            "strong_negative_pair_count": len(negative_pairs),
            "cluster_count": len(clusters),
            "multielement_cluster_count": len(multiclusters),
            "top_pairs": _records(ordered_strong_pairs, 10),
            "top_negative_pairs": _records(negative_pairs.sort_values("pearson_r") if not negative_pairs.empty else negative_pairs, 10),
            "clusters": _records(clusters, 50),
            "overlap": _records(overlap, 50),
            "geology_summary": _records(geology_summary, 50),
            "threshold_sensitivity": _records(sensitivity, 20),
            "pearson_spearman_stable_pair_count": int((pairs.get("stability_note", pd.Series(dtype=str)) == "Pearson与Spearman均较强且方向一致").sum()) if not pairs.empty else 0,
            "excluded_element_count": max(0, element_count - (int(clusters["element_count"].sum()) if not clusters.empty else 0)),
            "conclusions": correlation_conclusions,
        },
        "outputs": _relative_files(root),
    }


def _truthy_count(frame: pd.DataFrame, column: str) -> int:
    if frame.empty or column not in frame:
        return 0
    return int(frame[column].astype(str).str.lower().isin(["true", "1", "yes"]).sum())


def build_summary(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Build the v5 evidence-first summary consumed by the API and frontend."""

    variation_dir = root / "element_variation"
    correlation_dir = root / "element_correlation"
    fused = _read_csv(variation_dir / "fused_assay_dataset.csv")
    baseline = _read_csv(variation_dir / "baseline_assay_dataset.csv")
    invalid = _read_csv(variation_dir / "invalid_interval_audit.csv")
    stats = _read_csv(variation_dir / "element_background_statistics.csv")
    anomalies = _read_csv(variation_dir / "anomaly_intervals.csv")
    segments = _read_csv(variation_dir / "merged_anomaly_segments.csv")
    section_enrichment = _read_csv(variation_dir / "section_element_enrichment.csv")
    pairs = _read_csv(correlation_dir / "correlation_pairs.csv")
    clusters = _read_csv(correlation_dir / "r_type_cluster_groups.csv")
    cards = _read_csv(correlation_dir / "candidate_combination_cards.csv")
    coanomaly = _read_csv(correlation_dir / "target_pair_coanomaly.csv")
    same_hole = _read_csv(correlation_dir / "same_hole_coanomaly.csv")
    cross_hole = _read_csv(correlation_dir / "cross_hole_coanomaly.csv")
    sensitivity = _read_csv(correlation_dir / "cluster_threshold_sensitivity.csv")
    input_summary = params.get("input_summary") or {}

    top_elements = pd.DataFrame()
    if not stats.empty:
        top_elements = stats.copy()
        if not anomalies.empty:
            counts = anomalies.groupby("element").agg(
                attention_record_count=("assay_id", "size"),
                unique_attention_assay_count=("assay_id", "nunique"),
                max_value_to_background_ratio=("value_to_background_ratio", "max"),
            )
            professional = anomalies.loc[
                anomalies.get("meets_boundary_grade", False).astype(str).str.lower().isin(["true", "1"])
                | anomalies.get("meets_industrial_grade", False).astype(str).str.lower().isin(["true", "1"])
            ].groupby("element").size().rename("professional_grade_count")
            statistical = anomalies.loc[
                anomalies.get("meets_statistical_threshold", False).astype(str).str.lower().isin(["true", "1"])
            ].groupby("element").size().rename("statistical_anomaly_count")
            boundary = anomalies.loc[
                anomalies.get("meets_boundary_grade", False).astype(str).str.lower().isin(["true", "1"])
            ].groupby("element").size().rename("boundary_grade_count")
            industrial = anomalies.loc[
                anomalies.get("meets_industrial_grade", False).astype(str).str.lower().isin(["true", "1"])
            ].groupby("element").size().rename("industrial_grade_count")
            inner = anomalies.loc[
                anomalies.get("mining_level", "") == "统计异常内带"
            ].groupby("element").size().rename("inner_anomaly_count")
            counts = counts.join(
                [professional, statistical, boundary, industrial, inner],
                how="left",
            )
            top_elements = top_elements.merge(counts, left_on="element", right_index=True, how="left")
        for column in (
            "attention_record_count",
            "unique_attention_assay_count",
            "professional_grade_count",
            "statistical_anomaly_count",
            "boundary_grade_count",
            "industrial_grade_count",
            "inner_anomaly_count",
        ):
            if column not in top_elements:
                top_elements[column] = 0
            top_elements[column] = top_elements[column].fillna(0).astype(int)
        if "max_value_to_background_ratio" not in top_elements:
            top_elements["max_value_to_background_ratio"] = np.nan
        top_elements["statistical_anomaly_rate"] = (
            top_elements["statistical_anomaly_count"]
            / pd.to_numeric(top_elements.get("positive_count"), errors="coerce").replace(0, pd.NA)
        )
        top_elements = top_elements.sort_values(
            [
                "inner_anomaly_count",
                "statistical_anomaly_rate",
                "max_value_to_background_ratio",
                "statistical_anomaly_count",
                "professional_grade_count",
            ],
            ascending=False,
        )

    formal_cards = (
        cards.loc[cards.get("formal_candidate", False).astype(str).str.lower().isin(["true", "1"])]
        if not cards.empty else cards
    )
    review_cards = (
        cards.loc[cards.get("recommended_for_review", False).astype(str).str.lower().isin(["true", "1"])]
        if not cards.empty else cards
    )
    tier_counts = (
        {str(key): int(value) for key, value in cards["evidence_tier"].value_counts().to_dict().items()}
        if not cards.empty else {}
    )
    variation_conclusions: list[dict[str, Any]] = []
    for _, row in top_elements.head(5).iterrows():
        variation_conclusions.append(
            {
                "type": "element_evidence",
                "element": row["element"],
                "text": (
                    f"{row['element']} 有 {int(row['statistical_anomaly_count'])} 个统计异常区间，"
                    f"其中 4T 内带 {int(row['inner_anomaly_count'])} 条、"
                    f"边界品位 {int(row['boundary_grade_count'])} 条、"
                    f"最低工业品位 {int(row['industrial_grade_count'])} 条；"
                    f"最高约为背景的 {float(row['max_value_to_background_ratio']):.2f} 倍。"
                )
                if pd.notna(row["max_value_to_background_ratio"])
                else f"{row['element']} 的背景倍数当前不可判定。",
            }
        )
    correlation_conclusions: list[dict[str, Any]] = []
    for _, row in review_cards.head(10).iterrows():
        correlation_conclusions.append(
            {
                "type": "prospecting_combination_reference",
                "target_element": row["target_element"],
                "candidate_element": row["candidate_element"],
                "text": (
                    f"{row['target_element']}-{row['candidate_element']} 为"
                    f"{row.get('reference_status', str(row['evidence_tier']) + '级找矿参考')}："
                    f"同区间共异常 {int(row['co_anomaly_count'])} 次，"
                    f"lift={float(row['lift']):.2f}，"
                    f"log-Pearson={float(row['pearson_log_r']):.3f}，"
                    f"Spearman={float(row['spearman_r']):.3f}。"
                ),
            }
        )
    if review_cards.empty and not cards.empty:
        correlation_conclusions.append(
            {
                "type": "evidence_limitation",
                "text": "当前仅形成 C 级统计线索，尚无可优先复核的找矿组合；可结合地质认识继续筛选。",
            }
        )
    if cards.empty:
        correlation_conclusions.append(
            {
                "type": "evidence_limitation",
                "text": "未绑定口径一致的变化规律任务，本次只输出统计相关，不能判断元素是否在异常区间共同出现。",
            }
        )

    assay_count = len(fused) or int(input_summary.get("assay_count", 0))
    background_assay_count = len(baseline) or assay_count
    hole_count = (
        int(fused["hole_id"].nunique())
        if not fused.empty and "hole_id" in fused
        else int(input_summary.get("chemical_hole_count", 0))
    )
    coordinate_quality = (
        "approximate_vertical"
        if not fused.empty and "coordinate_quality" in fused
        and (fused["coordinate_quality"] == "approximate_vertical").any()
        else input_summary.get("coordinate_quality", "unavailable")
    )
    section_match_rate = (
        float((fused["section_match_status"] == "unique_match").mean())
        if not fused.empty and "section_match_status" in fused
        else float(input_summary.get("section_match_rate", 0.0))
    )
    level_counts = (
        anomalies["mining_level"].value_counts().to_dict()
        if not anomalies.empty and "mining_level" in anomalies else {}
    )
    strong_threshold = float(
        (params.get("correlation") or {}).get("strong_correlation_threshold", 0.5)
    )
    strong_pairs = (
        pairs.loc[
            (pairs.get("pair_status") == "ok")
            & (pd.to_numeric(pairs.get("pearson_log_r"), errors="coerce").abs() >= strong_threshold)
        ]
        if not pairs.empty else pairs
    )
    return {
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "algorithm_versions": {
            "variation": VARIATION_ALGORITHM_VERSION,
            "correlation": CORRELATION_ALGORITHM_VERSION,
        },
        "model_id": params["model_id"],
        "algorithm_mode": params["algorithm_mode"],
        "provenance": {
            "rule_set_id": params.get("rule_set_id"),
            "rule_set_status": (params.get("rule_set") or {}).get("status"),
            "source_variation_job_id": params.get("source_variation_job_id"),
            "evidence_mode": params.get("evidence_mode"),
            "analysis_data_sha256": input_summary.get("analysis_data_sha256"),
            "expected_analysis_data_sha256": params.get("analysis_data_sha256"),
            "database_file_sha256_at_run": input_summary.get("database_file_sha256"),
            "random_seed": params.get("random_seed"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "data_quality": {
            "assay_count": assay_count,
            "background_assay_count": background_assay_count,
            "valid_interval_count": int(input_summary.get("valid_interval_count", max(0, assay_count - len(invalid)))),
            "invalid_interval_count": int(input_summary.get("invalid_interval_count", len(invalid))),
            "chemical_hole_count": hole_count,
            "detected_element_count": int(input_summary.get("detected_element_count", len(stats))),
            "coordinate_quality": coordinate_quality,
            "section_match_rate": section_match_rate,
        },
        "variation": {
            "used_element_count": _truthy_count(stats, "used_for_variation"),
            "attention_record_count": len(anomalies),
            "anomaly_record_count": len(anomalies),
            "anomaly_interval_count": len(anomalies),
            "statistical_anomaly_count": _truthy_count(anomalies, "meets_statistical_threshold"),
            "boundary_grade_count": _truthy_count(anomalies, "meets_boundary_grade"),
            "industrial_grade_count": _truthy_count(anomalies, "meets_industrial_grade"),
            "unique_attention_assay_count": int(anomalies["assay_id"].nunique()) if not anomalies.empty else 0,
            "unique_anomalous_assay_count": int(anomalies["assay_id"].nunique()) if not anomalies.empty else 0,
            "attention_hole_count": int(anomalies["hole_id"].nunique()) if not anomalies.empty else 0,
            "anomalous_hole_count": int(anomalies["hole_id"].nunique()) if not anomalies.empty else 0,
            "merged_segment_count": len(segments),
            "weak_relative_anomaly_count": int(level_counts.get("轻微相对富集", 0)),
            "moderate_relative_anomaly_count": int(level_counts.get("相对富集", 0)),
            "strong_relative_anomaly_count": int(level_counts.get("强相对富集", 0)),
            "level_counts": {str(key): int(value) for key, value in level_counts.items()},
            "professional_thresholds": _records(
                stats[
                    [
                        "element", "boundary_grade_ppm", "industrial_grade_ppm",
                        "industrial_grade_source", "rule_set_status",
                    ]
                ] if not stats.empty else stats,
                100,
            ),
            "top_elements": _records(top_elements, 15),
            "top_segments": _records(
                segments.sort_values("max_value_to_background_ratio", ascending=False)
                if not segments.empty else segments,
                15,
            ),
            "top_enriched_sections": _records(
                section_enrichment.sort_values("E_internal", ascending=False)
                if not section_enrichment.empty else section_enrichment,
                15,
            ),
            "conclusions": variation_conclusions,
        },
        "correlation": {
            "primary_scale": "log_positive_values",
            "strong_pair_count": len(strong_pairs),
            "cluster_count": len(clusters),
            "cluster_element_count": (
                int(pd.to_numeric(clusters.get("element_count"), errors="coerce").sum())
                if not clusters.empty else 0
            ),
            "multielement_cluster_count": _truthy_count(clusters, "is_multielement_combination"),
            "target_pair_count": len(coanomaly),
            "same_hole_event_count": len(same_hole),
            "cross_hole_pair_count": len(cross_hole),
            "candidate_card_count": len(cards),
            "formal_candidate_count": len(formal_cards),
            "review_reference_count": len(review_cards),
            "tier_counts": tier_counts,
            "top_pairs": _records(
                strong_pairs.sort_values("pearson_log_r", key=lambda values: values.abs(), ascending=False)
                if not strong_pairs.empty else strong_pairs,
                15,
            ),
            "candidate_cards": _records(cards, 30),
            "clusters": _records(clusters, 50),
            "threshold_sensitivity": _records(sensitivity, 20),
            "conclusions": correlation_conclusions,
        },
        "outputs": _relative_files(root),
    }


def run(params_path: Path, output_dir: Path) -> dict[str, Any]:
    params = json.loads(params_path.read_text(encoding="utf-8-sig"))
    mode = params["algorithm_mode"]
    filters = params.get("filters") or {}
    output_dir.mkdir(parents=True, exist_ok=True)

    dataset = load_geochem_dataset(
        params["db_path"], model_id=params["model_id"], **filters
    )
    analysis_dataset = load_geochem_dataset(
        params["db_path"],
        model_id=params["model_id"],
    )
    valid = valid_intervals(dataset.assays)
    params["input_summary"] = {
        "assay_count": len(dataset.assays),
        "valid_interval_count": len(valid),
        "invalid_interval_count": len(dataset.invalid_intervals),
        "chemical_hole_count": int(dataset.assays["hole_id"].nunique()),
        "detected_element_count": len(dataset.elements),
        "coordinate_quality": "approximate_vertical" if (dataset.assays["coordinate_quality"] == "approximate_vertical").any() else "unavailable",
        "section_match_rate": float((dataset.assays["section_match_status"] == "unique_match").mean()),
        "analysis_data_sha256": dataset_sha256(analysis_dataset),
        "database_file_sha256": _sha256(Path(params["db_path"])),
    }
    expected_database_sha256 = params.get("analysis_data_sha256")
    if (
        expected_database_sha256
        and expected_database_sha256 != params["input_summary"]["analysis_data_sha256"]
    ):
        raise ValueError("分析数据在任务排队后发生变化，已终止计算以避免证据链口径漂移")

    rule_set = params.get("rule_set") or {}
    variation_settings = dict(params.get("variation") or {})
    variation_settings.update(
        {
            "rule_set_id": rule_set.get("id") or params.get("rule_set_id"),
            "rule_set_status": rule_set.get("status", "draft"),
            "minimum_positive_count": rule_set.get(
                "minimum_positive_count",
                variation_settings.get("minimum_positive_count", 30),
            ),
            "background_method": rule_set.get(
                "background_method",
                variation_settings.get("background_method", "log_mad"),
            ),
            "background_scope": rule_set.get(
                "background_scope",
                variation_settings.get("background_scope", "model"),
            ),
            "merge_gap_m": rule_set.get(
                "merge_gap_m",
                variation_settings.get("merge_gap_m", 0.5),
            ),
            "minimum_segment_length_m": rule_set.get(
                "minimum_segment_length_m",
                variation_settings.get("minimum_segment_length_m", 0.0),
            ),
            "element_rules": rule_set.get("element_rules", {}),
            "relative_ratio_cutoffs": rule_set.get("relative_ratio_cutoffs", [1.25, 1.5, 2.0]),
            "boundary_grades_ppm": rule_set.get("boundary_grades_ppm", {}),
            "industrial_grades_ppm": {
                **rule_set.get("industrial_grades_ppm", {}),
                **variation_settings.get("industrial_grades_ppm", {}),
            },
        }
    )
    variation_cfg = VariationConfig(**variation_settings)
    correlation_settings = dict(params.get("correlation") or {})
    correlation_settings["minimum_positive_count"] = rule_set.get(
        "minimum_positive_count",
        correlation_settings.get("minimum_positive_count", 30),
    )
    correlation_settings["rule_set_status"] = rule_set.get("status", "draft")
    if not correlation_settings.get("focus_elements"):
        correlation_settings["focus_elements"] = tuple(
            element
            for element, item in (rule_set.get("element_rules") or {}).items()
            if item.get("role") in {"target", "companion", "indicator"}
        )
    correlation_cfg = CorrelationConfig(**correlation_settings)
    if mode in {"variation", "both"}:
        run_variation_algorithm(
            db_path=params["db_path"], model_id=params["model_id"],
            output_dir=output_dir / "element_variation", config=variation_cfg, filters=filters,
        )
    if mode in {"correlation", "both"}:
        variation_source = (
            Path(params["variation_output_dir"])
            if mode == "correlation" and params.get("variation_output_dir")
            else output_dir / "element_variation"
        )
        run_correlation_algorithm(
            db_path=params["db_path"], model_id=params["model_id"],
            output_dir=output_dir / "element_correlation",
            variation_output_dir=variation_source,
            config=correlation_cfg, filters=filters,
        )

    if params.get("generate_figures", True):
        configure_style()
        figure_dir = output_dir / "figures"
        generated: list[Path] = []
        if mode in {"variation", "both"}:
            generated += plot_variation_overview(output_dir / "element_variation", figure_dir / "element_variation")
            generated += plot_variation_depth_segments(output_dir / "element_variation", figure_dir / "element_variation")
        if mode in {"correlation", "both"}:
            generated += plot_correlation_heatmap(output_dir / "element_correlation", figure_dir / "element_correlation")
            generated += plot_r_type_dendrogram(output_dir / "element_correlation", figure_dir / "element_correlation")
            generated += plot_cluster_summary(output_dir / "element_correlation", figure_dir / "element_correlation")
        if generated:
            write_figure_contract(figure_dir / "图件说明.md")

    summary = build_summary(output_dir, params)
    if params.get("generate_figures", True):
        _task_report_pdf(output_dir, summary)
        summary = build_summary(output_dir, params)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.params, args.output)


if __name__ == "__main__":
    main()
