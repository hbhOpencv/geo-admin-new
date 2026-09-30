"""Sanitize internal project wording before content reaches delivery-facing APIs."""

from __future__ import annotations

from typing import Any


_DELIVERY_REPLACEMENTS = (
    ("待专家确认", "待项目确认"),
    ("专家确认", "项目确认"),
    ("地质专家", "项目审核"),
    ("肖博士", "项目方"),
    ("王老师", "项目方"),
    ("师兄", "原始方案"),
    ("老师", "项目方"),
)


def sanitize_delivery_content(value: Any) -> Any:
    """Recursively replace internal names while preserving API data types."""
    if isinstance(value, str):
        for old, new in _DELIVERY_REPLACEMENTS:
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [sanitize_delivery_content(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_delivery_content(item) for item in value)
    if isinstance(value, dict):
        return {key: sanitize_delivery_content(item) for key, item in value.items()}
    return value
