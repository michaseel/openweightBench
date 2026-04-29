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
    ("hallucination", "Halluzination"),
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
    # NIAH baut den Prompt zur Laufzeit (siehe NIAHTask). Zwei-Turn-Flow:
    # erst Zusammenfassung anfragen, dann im selben Chat die Needle-Fragen.
    niah_template = (
        "TURN 1 (User):\n"
        "Im folgenden Abschnitt befindet sich ein längerer Mischtext aus "
        "deutschsprachiger Erzählung und Quellcode.\n\n"
        "===== TEXT BEGINN =====\n"
        "<korpus mit eingestreuten needles, je nach stage 32k–128k tokens>\n"
        "===== TEXT ENDE =====\n\n"
        "Fasse den Inhalt des Textes in 3-5 Sätzen zusammen. Nenne "
        "Hauptfiguren, Schauplatz und die wichtigsten Themen.\n\n"
        "TURN 2 (User, im selben Chat-Kontext):\n"
        "Beantworte jetzt die folgenden Fragen ausschließlich anhand des "
        "oben gezeigten Textes — erfinde nichts, ergänze nichts und "
        "übernehme keine Allgemeinwissen-Annahmen.\n\n"
        "Fragen:\n"
        "1. <frage zu needle 1>\n"
        "2. <frage zu needle 2>\n"
        "...\n\n"
        "Antworte als nummerierte Liste 1., 2., 3. mit jeweils einem kurzen Satz."
    )
    niah_deep_template = niah_template.replace("32k–128k", "32k–200k")
    out["niah"] = {"system": "", "user": niah_template}
    out["niah_deep"] = {"system": "", "user": niah_deep_template}
    from ..tasks.context_growth import SYSTEM_PROMPT as _CG_SYS, USER_TEMPLATE as _CG_USR
    out["context_growth"] = {
        "system": _CG_SYS,
        "user": _CG_USR.format(
            chunk="<3-4 Sätze aus dem Buchkorpus, pro Turn ein neuer Chunk>"
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
        "Aufgabe: Aus einem ~200-Wörter-Prompt soll das Modell ein voll "
        "funktionales Kanban-Board als single-file HTML mit Drag & Drop, "
        "localStorage-Persistenz, Edit/Delete und Confetti-Animation generieren — "
        "in einem einzigen Chat ohne Iteration. Der Prompt enthält zusätzlich "
        "einen kleinen `data-testid`-Vertrag, damit ein Playwright-Test die "
        "App ferngesteuert bedienen kann.\n\n"
        "Drei Signale fließen in den Score ein:\n"
        "(1) Statisch — Linter prüft konkrete Constraints im HTML "
        "(Spalten, Tailwind, localStorage-Aufruf, kein Framework, kein "
        "window.alert/prompt, …).\n"
        "(2) Funktional — Playwright fährt eine kleine CRUD-Sequenz: Karte "
        "anlegen, Karte löschen mit Bestätigung, Reload — bleibt der Zustand "
        "erhalten? — und prüft, ob während des gesamten Flows JS-Konsolen-"
        "Fehler auftreten. Drag & Drop und Confetti werden bewusst nicht "
        "funktional getestet (zu viele Implementierungsvarianten).\n"
        "(3) Qualitativ — LLM-as-Judge bewertet Screenshot und Code "
        "(Visual + Codequalität + Konsistenz Render↔Code).\n\n"
        "Score = Mittelwert über die vorhandenen Signale.\n\n"
        "Warum Modelle scheitern: Reasoning-Modelle verbrauchen ihre Token "
        "im Denken statt im Schreiben. Sliding-Window-Modelle (Gemma 4) "
        "verlieren Constraints am Anfang des Prompts. Kleine Modelle (<3B) "
        "produzieren oft kein zusammenhängendes HTML — oder ignorieren den "
        "data-testid-Vertrag, was die funktionalen Tests reihenweise rot "
        "färbt."
    ),
    "niah": (
        "Aufgabe: In einem deutschen Buch-Korpus (mit eingestreutem Quellcode) "
        "werden 10 künstliche Fakten an gleichmäßig verteilten Tiefen "
        "(5% – 95%) versteckt. Das Modell muss alle wiederfinden.\n\n"
        "Ablauf — DREI Turns im selben Chat-Kontext (Prefill nur einmal):\n"
        "Turn 1 — Korpus-Zusammenfassung: Modell bekommt den langen Korpus "
        "und soll ihn in 3-5 Sätzen zusammenfassen. Erzwingt eine echte "
        "Verarbeitung des Textes.\n"
        "Turn 2 — Needle-Retrieval: gleiche Konversation, jetzt die "
        "Bedürfnisfragen für die 10 versteckten Fakten.\n"
        "Turn 3 — Verstehen + Halluzinations-Fallen: 4 Faktenfragen zum "
        "Buchinhalt + 3 Halluzinations-Fallen (Modell soll erkennen, dass "
        "die Frage im Text NICHT beantwortet wird).\n\n"
        "Default-Modus läuft EINE einheitliche Stufe für alle Modelle: "
        "120k Tokens. Modelle ohne ausreichendes max_context werden in dieser "
        "Stufe nicht ausgeführt. `niah_deep` läuft zusätzlich 32k / 64k / 200k "
        "für eine vollständige Heatmap.\n\n"
        "Score-Gewichtung: Zusammenfassung 20% + Needle-Retrieval 50% + "
        "Verstehen/Halluzinations-Resistenz 30%.\n\n"
        "Warum Modelle scheitern: Sliding-Window-Attention (Gemma 4) sieht "
        "nur die letzten 1-2k Tokens scharf. Reasoning-Modelle laufen auf "
        "Token-Limit, bevor sie antworten. Q4-KV-Cache verfälscht Recall "
        "messbar bei langen Kontexten. Bei den Halluzinations-Fallen "
        "verleitet der Helpful-Bias zu plausibel klingenden Erfindungen."
    ),
    "vision": (
        "Aufgabe: Vier Sub-Tasks, je 1 Bild. (1) handgeschriebene Meeting-Notiz "
        "leicht/mittel/schwer lesbar — Modell soll Text transkribieren. "
        "(2) Buchseite in Frakturschrift — gleiche Aufgabe.\n\n"
        "Was getestet wird: OCR-Qualität, Erkennen von Layout-Strukturen "
        "(Spalten, Bullet-Punkte, Datumsangaben), Umgang mit unleserlicher "
        "Schrift.\n\n"
        "Warum Modelle scheitern: Reine Text-Modelle haben keine Vision-"
        "Fähigkeit (werden gefiltert). Schwache VLMs erkennen nur den "
        "deutlichsten Teil. Manche bricht der Output ab oder geht ins "
        "Reasoning ohne sichtbare Antwort."
    ),
    "long_context": (
        "Aufgabe: Das gesamte Buch (~150k Tokens) wird in EINEM Chat "
        "verarbeitet. Modell liefert: (1) eine 4-Satz-Zusammenfassung des "
        "Inhalts, (2) Antworten auf 7 Verstehens-Fragen (4 echte Fakten "
        "+ 3 Halluzinations-Fallen).\n\n"
        "Was getestet wird: Long-Context-Verständnis (anders als NIAH-Retrieval): "
        "Beziehungen zwischen Figuren, Handlungsstränge, Schauplätze. Plus "
        "Halluzinations-Resistenz: erfindet das Modell einen Onkel, wenn "
        "im Buch keiner vorkommt?\n\n"
        "Warum Modelle scheitern: 150k Tokens passen nicht in jeden "
        "Kontext (min ~195k Loaded-Ctx). Kleine Modelle scheitern an der "
        "Synthese aus 5 Kapiteln. Reasoning-Modelle timeouten oft bei "
        "der Prompt-Verarbeitung."
    ),
    "diagram_to_svg": (
        "Aufgabe: Foto eines handgezeichneten Diagramms (Architektur, "
        "Sequenz, Quadrant-Matrix) → Modell soll eine inline-SVG-"
        "Repräsentation desselben Diagramms erzeugen.\n\n"
        "Zwei Score-Signale:\n"
        "(1) Deterministisch — SVG ist parsbar, hat <svg>-Wurzel, "
        "ausreichend Elemente und mindestens ein <text>; alle "
        "erwarteten Begriffe (Boxen, Beschriftungen) tauchen im "
        "Text-Content auf. Im Endscore zählen Validität und Begriffs-Abdeckung "
        "jeweils 15%.\n"
        "(2) Qualitativ — der `diagram-svg-judge`-Skill screenshottet "
        "die SVG und vergleicht sie visuell mit dem Original entlang "
        "fester Achsen (Vollständigkeit, Verbindungen, Pfeilrichtung, "
        "Gruppierung, Layout-Lesbarkeit, Diagrammtyp-Treue, Schönheit). "
        "Der Judge zählt 70%; Schönheit zählt im Judge doppelt.\n\n"
        "Warum Modelle scheitern: SVG-Generierung verlangt räumliches "
        "Denken (Boxen positionieren, Pfade berechnen, viewBox setzen) — "
        "das ist deutlich schwerer als deklarative Mermaid-Syntax. "
        "Schwache VLMs liefern oft nur einen leeren <svg> oder ein "
        "Element-Salat ohne Topologie."
    ),
    "diagram_to_mermaid": (
        "Aufgabe: Foto eines handgezeichneten Diagramms (Architektur, "
        "Sequenz, Quadrant-Matrix) → Modell soll äquivalente Mermaid-"
        "Syntax generieren.\n\n"
        "Was getestet wird: Strukturelles Diagramm-Verständnis und "
        "Übersetzung in eine formale Notation. Score: parseable Mermaid "
        "(beginnt mit flowchart/graph/sequenceDiagram/etc.). Im Report "
        "wird die generierte Mermaid live mit mermaid.js gerendert.\n\n"
        "Warum Modelle scheitern: Modell schreibt Erklärung statt nur "
        "Mermaid. Komplexe Layouts (Quadrant) schlagen oft fehl."
    ),
    "instruction_following": (
        "Aufgabe: Ein Prompt mit 8 expliziten, sich teilweise widersprechenden "
        "Constraints (Absatz-Anzahl, Wortzahl, beginnen mit 'Listen:', kein "
        "Buchstabe 'q', Stahl muss vorkommen, kein Markdown, JSON-Anhang).\n\n"
        "Was getestet wird: Befolgung mehrerer Bedingungen gleichzeitig. "
        "Jede Bedingung wird automatisch verifiziert.\n\n"
        "Warum Modelle scheitern: Reasoning-Modelle ignorieren oft kleine "
        "Constraints. Kleine Modelle können Wortzahlen nicht zuverlässig "
        "zählen. Conditional-Constraints (kein 'q') sind generell hart für "
        "LLMs ohne Reasoning-Phase."
    ),
    "hallucination": (
        "Aufgabe: 11 Fragen mit subtilen, plausibel klingenden, aber "
        "faktisch falschen Prämissen (z.B. 'Welches Album veröffentlichte "
        "Tocotronic 1991?' — Band wurde erst 1993 gegründet).\n\n"
        "Was getestet wird: Erkennt das Modell die falsche Prämisse "
        "('corrected'), gibt es zu, etwas nicht zu wissen ('abstained'), "
        "oder erfindet es eine plausibel klingende Antwort ('fabricated')?\n\n"
        "Warum Modelle scheitern: Trainings-Bias zur Hilfsbereitschaft "
        "verleitet zu plausiblen Halluzinationen. Kleine Modelle haben "
        "schlechtere Fakten-Verankerung. Subtile Fragen über "
        "Persönlichkeitsdetails (z.B. Politiker-Kinder) sind besonders "
        "verlockend zum Erfinden."
    ),
    "nonsense": (
        "Aufgabe: 8 absichtlich absurde Kategorienfehler-Fragen (z.B. "
        "'Welche Tonart hat das Wort Donnerstag?', 'Wie viel Schlaf "
        "benötigt ein Vertrag?').\n\n"
        "Was getestet wird: Erkennt das Modell, dass die Frage selbst "
        "sinnlos ist, oder spielt es mit und erfindet eine Antwort? "
        "Heuristik prüft auf Pushback-Phrasen (Sinnlos, kategorienfehler, "
        "kein physischer Gegenstand etc.).\n\n"
        "Warum Modelle scheitern: Helpful-Bias führt zu Pseudo-Antworten. "
        "Kleine Modelle übersehen die Kategorienfehler. Manche Modelle "
        "antworten metaphorisch — was als Pushback durchgehen kann."
    ),
    "context_growth": (
        "Aufgabe: 20 aufeinanderfolgende Echo-Turns im selben Chat. Pro "
        "Turn schickt der Bench einen Chunk aus 3–4 Buchsätzen, das Modell "
        "soll exakt diesen Text zurückgeben. Antwort wird in die Historie "
        "übernommen — der Konversationskontext wächst monoton.\n\n"
        "Was getestet wird: (1) Wie sich Tokens/s entwickelt, während der "
        "Kontext voller wird — die Kontext-Window-Größe bleibt fix, nur "
        "der Inhalt wächst. (2) Ob das Modell die simple Echo-Aufgabe über "
        "alle 20 Turns zuverlässig erfüllt, ohne irgendwann zu kommentieren, "
        "zu kürzen oder zu reformulieren.\n\n"
        "Score = Anteil normalisiert-übereinstimmender Antworten. "
        "Slowdown-% = relativer Verlust zwischen Turn 1 und Turn 20.\n\n"
        "Warum Modelle scheitern: Manche Modelle ergänzen Markdown- oder "
        "Anführungszeichen, fügen 'Hier ist der Text:' davor ein oder "
        "kürzen leicht ab. Bei kleinen Kontexten greift Cache-Eviction; "
        "bei großen Modellen und Apple Silicon zeigt sich der typische "
        "Speed-Verfall durch wachsende KV-Cache-Bewertung."
    ),
    "tool_use": (
        "Aufgabe: 3 Szenarien (leicht/mittel/schwer) mit gemockten Tools "
        "(read_file, apply_diff, get_weather, list_files). Modell muss "
        "die richtigen Tools in der richtigen Reihenfolge aufrufen und "
        "abschließend antworten. Multi-Turn (Tool-Result kommt zurück, "
        "Modell kann weitere Tools aufrufen).\n\n"
        "Was getestet wird: OpenAI-style function calling, Argument-"
        "Korrektheit, Reihenfolge bei Multi-Step-Aufgaben (Datei "
        "lesen → JSON parsen → Wetter abfragen).\n\n"
        "Warum Modelle scheitern: Modelle ohne 'tool_use'-Capability "
        "ignorieren Tool-Schemas. Schwache Modelle wählen falsche Tools "
        "oder fehlerhafte Argumente. Hard-Tier (JSON-Format-Output) "
        "bricht oft bei der Gesamt-Synthese."
    ),
    "comprehension": (
        "Eigenständige Variante des Verstehens-Teils von long_context. "
        "Identische 7 Fragen, aber ohne Summary. Nur opt-in via "
        "--tasks comprehension."
    ),
    "summarization": (
        "Eigenständige Summary-Variante. Nur opt-in via --tasks summarization."
    ),
    "niah_deep": (
        "NIAH mit ALLEN vier Kontextstufen (32k/64k/128k/200k) für eine "
        "vollständige Heatmap. Standard-`niah` läuft nur die größte "
        "Stufe und ist deutlich schneller."
    ),
}

