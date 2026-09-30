# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_admin_user, get_current_user
from ..models import Borehole, BoreholeSection
from ..schemas import BoreholeOut, BoreholeCreate, BoreholeUpdate, BoreholeSectionOut, BoreholeSectionCreate, BoreholeSectionUpdate, Page
from ..utils import paginate

router = APIRouter(tags=["boreholes"])


@router.get("/models/{model_id}/boreholes", response_model=Page)
def list_boreholes(
    model_id: int,
    keyword: str | None = Query(default=None),
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _ = Depends(get_current_user),
):
    q = db.query(Borehole).filter(Borehole.model_id == model_id)
    if keyword:
        q = q.filter(Borehole.Borehole.like(f"%{keyword}%"))
    q = q.order_by(Borehole.id.asc())
    items, total, page, page_size = paginate(q, page, page_size)
    return {"items": [BoreholeOut.model_validate(x) for x in items], "total": total, "page": page, "page_size": page_size}


@router.post("/boreholes", response_model=BoreholeOut)
def create_borehole(
    payload: BoreholeCreate,
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    obj = Borehole(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return BoreholeOut.model_validate(obj)


@router.get("/boreholes/{borehole_id}", response_model=BoreholeOut)
def get_borehole(
    borehole_id: int,
    db: Session = Depends(get_db),
    _ = Depends(get_current_user),
):
    obj = db.query(Borehole).filter(Borehole.id == borehole_id).first()
    if not obj:
        raise HTTPException(404, "Borehole not found")
    return BoreholeOut.model_validate(obj)


@router.put("/boreholes/{borehole_id}", response_model=BoreholeOut)
def update_borehole(
    borehole_id: int,
    payload: BoreholeUpdate,
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    obj = db.query(Borehole).filter(Borehole.id == borehole_id).first()
    if not obj:
        raise HTTPException(404, "Borehole not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return BoreholeOut.model_validate(obj)


@router.delete("/boreholes/{borehole_id}")
def delete_borehole(
    borehole_id: int,
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    obj = db.query(Borehole).filter(Borehole.id == borehole_id).first()
    if not obj:
        raise HTTPException(404, "Borehole not found")
    db.delete(obj)
    db.commit()
    return {"ok": True}


# -----------------------------
# Sections
# -----------------------------
@router.get("/boreholes/{borehole_id}/sections", response_model=Page)
def list_sections(
    borehole_id: int,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _ = Depends(get_current_user),
):
    q = (
        db.query(BoreholeSection)
        .filter(BoreholeSection.borehole_id == borehole_id)
        .order_by(BoreholeSection.Depth.asc().nulls_last(), BoreholeSection.id.asc())
    )
    items, total, page, page_size = paginate(q, page, page_size)
    return {
        "items": [BoreholeSectionOut.model_validate(x) for x in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }



@router.post("/sections", response_model=BoreholeSectionOut)
def create_section(
    payload: BoreholeSectionCreate,
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    obj = BoreholeSection(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return BoreholeSectionOut.model_validate(obj)


@router.put("/sections/{section_id}", response_model=BoreholeSectionOut)
def update_section(
    section_id: int,
    payload: BoreholeSectionUpdate,
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    obj = db.query(BoreholeSection).filter(BoreholeSection.id == section_id).first()
    if not obj:
        raise HTTPException(404, "Section not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return BoreholeSectionOut.model_validate(obj)


@router.delete("/sections/{section_id}")
def delete_section(
    section_id: int,
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    obj = db.query(BoreholeSection).filter(BoreholeSection.id == section_id).first()
    if not obj:
        raise HTTPException(404, "Section not found")
    db.delete(obj)
    db.commit()
    return {"ok": True}
