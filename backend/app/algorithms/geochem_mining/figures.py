"""从两个算法的离线结果生成中文科研图件。

本脚本不连接前端，也不生成三维模型；只将已经计算的 CSV 结果绘制为
可阅读的 PNG、PDF 和 SVG 图件。
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import dendrogram

from .professional_rules import MINING_LEVEL_COLORS, MINING_LEVEL_RANK


ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "outputs"
FIGURE_DIR = OUTPUT_DIR / "figures"

PALETTE = {
    "blue": "#0F4D92",
    "teal": "#42949E",
    "red": "#B64342",
    "gold": "#D89B17",
    "gray": "#767676",
    "light_gray": "#E7E7E7",
    "outer": "#9BC5E8",
    "middle": "#F2B86C",
    "inner": "#C94F4F",
    **MINING_LEVEL_COLORS,
}

MINING_LEVELS = list(MINING_LEVEL_COLORS)


def configure_style() -> None:
    """设置中文字体与可编辑 SVG/PDF 文本。"""

    logging.getLogger("fontTools.subset").setLevel(logging.ERROR)
    font_candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    chinese_font = None
    for path in font_candidates:
        if path.exists():
            chinese_font = font_manager.FontProperties(fname=str(path)).get_name()
            break
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [chinese_font, "Arial", "DejaVu Sans", "sans-serif"],
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "axes.unicode_minus": False,
            "font.size": 9,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "axes.linewidth": 0.8,
            "legend.frameon": False,
        }
    )


def save_figure(figure: plt.Figure, target: Path) -> list[Path]:
    target.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    for suffix, dpi in (("svg", None), ("pdf", None), ("png", 300)):
        path = target.with_suffix(f".{suffix}")
        kwargs = {"bbox_inches": "tight", "facecolor": "white"}
        if dpi:
            kwargs["dpi"] = dpi
        figure.savefig(path, **kwargs)
        paths.append(path)
    plt.close(figure)
    return paths


def _panel_label(ax: plt.Axes, label: str) -> None:
    ax.text(-0.13, 1.04, label, transform=ax.transAxes, fontsize=12, fontweight="bold", va="bottom")


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"找不到算法结果文件：{path}")
    return pd.read_csv(path, encoding="utf-8-sig")


def plot_variation_overview(variation_dir: Path, figure_dir: Path) -> list[Path]:
    """CV 与异常浓度分带：回答哪些元素分异更明显。"""

    stats = _read_csv(variation_dir / "element_background_statistics.csv")
    anomalies = _read_csv(variation_dir / "anomaly_intervals.csv")
    eligible = stats.loc[stats["used_for_variation"]].copy()
    level_column = "mining_level" if "mining_level" in anomalies else "anomaly_level"
    if level_column == "mining_level":
        observed_levels = set(anomalies[level_column].dropna().astype(str))
        display_levels = [level for level in MINING_LEVELS if level in observed_levels]
    else:
        display_levels = ["外带异常", "中带异常", "内带异常"]
    counts = anomalies.pivot_table(
        index="element", columns=level_column, values="assay_id", aggfunc="count", fill_value=0
    )
    for level in display_levels:
        if level not in counts:
            counts[level] = 0
    eligible = eligible.merge(counts[display_levels], left_on="element", right_index=True, how="left").fillna(0)
    eligible = eligible.sort_values("CV_raw", ascending=False).head(16).sort_values("CV_raw")

    fig, axes = plt.subplots(1, 2, figsize=(11.4, 5.2), gridspec_kw={"width_ratios": [1, 1.15]})
    ax = axes[0]
    ax.barh(eligible["element"], eligible["CV_raw"], color=PALETTE["blue"], edgecolor="white")
    ax.axvline(1.0, color=PALETTE["red"], linewidth=1.1, linestyle="--")
    ax.text(1.0, len(eligible) - 0.5, "CV = 1 强分异参考线", color=PALETTE["red"], ha="right", va="bottom", fontsize=8)
    ax.set_xlabel("CV_raw（有效正值的变异系数）")
    ax.set_ylabel("元素")
    ax.set_title("分异最明显的元素")
    ax.grid(axis="x", color=PALETTE["light_gray"], linewidth=0.6)
    _panel_label(ax, "a")

    ax = axes[1]
    stacked = eligible.set_index("element")[display_levels]
    left = np.zeros(len(stacked))
    legacy_colors = {"外带异常": PALETTE["outer"], "中带异常": PALETTE["middle"], "内带异常": PALETTE["inner"]}
    for level in display_levels:
        color = PALETTE.get(level, legacy_colors.get(level, PALETTE["gray"]))
        ax.barh(stacked.index, stacked[level], left=left, label=level, color=color, edgecolor="white", linewidth=0.4)
        left += stacked[level].to_numpy()
    ax.set_xlabel("异常化验区间数量")
    ax.set_ylabel("元素")
    ax.set_title("同批元素的找矿关注等级")
    ax.grid(axis="x", color=PALETTE["light_gray"], linewidth=0.6)
    ax.legend(loc="lower right", title="找矿关注等级", fontsize=8, title_fontsize=8)
    _panel_label(ax, "b")
    fig.suptitle("算法一：元素分异与找矿关注等级概览", y=1.02, fontsize=13, fontweight="bold")
    fig.tight_layout()
    return save_figure(fig, figure_dir / "variation_element_overview")


def plot_variation_depth_segments(variation_dir: Path, figure_dir: Path) -> list[Path]:
    """按元素展示重点异常段深度位置，不暗示真实三维轨迹。"""

    segments = _read_csv(variation_dir / "merged_anomaly_segments.csv")
    if segments.empty:
        raise ValueError("没有异常段，无法绘制深度分布图。")
    level_column = "highest_mining_level" if "highest_mining_level" in segments else "highest_anomaly_level"
    level_rank = {
        "外带异常": 5,
        "中带异常": 6,
        "内带异常": 7,
        **MINING_LEVEL_RANK,
    }
    segments["level_rank"] = segments[level_column].map(level_rank).fillna(0)
    priority = (
        segments.groupby("element")["level_rank"].agg(["sum", "count"]).sort_values(["sum", "count"], ascending=False)
    )
    selected = priority.head(4).index.tolist()
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 7.2), sharex=False)
    color_map = {
        **{"外带异常": PALETTE["outer"], "中带异常": PALETTE["middle"], "内带异常": PALETTE["inner"]},
        **{level: PALETTE[level] for level in MINING_LEVELS},
    }

    for ax, element in zip(axes.ravel(), selected):
        data = segments.loc[segments["element"] == element].copy()
        holes = sorted(data["hole_id"].unique())
        y_positions = {hole: index for index, hole in enumerate(holes)}
        for _, row in data.iterrows():
            y = y_positions[row["hole_id"]]
            level = row[level_column]
            ax.hlines(y, row["segment_from_depth"], row["segment_to_depth"], color=color_map.get(level, PALETTE["gray"]), linewidth=3.2)
        ax.set_yticks(range(len(holes)))
        ax.set_yticklabels(holes, fontsize=7)
        ax.set_xlabel("深度（m）")
        ax.set_title(f"{element}：重点异常段深度分布")
        ax.grid(axis="x", color=PALETTE["light_gray"], linewidth=0.6)
        for level in sorted(data[level_column].dropna().unique(), key=lambda item: level_rank.get(item, 0)):
            color = color_map.get(level, PALETTE["gray"])
            ax.plot([], [], color=color, linewidth=3.2, label=level)
        ax.legend(loc="lower right", fontsize=7, ncol=1)
    for ax in axes.ravel()[len(selected) :]:
        ax.set_visible(False)
    fig.suptitle("算法一：重点元素在钻孔中的异常深度段", y=0.995, fontsize=13, fontweight="bold")
    fig.text(0.5, 0.005, "注：横轴为钻孔深度；本图不表达真实钻孔轨迹或三维空间位置。", ha="center", fontsize=8, color=PALETTE["gray"])
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    return save_figure(fig, figure_dir / "variation_anomaly_depth_segments")


def _load_linkage(correlation_dir: Path) -> np.ndarray:
    linkage_frame = _read_csv(correlation_dir / "r_type_linkage.csv")
    return linkage_frame[["left_id", "right_id", "distance", "cluster_size"]].to_numpy(dtype=float)


def _load_cluster_matrix(correlation_dir: Path) -> pd.DataFrame:
    matrix = _read_csv(correlation_dir / "pearson_correlation_matrix.csv")
    return matrix.set_index("element")


def plot_correlation_heatmap(correlation_dir: Path, figure_dir: Path) -> list[Path]:
    """按 R 型树顺序排列的 Pearson 相关热图。"""

    matrix = _load_cluster_matrix(correlation_dir)
    linkage_matrix = _load_linkage(correlation_dir)
    order = dendrogram(linkage_matrix, no_plot=True)["leaves"]
    ordered = matrix.iloc[order, order]
    fig, ax = plt.subplots(figsize=(9.8, 8.6))
    image = ax.imshow(ordered.to_numpy(dtype=float), cmap="RdBu_r", vmin=-1, vmax=1, aspect="equal")
    ax.set_xticks(range(len(ordered.columns)))
    ax.set_xticklabels(ordered.columns, rotation=90, fontsize=7)
    ax.set_yticks(range(len(ordered.index)))
    ax.set_yticklabels(ordered.index, fontsize=7)
    ax.set_title("算法二：按 R 型聚类重排的 Pearson 相关系数矩阵", pad=12, fontsize=13, fontweight="bold")
    colorbar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    colorbar.set_label("Pearson 相关系数 r")
    ax.set_xlabel("元素")
    ax.set_ylabel("元素")
    fig.tight_layout()
    return save_figure(fig, figure_dir / "correlation_clustered_heatmap")


def plot_r_type_dendrogram(correlation_dir: Path, figure_dir: Path) -> list[Path]:
    """R 型层次聚类树状图。"""

    matrix = _load_cluster_matrix(correlation_dir)
    linkage_matrix = _load_linkage(correlation_dir)
    fig, ax = plt.subplots(figsize=(11.6, 6.3))
    dendrogram(
        linkage_matrix,
        labels=matrix.index.tolist(),
        color_threshold=0.5,
        above_threshold_color=PALETTE["gray"],
        leaf_rotation=90,
        leaf_font_size=8,
        ax=ax,
    )
    ax.axhline(0.5, color=PALETTE["red"], linestyle="--", linewidth=1.0)
    ax.text(len(matrix.index) - 0.2, 0.51, "r = 0.50 对应 distance = 0.50", ha="right", va="bottom", fontsize=8, color=PALETTE["red"])
    ax.set_title("算法二：R 型层次聚类树状图", fontsize=13, fontweight="bold")
    ax.set_xlabel("元素")
    ax.set_ylabel("聚类距离（distance = 1 - r）")
    ax.grid(axis="y", color=PALETTE["light_gray"], linewidth=0.6)
    fig.tight_layout()
    return save_figure(fig, figure_dir / "correlation_r_type_dendrogram")


def plot_cluster_summary(correlation_dir: Path, figure_dir: Path) -> list[Path]:
    """多元素组合的内部相关性和共异常重合率。"""

    clusters = _read_csv(correlation_dir / "r_type_cluster_groups.csv")
    overlap = _read_csv(correlation_dir / "cluster_anomaly_overlap.csv")
    clusters = clusters.loc[clusters["is_multielement_combination"]].copy()
    if clusters.empty:
        raise ValueError("当前数据没有可绘制的多元素组合。")
    clusters["cluster_id"] = pd.to_numeric(clusters["cluster_id"], errors="coerce").astype("Int64")
    clusters["elements"] = clusters["elements"].fillna("").astype(str)
    overlap["cluster_id"] = pd.to_numeric(overlap["cluster_id"], errors="coerce").astype("Int64")
    overlap["elements"] = overlap["elements"].fillna("").astype(str)
    data = clusters.merge(overlap, on=["cluster_id", "elements"], how="left")
    data = data.sort_values("mean_internal_pearson_r")
    labels = data["elements"].tolist()
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8), sharey=True)

    axes[0].barh(labels, data["mean_internal_pearson_r"], color=PALETTE["blue"], edgecolor="white")
    axes[0].axvline(0.5, color=PALETTE["red"], linestyle="--", linewidth=1.0)
    axes[0].set_xlabel("组合内部平均 Pearson r")
    axes[0].set_xlim(0, 1)
    axes[0].set_title("组合内部相关性")
    axes[0].grid(axis="x", color=PALETTE["light_gray"], linewidth=0.6)
    _panel_label(axes[0], "a")

    axes[1].barh(labels, data["co_anomaly_ratio"].fillna(0), color=PALETTE["teal"], edgecolor="white")
    axes[1].set_xlabel("同一化验区间内至少两元素异常的比例")
    axes[1].set_xlim(0, 1)
    axes[1].set_title("组合与算法一异常结果的重合")
    axes[1].grid(axis="x", color=PALETTE["light_gray"], linewidth=0.6)
    _panel_label(axes[1], "b")
    fig.suptitle("算法二：多元素组合摘要", y=1.02, fontsize=13, fontweight="bold")
    fig.tight_layout()
    return save_figure(fig, figure_dir / "correlation_cluster_summary")


def write_figure_contract(path: Path) -> Path:
    text = """# 离线算法图件说明

