# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi import Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..deps import get_current_admin_user, get_current_user
from ..security import create_access_token, hash_password, verify_password
from ..schemas import Token, RegisterIn, UserOut, PasswordChangeIn, AdminResetPasswordIn

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not user.is_active or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    token = create_access_token(subject=user.username)
    return Token(access_token=token)


@router.post("/auth/register", response_model=UserOut, status_code=201)
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")

    exists = db.query(User).filter(User.username == username).first()
    if exists:
        raise HTTPException(status_code=400, detail="Username already exists")

    user = User(
        username=username,
        hashed_password=hash_password(payload.password),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.get("/auth/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user)):
    return UserOut.model_validate(current_user)


@router.get("/users", response_model=list[UserOut])
def list_users(admin_user: User = Depends(get_current_admin_user), db: Session = Depends(get_db)):
    _ = admin_user
    users = db.query(User).order_by(User.created_at.desc(), User.id.desc()).all()
    return [UserOut.model_validate(user) for user in users]


@router.post("/auth/change-password", status_code=204)
def change_password(
    payload: PasswordChangeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="New password must be different")

    current_user.hashed_password = hash_password(payload.new_password)
    db.add(current_user)
    db.commit()


@router.post("/users/{user_id}/reset-password", status_code=204)
def reset_user_password(
    user_id: int,
    payload: AdminResetPasswordIn,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_current_admin_user),
):
    _ = admin_user
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.hashed_password = hash_password(payload.new_password)
    db.add(user)
    db.commit()
