from __future__ import annotations

import re
from pathlib import Path

import yaml

from .results import ModelInfo


_QUANT_Q_RE = re.compile(r"(?:^|[^0-9a-z])q(\d{1,2})(?:[_\-]|$)")
_QUANT_BIT_RE = re.compile(r"(\d{1,2})\s*-?\s*bit")
_QUANT_FP_RE = re.compile(r"(?:mxfp|fp|bf)(\d{1,2})")


def quant_bits(quant: str | None) -> int | None:
    """Best-effort: extract the bit-width from a quantization label.

    Recognizes GGUF-style (``Q4_K_M``, ``q8_0``), MLX-style (``4bit``,
    ``8-bit``) and float labels (``MXFP4``, ``FP16``, ``BF16``).
    Returns ``None`` when no width can be inferred.
    """
    if not quant:
        return None
    s = quant.lower()
    for rx in (_QUANT_Q_RE, _QUANT_BIT_RE, _QUANT_FP_RE):
        m = rx.search(s)
        if m:
            return int(m.group(1))
    return None


def size_bucket(model_id: str) -> str:
    """Heuristic: derive a size bucket from the model id ('30b', '4b', etc.).

    Returns one of: 'nano' (<2B), 'tiny' (2-8B), 'small' (9-18B),
    'mid' (19-40B), 'large' (41-84B), 'xlarge' (85B+), or 'unknown'.
    """
    m = re.search(r"(\d+(?:\.\d+)?)\s*[bB]\b", model_id)
    if not m:
        return "unknown"
    n = float(m.group(1))
    if n < 2:
        return "nano"
    if n < 9:
        return "tiny"
    if n < 19:
        return "small"
    if n < 41:
        return "mid"
    if n < 85:
        return "large"
    return "xlarge"


def filter_models(
    models: list[ModelInfo],
    *,
    only_vlm: bool = False,
    only_llm: bool = False,
    only_tool_use: bool = False,
    only_mlx: bool = False,
    only_gguf: bool = False,
    min_context: int = 0,
    size_buckets: list[str] | None = None,
    quant_widths: list[int] | None = None,
    allowlist: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[ModelInfo]:
    """Apply filter combination to a list of ModelInfo."""
    out = []
    for m in models:
        if only_vlm and m.type != "vlm":
            continue
        if only_llm and m.type != "llm":
            continue
        if m.type == "embeddings":
            # Embeddings models are not benchmarkable as chat models
            continue
        if only_tool_use and not m.supports_tools:
            continue
        if only_mlx and not m.is_mlx:
            continue
        if only_gguf and m.compatibility_type != "gguf":
            continue
        if m.max_context_length < min_context:
            continue
        if size_buckets and size_bucket(m.id) not in size_buckets:
            continue
        if quant_widths and quant_bits(m.quantization) not in quant_widths:
            continue
        if allowlist and m.id not in allowlist:
            continue
        if exclude and m.id in exclude:
            continue
        out.append(m)
    return out


def load_allowlist(path: Path) -> list[str] | None:
    """Load `models.allowlist.yaml` if present. Returns None if file missing."""
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text())
    if not data:
        return None
    return list(data.get("models", []))
