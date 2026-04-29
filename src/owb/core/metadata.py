"""Curated model metadata that LM Studio doesn't supply (release dates,
parameter counts, MoE info) plus vendor display config (colors)."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from .results import ModelInfo

# Accepts either YYYY-MM (month precision is enough for the report) or
# YYYY-MM-DD (when the exact day is known).
_DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def release_age_bucket(released: str | None, today: date | None = None) -> str:
    """Bucket a release date into ``recent`` (≤6 months), ``older`` (7-12),
    ``ancient`` (13+ months) or ``unknown`` when no date is given.
    Boundaries are computed in whole calendar months."""
    if not released or not _DATE_RE.match(released):
        return "unknown"
    today = today or date.today()
    parts = released.split("-")
    y, m = int(parts[0]), int(parts[1])
    months = (today.year - y) * 12 + (today.month - m)
    if months <= 6:
        return "recent"
    if months <= 12:
        return "older"
    return "ancient"


class ModelMeta:
    """Curated per-model metadata from a single JSON file.

    Schema per id:
        {"released": "YYYY-MM" | "YYYY-MM-DD",
         "params_b": <number>,
         "active_params_b": <number>}  # optional, MoE only

    All fields are optional. Lookups are case-sensitive on the model id;
    the `@quant` suffix is stripped automatically as a fallback.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, dict[str, Any]] = {}
        if path.exists():
            raw = json.loads(path.read_text())
            for k, v in raw.items():
                if k.startswith("_") or not isinstance(v, dict):
                    continue
                self._data[k] = v

    def _entry(self, model: ModelInfo) -> dict[str, Any] | None:
        e = self._data.get(model.id)
        if e is None:
            base = model.id.split("@")[0]
            e = self._data.get(base)
        return e

    def released(self, model: ModelInfo) -> str | None:
        """Release date string (YYYY-MM or YYYY-MM-DD), or None."""
        e = self._entry(model)
        if not e:
            return None
        v = e.get("released")
        if isinstance(v, str) and _DATE_RE.match(v):
            return v
        return None

    def params_b(self, model: ModelInfo) -> float | None:
        """Total parameter count in billions, or None."""
        e = self._entry(model)
        if not e:
            return None
        v = e.get("params_b")
        if isinstance(v, (int, float)):
            return float(v)
        return None

    def active_params_b(self, model: ModelInfo) -> float | None:
        """Active params for MoE models, in billions; None for dense / unknown."""
        e = self._entry(model)
        if not e:
            return None
        v = e.get("active_params_b")
        if isinstance(v, (int, float)):
            return float(v)
        return None

    def is_moe(self, model: ModelInfo) -> bool | None:
        """Returns True only when the file has an explicit MoE signal
        (i.e. `active_params_b` is set). None otherwise — callers must
        fall back to an architectural heuristic, since absence of
        `active_params_b` does not prove the model is dense (the field
        may simply be unknown)."""
        return True if self.active_params_b(model) is not None else None


def vendor(model: ModelInfo) -> str:
    """Best-effort manufacturer name. LM Studio's `publisher` is usually right;
    fall back to the part before the first slash in the id."""
    if model.publisher:
        return model.publisher
    if "/" in model.id:
        return model.id.split("/", 1)[0]
    return "—"


class Vendors:
    """Vendor → display-config (color, label-dark flag) loaded from
    `data/vendors.json`. Also resolves a model's vendor key by
    inspecting ID-prefix shortcuts (so `qwen3.5-122b-a10b` lands on
    `qwen` even though the publisher is `lmstudio-community`)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._vendors: dict[str, dict[str, Any]] = {}
        self._prefixes: list[tuple[str, str]] = []
        self.fallback_color: str = "#475569"
        if path.exists():
            raw = json.loads(path.read_text())
            self.fallback_color = raw.get("fallback_color", self.fallback_color)
            for k, v in (raw.get("vendors") or {}).items():
                if isinstance(v, dict):
                    self._vendors[k] = v
            for entry in raw.get("id_prefixes") or []:
                if isinstance(entry, (list, tuple)) and len(entry) == 2:
                    self._prefixes.append((str(entry[0]), str(entry[1])))

    def color(self, key: str) -> str:
        v = self._vendors.get(key)
        if v and isinstance(v.get("color"), str):
            return v["color"]
        return self.fallback_color

    def label_dark(self, key: str) -> bool:
        """True when the vendor's fill is light enough that text on top
        should be dark (e.g. lime, sky-cyan, pink)."""
        v = self._vendors.get(key)
        return bool(v and v.get("label_dark"))

    def label_color(self, key: str) -> str:
        return "#1c1917" if self.label_dark(key) else "#fff"

    def vendor_key(self, model_info: ModelInfo | None, model_id: str) -> str:
        """Canonical vendor key for color lookup. ID-prefix wins over
        publisher so community-republished models still group with the
        original vendor's color."""
        base = model_id.split("@")[0].split("/", 1)[-1].lower()
        for prefix, group in self._prefixes:
            if base.startswith(prefix):
                return group
        if model_info is not None:
            return vendor(model_info)
        return "—"
