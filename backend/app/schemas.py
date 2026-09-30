# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Any

from pydantic import BaseModel, Field, ConfigDict


class Page(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Any]
    total: int
    page: int
    page_size: int


# -----------------
# Auth
# -----------------
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginIn(BaseModel):
    username: str
    password: str


class RegisterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    is_active: bool
    is_admin: bool
    created_at: datetime


class PasswordChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=6, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class AdminResetPasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_password: str = Field(min_length=6, max_length=128)


# -----------------
# Models
# -----------------
class GeologicalModelBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    description: Optional[str] = None


class GeologicalModelCreate(GeologicalModelBase):
    pass


class GeologicalModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = None
    description: Optional[str] = None


class GeologicalModelOut(GeologicalModelBase):
    id: int
    created_at: datetime


# -----------------
# Boreholes
# -----------------
class BoreholeBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    model_id: int
    Borehole: str
    # 坐标信息（UI 需要）
    Northing: Optional[float] = None  # Y
    Easting: Optional[float] = None   # X
    Elevation: Optional[float] = None # Z
    Holedepth: Optional[float] = None # 钻孔深度

    # 原始字段（来自导入；前端一般不展示，但保留以兼容历史数据）
    原点: Optional[str] = None
    范围下限: Optional[str] = None
    范围上限: Optional[str] = None


class BoreholeCreate(BoreholeBase):
    pass


class BoreholeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    Borehole: Optional[str] = None
    Northing: Optional[float] = None
    Easting: Optional[float] = None
    Elevation: Optional[float] = None
    Holedepth: Optional[float] = None
    原点: Optional[str] = None
    范围下限: Optional[str] = None
    范围上限: Optional[str] = None


class BoreholeOut(BoreholeBase):
    id: int
    created_at: datetime

    # 便于前端展示的派生字段
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None


# -----------------
# Borehole Sections
# -----------------
class BoreholeSectionBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    borehole_id: int
    项: Optional[str] = None
    高度: Optional[float] = None
    底坐标: Optional[str] = None
    底半径: Optional[float] = None
    顶坐标: Optional[str] = None
    Borehole: Optional[str] = None
    Depth: Optional[float] = None
    Bottom: Optional[float] = None
    Description: Optional[str] = None
    元素ID: Optional[int] = None
    范围下限: Optional[str] = None
    范围上限: Optional[str] = None
    geobody_key: Optional[str] = None


class BoreholeSectionCreate(BoreholeSectionBase):
    pass


class BoreholeSectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    项: Optional[str] = None
    高度: Optional[float] = None
    底坐标: Optional[str] = None
    底半径: Optional[float] = None
    顶坐标: Optional[str] = None
    Borehole: Optional[str] = None
    Depth: Optional[float] = None
    Bottom: Optional[float] = None
    Description: Optional[str] = None
    元素ID: Optional[int] = None
    范围下限: Optional[str] = None
    范围上限: Optional[str] = None
    geobody_key: Optional[str] = None


class BoreholeSectionOut(BoreholeSectionBase):
    id: int
    created_at: datetime


# -----------------
# Geobodies
# -----------------
class GeobodyBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    model_id: int
    key: Optional[str] = None

    层: Optional[str] = None
    体积: Optional[str] = None
    表面积: Optional[str] = None
    元素ID: Optional[str] = None
    范围下限: Optional[str] = None
    范围上限: Optional[str] = None
    潮湿程度: Optional[str] = None
    单轴饱和抗压强度: Optional[str] = None
    地层代号: Optional[str] = None
    风化程度: Optional[str] = None
    工程等级: Optional[str] = None
    基本承载力: Optional[str] = None
    基地摩擦系数: Optional[str] = None
    临时挖方边坡率: Optional[str] = None
    密实状态: Optional[str] = None
    内摩擦角: Optional[str] = None
    凝聚力: Optional[str] = None
    时代成因: Optional[str] = None
    塑性状态: Optional[str] = None
    天然密度: Optional[str] = None
    填料类别: Optional[str] = None
    岩土名称: Optional[str] = None
    永久挖方边坡率: Optional[str] = None
    钻孔灌注桩桩周极限摩阻力: Optional[str] = None


class GeobodyCreate(GeobodyBase):
    pass


class GeobodyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: Optional[str] = None
    层: Optional[str] = None
    体积: Optional[str] = None
    表面积: Optional[str] = None
    元素ID: Optional[str] = None
    范围下限: Optional[str] = None
    范围上限: Optional[str] = None
    潮湿程度: Optional[str] = None
    单轴饱和抗压强度: Optional[str] = None
    地层代号: Optional[str] = None
    风化程度: Optional[str] = None
    工程等级: Optional[str] = None
    基本承载力: Optional[str] = None
    基地摩擦系数: Optional[str] = None
    临时挖方边坡率: Optional[str] = None
    密实状态: Optional[str] = None
    内摩擦角: Optional[str] = None
    凝聚力: Optional[str] = None
    时代成因: Optional[str] = None
    塑性状态: Optional[str] = None
    天然密度: Optional[str] = None
    填料类别: Optional[str] = None
    岩土名称: Optional[str] = None
    永久挖方边坡率: Optional[str] = None
    钻孔灌注桩桩周极限摩阻力: Optional[str] = None


class GeobodyOut(GeobodyBase):
    id: int
    created_at: datetime
