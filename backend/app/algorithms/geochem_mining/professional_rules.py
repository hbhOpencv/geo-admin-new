"""Project grade rules and the one shared six-colour geochemical legend.

The values below are transcribed from the user-confirmed workbook
``常见矿物元素品位值.xlsx`` supplied on 2026-07-30.  A missing industrial
grade (currently Rb only) stays missing; it must never be guessed.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

import pandas as pd


BOUNDARY_GRADES_PPM: dict[str, float] = {
    "S": 80000.0,
    "Ti": 2996.0,
    "V": 1400.0,
    "Cr": 34211.0,
    "Mn": 80000.0,
    "Fe": 200000.0,
    "Co": 200.0,
    "Ni": 2000.0,
    "Cu": 2000.0,
    "Zn": 5000.0,
    "Rb": 914.0,
    "Zr": 2221.0,
    "Sn": 1000.0,
    "Sb": 7000.0,
    "W": 507.0,
    "Pb": 3000.0,
    "U": 300.0,
    "Ag": 40.0,
    "Mo": 300.0,
    "Hg": 400.0,
    "Au": 0.8,
    "Li": 465.0,
    "Mg": 114584.0,
    "Ca": 343053.0,
    "Nb": 350.0,
    "Cd": 100.0,
    "Ba": 176570.0,
    "Bi": 2500.0,
    "Th": 943.0,
    "Y": 394.0,
    "La": 426.0,
    "Ce": 407.0,
    "Pr": 414.0,
    "Nd": 429.0,
}

INDUSTRIAL_GRADES_PPM: dict[str, float] = {
    "S": 140000.0,
    "Ti": 2996.0,
    "V": 1961.0,
    "Cr": 82104.0,
    "Mn": 120000.0,
    "Fe": 250000.0,
    "Co": 300.0,
    "Ni": 3000.0,
    "Cu": 4000.0,
    "Zn": 10000.0,
    "Zr": 5922.0,
    "Sn": 2000.0,
    "Sb": 15000.0,
    "W": 952.0,
    "Pb": 7000.0,
    "U": 500.0,
    "Ag": 80.0,
    "Mo": 600.0,
    "Hg": 800.0,
    "Au": 1.0,
    "Li": 929.0,
    "Mg": 114584.0,
    "Ca": 357347.0,
    "Nb": 559.0,
    "Cd": 100.0,
    "Ba": 294283.0,
    "Bi": 5000.0,
    "Th": 943.0,
    "Y": 630.0,
    "La": 682.0,
    "Ce": 651.0,
    "Pr": 662.0,
    "Nd": 686.0,
}
DEFAULT_RELATIVE_RATIO_CUTOFFS: tuple[float, float, float] = (1.25, 1.5, 2.0)

COMMON_METAL_ELEMENTS: tuple[str, ...] = (
    "Au", "Ag", "Cu", "Pb", "Zn", "W", "Sn", "Mo", "Bi", "Cd", "Co",
    "Ni", "Fe", "Mn", "Cr", "V", "Ti", "Sb", "U", "Li", "Hg",
)

MINING_DISPLAY_LEVEL_COLORS: dict[str, str] = {
    "背景范围": "#FFF7F3",
    "相对富集": "#FDD0C4",
    "明显正异常": "#FC8A6A",
    "强正异常": "#EF3B2C",
    "最低边界品位": "#A50F15",
    "工业品位": "#7A0177",
}


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def continuous_mining_color(value: Any, profile: Mapping[str, Any]) -> tuple[int, int, int]:
    """Return the shared six-colour palette as a continuous colour ramp.

    The six named levels remain the auditable legend and classification.  This
    function only interpolates *between* their real, element-specific anchors;
    it adds no noise, no copied Algorithm-B values, and no fabricated samples.
    Logarithmic interpolation is used because element concentrations commonly
    span orders of magnitude.
    """

    number = _finite_positive(value)
    background = _finite_positive(profile.get("background_mean"))
    threshold = _finite_positive(profile.get("threshold_T_auto"))
    boundary = _finite_positive(profile.get("boundary_grade_ppm"))
    industrial = _finite_positive(profile.get("industrial_grade_ppm"))
    cut_1, cut_2, cut_3 = _relative_cutoffs(profile.get("relative_ratio_cutoffs"))
    if not math.isfinite(number) or not math.isfinite(background):
        return _rgb(MINING_DISPLAY_LEVEL_COLORS["背景范围"])

    # A monotonic anchor sequence is required because a statistical T can be
    # below/above a professional grade for different elements.  Duplicate or
    # reversed anchors collapse safely without changing the reported level.
    anchors: list[tuple[float, str]] = [
        (max(background * 0.5, 1e-12), "背景范围"),
        (max(background * cut_1, 1e-12), "背景范围"),
        (max(background * cut_2, 1e-12), "相对富集"),
        (max(background * cut_3, 1e-12), "相对富集"),
        (threshold if math.isfinite(threshold) else background * cut_3, "明显正异常"),
        (2.0 * threshold if math.isfinite(threshold) else background * cut_3, "强正异常"),
    ]
    if math.isfinite(boundary):
        anchors.append((boundary, "最低边界品位"))
    if math.isfinite(industrial):
        anchors.append((industrial, "工业品位"))
    anchors.sort(key=lambda item: item[0])
    compact: list[tuple[float, str]] = []
    for anchor, level in anchors:
        if compact and anchor <= compact[-1][0] * (1.0 + 1e-12):
            compact[-1] = (max(anchor, compact[-1][0]), level)
        else:
            compact.append((anchor, level))

    if number <= compact[0][0]:
        return _rgb(MINING_DISPLAY_LEVEL_COLORS[compact[0][1]])
    for (left_value, left_level), (right_value, right_level) in zip(compact, compact[1:]):
        if number <= right_value:
            left = _rgb(MINING_DISPLAY_LEVEL_COLORS[left_level])
            right = _rgb(MINING_DISPLAY_LEVEL_COLORS[right_level])
            position = (math.log(number) - math.log(left_value)) / max(
                math.log(right_value) - math.log(left_value), 1e-12
            )
            position = min(1.0, max(0.0, position))
            return tuple(round(a + (b - a) * position) for a, b in zip(left, right))
    return _rgb(MINING_DISPLAY_LEVEL_COLORS[compact[-1][1]])


def continuous_relative_color(value: Any, low: Any, high: Any) -> tuple[int, int, int]:
    """Map one element's robust log range through the shared six-colour palette."""

    number = _finite_positive(value)
    lower = _finite_positive(low)
    upper = _finite_positive(high)
    palette = [_rgb(color) for color in MINING_DISPLAY_LEVEL_COLORS.values()]
    if not math.isfinite(number) or not math.isfinite(lower) or not math.isfinite(upper):
        return palette[0]
    if upper <= lower:
        return palette[len(palette) // 2]
    position = (math.log(number) - math.log(lower)) / max(
        math.log(upper) - math.log(lower), 1.0e-12
    )
    position = min(1.0, max(0.0, position)) * (len(palette) - 1)
    left_index = min(int(math.floor(position)), len(palette) - 1)
    right_index = min(left_index + 1, len(palette) - 1)
    fraction = position - left_index
    return tuple(
        round(left + (right - left) * fraction)
        for left, right in zip(palette[left_index], palette[right_index])
    )

# Keep persisted/computational keys stable. Only user-facing text uses aliases.
PROFESSIONAL_DISPLAY_LABELS: dict[str, str] = {
    "明显正异常": "明显异常",
    "最低边界品位": "边界品位",
    "工业品位": "最低工业品位",
}


def professional_display_label(value: object) -> str:
    text = str(value or "")
    return PROFESSIONAL_DISPLAY_LABELS.get(text, text)

MINING_LEVEL_TO_DISPLAY_LEVEL: dict[str, str] = {
    "背景范围": "背景范围",
    "轻微相对富集": "相对富集",
    "相对富集": "相对富集",
    "强相对富集": "相对富集",
    "统计异常外带": "明显正异常",
    "统计异常中带": "强正异常",
    "统计异常内带": "强正异常",
    "最低边界品位": "最低边界品位",
    "工业品位": "工业品位",
}

# Detailed labels remain in CSV/report data. Their map colours intentionally
# collapse to the six public display classes above.
MINING_LEVEL_COLORS: dict[str, str] = {
    level: MINING_DISPLAY_LEVEL_COLORS[display_level]
    for level, display_level in MINING_LEVEL_TO_DISPLAY_LEVEL.items()
}

RELIABILITY_LEVEL_COLORS: dict[str, str] = {
    "无可靠估计": "#B8C0CC",
    "低可信度": "#A7B0BD",
}

MINING_LEVEL_RANK: dict[str, int] = {
    "背景范围": 1,
    "轻微相对富集": 2,
    "相对富集": 3,
    "强相对富集": 4,
    "统计异常外带": 5,
    "统计异常中带": 6,
    "统计异常内带": 7,
    "最低边界品位": 8,
    "工业品位": 9,
}


def _finite_positive(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) and number > 0 else math.nan


def _relative_cutoffs(values: Iterable[Any] | None) -> tuple[float, float, float]:
    raw = list(values or DEFAULT_RELATIVE_RATIO_CUTOFFS)
    # Four-value arrays were stored by the legacy rule UI.  The fourth value
    # never defines a V2 relative band because T/2T/4T own the statistical
    # anomaly bands.  Accept old data but expose only the three effective cuts.
    if len(raw) not in {3, 4}:
        return DEFAULT_RELATIVE_RATIO_CUTOFFS
    parsed = tuple(_finite_positive(value) for value in raw[:3])
    if (
        any(not math.isfinite(value) or value <= 1 for value in parsed)
        or any(right <= left for left, right in zip(parsed, parsed[1:]))
    ):
        return DEFAULT_RELATIVE_RATIO_CUTOFFS
    return parsed  # type: ignore[return-value]


def relative_enrichment_ratio(value: Any, background_mean: Any) -> float:
    """Return value/background for the same element, never an absolute ppm gap."""

    number = _finite_positive(value)
    background = _finite_positive(background_mean)
    return number / background if math.isfinite(number) and math.isfinite(background) else math.nan


def _resolved_grades(
    built_in: Mapping[str, Any],
    overrides: Mapping[str, Any] | None,
) -> dict[str, float]:
    resolved = {
        str(element): number
        for element, value in built_in.items()
        if math.isfinite(number := _finite_positive(value))
    }
    for element, value in (overrides or {}).items():
        number = _finite_positive(value)
        if math.isfinite(number):
            resolved[str(element)] = number
    return resolved


def build_threshold_profile(
    element: str,
    positive_values: pd.Series,
    background_mean: Any,
    statistical_threshold: Any,
    industrial_grades_ppm: Mapping[str, Any] | None = None,
    *,
    boundary_grades_ppm: Mapping[str, Any] | None = None,
    relative_ratio_cutoffs: Iterable[Any] | None = None,
    rule_set_id: str | None = None,
    rule_set_status: str = "draft",
) -> dict[str, Any]:
    """Build independent statistical and professional interpretation thresholds."""

    element = str(element)
    background = _finite_positive(background_mean)
    threshold = _finite_positive(statistical_threshold)
    boundary = _finite_positive(
        _resolved_grades(BOUNDARY_GRADES_PPM, boundary_grades_ppm).get(element)
    )
    industrial = _finite_positive(
        _resolved_grades(INDUSTRIAL_GRADES_PPM, industrial_grades_ppm).get(element)
    )
    cut_1, cut_2, cut_3 = _relative_cutoffs(relative_ratio_cutoffs)
    values = pd.to_numeric(positive_values, errors="coerce")
    valid_count = int((values > 0).sum())

    return {
        "element": element,
        "unit": "ppm",
        "valid_positive_count": valid_count,
        "background_mean": background,
        "threshold_T_auto": threshold,
        "statistical_threshold_to_background_ratio": (
            threshold / background
            if math.isfinite(threshold) and math.isfinite(background)
            else math.nan
        ),
        "relative_band_cut_1": cut_1,
        "relative_band_cut_2": cut_2,
        "relative_band_cut_3": cut_3,
        "relative_ratio_cutoffs": [cut_1, cut_2, cut_3],
        "relative_band_method": "project_wide_fixed_background_ratios",
        "relative_band_status": rule_set_status,
        "boundary_grade_ppm": boundary if math.isfinite(boundary) else None,
        "boundary_grade_source": (
            "confirmed_34_element_grade_workbook_2026_07_30"
            if math.isfinite(boundary)
            else "not_available"
        ),
        "industrial_grade_ppm": industrial if math.isfinite(industrial) else None,
        "industrial_grade_source": (
            "confirmed_34_element_grade_workbook_2026_07_30"
            if math.isfinite(industrial) else "not_available_in_source_workbook"
        ),
        "rule_set_id": rule_set_id,
        "rule_set_status": rule_set_status,
    }


def classify_mining_level(value: Any, profile: Mapping[str, Any]) -> dict[str, Any]:
    number = _finite_positive(value)
    background = _finite_positive(profile.get("background_mean"))
    threshold = _finite_positive(profile.get("threshold_T_auto"))
    boundary = _finite_positive(profile.get("boundary_grade_ppm"))
    industrial = _finite_positive(profile.get("industrial_grade_ppm"))
    ratio = relative_enrichment_ratio(number, background)
    cutoffs = _relative_cutoffs(profile.get("relative_ratio_cutoffs"))

    if not math.isfinite(number) or not math.isfinite(background):
        level = "无可靠估计"
    elif math.isfinite(industrial) and number >= industrial:
        level = "工业品位"
    elif math.isfinite(boundary) and number >= boundary:
        level = "最低边界品位"
    elif math.isfinite(threshold) and number >= 4.0 * threshold:
        level = "统计异常内带"
    elif math.isfinite(threshold) and number >= 2.0 * threshold:
        level = "统计异常中带"
    elif math.isfinite(threshold) and number >= threshold:
        level = "统计异常外带"
    elif ratio < cutoffs[0]:
        level = "背景范围"
    elif ratio < cutoffs[1]:
        level = "轻微相对富集"
    elif ratio < cutoffs[2]:
        level = "相对富集"
    else:
        level = "强相对富集"

    display_level = (
        level
        if level in RELIABILITY_LEVEL_COLORS
        else MINING_LEVEL_TO_DISPLAY_LEVEL[level]
    )
    color_catalog = {**RELIABILITY_LEVEL_COLORS, **MINING_DISPLAY_LEVEL_COLORS}
    rank = -1 if level == "无可靠估计" else MINING_LEVEL_RANK[level]

    return {
        "mining_level": level,
        "display_level": display_level,
        "mining_level_rank": rank,
        "display_color_hex": color_catalog[display_level],
        "value_to_background_ratio": ratio,
        "relative_deviation": ratio - 1.0 if math.isfinite(ratio) else math.nan,
        "meets_statistical_threshold": bool(
            math.isfinite(number) and math.isfinite(threshold) and number >= threshold
        ),
        "meets_boundary_grade": bool(
            math.isfinite(number) and math.isfinite(boundary) and number >= boundary
        ),
        "meets_industrial_grade": bool(
            math.isfinite(number) and math.isfinite(industrial) and number >= industrial
        ),
    }


def public_rule_catalog(available_elements: list[str] | None = None) -> dict[str, Any]:
    available = set(available_elements or [])
    focus = [element for element in COMMON_METAL_ELEMENTS if not available or element in available]
    thresholds = []
    for element, boundary in BOUNDARY_GRADES_PPM.items():
        if available and element not in available:
            continue
        industrial = INDUSTRIAL_GRADES_PPM.get(element)
        thresholds.append(
            {
                "element": element,
                "unit": "ppm",
                "boundary_grade_ppm": boundary,
                "boundary_grade_status": "confirmed",
                "industrial_grade_ppm": industrial,
                "industrial_grade_status": "confirmed" if industrial is not None else "pending",
            }
        )
    return {
        "focus_metal_elements": focus,
        "professional_thresholds": thresholds,
        "relative_ratio_cutoffs": list(DEFAULT_RELATIVE_RATIO_CUTOFFS),
        "relative_ratio_status": "draft",
        "statistical_band_multipliers": [1.0, 2.0, 4.0],
        "reliability_level_colors": [
            {"level": level, "color": color}
            for level, color in RELIABILITY_LEVEL_COLORS.items()
        ],
        "mining_level_colors": [
            {"level": professional_display_label(level), "color": color, "rank": rank}
            for rank, (level, color) in enumerate(MINING_DISPLAY_LEVEL_COLORS.items(), 1)
        ],
    }
