# -*- coding: utf-8 -*-
"""Import borehole + geobody CSVs into a model.

This is adapted from backend/scripts/import_two_csv.py but works with
FastAPI UploadFile (bytes) and an injected SQLAlchemy Session.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import GeologicalModel, Borehole, BoreholeSection, Geobody


def read_csv_rows_bytes(data: bytes) -> List[List[str]]:
    """Read CSV rows from bytes with robust encoding fallbacks."""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            text = data.decode(enc)
            f = io.StringIO(text)
            reader = csv.reader(f, delimiter=",", quotechar='"')
            rows = [row for row in reader if row and any(str(x).strip() for x in row)]
            if rows:
                return rows
        except Exception:
            continue

    # Fallback
    text = data.decode("utf-8", errors="replace")
    f = io.StringIO(text)
    reader = csv.reader(f, delimiter=",", quotechar='"')
    return [row for row in reader if row and any(str(x).strip() for x in row)]


def normalize_borehole_header(header: List[str]) -> List[str]:
    # 钻孔CSV里两个空列 -> 临时命名（只为读取，分段表不会用到）
    out: List[str] = []
    empty_cnt = 0
    for h in header:
        h = (h or "").strip()
        if h == "":
            empty_cnt += 1
            out.append("体积" if empty_cnt == 1 else "表面积")
        else:
            out.append(h)
    return out


def row_to_dict(header: List[str], row: List[str]) -> Dict[str, str]:
    if len(row) < len(header):
        row = row + [""] * (len(header) - len(row))
    if len(row) > len(header):
        row = row[: len(header)]
    return {header[i]: row[i] for i in range(len(header))}


_num_keep = re.compile(r"[^0-9eE\+\-\.]")


def parse_float_any(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "/":
        return None
    s = s.replace("立方m", "").replace("平方m", "")
    s = s.replace("立方 m", "").replace("平方 m", "")
    s = s.replace("m", "").replace("米", "")
    s = s.replace(" ", "")
    s2 = _num_keep.sub("", s)
    if not s2:
        return None
    try:
        return float(s2)
    except Exception:
        return None


def norm_text(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "/":
        return None
    return s


def extract_geobody_key(item_tag: Optional[str]) -> Optional[str]:
    if not item_tag:
        return None
    m = re.match(r"^\s*<([^>,]+)", str(item_tag).strip())
    return m.group(1).strip() if m else None


def is_collar_row(d: Dict[str, str]) -> bool:
    # 汇总行：Borehole 列空 + 项 以 DZ- 开头
    return (not norm_text(d.get("Borehole"))) and (norm_text(d.get("项")) or "").startswith("DZ-")


def is_section_row(d: Dict[str, str]) -> bool:
    # 分段行：Borehole 列有值
    return norm_text(d.get("Borehole")) is not None


def import_two_csv_into_model(
    db: Session,
    *,
    model_name: str,
    description: Optional[str],
    borehole_csv_bytes: bytes,
    geobody_csv_bytes: bytes,
    reset_if_exists: bool = False,
) -> Tuple[GeologicalModel, Dict[str, int]]:
    """Create/replace a model and import two CSVs.

    Returns (model, stats).
    """

    if not model_name.strip():
        raise ValueError("model_name is empty")

    # Do everything in a single transaction. If anything fails, rollback will
    # keep DB consistent (including optional deletion when reset_if_exists=True).
    stats: Dict[str, int] = {"geobodies": 0, "boreholes": 0, "sections": 0}

    with db.begin():
        # Model create/replace
        existing = db.query(GeologicalModel).filter(GeologicalModel.name == model_name).first()
        if existing:
            if not reset_if_exists:
                raise RuntimeError("MODEL_EXISTS")
            db.delete(existing)
            db.flush()

        model = GeologicalModel(name=model_name, description=description)
        db.add(model)
        db.flush()  # get model.id

        # Read CSVs
        bh_rows = read_csv_rows_bytes(borehole_csv_bytes)
        gb_rows = read_csv_rows_bytes(geobody_csv_bytes)
        if not bh_rows or len(bh_rows) < 2:
            raise ValueError("borehole_csv is empty")
        if not gb_rows or len(gb_rows) < 2:
            raise ValueError("geobody_csv is empty")

        bh_header = normalize_borehole_header([str(x or "").strip() for x in bh_rows[0]])
        gb_header = [str(x or "").strip() for x in gb_rows[0]]

        # Import geobodies
        for row in gb_rows[1:]:
            d = row_to_dict(gb_header, row)
            layer = norm_text(d.get("层"))
            if not layer:
                continue
            obj = Geobody(model_id=model.id)
            obj.层 = d.get("层")
            obj.key = layer.replace("地质-", "", 1) if layer.startswith("地质-") else layer
            for col in [
                "体积",
                "表面积",
                "元素ID",
                "范围下限",
                "范围上限",
                "潮湿程度",
                "单轴饱和抗压强度",
                "地层代号",
                "风化程度",
                "工程等级",
                "基本承载力",
                "基地摩擦系数",
                "临时挖方边坡率",
                "密实状态",
                "内摩擦角",
                "凝聚力",
                "时代成因",
                "塑性状态",
                "天然密度",
                "填料类别",
                "岩土名称",
                "永久挖方边坡率",
                "钻孔灌注桩桩周极限摩阻力",
            ]:
                if hasattr(obj, col):
                    setattr(obj, col, d.get(col))
            db.add(obj)
            stats["geobodies"] += 1

        # Process borehole CSV
        agg: Dict[str, Dict[str, object]] = {}
        sections: List[Dict[str, object]] = []

    def ensure(code: str):
        if code not in agg:
            agg[code] = {
                "原点": None,
                "Northing": None,
                "Easting": None,
                "Elevation": None,
                "Holedepth": None,
                "范围下限": None,
                "范围上限": None,
            }

    def put_first(code: str, k: str, v):
        ensure(code)
        if agg[code][k] is None and v is not None:
            agg[code][k] = v

    for row in bh_rows[1:]:
        d = row_to_dict(bh_header, row)

        if is_collar_row(d):
            code = norm_text(d.get("项"))
            if not code:
                continue
            ensure(code)

            put_first(code, "原点", norm_text(d.get("原点")))

            n = parse_float_any(d.get("Northing")) if d.get("Northing") else parse_float_any(d.get("Northong"))
            put_first(code, "Northing", n)
            put_first(code, "Easting", parse_float_any(d.get("Easting")))
            put_first(code, "Elevation", parse_float_any(d.get("Elevation")))
            put_first(code, "Holedepth", parse_float_any(d.get("Holedepth")))
            put_first(code, "范围下限", norm_text(d.get("范围下限")))
            put_first(code, "范围上限", norm_text(d.get("范围上限")))

        elif is_section_row(d):
            code = norm_text(d.get("Borehole"))
            if not code:
                continue
            ensure(code)

            item_tag = norm_text(d.get("项"))
            sections.append(
                {
                    "code": code,
                    "项": item_tag,
                    "高度": parse_float_any(d.get("高度")),
                    "底坐标": norm_text(d.get("底坐标")),
                    "底半径": parse_float_any(d.get("底半径")),
                    "顶坐标": norm_text(d.get("顶坐标")),
                    "Borehole": code,
                    "Depth": parse_float_any(d.get("Depth")),
                    "Bottom": parse_float_any(d.get("Bottom")),
                    "Description": norm_text(d.get("Description")),
                    "元素ID": int(parse_float_any(d.get("元素ID"))) if parse_float_any(d.get("元素ID")) is not None else None,
                    "范围下限": norm_text(d.get("范围下限")),
                    "范围上限": norm_text(d.get("范围上限")),
                    "geobody_key": extract_geobody_key(item_tag),
                }
            )

            put_first(code, "范围下限", norm_text(d.get("范围下限")))
            put_first(code, "范围上限", norm_text(d.get("范围上限")))

        # Write boreholes and sections
        borehole_id: Dict[str, int] = {}
        for code in sorted(agg.keys()):
            bh = Borehole(model_id=model.id, Borehole=code)
            bh.原点 = agg[code]["原点"]
            bh.Northing = agg[code]["Northing"]
            bh.Easting = agg[code]["Easting"]
            bh.Elevation = agg[code]["Elevation"]
            bh.Holedepth = agg[code]["Holedepth"]
            bh.范围下限 = agg[code]["范围下限"]
            bh.范围上限 = agg[code]["范围上限"]
            db.add(bh)
            db.flush()
            borehole_id[code] = bh.id
            stats["boreholes"] += 1

        for sec in sections:
            bhid = borehole_id.get(str(sec["code"]))
            if not bhid:
                continue
            obj = BoreholeSection(
                borehole_id=bhid,
                项=sec["项"],
                高度=sec["高度"],
                底坐标=sec["底坐标"],
                底半径=sec["底半径"],
                顶坐标=sec["顶坐标"],
                Borehole=sec["Borehole"],
                Depth=sec["Depth"],
                Bottom=sec["Bottom"],
                Description=sec["Description"],
                元素ID=sec["元素ID"],
                范围下限=sec["范围下限"],
                范围上限=sec["范围上限"],
                geobody_key=sec["geobody_key"],
            )
            db.add(obj)
            stats["sections"] += 1

        # Make sure model has PK loaded
        db.flush()

    # transaction committed
    db.refresh(model)
    return model, stats
