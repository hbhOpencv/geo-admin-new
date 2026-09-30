# models.py
# -*- coding: utf-8 -*-
import os
from datetime import datetime

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DEFAULT_DB_URL = "sqlite:///./geology_norm.db"
DB_URL = os.getenv("DB_URL", DEFAULT_DB_URL)

Base = declarative_base()

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
        _engine = create_engine(DB_URL, echo=False, future=True, connect_args=connect_args)
    return _engine


def get_session():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)
    return _SessionLocal()


class GeologicalModel(Base):
    __tablename__ = "geological_models"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False, index=True)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    boreholes = relationship("Borehole", back_populates="model", cascade="all, delete-orphan")
    geobodies = relationship("Geobody", back_populates="model", cascade="all, delete-orphan")
    chemical_boreholes = relationship("ChemicalBorehole", back_populates="model", cascade="all, delete-orphan")
    geochem_rule_sets = relationship("GeochemRuleSet", back_populates="model", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Borehole(Base):
    """
    ✅ boreholes 主表：只保留你要的字段
    （已删除 x / y / collar_z / total_depth）
    """
    __tablename__ = "boreholes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("geological_models.id"), nullable=False, index=True)

    Borehole = Column(String, nullable=False, index=True)  # DZ-德达-深-xx

    原点 = Column(Text)        # "x,y,z"（来自汇总行）
    Northing = Column(Float)
    Easting = Column(Float)
    Elevation = Column(Float)
    Holedepth = Column(Float)
    范围下限 = Column(Text)
    范围上限 = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    model = relationship("GeologicalModel", back_populates="boreholes")
    sections = relationship("BoreholeSection", back_populates="borehole", cascade="all, delete-orphan")

    # ----
    # Computed fields for UI
    # ----
    @property
    def x(self):
        """X coordinate for display (prefers Easting, falls back to parsing 原点)."""
        if self.Easting is not None:
            return self.Easting
        try:
            from .utils import parse_origin_xyz

            x, _, _ = parse_origin_xyz(self.原点)
            return x
        except Exception:
            return None

    @property
    def y(self):
        """Y coordinate for display (prefers Northing, falls back to parsing 原点)."""
        if self.Northing is not None:
            return self.Northing
        try:
            from .utils import parse_origin_xyz

            _, y, _ = parse_origin_xyz(self.原点)
            return y
        except Exception:
            return None

    @property
    def z(self):
        """Z coordinate for display: strictly uses Elevation."""
        return self.Elevation


class BoreholeSection(Base):
    """
    ✅ 分段表：按你的要求，不含
      原点/体积/表面积/Northing/Easting/Elevation/Holedepth/raw_json
    """
    __tablename__ = "borehole_sections"
    id = Column(Integer, primary_key=True, autoincrement=True)
    borehole_id = Column(Integer, ForeignKey("boreholes.id"), nullable=False, index=True)

    项 = Column(Text)            # <7-17>
    高度 = Column(Float)
    底坐标 = Column(Text)
    底半径 = Column(Float)
    顶坐标 = Column(Text)

    Borehole = Column(String)    # DZ-xxx（分段行）
    Depth = Column(Float)
    Bottom = Column(Float)
    Description = Column(Text)
    元素ID = Column(Integer)
    范围下限 = Column(Text)
    范围上限 = Column(Text)

    geobody_key = Column(String, index=True)  # 由 <7-17> 提取 7-17

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    borehole = relationship("Borehole", back_populates="sections")


class ChemicalBorehole(Base):
    """Chemical/XRF drillhole collar and summary information."""
    __tablename__ = "chemical_boreholes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("geological_models.id"), nullable=False, index=True)

    hole_id = Column(String, nullable=False, index=True)
    collar_x = Column(Float)
    collar_y = Column(Float)
    collar_z = Column(Float)
    depth_min = Column(Float)
    depth_max = Column(Float)
    sample_count = Column(Integer, default=0, nullable=False)
    element_count = Column(Integer, default=0, nullable=False)
    unit = Column(String)
    data_source = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    model = relationship("GeologicalModel", back_populates="chemical_boreholes")
    assays = relationship("ChemicalAssay", back_populates="chemical_borehole", cascade="all, delete-orphan")


class ChemicalAssay(Base):
    """Chemical/XRF interval assay data for a chemical drillhole."""
    __tablename__ = "chemical_assays"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("geological_models.id"), nullable=False, index=True)
    chemical_borehole_id = Column(Integer, ForeignKey("chemical_boreholes.id"), nullable=False, index=True)

    hole_id = Column(String, nullable=False, index=True)
    from_depth = Column(Float, nullable=False)
    to_depth = Column(Float, nullable=False)
    unit = Column(String)
    elements_json = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    model = relationship("GeologicalModel")
    chemical_borehole = relationship("ChemicalBorehole", back_populates="assays")