## 图件结论

- 算法一图件展示：元素的离散程度、相对本元素背景值的异常等级、边界品位命中情况及重点元素在钻孔深度方向的异常段。
- 算法二图件展示：通过共同有效正值样本计算得到的元素相关结构、R 型聚类及多元素组合的共异常重合情况。

## 图件边界

1. 图件全部由已生成的算法 CSV 结果绘制，不引入前端或三维模型数据。
2. 异常深度图表达深度区间，不表达真实钻孔轨迹或三维空间体。
3. 热图和树状图只包含通过共同样本数检查、实际进入 R 型聚类的元素。
4. 图中的相关性和共异常重合只表示数据关联，不表示地质因果关系。
5. 达到边界品位或后续补充的最低工业品位，仅表示候选找矿证据，不能单独等同于可采矿体。

## 输出格式

每张图同时输出 SVG（可编辑文字）、PDF 和 PNG（查看预览）。
"""
    path.write_text(text, encoding="utf-8")
    return path


def generate_all_figures(
    output_dir: Path | str | None = None,
    figure_dir: Path | str | None = None,
) -> dict[str, list[Path] | Path]:
    """生成所有离线算法图件。运行前应先运行两个算法。"""

    configure_style()
    output_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    figure_dir = Path(figure_dir) if figure_dir else output_dir / "figures"
    variation_dir = output_dir / "element_variation"
    correlation_dir = output_dir / "element_correlation"
    variation_figures = figure_dir / "element_variation"
    correlation_figures = figure_dir / "element_correlation"
    outputs: dict[str, list[Path] | Path] = {
        "variation_element_overview": plot_variation_overview(variation_dir, variation_figures),
        "variation_anomaly_depth_segments": plot_variation_depth_segments(variation_dir, variation_figures),
        "correlation_clustered_heatmap": plot_correlation_heatmap(correlation_dir, correlation_figures),
        "correlation_r_type_dendrogram": plot_r_type_dendrogram(correlation_dir, correlation_figures),
        "correlation_cluster_summary": plot_cluster_summary(correlation_dir, correlation_figures),
        "figure_contract": write_figure_contract(figure_dir / "图件说明.md"),
    }
    return outputs


if __name__ == "__main__":
    generated = generate_all_figures()
    print("离线算法图件生成完成。")
    for name, paths in generated.items():
        if isinstance(paths, list):
            print(f"- {name}:")
            for path in paths:
                print(f"  {path}")
        else:
            print(f"- {name}: {paths}")
