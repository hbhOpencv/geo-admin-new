# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import re
from typing import List, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_admin_user, get_current_user
from ..models import GeologicalModel, Borehole, BoreholeSection, Geobody
from ..schemas import GeologicalModelOut, GeologicalModelCreate, GeologicalModelUpdate, Page
from ..utils import paginate, parse_float_any

router = APIRouter(tags=["models"])


@router.get("/models", response_model=Page)
def list_models(
    keyword: str | None = Query(default=None),
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _ = Depends(get_current_user),
):
    q = db.query(GeologicalModel)
    if keyword:
        q = q.filter(GeologicalModel.name.like(f"%{keyword}%"))
    q = q.order_by(GeologicalModel.id.desc())
    items, total, page, page_size = paginate(q, page, page_size)
    return {"items": [GeologicalModelOut.model_validate(x) for x in items], "total": total, "page": page, "page_size": page_size}


@router.post("/models", response_model=GeologicalModelOut)
def create_model(
    payload: GeologicalModelCreate,
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    obj = GeologicalModel(name=payload.name, description=payload.description)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return GeologicalModelOut.model_validate(obj)


# ---------------------------------
# Create model by uploading two CSVs
# ---------------------------------
_num_keep = re.compile(r"[^0-9eE\+\-\.]" )


def _norm_text(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "/":
        return None
    return s


def _read_csv_rows(upload: UploadFile) -> List[List[str]]:
    data = upload.file.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            text = data.decode(enc)
            break
        except Exception:
            text = None
    if text is None:
        text = data.decode("utf-8", errors="replace")
    reader = csv.reader(text.splitlines(), delimiter=",", quotechar='"')
    rows = [row for row in reader if row and any(str(x).strip() for x in row)]
    return rows


def _normalize_borehole_header(header: List[str]) -> List[str]:
    out = []
    empty_cnt = 0
    for h in header:
        h = (h or "").strip()
        if h == "":
            empty_cnt += 1
            out.append("体积" if empty_cnt == 1 else "表面积")
        else:
            out.append(h)
    return out


def _row_to_dict(header: List[str], row: List[str]) -> Dict[str, str]:
    if len(row) < len(header):
        row = row + [""] * (len(header) - len(row))
    if len(row) > len(header):
        row = row[:len(header)]
    return {header[i]: row[i] for i in range(len(header))}


def _extract_geobody_key(item_tag: Optional[str]) -> Optional[str]:
    if not item_tag:
        return None
    m = re.match(r"^\s*<([^>,]+)", str(item_tag).strip())
    return m.group(1).strip() if m else None


def _is_collar_row(d: Dict[str, str]) -> bool:
    return (not _norm_text(d.get("Borehole"))) and (_norm_text(d.get("项")) or "").startswith("DZ-")


def _is_section_row(d: Dict[str, str]) -> bool:
    return _norm_text(d.get("Borehole")) is not None


@router.post("/models/import", response_model=GeologicalModelOut)
def create_model_with_csv(
    name: str = Form(...),
    description: str | None = Form(default=None),
    borehole_csv: UploadFile = File(...),
    geobody_csv: UploadFile = File(...),
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    """Create a model and import boreholes + geobodies from two CSV files."""

    # name unique
    exists = db.query(GeologicalModel).filter(GeologicalModel.name == name).first()
    if exists:
        raise HTTPException(400, "Model name already exists")

    model = GeologicalModel(name=name, description=description)
    db.add(model)
    db.commit()
    db.refresh(model)
    model_id = model.id

    # --- Read CSVs ---
    bh_rows = _read_csv_rows(borehole_csv)
    gb_rows = _read_csv_rows(geobody_csv)
    if not bh_rows or not gb_rows:
        raise HTTPException(400, "CSV 文件为空或无法读取")

    bh_header = _normalize_borehole_header([str(x or "").strip() for x in bh_rows[0]])
    gb_header = [str(x or "").strip() for x in gb_rows[0]]

    # --- Import geobodies ---
    for row in gb_rows[1:]:
        d = _row_to_dict(gb_header, row)
        layer = _norm_text(d.get("层"))
        if not layer:
            continue
        obj = Geobody(model_id=model_id)
        obj.层 = d.get("层")
        obj.key = layer.replace("地质-", "", 1) if layer.startswith("地质-") else layer
        for col in [
            "体积","表面积","元素ID","范围下限","范围上限","潮湿程度","单轴饱和抗压强度","地层代号","风化程度",
            "工程等级","基本承载力","基地摩擦系数","临时挖方边坡率","密实状态","内摩擦角","凝聚力","时代成因",
            "塑性状态","天然密度","填料类别","岩土名称","永久挖方边坡率","钻孔灌注桩桩周极限摩阻力"
        ]:
            if hasattr(obj, col):
                setattr(obj, col, d.get(col))
        db.add(obj)
    db.commit()

    # --- Import boreholes + sections ---
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
        d = _row_to_dict(bh_header, row)

        if _is_collar_row(d):
            code = _norm_text(d.get("项"))
            if not code:
                continue
            ensure(code)
            put_first(code, "原点", _norm_text(d.get("原点")))
            n = parse_float_any(d.get("Northing")) if d.get("Northing") else parse_float_any(d.get("Northong"))
            put_first(code, "Northing", n)
            put_first(code, "Easting", parse_float_any(d.get("Easting")))
            put_first(code, "Elevation", parse_float_any(d.get("Elevation")))
            put_first(code, "Holedepth", parse_float_any(d.get("Holedepth")))
            put_first(code, "范围下限", _norm_text(d.get("范围下限")))
            put_first(code, "范围上限", _norm_text(d.get("范围上限")))

        elif _is_section_row(d):
            code = _norm_text(d.get("Borehole"))
            if not code:
                continue
            ensure(code)
            item_tag = _norm_text(d.get("项"))
            sections.append({
                "code": code,
                "项": item_tag,
                "高度": parse_float_any(d.get("高度")),
                "底坐标": _norm_text(d.get("底坐标")),
                "底半径": parse_float_any(d.get("底半径")),
                "顶坐标": _norm_text(d.get("顶坐标")),
                "Borehole": code,
                "Depth": parse_float_any(d.get("Depth")),
                "Bottom": parse_float_any(d.get("Bottom")),
                "Description": _norm_text(d.get("Description")),
                "元素ID": int(parse_float_any(d.get("元素ID"))) if parse_float_any(d.get("元素ID")) is not None else None,
                "范围下限": _norm_text(d.get("范围下限")),
                "范围上限": _norm_text(d.get("范围上限")),
                "geobody_key": _extract_geobody_key(item_tag),
            })
            put_first(code, "范围下限", _norm_text(d.get("范围下限")))
            put_first(code, "范围上限", _norm_text(d.get("范围上限")))

    borehole_id: Dict[str, int] = {}
    for code in sorted(agg.keys()):
        bh = Borehole(model_id=model_id, Borehole=code)
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
    db.commit()

    for sec in sections:
        bhid = borehole_id.get(sec["code"])
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
    db.commit()

    return GeologicalModelOut.model_validate(model)


@router.get("/models/{model_id}", response_model=GeologicalModelOut)
def get_model(
    model_id: int,
    db: Session = Depends(get_db),
    _ = Depends(get_current_user),
):
    obj = db.query(GeologicalModel).filter(GeologicalModel.id == model_id).first()
    if not obj:
        raise HTTPException(404, "Model not found")
    return GeologicalModelOut.model_validate(obj)


@router.put("/models/{model_id}", response_model=GeologicalModelOut)
def update_model(
    model_id: int,
    payload: GeologicalModelUpdate,
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    obj = db.query(GeologicalModel).filter(GeologicalModel.id == model_id).first()
    if not obj:
        raise HTTPException(404, "Model not found")

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return GeologicalModelOut.model_validate(obj)


@router.delete("/models/{model_id}")
def delete_model(
    model_id: int,
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    obj = db.query(GeologicalModel).filter(GeologicalModel.id == model_id).first()
    if not obj:
        raise HTTPException(404, "Model not found")
    db.delete(obj)
    db.commit()
    return {"ok": True}


@router.post("/models/{model_id}/geobodies/import")
def import_geobodies_for_model(
    model_id: int,
    geobody_csv: UploadFile = File(...),
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    model = db.query(GeologicalModel).filter(GeologicalModel.id == model_id).first()
    if not model:
        raise HTTPException(404, "Model not found")

    rows = _read_csv_rows(geobody_csv)
    if not rows:
        raise HTTPException(400, "CSV 文件为空或无法读取")
    header = [str(x or "").strip() for x in rows[0]]

    db.query(Geobody).filter(Geobody.model_id == model_id).delete(synchronize_session=False)
    imported = 0
    for row in rows[1:]:
        d = _row_to_dict(header, row)
        layer = _norm_text(d.get("层") or d.get("灞?"))
        if not layer and not any(_norm_text(v) for v in d.values()):
            continue
        obj = Geobody(model_id=model_id)
        if layer:
            obj.key = layer.replace("地质-", "", 1) if layer.startswith("地质-") else layer
        for col, value in d.items():
            if hasattr(obj, col):
                setattr(obj, col, value)
        db.add(obj)
        imported += 1
    db.commit()
    return {"ok": True, "imported": imported}


@router.post("/models/{model_id}/boreholes/import")
def import_boreholes_for_model(
    model_id: int,
    borehole_csv: UploadFile = File(...),
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    model = db.query(GeologicalModel).filter(GeologicalModel.id == model_id).first()
    if not model:
        raise HTTPException(404, "Model not found")

    rows = _read_csv_rows(borehole_csv)
    if not rows:
        raise HTTPException(400, "CSV 文件为空或无法读取")
    header = _normalize_borehole_header([str(x or "").strip() for x in rows[0]])

    existing_ids = [x.id for x in db.query(Borehole.id).filter(Borehole.model_id == model_id).all()]
    if existing_ids:
        db.query(BoreholeSection).filter(BoreholeSection.borehole_id.in_(existing_ids)).delete(synchronize_session=False)
        db.query(Borehole).filter(Borehole.id.in_(existing_ids)).delete(synchronize_session=False)
        db.commit()

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

    def put_first(code: str, key: str, value):
        ensure(code)
        if agg[code][key] is None and value is not None:
            agg[code][key] = value

    for row in rows[1:]:
        d = _row_to_dict(header, row)
        collar_code = _norm_text(d.get("项") or d.get("椤?"))
        if (not _norm_text(d.get("Borehole"))) and collar_code and collar_code.startswith("DZ-"):
            code = collar_code
            put_first(code, "原点", _norm_text(d.get("原点") or d.get("鍘熺偣")))
            northing = parse_float_any(d.get("Northing")) if d.get("Northing") else parse_float_any(d.get("Northong"))
            put_first(code, "Northing", northing)
            put_first(code, "Easting", parse_float_any(d.get("Easting")))
            put_first(code, "Elevation", parse_float_any(d.get("Elevation")))
            put_first(code, "Holedepth", parse_float_any(d.get("Holedepth")))
            put_first(code, "范围下限", _norm_text(d.get("范围下限") or d.get("鑼冨洿涓嬮檺")))
            put_first(code, "范围上限", _norm_text(d.get("范围上限") or d.get("鑼冨洿涓婇檺")))
            continue

        code = _norm_text(d.get("Borehole"))
        if not code:
            continue
        ensure(code)
        item_tag = _norm_text(d.get("项") or d.get("椤?"))
        sections.append({
            "code": code,
            "item": item_tag,
            "height": parse_float_any(d.get("高度") or d.get("楂樺害")),
            "bottom_coord": _norm_text(d.get("底坐标") or d.get("搴曞潗鏍?")),
            "bottom_radius": parse_float_any(d.get("底半径") or d.get("搴曞崐寰?")),
            "top_coord": _norm_text(d.get("顶坐标") or d.get("椤跺潗鏍?")),
            "Depth": parse_float_any(d.get("Depth")),
            "Bottom": parse_float_any(d.get("Bottom")),
            "Description": _norm_text(d.get("Description")),
            "element_id": int(parse_float_any(d.get("元素ID") or d.get("鍏冪礌ID"))) if parse_float_any(d.get("元素ID") or d.get("鍏冪礌ID")) is not None else None,
            "lower": _norm_text(d.get("范围下限") or d.get("鑼冨洿涓嬮檺")),
            "upper": _norm_text(d.get("范围上限") or d.get("鑼冨洿涓婇檺")),
            "geobody_key": _extract_geobody_key(item_tag),
        })

    borehole_id: Dict[str, int] = {}
    for code in sorted(agg.keys()):
        bh = Borehole(model_id=model_id, Borehole=code)
        if hasattr(bh, "原点"):
            setattr(bh, "原点", agg[code]["原点"])
        if hasattr(bh, "鍘熺偣"):
            setattr(bh, "鍘熺偣", agg[code]["原点"])
        bh.Northing = agg[code]["Northing"]
        bh.Easting = agg[code]["Easting"]
        bh.Elevation = agg[code]["Elevation"]
        bh.Holedepth = agg[code]["Holedepth"]
        for attr, key in [("范围下限", "范围下限"), ("范围上限", "范围上限"), ("鑼冨洿涓嬮檺", "范围下限"), ("鑼冨洿涓婇檺", "范围上限")]:
            if hasattr(bh, attr):
                setattr(bh, attr, agg[code][key])
        db.add(bh)
        db.flush()
        borehole_id[code] = bh.id
    db.commit()

    for sec in sections:
        bhid = borehole_id.get(sec["code"])
        if not bhid:
            continue
        obj = BoreholeSection(borehole_id=bhid, Borehole=sec["code"], Depth=sec["Depth"], Bottom=sec["Bottom"], Description=sec["Description"], geobody_key=sec["geobody_key"])
        for attr, value in [
            ("项", sec["item"]), ("椤?", sec["item"]),
            ("高度", sec["height"]), ("楂樺害", sec["height"]),
            ("底坐标", sec["bottom_coord"]), ("搴曞潗鏍?", sec["bottom_coord"]),
            ("底半径", sec["bottom_radius"]), ("搴曞崐寰?", sec["bottom_radius"]),
            ("顶坐标", sec["top_coord"]), ("椤跺潗鏍?", sec["top_coord"]),
            ("元素ID", sec["element_id"]), ("鍏冪礌ID", sec["element_id"]),
            ("范围下限", sec["lower"]), ("鑼冨洿涓嬮檺", sec["lower"]),
            ("范围上限", sec["upper"]), ("鑼冨洿涓婇檺", sec["upper"]),
        ]:
            if hasattr(obj, attr):
                setattr(obj, attr, value)
        db.add(obj)
    db.commit()

    return {"ok": True, "boreholes": len(borehole_id), "sections": len(sections)}