class Geobody(Base):
    __tablename__ = "geobodies"
    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("geological_models.id"), nullable=False, index=True)

    key = Column(String, index=True)  # 额外派生字段（不改你的原始字段）

    层 = Column(Text)
    体积 = Column(Text)
    表面积 = Column(Text)
    元素ID = Column(Text)
    范围下限 = Column(Text)
    范围上限 = Column(Text)
    潮湿程度 = Column(Text)
    单轴饱和抗压强度 = Column(Text)
    地层代号 = Column(Text)
    风化程度 = Column(Text)
    工程等级 = Column(Text)
    基本承载力 = Column(Text)
    基地摩擦系数 = Column(Text)
    临时挖方边坡率 = Column(Text)
    密实状态 = Column(Text)
    内摩擦角 = Column(Text)
    凝聚力 = Column(Text)
    时代成因 = Column(Text)
    塑性状态 = Column(Text)
    天然密度 = Column(Text)
    填料类别 = Column(Text)
    岩土名称 = Column(Text)
    永久挖方边坡率 = Column(Text)
    钻孔灌注桩桩周极限摩阻力 = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    model = relationship("GeologicalModel", back_populates="geobodies")


class ReconstructJob(Base):
    """3D reconstruct job per model run (uploads each time)."""
    __tablename__ = "reconstruct_jobs"
    id = Column(String, primary_key=True)  # uuid
    model_id = Column(Integer, ForeignKey("geological_models.id"), nullable=False, index=True)

    status = Column(String, default="queued", nullable=False, index=True)  # queued/running/success/failed
    params_json = Column(Text)
    out_dir = Column(Text)
    log_path = Column(Text)
    error = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    model = relationship("GeologicalModel")


class DeepReconstructJob(Base):
    """Algorithm C reconstruction job."""
    __tablename__ = "deep_reconstruct_jobs"

    id = Column(String, primary_key=True)
    model_id = Column(Integer, ForeignKey("geological_models.id"), nullable=False, index=True)

    status = Column(String, default="queued", nullable=False, index=True)
    params_json = Column(Text)
    out_dir = Column(Text)
    log_path = Column(Text)
    summary_json = Column(Text)
    error = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    model = relationship("GeologicalModel")