_BENCH_DESCRIPTIONS = {
    "coding": (
        "Single-Shot Code-Generierung (Kanban-Board als HTML-Datei). Misst, wie "
        "schnell und wie funktional die Modelle eine konkrete UI-Aufgabe lösen. "
        "Hover über die Modell-Zeile zeigt einen Screenshot der gerenderten App."
    ),
    "niah": (
        "Drei Sub-Benchmarks in einem Chat (Prefill nur einmal): "
        "Korpus-Zusammenfassung · Needle-Retrieval (10 versteckte Fakten in "
        "120k Tokens — einheitlich für alle Modelle) · Verstehensfragen + "
        "Halluzinations-Fallen zum eigentlichen Buchinhalt."
    ),
    "vision": (
        "Drei Sub-Tests für Vision-fähige Modelle: Handschrift-OCR aus einem "
        "Notizbuch, OCR einer alten Buchseite (Fraktur) und Vergleich/Aggregation "
        "von drei Sketchnote-Fotos."
    ),
    "diagram_to_svg": (
        "Bild eines Diagramms (Architektur, Flowchart, Sequenz, Quadrant) wird "
        "vom Modell direkt als inline-SVG ausgegeben. Original-Bild und SVG-"
        "Render stehen im Report direkt nebeneinander — visuelle Vergleichbarkeit "
        "ohne externe Render-Engine. Score = 15% SVG-Validität, 15% Begriffs-"
        "Abdeckung und 70% `diagram-svg-judge`; Schönheit zählt im Judge doppelt."
    ),
    "diagram_to_mermaid": (
        "Optionaler älterer Bench: Modell gibt Mermaid-Code aus, der live mit "
        "mermaid.js gerendert wird. Standardmäßig deaktiviert; via `--tasks "
        "diagram_to_mermaid` weiterhin lauffähig. Strukturelles Scoring "
        "(required_edges, required_groups, kind)."
    ),
    "comprehension": (
        "Inhaltliche Verständnisfragen zu einem ~150k Tokens langen deutschen "
        "Buch. Anders als NIAH werden hier keine künstlichen Fakten retrieved, "
        "sondern Beziehungen, Schauplätze und Plot-Zusammenhänge geprüft."
    ),
    "summarization": (
        "Das gesamte Buch (~150k Tokens) muss in EXAKT vier Sätzen "
        "zusammengefasst werden — testet sowohl Long-Context-Verständnis als "
        "auch die Fähigkeit, harte Längenvorgaben einzuhalten."
    ),
    "long_context": (
        "Kombinierter Long-Context-Test: in einem einzigen Chat liefert das "
        "Modell eine 4-Satz-Zusammenfassung des Buchs UND beantwortet 7 "
        "Verstehens-Fragen (4 Fakten + 3 Halluzinations-Fallen). Spart pro "
        "Modell die zweite 150k-Prompt-Verarbeitung."
    ),
    "niah_deep": (
        "NIAH mit allen vier Kontextstufen (32k/64k/128k/200k) — vollständige "
        "Heatmap. Standard-`niah` läuft nur die größte für das Modell mögliche "
        "Stufe und ist deutlich schneller."
    ),
    "tool_use": (
        "Drei Aufgaben mit OpenAI-style Function-Calling: leicht (notes.md "
        "Lesen), mittel (FizzBuzz-Bug per unified-diff fixen), schwer "
        "(config.json + Wetter-Mock kombinieren). Modell muss Tools wählen, "
        "korrekte Argumente liefern und das Endergebnis korrekt formatieren."
    ),
    "instruction_following": (
        "Komplexer Prompt mit acht expliziten Constraints (Wortzahl, Format, "
        "Forbidden Letters, JSON-Anhang). Automatische Validierung pro Constraint."
    ),
    "hallucination": (
        "Fragen mit erfundenen Prämissen. Erfindet das Modell eine Antwort, sagt "
        "es 'weiß ich nicht', oder korrigiert es die Prämisse?"
    ),
    "nonsense": (
        "Bewusst absurde Quatsch-Fragen. Erkennt das Modell den Unsinn, oder "
        "spielt es mit?"
    ),
    "context_growth": (
        "Echo-Loop über 20 Turns: pro Schritt 3–4 Buchsätze rein, der Bench "
        "misst, wie Tokens/s und Antwort-Treue verlaufen, während der Chat-"
        "Kontext wächst. Liefert einen Verlaufs-Chart pro Modell."
    ),
}
