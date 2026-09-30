"""德达隧道化验区间的读取、质量审计与空间/分段融合。

本模块只做公共数据准备，不在这里判断异常或元素相关性。
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = BACKEND_DIR / "geology_norm.db"

REQUIRED_TABLES = (
    "chemical_assays",
    "chemical_boreholes",
    "boreholes",
    "borehole_sections",
    "geobodies",
)

METADATA_COLUMNS = {
    "assay_id",
    "model_id",
    "chemical_borehole_id",
    "hole_id",
    "from_depth",
    "to_depth",
    "mid_depth",
    "interval_length",
    "unit",
    "created_at",
    "collar_x",
    "collar_y",
    "collar_z",
    "x_approx",
    "y_approx",
    "z_approx",
    "coordinate_quality",
    "section_match_status",
    "section_id",
    "section_depth",
    "section_bottom",
    "section_description",
    "section_geobody_key",
    "geobody_property_status",
    "geobody_key_exact",
    "stratum_code",
    "lithology",
    "weathering",
    "engineering_grade",
    "data_quality_flags",
}


@dataclass
class GeochemDataset:
    """已完成区间、坐标和地质分段关联的化验数据。"""

    db_path: Path
    model_id: int
    assays: pd.DataFrame
    elements: list[str]
    invalid_intervals: pd.DataFrame
    section_match_audit: pd.DataFrame
    table_counts: dict[str, int]


def dataset_sha256(dataset: GeochemDataset) -> str:
    """Hash analytical inputs while excluding mutable task/audit tables."""

    digest = hashlib.sha256()
    digest.update(f"model_id={dataset.model_id}\n".encode())
    digest.update(("elements=" + ",".join(sorted(dataset.elements)) + "\n").encode())
    for name, frame in (
        ("assays", dataset.assays),
        ("invalid_intervals", dataset.invalid_intervals),
        ("section_match_audit", dataset.section_match_audit),
    ):
        digest.update((name + "\n").encode())
        ordered = frame.reindex(sorted(frame.columns), axis=1).copy()
        sort_columns = [
            column
            for column in ("assay_id", "hole_id", "from_depth", "to_depth", "section_id")
            if column in ordered.columns
        ]
        if sort_columns:
            ordered = ordered.sort_values(sort_columns, kind="stable", na_position="last")
        digest.update(
            ordered.to_csv(index=False, lineterminator="\n", float_format="%.15g").encode("utf-8")
        )
    return digest.hexdigest()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_csv(frame: pd.DataFrame, path: Path) -> Path:
    ensure_dir(path.parent)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def write_text(text: str, path: Path) -> Path:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")
    return path


def _read_tables(db_path: Path, model_id: int) -> dict[str, pd.DataFrame]:
    if not db_path.exists():
        raise FileNotFoundError(f"找不到数据库：{db_path}")

    with sqlite3.connect(str(db_path)) as connection:
        available = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = [name for name in REQUIRED_TABLES if name not in available]
        if missing:
            raise ValueError(f"数据库缺少必要数据表：{', '.join(missing)}")
        model = connection.execute(
            "SELECT id FROM geological_models WHERE id = ?", (model_id,)
        ).fetchone()
        if model is None:
            raise ValueError(f"地质模型不存在：{model_id}")

        direct_tables = ("chemical_assays", "chemical_boreholes", "boreholes", "geobodies")
        tables = {
            name: pd.read_sql_query(
                f'SELECT * FROM "{name}" WHERE model_id = ?', connection, params=(model_id,)
            )
            for name in direct_tables
        }
        tables["borehole_sections"] = pd.read_sql_query(
            """
            SELECT section.*
            FROM borehole_sections AS section
            JOIN boreholes AS hole ON hole.id = section.borehole_id
            WHERE hole.model_id = ?
            """,
            connection,
            params=(model_id,),
        )
        return tables


def _to_number(value: Any) -> float:
    if value is None or pd.isna(value):
        return np.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def _parse_elements(value: Any) -> dict[str, float]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): _to_number(item) for key, item in raw.items()}


def _normalise_key(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).replace("<", "").replace(">", "").strip().upper()


def _expand_assays(assays: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    rows: list[dict[str, Any]] = []
    element_names: set[str] = set()

    for _, source in assays.iterrows():
        element_values = _parse_elements(source.get("elements_json"))
        element_names.update(element_values)
        row = {
            "assay_id": source.get("id"),
            "model_id": source.get("model_id"),
            "chemical_borehole_id": source.get("chemical_borehole_id"),
            "hole_id": source.get("hole_id"),
            "from_depth": _to_number(source.get("from_depth")),
            "to_depth": _to_number(source.get("to_depth")),
            "unit": source.get("unit"),
            "created_at": source.get("created_at"),
        }
        row.update(element_values)
        rows.append(row)

    elements = sorted(element_names)
    result = pd.DataFrame(rows)
    for element in elements:
        result[element] = pd.to_numeric(result[element], errors="coerce")
    result["mid_depth"] = (result["from_depth"] + result["to_depth"]) / 2.0
    result["interval_length"] = result["to_depth"] - result["from_depth"]
    return result, elements


def _attach_coordinates(
    assays: pd.DataFrame, chemical_boreholes: pd.DataFrame
) -> pd.DataFrame:
    cols = ["hole_id", "collar_x", "collar_y", "collar_z", "depth_min", "depth_max"]
    existing = [column for column in cols if column in chemical_boreholes.columns]
    result = assays.merge(chemical_boreholes[existing], on="hole_id", how="left")
    for column in ("collar_x", "collar_y", "collar_z"):
        if column not in result:
            result[column] = np.nan
        result[column] = pd.to_numeric(result[column], errors="coerce")

    result["x_approx"] = result["collar_x"]
    result["y_approx"] = result["collar_y"]
    result["z_approx"] = result["collar_z"] - result["mid_depth"]
    has_coordinates = result[["collar_x", "collar_y", "collar_z"]].notna().all(axis=1)
    result["coordinate_quality"] = np.where(
        has_coordinates & (result["interval_length"] > 0),
        "approximate_vertical",
        "unavailable",
    )
    return result


def _attach_sections(
    assays: pd.DataFrame, sections: pd.DataFrame, geobodies: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    result = assays.copy()
    result["section_match_status"] = "unmatched"
    result["section_id"] = np.nan
    result["section_depth"] = np.nan
    result["section_bottom"] = np.nan
    result["section_description"] = ""
    result["section_geobody_key"] = ""
    result["geobody_property_status"] = "unmatched"
    result["geobody_key_exact"] = ""
    result["stratum_code"] = ""
    result["lithology"] = ""
    result["weathering"] = ""
    result["engineering_grade"] = ""

    required = {"Borehole", "Depth", "Bottom", "geobody_key"}
    if not required.issubset(sections.columns):
        audit = pd.DataFrame(
            [{"check": "section_schema", "status": "missing_columns", "detail": ", ".join(sorted(required - set(sections.columns)))}]
        )
        return result, audit

    prepared = sections.copy()
    prepared["Depth"] = pd.to_numeric(prepared["Depth"], errors="coerce")
    prepared["Bottom"] = pd.to_numeric(prepared["Bottom"], errors="coerce")
    prepared = prepared.dropna(subset=["Borehole", "Depth", "Bottom"])
    sections_by_hole = {
        hole_id: group.to_dict("records")
        for hole_id, group in prepared.groupby("Borehole", dropna=False)
    }

    exact_geobodies: dict[str, dict[str, Any]] = {}
    if "key" in geobodies.columns:
        for _, row in geobodies.iterrows():
            key = _normalise_key(row.get("key"))
            if key:
                exact_geobodies[key] = row.to_dict()

    audit_rows: list[dict[str, Any]] = []
    for index, assay in result.iterrows():
        if not np.isfinite(assay["mid_depth"]) or assay["interval_length"] <= 0:
            result.at[index, "section_match_status"] = "invalid_interval"
            continue
        hits = [
            section
            for section in sections_by_hole.get(assay["hole_id"], [])
            if section["Depth"] <= assay["mid_depth"] <= section["Bottom"]
        ]
        if len(hits) != 1:
            status = "unmatched" if not hits else "multiple_matches"
            result.at[index, "section_match_status"] = status
            audit_rows.append(
                {
                    "assay_id": assay["assay_id"],
                    "hole_id": assay["hole_id"],
                    "mid_depth": assay["mid_depth"],
                    "section_match_status": status,
                    "matched_section_count": len(hits),
                }
            )
            continue

        section = hits[0]
        result.at[index, "section_match_status"] = "unique_match"
        result.at[index, "section_id"] = section.get("id")
        result.at[index, "section_depth"] = section.get("Depth")
        result.at[index, "section_bottom"] = section.get("Bottom")
        result.at[index, "section_description"] = section.get("Description", "")
        section_key = _normalise_key(section.get("geobody_key"))
        result.at[index, "section_geobody_key"] = section_key

        # 只接受精确键匹配；禁止把 25-4 或 17-11 猜成某一条 W2/W3 地质体。
        if section_key in exact_geobodies:
            geo = exact_geobodies[section_key]
            result.at[index, "geobody_property_status"] = "exact"
            result.at[index, "geobody_key_exact"] = section_key
            result.at[index, "stratum_code"] = geo.get("地层代号", "")
            result.at[index, "lithology"] = geo.get("岩土名称", "")
            result.at[index, "weathering"] = geo.get("风化程度", "")
            result.at[index, "engineering_grade"] = geo.get("工程等级", "")
        elif section_key:
            result.at[index, "geobody_property_status"] = "needs_mapping"

    summary = (
        result.groupby(["section_match_status", "geobody_property_status"], dropna=False)
        .size()
        .reset_index(name="record_count")
    )
    if audit_rows:
        audit = pd.concat([summary, pd.DataFrame(audit_rows)], ignore_index=True, sort=False)
    else:
        audit = summary
    return result, audit


def _build_invalid_interval_audit(assays: pd.DataFrame) -> pd.DataFrame:
    invalid = assays.loc[
        assays["from_depth"].isna()
        | assays["to_depth"].isna()
        | (assays["from_depth"] >= assays["to_depth"]),
        ["assay_id", "hole_id", "from_depth", "to_depth", "interval_length"],
    ].copy()
    if invalid.empty:
        invalid["invalid_reason"] = pd.Series(dtype="object")
        return invalid
    invalid["invalid_reason"] = np.select(
        [
            invalid["from_depth"].isna() | invalid["to_depth"].isna(),
            invalid["from_depth"] == invalid["to_depth"],
            invalid["from_depth"] > invalid["to_depth"],
        ],
        ["missing_depth", "zero_length_interval", "reversed_interval"],
        default="invalid_interval",
    )
    return invalid.reset_index(drop=True)


def _add_quality_flags(assays: pd.DataFrame) -> pd.DataFrame:
    result = assays.copy()
    flags: list[str] = []
    for _, row in result.iterrows():
        row_flags: list[str] = []
        if not np.isfinite(row["interval_length"]) or row["interval_length"] <= 0:
            row_flags.append("invalid_interval")
        if row["coordinate_quality"] != "approximate_vertical":
            row_flags.append("coordinates_unavailable")
        if row["section_match_status"] != "unique_match":
            row_flags.append(f"section_{row['section_match_status']}")
        if row["geobody_property_status"] == "needs_mapping":
            row_flags.append("geobody_property_needs_mapping")
        flags.append(";".join(row_flags))
    result["data_quality_flags"] = flags
    return result


def load_geochem_dataset(
    db_path: Path | str = DEFAULT_DB_PATH,
    model_id: int | None = None,
    selected_elements: list[str] | None = None,
    hole_ids: list[str] | None = None,
    depth_min: float | None = None,
    depth_max: float | None = None,
    geobody_keys: list[str] | None = None,
) -> GeochemDataset:
    """读取数据库，返回供两个算法共享的可追溯融合数据。"""

    db_path = Path(db_path)
    if model_id is None:
        raise ValueError("系统集成模式必须提供 model_id")
    tables = _read_tables(db_path, int(model_id))
    if tables["chemical_assays"].empty:
        raise ValueError("该模型没有化学化验区间，请先导入化学钻孔数据。")
    assays, elements_detected = _expand_assays(tables["chemical_assays"])
    assays = _attach_coordinates(assays, tables["chemical_boreholes"])
    assays, section_audit = _attach_sections(
        assays, tables["borehole_sections"], tables["geobodies"]
    )
    assays = _add_quality_flags(assays)
    if hole_ids:
        wanted_holes = {str(value).strip() for value in hole_ids if str(value).strip()}
        assays = assays.loc[assays["hole_id"].astype(str).isin(wanted_holes)].copy()
    if depth_min is not None:
        assays = assays.loc[assays["to_depth"] >= float(depth_min)].copy()
    if depth_max is not None:
        assays = assays.loc[assays["from_depth"] <= float(depth_max)].copy()
    if geobody_keys:
        wanted_keys = {_normalise_key(value) for value in geobody_keys if _normalise_key(value)}
        assays = assays.loc[assays["section_geobody_key"].isin(wanted_keys)].copy()
    if assays.empty:
        raise ValueError("当前筛选条件下没有可分析的化验区间。")
    invalid_intervals = _build_invalid_interval_audit(assays)
    if selected_elements:
        requested = [str(value).strip() for value in selected_elements if str(value).strip()]
        unknown = sorted(set(requested) - set(elements_detected))
        if unknown:
            raise ValueError(f"数据库中不存在所选元素：{', '.join(unknown)}")
        elements = requested
        unselected = [name for name in elements_detected if name not in set(requested)]
        assays = assays.drop(columns=unselected, errors="ignore")
    else:
        elements = elements_detected
    table_counts = {name: len(frame) for name, frame in tables.items()}
    return GeochemDataset(
        db_path=db_path,
        model_id=int(model_id),
        assays=assays,
        elements=elements,
        invalid_intervals=invalid_intervals,
        section_match_audit=section_audit,
        table_counts=table_counts,
    )


def valid_intervals(assays: pd.DataFrame) -> pd.DataFrame:
    """返回深度合法的区间；不因坐标或地质分段不全而删除。"""

    return assays.loc[
        assays["from_depth"].notna()
        & assays["to_depth"].notna()
        & (assays["from_depth"] < assays["to_depth"])
    ].copy()


def markdown_table(frame: pd.DataFrame, columns: list[str], max_rows: int = 10) -> str:
    """无需额外 tabulate 依赖的简洁 Markdown 表格。"""

    if frame.empty:
        return "无数据。"
    sample = frame.loc[:, [column for column in columns if column in frame.columns]].head(max_rows)
    headers = list(sample.columns)
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for _, row in sample.iterrows():
        values = [str(row[column]).replace("|", "/").replace("\n", " ") for column in headers]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)