class GeochemReconstructJob(Base):
    """Geochemical element spatial reconstruction job."""
    __tablename__ = "geochem_reconstruct_jobs"

    id = Column(String, primary_key=True)
    model_id = Column(Integer, ForeignKey("geological_models.id"), nullable=False, index=True)

    status = Column(String, default="queued", nullable=False, index=True)
    params_json = Column(Text)
    out_dir = Column(Text)
    log_path = Column(Text)
    summary_json = Column(Text)
    error = Column(Text)
    rule_set_id = Column(String, nullable=True, index=True)
    source_variation_job_id = Column(String, nullable=True, index=True)
    source_correlation_job_id = Column(String, nullable=True, index=True)
    workflow_id = Column(String, nullable=True, index=True)
    selected_clue_id = Column(String, nullable=True, index=True)
    dataset_hash = Column(String, nullable=True, index=True)
    algorithm_version = Column(String, nullable=True)
    algorithm_profile = Column(String, nullable=True)
    scene_manifest_path = Column(Text, nullable=True)
    validation_status = Column(String, nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    model = relationship("GeologicalModel")


class GeochemMiningJob(Base):
    """Element variation/correlation mining job."""

    __tablename__ = "geochem_mining_jobs"

    id = Column(String, primary_key=True)
    model_id = Column(Integer, ForeignKey("geological_models.id"), nullable=False, index=True)
    algorithm_mode = Column(String, nullable=False, default="both")
    status = Column(String, default="queued", nullable=False, index=True)
    progress = Column(Integer, default=0, nullable=False)
    stage = Column(String, default="已排队", nullable=False)
    params_json = Column(Text)
    out_dir = Column(Text)
    log_path = Column(Text)
    summary_json = Column(Text)
    error = Column(Text)
    rule_set_id = Column(String, nullable=True, index=True)
    source_variation_job_id = Column(String, nullable=True, index=True)
    workflow_id = Column(String, nullable=True, index=True)
    dataset_hash = Column(String, nullable=True, index=True)
    algorithm_version = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    model = relationship("GeologicalModel")


class GeochemRuleSet(Base):
    """Versioned geochemical interpretation rules for one geological model."""

    __tablename__ = "geochem_rule_sets"
    __table_args__ = (
        UniqueConstraint("model_id", "version", name="uq_geochem_rule_set_model_version"),
    )

    id = Column(String, primary_key=True)
    model_id = Column(Integer, ForeignKey("geological_models.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String, nullable=False, default="draft", index=True)
    background_method = Column(String, nullable=False, default="log_mad")
    background_scope = Column(String, nullable=False, default="model")
    relative_ratio_cutoffs_json = Column(Text, nullable=False)
    support_probability_cutoff = Column(Float, nullable=False, default=0.5)
    minimum_positive_count = Column(Integer, nullable=False, default=30)
    merge_gap_m = Column(Float, nullable=False, default=0.5)
    minimum_segment_length_m = Column(Float, nullable=False, default=0.0)
    source_document = Column(Text)
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    confirmed_at = Column(DateTime, nullable=True)

    model = relationship("GeologicalModel", back_populates="geochem_rule_sets")
    element_rules = relationship(
        "GeochemElementRule",
        back_populates="rule_set",
        cascade="all, delete-orphan",
        order_by="GeochemElementRule.element",
    )
    background_profiles = relationship(
        "GeochemBackgroundProfile",
        back_populates="rule_set",
        cascade="all, delete-orphan",
    )


class GeochemElementRule(Base):
    """Element-specific professional grade thresholds within a rule set."""

    __tablename__ = "geochem_element_rules"
    __table_args__ = (
        UniqueConstraint("rule_set_id", "element", name="uq_geochem_element_rule"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_set_id = Column(String, ForeignKey("geochem_rule_sets.id"), nullable=False, index=True)
    element = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False, default="other")
    unit = Column(String, nullable=False, default="ppm")
    detection_limit = Column(Float, nullable=True)
    detection_limit_status = Column(String, nullable=False, default="pending")
    applicable_sample_medium = Column(String, nullable=False, default="drill_core_assay")
    boundary_grade_ppm = Column(Float, nullable=True)
    boundary_status = Column(String, nullable=False, default="pending")
    boundary_source = Column(Text)
    industrial_grade_ppm = Column(Float, nullable=True)
    industrial_status = Column(String, nullable=False, default="pending")
    industrial_source = Column(Text)
    notes = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    rule_set = relationship("GeochemRuleSet", back_populates="element_rules")


class GeochemBackgroundProfile(Base):
    """Frozen background/threshold snapshot produced by a variation job."""

    __tablename__ = "geochem_background_profiles"
    __table_args__ = (
        UniqueConstraint("variation_job_id", "element", name="uq_geochem_background_job_element"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("geological_models.id"), nullable=False, index=True)
    rule_set_id = Column(String, ForeignKey("geochem_rule_sets.id"), nullable=False, index=True)
    variation_job_id = Column(String, ForeignKey("geochem_mining_jobs.id"), nullable=False, index=True)
    element = Column(String, nullable=False, index=True)
    method = Column(String, nullable=False)
    scope_signature = Column(Text, nullable=False)
    background_value = Column(Float, nullable=False)
    statistical_threshold = Column(Float, nullable=False)
    statistics_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    rule_set = relationship("GeochemRuleSet", back_populates="background_profiles")


class GeochemWorkflowRun(Base):
    """One reproducible variation -> correlation -> 3-D evidence workflow."""

    __tablename__ = "geochem_workflow_runs"
    __table_args__ = (
        UniqueConstraint("model_id", "client_request_id", name="uq_geochem_workflow_client_request"),
    )

    id = Column(String, primary_key=True)
    model_id = Column(Integer, ForeignKey("geological_models.id"), nullable=False, index=True)
    rule_set_id = Column(String, ForeignKey("geochem_rule_sets.id"), nullable=False, index=True)
    dataset_hash = Column(String, nullable=False, index=True)
    algorithm_version = Column(String, nullable=False, default="workflow-v1")
    status = Column(String, nullable=False, default="created", index=True)
    stage = Column(String, nullable=False, default="variation_pending", index=True)
    progress = Column(Integer, nullable=False, default=0)
    variation_job_id = Column(String, ForeignKey("geochem_mining_jobs.id"), nullable=True, index=True)
    correlation_job_id = Column(String, ForeignKey("geochem_mining_jobs.id"), nullable=True, index=True)
    reconstruct_job_id = Column(String, ForeignKey("geochem_reconstruct_jobs.id"), nullable=True, index=True)
    selected_clue_id = Column(String, nullable=True, index=True)
    limitations_json = Column(Text)
    client_request_id = Column(String, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class GeochemCandidateClue(Base):
    """Persisted, traceable clue produced by variation or correlation."""

    __tablename__ = "geochem_candidate_clues"
    __table_args__ = (
        UniqueConstraint("workflow_id", "source_key", name="uq_geochem_clue_source_key"),
    )

    id = Column(String, primary_key=True)
    workflow_id = Column(String, ForeignKey("geochem_workflow_runs.id"), nullable=False, index=True)
    source_job_id = Column(String, nullable=False, index=True)
    source_key = Column(String, nullable=False)
    kind = Column(String, nullable=False, index=True)
    main_element = Column(String, nullable=True, index=True)
    members_json = Column(Text)
    hole_ids_json = Column(Text)
    from_depth = Column(Float, nullable=True)
    to_depth = Column(Float, nullable=True)
    highest_level = Column(String, nullable=True, index=True)
    max_background_ratio = Column(Float, nullable=True)
    same_hole_count = Column(Integer, nullable=False, default=0)
    cross_hole_count = Column(Integer, nullable=False, default=0)
    professional_evidence_json = Column(Text)
    evidence_json = Column(Text)
    limitations_json = Column(Text)
    status = Column(String, nullable=False, default="candidate", index=True)
    rank = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class GeochemEvidenceLink(Base):
    """Directed provenance edge between data, jobs, clues and artifacts."""

    __tablename__ = "geochem_evidence_links"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id",
            "from_type",
            "from_id",
            "relation",
            "to_type",
            "to_id",
            name="uq_geochem_evidence_edge",
        ),
    )

    id = Column(String, primary_key=True)
    workflow_id = Column(String, ForeignKey("geochem_workflow_runs.id"), nullable=False, index=True)
    from_type = Column(String, nullable=False)
    from_id = Column(String, nullable=False)
    relation = Column(String, nullable=False)
    to_type = Column(String, nullable=False)
    to_id = Column(String, nullable=False)
    evidence_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class GeochemArtifact(Base):
    """Manifest entry for a durable or regenerable workflow artifact."""

    __tablename__ = "geochem_artifacts"
    __table_args__ = (
        UniqueConstraint("workflow_id", "artifact_type", "path", name="uq_geochem_artifact_path"),
    )

    id = Column(String, primary_key=True)
    workflow_id = Column(String, ForeignKey("geochem_workflow_runs.id"), nullable=False, index=True)
    job_id = Column(String, nullable=True, index=True)
    artifact_type = Column(String, nullable=False, index=True)
    path = Column(Text, nullable=False)
    sha256 = Column(String, nullable=True)
    byte_size = Column(Integer, nullable=False, default=0)
    is_intermediate = Column(Boolean, nullable=False, default=False, index=True)
    retention_policy = Column(String, nullable=False, default="keep")
    reference_count = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
