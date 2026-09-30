"""Small utilities.

This project stores some coordinates as strings like "540144.23m,3349559.88m,3758.299m".
For UI convenience we expose parsed X/Y/Z values.
"""

# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Optional, Tuple

from sqlalchemy.orm import Query


_num_keep = re.compile(r"[^0-9eE\+\-\.]" )


def parse_float_any(v: object) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "/":
        return None
    s = s.replace("立方m", "").replace("平方m", "")
    s = s.replace("立方 m", "").replace("平方 m", "")
    s = s.replace("m", "").replace("米", "").replace(" ", "")
    s2 = _num_keep.sub("", s)
    if not s2:
        return None
    try:
        return float(s2)
    except Exception:
        return None


def parse_origin_xyz(origin: Optional[str]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Parse "x,y,z" (optionally with units) into floats."""
    if not origin:
        return None, None, None
    s = str(origin).strip().strip('"')
    if not s:
        return None, None, None
    parts = [p.strip() for p in s.split(",")]
    if len(parts) < 3:
        return None, None, None
    x = parse_float_any(parts[0])
    y = parse_float_any(parts[1])
    z = parse_float_any(parts[2])
    return x, y, z


def paginate(query: Query, page: int, page_size: int):
    page = max(1, int(page))
    page_size = min(200, max(1, int(page_size)))
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return items, total, page, page_size
