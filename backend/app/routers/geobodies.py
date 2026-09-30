# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_admin_user, get_current_user
from ..models import Geobody
from ..schemas import GeobodyOut, GeobodyCreate, GeobodyUpdate, Page
from ..utils import paginate

router = APIRouter(tags=["geobodies"])


@router.get("/models/{model_id}/geobodies", response_model=Page)
def list_geobodies(
    model_id: int,
    keyword: str | None = Query(default=None),
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _ = Depends(get_current_user),
):
    q = db.query(Geobody).filter(Geobody.model_id == model_id)
    if keyword:
        q = q.filter(
            (Geobody.地层代号.like(f"%{keyword}%")) |
            (Geobody.岩土名称.like(f"%{keyword}%")) |
            (Geobody.元素ID.like(f"%{keyword}%"))
        )
    # ✅ 改这里：ID 升序
    q = q.order_by(Geobody.id.asc())

    items, total, page, page_size = paginate(q, page, page_size)
    return {"items": [GeobodyOut.model_validate(x) for x in items], "total": total, "page": page, "page_size": page_size}



@router.post("/geobodies", response_model=GeobodyOut)
def create_geobody(
    payload: GeobodyCreate,
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    obj = Geobody(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return GeobodyOut.model_validate(obj)


@router.get("/geobodies/{geobody_id}", response_model=GeobodyOut)
def get_geobody(
    geobody_id: int,
    db: Session = Depends(get_db),
    _ = Depends(get_current_user),
):
    obj = db.query(Geobody).filter(Geobody.id == geobody_id).first()
    if not obj:
        raise HTTPException(404, "Geobody not found")
    return GeobodyOut.model_validate(obj)


@router.put("/geobodies/{geobody_id}", response_model=GeobodyOut)
def update_geobody(
    geobody_id: int,
    payload: GeobodyUpdate,
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    obj = db.query(Geobody).filter(Geobody.id == geobody_id).first()
    if not obj:
        raise HTTPException(404, "Geobody not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return GeobodyOut.model_validate(obj)


@router.delete("/geobodies/{geobody_id}")
def delete_geobody(
    geobody_id: int,
    db: Session = Depends(get_db),
    _ = Depends(get_current_admin_user),
):
    obj = db.query(Geobody).filter(Geobody.id == geobody_id).first()
    if not obj:
        raise HTTPException(404, "Geobody not found")
    db.delete(obj)
    db.commit()
    return {"ok": True}
