"""Build a static, gh-pages-friendly site from results/.

Layout produced:
  <out>/index.html                     — landing with all benchmarks
  <out>/benchmarks/<task>.html         — table + tab nav + hover previews
  <out>/models/<safe-id>.html          — full per-model detail
  <out>/assets/                        — copies of artefacts referenced by reports
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..core.metadata import ModelMeta, Vendors, vendor
from ..core.results import BenchStore, ModelInfo, TaskResult, safe_model_id

TEMPLATES = Path(__file__).resolve().parent / "templates"


# Display order + labels for known benchmarks. Unknown benchmarks are appended.
BENCHMARK_ORDER = [
    ("coding", "Coding"),
    ("vision", "Vision"),
    ("niah", "Needle in a Haystack"),
    ("context_growth", "Context Growth"),
    ("tool_use", "Tool Use"),
    ("diagram_to_svg", "Diagram → SVG"),
    ("diagram_to_mermaid", "Diagram → Mermaid"),
    ("hallucination", "Hallucination"),
    ("nonsense", "Nonsense"),
    ("instruction_following", "Instruction Following"),
    ("niah_deep", "NIAH Heatmap"),
]


def benchmark_labels(task_names: list[str]) -> list[tuple[str, str]]:
    label_map = dict(BENCHMARK_ORDER)
    out = [(t, label_map[t]) for t, _ in BENCHMARK_ORDER if t in task_names]
    extra = [t for t in task_names if t not in label_map]
    out.extend((t, t.replace("_", " ").title()) for t in extra)
    return out


_QUANT_BIT_RE = __import__("re").compile(
    r"(?:^|[^a-z0-9])(?:q|fp|bf|mxfp|f)(\d+)", __import__("re").IGNORECASE
)
_QUANT_BIT_TAIL_RE = __import__("re").compile(r"(\d+)\s*bit", __import__("re").IGNORECASE)


def short_model_id(model_id: str) -> str:
    """Strip vendor/ prefix and @variant suffix for compact display."""
    s = (model_id or "").split("/", 1)[-1]
    s = s.split("@", 1)[0]
    return s


def pretty_quant(compat: str | None, quant: str | None) -> str:
    """Display-friendly quant string: 'mlx 4bit', 'gguf 8bit', etc."""
    if not quant:
        return compat or "—"
    q = str(quant).strip()
    bits: int | None = None
    m = _QUANT_BIT_RE.search(q)
    if m:
        bits = int(m.group(1))
    else:
        m = _QUANT_BIT_TAIL_RE.search(q)
        if m:
            bits = int(m.group(1))
    if bits:
        return f"{compat} {bits}bit" if compat else f"{bits}bit"
    return f"{compat} {q}" if compat else q


def _env() -> Environment:
    e = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    e.filters["safe_id"] = safe_model_id
    e.filters["short_id"] = short_model_id
    e.filters["mb"] = lambda v: f"{v:.0f} MB" if v else "—"
    e.filters["gb"] = lambda v: f"{v / 1024:.1f} GB" if v else "—"
    return e


def _copy_artifacts(store: BenchStore, out_dir: Path) -> None:
    """Mirror artifacts/ and select asset files into <out>/assets/.

    - artifacts/<safe>/<task>/...   →  docs/assets/artifacts/<safe>/<task>/...
    - assets/niah/haystack_*.txt    →  docs/assets/haystacks/...
    Keeps the report self-contained for gh-pages.
    """
    src = store.artifacts_dir
    dst = out_dir / "assets" / "artifacts"
    if dst.exists():
        shutil.rmtree(dst)
    if src.exists():
        shutil.copytree(src, dst)

    # Haystack files are shared across models, mirrored once.
    haystacks_src = store.root / "assets" / "niah"
    haystacks_dst = out_dir / "assets" / "haystacks"
    if haystacks_dst.exists():
        shutil.rmtree(haystacks_dst)
    if haystacks_src.exists():
        haystacks_dst.mkdir(parents=True, exist_ok=True)
        for f in haystacks_src.glob("haystack_*.txt"):
            shutil.copy(f, haystacks_dst / f.name)

    # Long-text corpus (book.txt) shared between comprehension + summarization.
    long_src = store.root / "assets" / "long_text"
    long_dst = out_dir / "assets" / "long_text"
    if long_dst.exists():
        shutil.rmtree(long_dst)
    if long_src.exists():
        long_dst.mkdir(parents=True, exist_ok=True)
        for f in long_src.glob("*.txt"):
            shutil.copy(f, long_dst / f.name)


# Vendor display config (colors, prefix mapping) lives in data/vendors.json.
# A module-level lazy instance avoids re-reading the file for every call.
_VENDORS_CACHE: Vendors | None = None


def _vendors() -> Vendors:
    global _VENDORS_CACHE
    if _VENDORS_CACHE is None:
        _VENDORS_CACHE = Vendors(Path(__file__).resolve().parents[3] / "data" / "vendors.json")
    return _VENDORS_CACHE


def _color_vendor_key(model_info: ModelInfo | None, model_id: str) -> str:
    return _vendors().vendor_key(model_info, model_id)


def _vendor_color(key: str) -> str:
    return _vendors().color(key)


def _label_color_for(vendor_key: str) -> str:
    return _vendors().label_color(vendor_key)


_PARAM_SEG_RE = re.compile(r"^e?(\d+)b$", re.IGNORECASE)
_MOE_ACTIVE_SEG_RE = re.compile(r"^a\d+(?:\.\d+)?b$", re.IGNORECASE)


def _params_b(
    model_id: str,
    model_info: ModelInfo | None,
    meta: ModelMeta,
) -> int | float | None:
    """Total parameter count in billions. Prefers the curated value from
    `data/model_meta.json`; falls back to a heuristic that parses ID
    segments like `35b` or `e4b`. Active-params segments (`a3b`) are
    ignored. Version numbers (e.g. the `4` in `gemma-4`) don't match
    because the pattern requires a trailing `b`. Returns int for whole
    numbers so templates render `120` not `120.0`."""
    v: float | None = None
    if model_info is not None:
        v = meta.params_b(model_info)
    if v is None:
        base = model_id.split("@")[0].split("/", 1)[-1]
        for seg in base.split("-"):
            m = _PARAM_SEG_RE.match(seg)
            if m:
                v = float(m.group(1))
                break
    if v is None:
        return None
    return int(v) if v.is_integer() else v


def _is_moe(model_info: ModelInfo | None, model_id: str, meta: ModelMeta | None = None) -> bool:
    """Curated `active_params_b` wins; otherwise fall back to architectural
    hints (arch contains 'moe', is gpt-oss, or the ID has an `aXb` segment)."""
    if meta is not None and model_info is not None:
        explicit = meta.is_moe(model_info)
        if explicit is not None:
            return explicit
    arch = ((model_info.arch if model_info else None) or "").lower()
    if "moe" in arch:
        return True
    if arch.replace("_", "-") == "gpt-oss":
        return True
    base = model_id.split("@")[0].split("/", 1)[-1].lower()
    return any(_MOE_ACTIVE_SEG_RE.match(seg) for seg in base.split("-"))


def _ram_estimate(ram_mb: float | None) -> tuple[float | None, float | None, float | None]:
    """4 GB System + Modellgewichte + KV-Cache-Heuristik für 64k Tokens.
    Liefert (weights_gb, kv_estimate_gb, total_ram_gb) — alle None wenn
    keine RAM-Messung vorliegt."""
    if not ram_mb:
        return None, None, None
    weights_gb = ram_mb / 1024.0
    kv_estimate_gb = max(2.0, weights_gb * 0.4)
    total_ram_gb = 4.0 + weights_gb + kv_estimate_gb
    return (
        round(weights_gb, 2),
        round(kv_estimate_gb, 2),
        round(total_ram_gb, 2),
    )


def _build_task_scatter(rows: list[dict]) -> list[dict]:
    """Pro Bench-Seite: ein Punkt pro Modell mit Score (Y) und Wall-Time (X)
    für genau diesen Bench. Wall-Time auf der X-Achse, weil Zeit die intuitiv
    erwartete X-Größe ist. Ausgeschlossen: Fehler, Score=None, wall_s≤0."""
    out: list[dict] = []
    for r in rows:
        if r.get("error"):
            continue
        score = r.get("score")
        wall_s = r.get("wall_s")
        if score is None or not wall_s or wall_s <= 0:
            continue
        weights_gb, kv_gb, total_gb = _ram_estimate(r.get("ram_mb"))
        out.append(
            {
                "model_id": r["model_id"],
                "short_id": short_model_id(r["model_id"]),
                "vendor": r["vendor"],
                "quant": r["quant"],
                "score": score,
                "wall_s": wall_s,
                "tps": r.get("tps"),
                "tokens": r.get("tokens"),
                "weights_gb": weights_gb,
                "kv_estimate_gb": kv_gb,
                "total_ram_gb": total_gb,
                "color": _vendor_color(r["color_key"]),
                "color_key": r["color_key"],
                "label_color": _label_color_for(r["color_key"]),
                "params_b": r.get("params_b"),
                "is_moe": r.get("is_moe", False),
            }
        )
    return out


# Bench-Seiten, auf denen der Walltime-vs-Score-Scatter erscheinen soll.
SCATTER_BENCHES = {"coding", "vision", "diagram_to_svg", "diagram_to_mermaid", "niah", "hallucination", "tool_use"}


def _ensure_mermaid_renders(store: BenchStore) -> None:
    """For every diagram_to_mermaid result, ensure each diagram has a
    rendered PNG screenshot of its mermaid code so the bench-overview
    can show a hover preview. Idempotent and best-effort."""
    results = store.all_for_task("diagram_to_mermaid")
    if not results:
        return
    try:
        from .screenshots import screenshot_mermaid
    except Exception:  # noqa: BLE001
        return
    for r in results:
        diagrams = (r.score_breakdown or {}).get("diagrams") or []
        if not diagrams:
            continue
        artifact_root = store.artifacts_dir / safe_model_id(r.model_id) / "diagram_to_mermaid"
        changed = False
        for d in diagrams:
            stem = d.get("id") or "diagram"
            existing = d.get("render_path")
            if existing and (store.root / existing).exists():
                continue
            png_path = artifact_root / f"{stem}.render.png"
            if not png_path.exists():
                rendered = screenshot_mermaid(
                    d.get("mermaid") or "", artifact_root, stem
                )
                if rendered is None:
                    continue
                png_path = rendered
            d["render_path"] = str(png_path.relative_to(store.root))
            changed = True
        if changed:
            store.save(r)


def _load_system_prompts(prompts_root: Path) -> dict[str, dict[str, str]]:
    """task name → {"system": str, "user": str} — beide best-effort.

    Bei Tasks mit mehreren Sub-Prompts (Vision-OCR) wird der erste Sub-Spec
    genommen; bei NIAH baut der Task den Prompt zur Laufzeit, daher leer.
    """

    def _spec(path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            return None

    def _entry(spec: dict | None, sys_key: str = "system") -> dict[str, str] | None:
        if not spec:
            return None
        sys = spec.get(sys_key) or ""
        usr = spec.get("user_prompt") or ""
        if not sys and not usr:
            return None
        return {"system": sys, "user": usr}

    out: dict[str, dict[str, str]] = {}
    if (e := _entry(_spec(prompts_root / "coding" / "kanban_board.json"), "system_prompt")):
        out["coding"] = e
    for task, fname in [
        ("tool_use", "tool_use.json"),
        ("instruction_following", "instruction_following.json"),
        ("hallucination", "hallucination.json"),
        ("nonsense", "nonsense.json"),
        ("comprehension", "comprehension.json"),
        ("summarization", "summarization.json"),
    ]:
        if (e := _entry(_spec(prompts_root / fname))):
            out[task] = e
    if (e := _entry(_spec(prompts_root / "comprehension.json"))):
        out["long_context"] = e
    if (e := _entry(_spec(prompts_root / "vision" / "diagram_to_mermaid.json"))):
        out["diagram_to_mermaid"] = e
    if (e := _entry(_spec(prompts_root / "vision" / "diagram_to_svg.json"))):
        out["diagram_to_svg"] = e
    if (e := _entry(_spec(prompts_root / "vision" / "handwriting_easy.json"))):
        out["vision"] = e
    # NIAH builds the prompt at runtime (see NIAHTask). Two-turn flow:
    # ask for a summary first, then the needle questions in the same chat.
    # NOTE: the actual prompt sent to the model is German (this is a display
    # template only). The benchmarks were run with German prompts.
    niah_template = (
        "TURN 1 (User):\n"
        "The following section contains a longer mixed text of German "
        "narrative and source code.\n\n"
        "===== TEXT BEGIN =====\n"
        "<corpus with embedded needles, 32k–128k tokens depending on stage>\n"
        "===== TEXT END =====\n\n"
        "Summarise the text in 3-5 sentences. Mention the main "
        "characters, setting and key themes.\n\n"
        "TURN 2 (User, same chat context):\n"
        "Now answer the following questions strictly from the text shown "
        "above — invent nothing, add nothing and do not rely on general "
        "knowledge.\n\n"
        "Questions:\n"
        "1. <question for needle 1>\n"
        "2. <question for needle 2>\n"
        "...\n\n"
        "Answer as a numbered list 1., 2., 3. with one short sentence each."
    )
    niah_deep_template = niah_template.replace("32k–128k", "32k–200k")
    out["niah"] = {"system": "", "user": niah_template}
    out["niah_deep"] = {"system": "", "user": niah_deep_template}
    from ..tasks.context_growth import SYSTEM_PROMPT as _CG_SYS, USER_TEMPLATE as _CG_USR
    out["context_growth"] = {
        "system": _CG_SYS,
        "user": _CG_USR.format(
            chunk="<3-4 sentences from the book corpus, a new chunk per turn>"
        ),
    }
    return out


def _effective_score(result: TaskResult) -> float | None:
    """Score-Anpassung nach Judge-Lauf. Im Zweifel zählt der Judge.

    - coding: gewichteter Blend aus 5% statisch, 20% E2E, 75% Judge.
      Innerhalb des Judge-Scores zählt design_quality doppelt.
    - diagram_to_svg: 15% SVG-Validität, 15% Begriffs-Abdeckung, 70% Judge.
      Innerhalb des Judge-Scores zählt aesthetic_quality doppelt.
    - hallucination: Judge ersetzt Regex-Score komplett.
    - niah: Pro Stage ersetzt der Summary-Judge den Regex-Summary-Score; combined
      wird mit den ursprünglichen Komponentengewichten neu berechnet, Mittelwert
      über alle Stages.
    Ohne Judge bleibt jeweils der ursprüngliche Score.
    """
    if result.score is None or result.error is not None:
        return result.score
    sb = result.score_breakdown or {}
    judge = sb.get("judge") or {}
    judge_score = judge.get("judge_score")

    if result.task == "coding":
        lint = sb.get("lint_score")
        e2e = sb.get("e2e_score") if sb.get("e2e_total") else None
        weighted_parts = [
            (lint, 0.05),
            (e2e, 0.20),
            (judge_score, 0.75),
        ]
        present = [(float(value), weight) for value, weight in weighted_parts if value is not None]
        if not present:
            return result.score
        weight_sum = sum(weight for _, weight in present)
        return sum(value * weight for value, weight in present) / weight_sum

    if result.task == "diagram_to_svg":
        # Per-diagram blend:
        # - 15% SVG validity
        # - 15% required-term coverage
        # - 70% visual judge
        # Falls back to the original deterministic score until a judge exists.
        diagrams = sb.get("diagrams") or []
        if not diagrams:
            return result.score
        per: list[float] = []
        any_judge = False
        for d in diagrams:
            if d.get("error"):
                continue
            grade = d.get("grade") or {}
            valid = (
                grade.get("parsed")
                and grade.get("has_root")
                and (grade.get("element_count") or 0) >= 5
                and (grade.get("text_count") or 0) >= 1
            )
            validity = 1.0 if valid else 0.0
            matched = len(grade.get("matched_terms") or [])
            missing = len(grade.get("missing_terms") or [])
            coverage = matched / (matched + missing) if (matched + missing) else 1.0
            dj = (d.get("judge") or {}).get("judge_score")
            if dj is not None:
                any_judge = True
                per.append(0.15 * validity + 0.15 * coverage + 0.70 * float(dj))
            elif d.get("score") is not None:
                per.append(float(d["score"]))
        if not per:
            return result.score
        return sum(per) / len(per) if any_judge else result.score

    if result.task == "hallucination":
        if judge_score is None:
            return result.score
        return float(judge_score)

    if result.task == "niah":
        lengths = sb.get("lengths") or []
        components = sb.get("score_components") or {}
        w_sum = float(components.get("summary", 0.2))
        w_ret = float(components.get("needle_retrieval", 0.5))
        w_comp = float(components.get("comprehension", 0.3))
        any_judge = False
        per_stage_combined: list[float] = []
        for L in lengths:
            if L.get("skipped") or L.get("error"):
                continue
            stage_js = (L.get("judge") or {}).get("judge_score")
            if stage_js is not None:
                any_judge = True
                summary = float(stage_js)
            else:
                summary = float(L.get("summary_score") or 0.0)
            retrieval = float(L.get("retrieval_score") or 0.0)
            comp_judge = (L.get("comprehension_judge") or {}).get("judge_score")
            if comp_judge is not None:
                any_judge = True
                comp = float(comp_judge)
            elif "comprehension_score" in L:
                comp = float(L.get("comprehension_score") or 0.0)
            else:
                comp = None
            if comp is not None:
                combined = summary * w_sum + retrieval * w_ret + comp * w_comp
            else:
                combined = summary * 0.3 + retrieval * 0.7
            per_stage_combined.append(combined)
        if not any_judge or not per_stage_combined:
            return result.score
        return sum(per_stage_combined) / len(per_stage_combined)

    return result.score


def _overall_weight(task: str) -> float:
    if task == "coding":
        return 2.0
    if task == "tool_use":
        return 0.5
    return 1.0


def _overall_score(cells: dict[str, dict]) -> tuple[float | None, float]:
    total = 0.0
    weight_sum = 0.0
    for task, cell in cells.items():
        score = cell.get("score")
        if score is None or cell.get("error") or cell.get("preliminary"):
            continue
        weight = _overall_weight(task)
        total += float(score) * weight
        weight_sum += weight
    if weight_sum == 0:
        return None, 0.0
    return total / weight_sum, weight_sum


def _is_preliminary(result: TaskResult) -> bool:
    """True wenn ein Judge-Lauf für diese Task fehlt (Score also nur regex/linter).

    - coding: kein judge_score in score_breakdown.judge
    - hallucination: kein judge_score in score_breakdown.judge
    - niah: keine einzige Stage hat einen lengths[i].judge.judge_score
    - andere Tasks: nie preliminary (kein Judge vorgesehen)
    """
    if result.error is not None or result.score is None:
        return False
    sb = result.score_breakdown or {}
    if result.task in ("coding", "hallucination"):
        judge = sb.get("judge") or {}
        return judge.get("judge_score") is None
    if result.task == "diagram_to_svg":
        for d in sb.get("diagrams") or []:
            if d.get("error"):
                continue
            if (d.get("judge") or {}).get("judge_score") is not None:
                return False
        return True
    if result.task == "niah":
        for L in sb.get("lengths") or []:
            if L.get("skipped") or L.get("error"):
                continue
            if (L.get("judge") or {}).get("judge_score") is not None:
                return False
        return True
    return False


def _row(result: TaskResult, meta: ModelMeta) -> dict[str, Any]:
    """Common table-row dict used across benchmark templates."""
    m = result.model_info
    eff = _effective_score(result)
    return {
        "model_id": result.model_id,
        "vendor": vendor(m),
        "color_key": _color_vendor_key(m, result.model_id),
        "params_b": _params_b(result.model_id, m, meta),
        "is_moe": _is_moe(m, result.model_id, meta),
        "type": m.type,
        "quant": pretty_quant(m.compatibility_type, m.quantization),
        "ctx_k": m.max_context_length // 1024,
        "released": meta.released(m) or "",
        "ram_mb": result.metrics.peak_rss_mb,
        "tps": result.metrics.tokens_per_second,
        "ttft_ms": result.metrics.time_to_first_token_ms,
        "wall_s": result.metrics.wall_seconds,
        "tokens": result.metrics.tokens_generated,
        "score": eff,
        "preliminary": _is_preliminary(result),
        "lint_score": result.score if result.task == "coding" else None,
        "score_breakdown": result.score_breakdown,
        "artifacts": result.artifacts,
        "error": result.error,
    }


def _artifact_url(art_path: str) -> str:
    """Path of an artifact file relative to a sub-page (e.g. /benchmarks/)."""
    if art_path.startswith("artifacts/"):
        return f"../assets/artifacts/{art_path.removeprefix('artifacts/')}"
    if art_path.startswith("assets/niah/haystack_"):
        return f"../assets/haystacks/{Path(art_path).name}"
    if art_path.startswith("assets/long_text/"):
        return f"../assets/long_text/{Path(art_path).name}"
    return f"../{art_path}"


def _model_detail_url(model_id: str) -> str:
    return f"../models/{safe_model_id(model_id)}.html"


def build_site(
    store: BenchStore,
    out_dir: Path,
    model_meta_path: Path,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "benchmarks").mkdir(exist_ok=True)
    (out_dir / "models").mkdir(exist_ok=True)
    (out_dir / "assets").mkdir(exist_ok=True)
    for stale in [*(out_dir / "benchmarks").glob("*.html"), *(out_dir / "models").glob("*.html")]:
        stale.unlink()

    meta = ModelMeta(model_meta_path)
    env = _env()
    env.globals["artifact_url"] = _artifact_url
    env.globals["model_detail_url"] = _model_detail_url

    system_prompts = _load_system_prompts(store.root / "prompts")

    # Backfill mermaid render PNGs so the bench-overview can show hover
    # previews. Best-effort — needs internet for the mermaid CDN.
    _ensure_mermaid_renders(store)

    # Mirror artefact tree.
    _copy_artifacts(store, out_dir)

    task_names = store.task_names()
    tabs = benchmark_labels(task_names)

    # Per-benchmark stats for the landing page.
    landing_cards = []
    for tname, label in tabs:
        results = store.all_for_task(tname)
        if not results:
            continue
        # Preliminary (lint-only Coding-Scores ohne Judge) bleiben aus best_score raus.
        scores = [
            s
            for r in results
            if r.error is None and not _is_preliminary(r)
            for s in [_effective_score(r)]
            if s is not None
        ]
        speeds = [
            r.metrics.tokens_per_second
            for r in results
            if r.error is None and r.metrics.tokens_per_second
        ]
        landing_cards.append(
            {
                "name": tname,
                "label": label,
                "model_count": len(results),
                "best_score": max(scores) if scores else None,
                "median_speed": sorted(speeds)[len(speeds) // 2] if speeds else None,
                "description": _BENCH_DESCRIPTIONS.get(tname, ""),
            }
        )

    # Build per-model aggregate row for the overview table.
    overall_rows = []
    for model_id in store.all_known_models():
        results = store.all_for_model(model_id)
        if not results:
            continue
        info = results[0].model_info
        scores_per_task = {r.task: r for r in results}
        total_wall = sum(r.metrics.wall_seconds for r in results)
        ram_mb = next(
            (r.metrics.peak_rss_mb for r in results if r.metrics.peak_rss_mb),
            None,
        )
        # task name → score (None on error/missing)
        cells: dict[str, dict] = {}
        for tname, _ in tabs:
            r = scores_per_task.get(tname)
            if r is None:
                cells[tname] = {"score": None, "error": None, "wall": None, "preliminary": False}
            else:
                cells[tname] = {
                    "score": _effective_score(r),
                    "error": r.error,
                    "wall": r.metrics.wall_seconds,
                    "preliminary": _is_preliminary(r),
                }
        overall_score, overall_weight = _overall_score(cells)
        overall_rows.append(
            {
                "model_id": model_id,
                "vendor": vendor(info),
                "color_key": _color_vendor_key(info, model_id),
                "params_b": _params_b(model_id, info, meta),
                "is_moe": _is_moe(info, model_id, meta),
                "type": info.type,
                "quant": pretty_quant(info.compatibility_type, info.quantization),
                "ctx_k": info.max_context_length // 1024,
                "released": meta.released(info) or "",
                "ram_mb": ram_mb,
                "total_wall": total_wall,
                "overall_score": overall_score,
                "overall_weight": overall_weight,
                "cells": cells,
            }
        )
    overall_rows.sort(key=lambda r: -(r["overall_score"] if r["overall_score"] is not None else -1))
    overall_scatter_rows = []
    for r in overall_rows:
        if r["overall_score"] is None or r["total_wall"] <= 0:
            continue
        weights_gb, kv_gb, total_gb = _ram_estimate(r["ram_mb"])
        overall_scatter_rows.append(
            {
                "model_id": r["model_id"],
                "short_id": short_model_id(r["model_id"]),
                "vendor": r["vendor"],
                "color_key": r["color_key"],
                "params_b": r["params_b"],
                "is_moe": r["is_moe"],
                "quant": r["quant"],
                "score": r["overall_score"],
                "wall_s": r["total_wall"],
                "ram_gb": (r["ram_mb"] / 1024) if r["ram_mb"] else None,
                "weights_gb": weights_gb,
                "kv_estimate_gb": kv_gb,
                "total_ram_gb": total_gb,
                "color": _vendor_color(r["color_key"]),
                "label_color": _label_color_for(r["color_key"]),
            }
        )

    # Landing page.
    landing = env.get_template("index.html").render(
        cards=landing_cards,
        tabs=tabs,
        all_models=store.all_known_models(),
        overall_rows=overall_rows,
        overall_scatter_rows=overall_scatter_rows,
        overall_scatter_rows_json=json.dumps(overall_scatter_rows, ensure_ascii=False),
    )
    (out_dir / "index.html").write_text(landing)

    # Per-benchmark pages.
    bench_tpl = env.get_template("benchmark.html")
    for tname, label in tabs:
        results = store.all_for_task(tname)
        rows = sorted(
            (_row(r, meta) for r in results),
            key=lambda x: (
                -(x["score"] if x["score"] is not None else -1),
                -(x["tps"] or 0),
            ),
        )
        scatter_rows = (
            _build_task_scatter(rows) if tname in SCATTER_BENCHES else []
        )
        page = bench_tpl.render(
            task_name=tname,
            task_label=label,
            tabs=tabs,
            current_tab=tname,
            rows=rows,
            description=_BENCH_DESCRIPTIONS.get(tname, ""),
            explainer=_BENCH_EXPLAINERS.get(tname, ""),
            prompt=system_prompts.get(tname),
            scatter_rows=scatter_rows,
            scatter_rows_json=json.dumps(scatter_rows, ensure_ascii=False),
        )
        (out_dir / "benchmarks" / f"{tname}.html").write_text(page)

    # Per-model detail pages.
    detail_tpl = env.get_template("model_detail.html")
    for model_id in store.all_known_models():
        results = store.all_for_model(model_id)
        if not results:
            continue
        info = results[0].model_info
        ordered = sorted(
            results,
            key=lambda r: [t for t, _ in tabs].index(r.task)
            if r.task in [t for t, _ in tabs]
            else 999,
        )
        # Blend judge into displayed score for coding tasks.
        ordered = [
            r.model_copy(update={"score": _effective_score(r)}) for r in ordered
        ]
        total_wall = sum(r.metrics.wall_seconds for r in ordered)
        peak_ram_mb = max(
            (r.metrics.peak_rss_mb for r in ordered if r.metrics.peak_rss_mb),
            default=None,
        )
        params_b = _params_b(model_id, info, meta)
        active_raw = meta.active_params_b(info)
        active_b = (
            None if active_raw is None
            else (int(active_raw) if float(active_raw).is_integer() else active_raw)
        )
        is_moe = _is_moe(info, model_id, meta)
        vkey = _color_vendor_key(info, model_id)
        page = detail_tpl.render(
            model_id=model_id,
            model_info=info,
            vendor=vendor(info),
            vendor_color=_vendor_color(vkey),
            vendor_label_dark=_vendors().label_dark(vkey),
            params_b=params_b,
            active_params_b=active_b,
            is_moe=is_moe,
            released=meta.released(info),
            results=ordered,
            tabs=tabs,
            explanations=_BENCH_EXPLAINERS,
            system_prompts=system_prompts,
            total_wall=total_wall,
            peak_ram_mb=peak_ram_mb,
        )
        (out_dir / "models" / f"{safe_model_id(model_id)}.html").write_text(page)

    return (out_dir / "index.html").resolve()


_BENCH_EXPLAINERS = {
    "coding": (
        "Task: From a ~200-word prompt the model must generate a fully "
        "functional Kanban board as a single-file HTML with drag & drop, "
        "localStorage persistence, edit/delete and a confetti animation — "
        "in a single chat without iteration. The prompt also includes a "
        "small `data-testid` contract so a Playwright test can drive the "
        "app remotely.\n\n"
        "Three signals feed into the score:\n"
        "(1) Static — a linter checks concrete constraints in the HTML "
        "(columns, Tailwind, localStorage call, no framework, no "
        "window.alert/prompt, …).\n"
        "(2) Functional — Playwright runs a small CRUD sequence: create a "
        "card, delete a card with confirmation, reload — does state "
        "persist? — and checks whether any JS console errors occur during "
        "the entire flow. Drag & drop and confetti are deliberately not "
        "tested functionally (too many implementation variants).\n"
        "(3) Qualitative — LLM-as-judge rates screenshot and code "
        "(visual + code quality + render↔code consistency).\n\n"
        "Score = mean over the available signals.\n\n"
        "Why models fail: reasoning models burn their tokens in thinking "
        "instead of writing. Sliding-window models (Gemma 4) lose the "
        "constraints at the start of the prompt. Small models (<3B) often "
        "fail to produce coherent HTML — or ignore the data-testid "
        "contract, which makes the functional tests fail in droves."
    ),
    "niah": (
        "Task: In a German book corpus (with embedded source code) 10 "
        "synthetic facts are hidden at evenly distributed depths "
        "(5% – 95%). The model must retrieve all of them.\n\n"
        "Flow — THREE turns in the same chat context (prefill only once):\n"
        "Turn 1 — corpus summary: model receives the long corpus and "
        "summarises it in 3-5 sentences. Forces real processing of the "
        "text.\n"
        "Turn 2 — needle retrieval: same conversation, now the questions "
        "for the 10 hidden facts.\n"
        "Turn 3 — comprehension + hallucination traps: 4 factual questions "
        "about the book content + 3 hallucination traps (model should "
        "recognise that the question is NOT answered in the text).\n\n"
        "Default mode runs ONE uniform stage for all models: 120k tokens. "
        "Models without sufficient max_context are skipped at this stage. "
        "`niah_deep` additionally runs 32k / 64k / 200k for a full heatmap.\n\n"
        "Score weighting: summary 20% + needle retrieval 50% + "
        "comprehension/hallucination resistance 30%.\n\n"
        "Why models fail: sliding-window attention (Gemma 4) only sees "
        "the last 1-2k tokens sharply. Reasoning models hit the token "
        "limit before answering. Q4 KV cache measurably degrades recall "
        "at long contexts. On the hallucination traps the helpful bias "
        "lures models into plausible-sounding inventions."
    ),
    "vision": (
        "Task: Four sub-tasks, 1 image each. (1) handwritten meeting note "
        "easy/medium/hard to read — model must transcribe the text. "
        "(2) book page in Fraktur typeface — same task.\n\n"
        "What is tested: OCR quality, recognising layout structure "
        "(columns, bullet points, dates), handling of illegible "
        "handwriting.\n\n"
        "Why models fail: text-only models have no vision capability "
        "(filtered out). Weak VLMs only recognise the clearest part. "
        "Some truncate output or get stuck in reasoning without a visible "
        "answer."
    ),
    "long_context": (
        "Task: The entire book (~150k tokens) is processed in ONE chat. "
        "The model produces: (1) a 4-sentence summary, (2) answers to 7 "
        "comprehension questions (4 real facts + 3 hallucination traps).\n\n"
        "What is tested: long-context understanding (different from NIAH "
        "retrieval): relationships between characters, plotlines, "
        "settings. Plus hallucination resistance: does the model invent "
        "an uncle when none appears in the book?\n\n"
        "Why models fail: 150k tokens don't fit into every context "
        "(min ~195k loaded ctx). Small models fail at the synthesis "
        "across 5 chapters. Reasoning models often time out during "
        "prompt processing."
    ),
    "diagram_to_svg": (
        "Task: Photo of a hand-drawn diagram (architecture, sequence, "
        "quadrant matrix) → model must produce an inline-SVG "
        "representation of the same diagram.\n\n"
        "Two score signals:\n"
        "(1) Deterministic — SVG is parseable, has an <svg> root, enough "
        "elements and at least one <text>; all expected terms (boxes, "
        "labels) appear in the text content. Validity and term coverage "
        "each count for 15% of the final score.\n"
        "(2) Qualitative — the `diagram-svg-judge` skill screenshots the "
        "SVG and visually compares it to the original along fixed axes "
        "(completeness, connections, arrow direction, grouping, layout "
        "readability, diagram-type fidelity, aesthetics). The judge "
        "counts 70%; aesthetics is double-weighted within the judge.\n\n"
        "Why models fail: SVG generation requires spatial reasoning "
        "(positioning boxes, computing paths, setting viewBox) — "
        "noticeably harder than declarative Mermaid syntax. Weak VLMs "
        "often produce only an empty <svg> or an element salad without "
        "topology."
    ),
    "diagram_to_mermaid": (
        "Task: Photo of a hand-drawn diagram (architecture, sequence, "
        "quadrant matrix) → model must produce equivalent Mermaid "
        "syntax.\n\n"
        "What is tested: structural diagram understanding and translation "
        "into a formal notation. Score: parseable Mermaid (starts with "
        "flowchart/graph/sequenceDiagram/etc.). The report renders the "
        "generated Mermaid live with mermaid.js.\n\n"
        "Why models fail: model writes an explanation instead of just "
        "Mermaid. Complex layouts (quadrant) often fail."
    ),
    "instruction_following": (
        "Task: A prompt with 8 explicit, partly conflicting constraints "
        "(paragraph count, word count, must start with 'Listen:', no "
        "letter 'q', the word 'Stahl' must appear, no Markdown, JSON "
        "appendix).\n\n"
        "What is tested: following multiple conditions simultaneously. "
        "Each condition is verified automatically.\n\n"
        "Why models fail: reasoning models often ignore small "
        "constraints. Small models can't reliably count words. "
        "Conditional constraints (no 'q') are generally hard for LLMs "
        "without a reasoning phase."
    ),
    "hallucination": (
        "Task: 11 questions with subtle, plausible-sounding but factually "
        "false premises (e.g. 'Which album did Tocotronic release in "
        "1991?' — the band was only formed in 1993).\n\n"
        "What is tested: does the model recognise the false premise "
        "('corrected'), admit it doesn't know ('abstained'), or invent a "
        "plausible-sounding answer ('fabricated')?\n\n"
        "Why models fail: training bias toward helpfulness encourages "
        "plausible hallucinations. Small models have weaker factual "
        "grounding. Subtle questions about personal details (e.g. "
        "politicians' children) are especially tempting to make up."
    ),
    "nonsense": (
        "Task: 8 deliberately absurd category-error questions (e.g. "
        "'What key is the word Thursday in?', 'How much sleep does a "
        "contract need?').\n\n"
        "What is tested: does the model recognise that the question "
        "itself is nonsensical, or does it play along and invent an "
        "answer? Heuristic checks for pushback phrases (nonsensical, "
        "category error, not a physical object, etc.).\n\n"
        "Why models fail: helpful bias leads to pseudo-answers. Small "
        "models miss the category errors. Some models answer "
        "metaphorically — which can pass as pushback."
    ),
    "context_growth": (
        "Task: 20 consecutive echo turns in the same chat. Per turn the "
        "bench sends a chunk of 3–4 book sentences, the model must "
        "return that exact text. The reply is added to history — the "
        "conversation context grows monotonically.\n\n"
        "What is tested: (1) how tokens/s evolves as the context fills "
        "up — context-window size stays fixed, only the content grows. "
        "(2) Whether the model handles the simple echo task reliably "
        "across all 20 turns without commenting, abridging or rephrasing "
        "at some point.\n\n"
        "Score = fraction of normalised-matching responses. "
        "Slowdown % = relative loss between turn 1 and turn 20.\n\n"
        "Why models fail: some models add Markdown or quotes, prepend "
        "'Here is the text:' or abridge slightly. At small contexts "
        "cache eviction kicks in; on large models and Apple Silicon the "
        "typical speed decay from a growing KV-cache shows up."
    ),
    "tool_use": (
        "Task: 3 scenarios (easy/medium/hard) with mocked tools "
        "(read_file, apply_diff, get_weather, list_files). The model has "
        "to call the right tools in the right order and answer at the "
        "end. Multi-turn (tool result comes back, model can call further "
        "tools).\n\n"
        "What is tested: OpenAI-style function calling, argument "
        "correctness, ordering on multi-step tasks (read file → parse "
        "JSON → query weather).\n\n"
        "Why models fail: models without 'tool_use' capability ignore "
        "tool schemas. Weak models pick wrong tools or malformed "
        "arguments. The hard tier (JSON-format output) often breaks "
        "during the final synthesis."
    ),
    "comprehension": (
        "Standalone variant of the comprehension portion of long_context. "
        "Identical 7 questions, but without the summary. Opt-in only via "
        "--tasks comprehension."
    ),
    "summarization": (
        "Standalone summary variant. Opt-in only via --tasks summarization."
    ),
    "niah_deep": (
        "NIAH with ALL four context stages (32k/64k/128k/200k) for a "
        "full heatmap. Default `niah` runs only the largest stage and "
        "is significantly faster."
    ),
}

_BENCH_DESCRIPTIONS = {
    "coding": (
        "Single-shot code generation (Kanban board as an HTML file). Measures how "
        "fast and how functionally models solve a concrete UI task. Hovering over "
        "a model row shows a screenshot of the rendered app."
    ),
    "niah": (
        "Three sub-benchmarks in one chat (prefill only once): corpus summary · "
        "needle retrieval (10 hidden facts in 120k tokens — uniform across all "
        "models) · comprehension questions + hallucination traps about the "
        "actual book content."
    ),
    "vision": (
        "Three sub-tests for vision-capable models: handwriting OCR from a "
        "notebook, OCR of an old book page (Fraktur), and comparison/aggregation "
        "of three sketchnote photos."
    ),
    "diagram_to_svg": (
        "An image of a diagram (architecture, flowchart, sequence, quadrant) is "
        "produced by the model directly as inline SVG. The original image and "
        "the SVG render sit side-by-side in the report — visual comparability "
        "without an external render engine. Score = 15% SVG validity, 15% term "
        "coverage and 70% `diagram-svg-judge`; aesthetics is double-weighted "
        "within the judge."
    ),
    "diagram_to_mermaid": (
        "Optional older bench: model emits Mermaid code that is rendered live "
        "with mermaid.js. Disabled by default; still runnable via `--tasks "
        "diagram_to_mermaid`. Structural scoring (required_edges, "
        "required_groups, kind)."
    ),
    "comprehension": (
        "Content-comprehension questions about a ~150k-token German book. "
        "Unlike NIAH, no synthetic facts are retrieved here; instead "
        "relationships, settings and plot connections are tested."
    ),
    "summarization": (
        "The entire book (~150k tokens) must be summarised in EXACTLY four "
        "sentences — tests both long-context understanding and the ability to "
        "honour hard length constraints."
    ),
    "long_context": (
        "Combined long-context test: in a single chat the model produces a "
        "4-sentence summary of the book AND answers 7 comprehension questions "
        "(4 facts + 3 hallucination traps). Saves the second 150k prompt "
        "processing per model."
    ),
    "niah_deep": (
        "NIAH with all four context stages (32k/64k/128k/200k) — full heatmap. "
        "Default `niah` runs only the largest stage the model can handle and "
        "is significantly faster."
    ),
    "tool_use": (
        "Three tasks with OpenAI-style function calling: easy (read notes.md), "
        "medium (fix a FizzBuzz bug via unified diff), hard (combine "
        "config.json + weather mock). The model has to pick tools, supply "
        "correct arguments and format the final result correctly."
    ),
    "instruction_following": (
        "Complex prompt with eight explicit constraints (word count, format, "
        "forbidden letters, JSON appendix). Automatic per-constraint validation."
    ),
    "hallucination": (
        "Questions with fabricated premises. Does the model invent an answer, "
        "say 'I don't know', or correct the premise?"
    ),
    "nonsense": (
        "Deliberately absurd nonsense questions. Does the model recognise the "
        "nonsense, or play along?"
    ),
    "context_growth": (
        "Echo loop over 20 turns: 3–4 book sentences in per step; the bench "
        "measures how tokens/s and answer fidelity evolve as the chat context "
        "grows. Produces a per-model trend chart."
    ),
}
