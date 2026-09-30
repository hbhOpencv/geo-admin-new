# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_admin_user, get_current_user
from ..models import ChemicalAssay, ChemicalBorehole, GeologicalModel

router = APIRouter(tags=["chemistry"])


DEPTH_FIELDS = {"from_depth", "to_depth", "mid_depth", "sample_length"}
META_FIELDS = {
    "hole_id",
    "unit",
    "collar_x",
    "collar_y",
    "collar_z",
    "x",
    "y",
    "z",
    *DEPTH_FIELDS,
}


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_element_column(name: str) -> bool:
    if name in META_FIELDS:
        return False
    return bool(name and name[0].isalpha())


def _element_columns(headers: list[str]) -> list[str]:
    return [name for name in headers if _is_element_column(name)]


def _paginate_items(items: list[dict[str, Any]], page: int, page_size: int) -> tuple[list[dict[str, Any]], int, int, int]:
    total = len(items)
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end], total, page, page_size


def _read_upload_csv(file: UploadFile) -> tuple[list[dict[str, str]], list[str]]:
    data = file.file.read()
    if not data:
        raise HTTPException(400, "CSV文件为空")
    text = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise HTTPException(400, "CSV编码无法识别，请使用UTF-8或GBK编码")

    reader = csv.DictReader(text.splitlines())
    headers = list(reader.fieldnames or [])
    rows = list(reader)
    required = {"hole_id", "from_depth", "to_depth", "collar_x", "collar_y", "collar_z"}
    missing = sorted(required - set(headers))
    if missing:
        raise HTTPException(400, f"CSV缺少必要字段: {', '.join(missing)}")
    return rows, headers


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return {}


@router.get("/models/{model_id}/chemical-boreholes")
def list_chemical_boreholes(
    model_id: int,
    keyword: str | None = Query(default=None),
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    q = db.query(ChemicalBorehole).filter(ChemicalBorehole.model_id == model_id)
    if keyword:
        q = q.filter(ChemicalBorehole.hole_id.like(f"%{keyword}%"))
    records = q.order_by(ChemicalBorehole.id.asc()).all()
    items = [
        {
            "id": item.id,
            "hole_id": item.hole_id,
            "collar_x": item.collar_x,
            "collar_y": item.collar_y,
            "collar_z": item.collar_z,
            "depth_min": item.depth_min,
            "depth_max": item.depth_max,
            "sample_count": item.sample_count,
            "element_count": item.element_count,
            "unit": item.unit,
            "problem_count": 0,
        }
        for item in records
    ]
    page_items, total, page, page_size = _paginate_items(items, page, page_size)
    return {"items": page_items, "total": total, "page": page, "page_size": page_size}


@router.post("/models/{model_id}/chemical-boreholes/import")
def import_chemical_boreholes(
    model_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _=Depends(get_current_admin_user),
):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "请上传CSV文件")
    if not db.query(GeologicalModel).filter(GeologicalModel.id == model_id).first():
        raise HTTPException(404, "Model not found")

    rows, headers = _read_upload_csv(file)
    element_columns = _element_columns(headers)

    db.query(ChemicalAssay).filter(ChemicalAssay.model_id == model_id).delete(synchronize_session=False)
    db.query(ChemicalBorehole).filter(ChemicalBorehole.model_id == model_id).delete(synchronize_session=False)

    by_hole: dict[str, dict[str, Any]] = {}
    for row in rows:
        hole_id = (row.get("hole_id") or "").strip()
        if not hole_id:
            continue
        item = by_hole.setdefault(
            hole_id,
            {
                "hole_id": hole_id,
                "collar_x": _to_float(row.get("collar_x") or row.get("x")),
                "collar_y": _to_float(row.get("collar_y") or row.get("y")),
                "collar_z": _to_float(row.get("collar_z") or row.get("z")),
                "depth_min": None,
                "depth_max": None,
                "sample_count": 0,
                "element_count": len(element_columns),
                "unit": row.get("unit") or "",
            },
        )
        from_depth = _to_float(row.get("from_depth"))
        to_depth = _to_float(row.get("to_depth"))
        if from_depth is not None:
            item["depth_min"] = from_depth if item["depth_min"] is None else min(item["depth_min"], from_depth)
        if to_depth is not None:
            item["depth_max"] = to_depth if item["depth_max"] is None else max(item["depth_max"], to_depth)
        item["sample_count"] += 1

    borehole_ids: dict[str, int] = {}
    for hole_id in sorted(by_hole):
        item = by_hole[hole_id]
        obj = ChemicalBorehole(
            model_id=model_id,
            hole_id=item["hole_id"],
            collar_x=item["collar_x"],
            collar_y=item["collar_y"],
            collar_z=item["collar_z"],
            depth_min=item["depth_min"],
            depth_max=item["depth_max"],
            sample_count=item["sample_count"],
            element_count=item["element_count"],
            unit=item["unit"],
            data_source=file.filename,
        )
        db.add(obj)
        db.flush()
        borehole_ids[hole_id] = obj.id

    assay_count = 0
    for row in rows:
        hole_id = (row.get("hole_id") or "").strip()
        chemical_borehole_id = borehole_ids.get(hole_id)
        if not chemical_borehole_id:
            continue
        from_depth = _to_float(row.get("from_depth"))
        to_depth = _to_float(row.get("to_depth"))
        if from_depth is None or to_depth is None:
            continue
        elements = {
            name: value
            for name in element_columns
            if (value := _to_float(row.get(name))) is not None
        }
        db.add(
            ChemicalAssay(
                model_id=model_id,
                chemical_borehole_id=chemical_borehole_id,
                hole_id=hole_id,
                from_depth=from_depth,
                to_depth=to_depth,
                unit=row.get("unit") or "",
                elements_json=json.dumps(elements, ensure_ascii=False, separators=(",", ":")),
            )
        )
        assay_count += 1

    db.commit()
    return {
        "ok": True,
        "filename": file.filename,
        "row_count": assay_count,
        "hole_count": len(borehole_ids),
        "element_count": len(element_columns),
    }


@router.get("/models/{model_id}/chemical-boreholes/{hole_id}/assays")
def list_chemical_assays(
    model_id: int,
    hole_id: str,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    records = (
        db.query(ChemicalAssay)
        .filter(ChemicalAssay.model_id == model_id, ChemicalAssay.hole_id == hole_id)
        .order_by(ChemicalAssay.from_depth.asc(), ChemicalAssay.id.asc())
        .all()
    )

    element_names: list[str] = []
    seen: set[str] = set()
    all_items: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        elements = _json_loads(record.elements_json)
        for name in elements:
            if name not in seen:
                seen.add(name)
                element_names.append(name)
        item = {
            "id": index,
            "hole_id": record.hole_id,
            "from_depth": record.from_depth,
            "to_depth": record.to_depth,
            "unit": record.unit,
            **elements,
        }
        all_items.append(item)

    page_items, total, page, page_size = _paginate_items(all_items, page, page_size)
    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "elements": element_names,
    }
