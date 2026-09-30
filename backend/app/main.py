# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .models import Base, get_engine
from .models import User, get_session
from .db_migrations import recover_interrupted_jobs, run_schema_migrations
from .security import hash_password
from .routers import (
    auth,
    models,
    boreholes,
    geobodies,
    reconstruct,
    chemistry,
    deep_reconstruct,
    geochem_reconstruct,
    geochem_mining,
    geochem_rules,
    geochem_workflows,
)

app = FastAPI(title="地质模型管理系统（管理员端）API", version="1.0.0")

origins = [x.strip() for x in settings.CORS_ORIGINS.split(",") if x.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create tables if not exist
engine = get_engine()
Base.metadata.create_all(engine)
run_schema_migrations(engine)
recover_interrupted_jobs(engine)

# Ensure default admin user exists
db = get_session()
try:
    admin = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
    if not admin:
        admin = User(
            username=settings.ADMIN_USERNAME,
            hashed_password=hash_password(settings.ADMIN_PASSWORD),
            is_active=True,
            is_admin=True,
        )
        db.add(admin)
        db.commit()
finally:
    db.close()

app.include_router(auth.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(boreholes.router, prefix="/api")
app.include_router(geobodies.router, prefix="/api")
app.include_router(reconstruct.router, prefix="/api")
app.include_router(chemistry.router, prefix="/api")
app.include_router(deep_reconstruct.router, prefix="/api")
app.include_router(geochem_reconstruct.router, prefix="/api")
app.include_router(geochem_mining.router, prefix="/api")
app.include_router(geochem_rules.router, prefix="/api")
app.include_router(geochem_workflows.router, prefix="/api")


@app.get("/health")
def health():
    return {"ok": True, "delivery": "geo-admin-2026-08-20"}


# The delivery build serves the compiled Vue application from the same
# process, so reviewers do not need Node.js just to run the system.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"
if FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS), name="frontend-assets")


@app.get("/{full_path:path}", include_in_schema=False)
def frontend(full_path: str):
    if not FRONTEND_DIST.is_dir():
        return {"detail": "frontend build not found"}
    requested = (FRONTEND_DIST / full_path).resolve()
    if requested.is_relative_to(FRONTEND_DIST.resolve()) and requested.is_file():
        return FileResponse(requested)
    return FileResponse(FRONTEND_DIST / "index.html")
