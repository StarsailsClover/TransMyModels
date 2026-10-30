"""Normalize upstream model ids into clean routed names.

Rules:
  1. Lowercase everything.
  2. Split on '-'.
  3. Drop every segment that looks like a date or build tag, anywhere in the
     chain (not just at the very end):
       - 4-digit MMDD date        e.g. 1017, 0813, 0731, 0902
       - 6-digit YYMMDD date      e.g. 251228, 260628
       - build tag: 2 letters + 2 digits, e.g. cd01, ac03, cx01
     Other segments (global, preview, exp, vision, vl, 27b, a10b, ...) are kept.

Examples:
  DeepSeek-V4.1-Flash-1017           -> deepseek-v4.1-flash
  DeepSeek-V4.1-Flash-1017-global    -> deepseek-v4.1-flash-global
  doubao-seed-2-1-turbo-260628       -> doubao-seed-2-1-turbo
  GLM-5.3-ac03                       -> glm-5.3
"""
from __future__ import annotations

import re

_DATE_RE = re.compile(r"^\d{4,6}$")
_BUILD_RE = re.compile(r"^[a-z]{2}\d{2}$")


def _is_noise(seg: str) -> bool:
    return bool(_DATE_RE.match(seg) or _BUILD_RE.match(seg))


def normalize_model_id(raw: str) -> str:
    s = (raw or "").strip().lower()
    parts = [p for p in s.split("-") if not _is_noise(p)]
    return "-".join(parts)


def dedupe(desired: str, taken: set) -> str:
    """Return desired if free, else append -2, -3, ... until free."""
    if desired not in taken:
        return desired
    i = 2
    while f"{desired}-{i}" in taken:
        i += 1
    return f"{desired}-{i}"
