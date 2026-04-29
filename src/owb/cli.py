from __future__ import annotations

from pathlib import Path
from typing import Optional

import questionary
import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from .client.lmstudio import DEFAULT_BASE_URL, LMStudioClient
from .core.discovery import filter_models, load_allowlist, quant_bits, size_bucket
from .core.metadata import ModelMeta, release_age_bucket, vendor
from .core.results import BenchStore, ModelInfo
from .core.runner import Runner
from .tasks import DEFAULT_TASKS, all_tasks

ROOT = Path(__file__).resolve().parents[2]

# Project-level .env (e.g. OPENROUTER_API_KEY) — loaded once at import time.
load_dotenv(ROOT / ".env")

app = typer.Typer(
    add_completion=False,
    help="Open Weight Bench — local LLM benchmark for LM Studio.",
)
console = Console()

ALLOWLIST_PATH = ROOT / "models.allowlist.yaml"
MODEL_META_PATH = ROOT / "data" / "model_meta.json"
DOCS_DIR = ROOT / "docs"


def _client(base_url: str) -> LMStudioClient:
    return LMStudioClient(base_url=base_url)


# ----- owb models ---------------------------------------------------------


@app.command("models")
def models_cmd(
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    only_vlm: bool = typer.Option(False, "--vlm"),
    only_llm: bool = typer.Option(False, "--llm"),
) -> None:
    """List all models available in the local LM Studio instance."""
    meta = ModelMeta(MODEL_META_PATH)
    with _client(base_url) as c:
        models = c.list_models()
    models = filter_models(models, only_vlm=only_vlm, only_llm=only_llm)

    table = Table(show_lines=False)
    table.add_column("ID", overflow="fold")
    table.add_column("Vendor")
    table.add_column("Type")
    table.add_column("Params", justify="right")
    table.add_column("Quant")
    table.add_column("Ctx", justify="right")
    table.add_column("Released")
    for m in models:
        params = meta.params_b(m)
        table.add_row(
            m.id,
            vendor(m),
            m.type,
            f"{params:g}B" if params is not None else "—",
            f"{m.compatibility_type}/{m.quantization}" if m.quantization else m.compatibility_type or "—",
            f"{m.max_context_length // 1024}k" if m.max_context_length else "—",
            meta.released(m) or "—",
        )
    console.print(table)
    console.print(f"[dim]Total: {len(models)} models[/dim]")


# ----- owb status ---------------------------------------------------------


@app.command("status")
def status_cmd() -> None:
    """Show how many models have been benchmarked per task."""
    store = BenchStore(ROOT)
    if not store.task_names():
        console.print("[dim]No benchmarks yet. Run `owb bench`.[/dim]")
        return
    table = Table()
    table.add_column("Task")
    table.add_column("Models", justify="right")
    table.add_column("Latest result")
    for t in store.task_names():
        results = store.all_for_task(t)
        latest = max((r.completed_at for r in results), default=None)
        table.add_row(
            t,
            str(len(results)),
            latest.strftime("%Y-%m-%d %H:%M") if latest else "—",
        )
    console.print(table)


# ----- owb bench ----------------------------------------------------------


def _select_models_interactive(
    models: list[ModelInfo],
    meta: ModelMeta,
    variants: dict[str, list[str]] | None = None,
) -> list[ModelInfo]:
    variants = variants or {}
    filter_choice = questionary.select(
        "Welche Modelle vorfiltern?",
        choices=[
            "Alle",
            "Nur VLMs",
            "Nur LLMs (text-only)",
            "Nur MLX",
            "Custom (Filter kombinieren)",
        ],
    ).ask()
    if filter_choice is None:
        raise typer.Abort()

    quant_widths: list[int] | None = None

    if filter_choice == "Nur VLMs":
        pool = filter_models(models, only_vlm=True)
    elif filter_choice == "Nur LLMs (text-only)":
        pool = filter_models(models, only_llm=True)
    elif filter_choice == "Nur MLX":
        pool = filter_models(models, only_mlx=True)
    elif filter_choice == "Custom (Filter kombinieren)":
        buckets = questionary.checkbox(
            "Größenklassen (leer = alle)",
            choices=[
                questionary.Choice("nano (<2B)", "nano"),
                questionary.Choice("tiny (2-8B)", "tiny"),
                questionary.Choice("small (9-18B)", "small"),
                questionary.Choice("mid (19-40B)", "mid"),
                questionary.Choice("large (41-84B)", "large"),
                questionary.Choice("xlarge (85B+)", "xlarge"),
            ],
        ).ask()
        if buckets is None:
            raise typer.Abort()
        age_choices = questionary.checkbox(
            "Release-Alter (leer = alle)",
            choices=[
                questionary.Choice("Letzte 6 Monate", "recent"),
                questionary.Choice("7-12 Monate", "older"),
                questionary.Choice("13+ Monate", "ancient"),
                questionary.Choice("Ohne Release-Datum", "unknown"),
            ],
        ).ask()
        if age_choices is None:
            raise typer.Abort()
        quant_picks = questionary.checkbox(
            "Quantisierung (leer = alle)",
            choices=[
                questionary.Choice("4-bit", 4),
                questionary.Choice("6-bit", 6),
                questionary.Choice("8-bit", 8),
            ],
        ).ask()
        if quant_picks is None:
            raise typer.Abort()
        quant_widths = quant_picks or None

        pool = filter_models(models, size_buckets=buckets or None)
        if age_choices:
            ages = set(age_choices)
            pool = [m for m in pool if release_age_bucket(meta.released(m)) in ages]
    else:
        pool = [m for m in models if m.type != "embeddings"]

    if not pool:
        console.print("[red]Keine Modelle nach diesem Filter.[/red]")
        raise typer.Abort()

    # Pro Modell mit Mehrfach-Quants jede Variante als eigenen Eintrag listen.
    # Default-Variante = bare modelKey (kompatibel zu bestehenden Results),
    # Alternativen kommen als synthetische `<base>@<quant>`-Einträge hinzu.
    expanded: list[ModelInfo] = []
    for m in pool:
        expanded.append(m)
        for variant_id in variants.get(m.id, []):
            quant = variant_id.split("@", 1)[1] if "@" in variant_id else None
            expanded.append(m.model_copy(update={"id": variant_id, "quantization": quant}))

    # Quant-Filter erst auf das expandierte Set anwenden — sonst würden
    # Varianten verloren gehen, deren Bit-Breite vom Default-Quant abweicht.
    if quant_widths:
        widths = set(quant_widths)
        expanded = [m for m in expanded if quant_bits(m.quantization) in widths]

    if not expanded:
        console.print("[red]Keine Modelle nach diesem Filter.[/red]")
        raise typer.Abort()

    chosen = questionary.checkbox(
        f"Welche der {len(expanded)} Modelle benchmarken?",
        choices=[
            questionary.Choice(
                f"{m.id}  ({m.type}, {m.quantization or '?'}, {size_bucket(m.id)}, "
                f"ctx={m.max_context_length // 1024}k)",
                value=m.id,
            )
            for m in expanded
        ],
    ).ask()
    if not chosen:
        raise typer.Abort()
    by_id = {m.id: m for m in expanded}
    return [by_id[i] for i in chosen]


def _select_tasks_interactive(tasks_registry: dict) -> list:
    chosen = questionary.checkbox(
        "Welche Tests laufen lassen?",
        choices=[
            questionary.Choice(
                t.label or t.name,
                value=name,
                checked=name in DEFAULT_TASKS,
            )
            for name, t in tasks_registry.items()
        ],
    ).ask()
    if not chosen:
        raise typer.Abort()
    return [tasks_registry[name] for name in chosen]


@app.command("bench")
def bench_cmd(
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    models_arg: Optional[str] = typer.Option(
        None, "--models", "-m",
        help="Comma-separated model IDs (skip interactive prompt).",
    ),
    tasks_arg: Optional[str] = typer.Option(
        None, "--tasks", "-t",
        help="Comma-separated task names (skip interactive prompt).",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Re-run every selected (model, task) pair, even if a result exists.",
    ),
    rerun_models: Optional[str] = typer.Option(
        None, "--rerun-models",
        help="Comma-separated model IDs to forcibly rerun (others stay cached).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
    auto_judge: bool = typer.Option(
        True, "--auto-judge/--no-auto-judge",
        help="Nach jedem Task den passenden Judge via OpenRouter aufrufen (default an).",
    ),
) -> None:
    """Run benchmarks. Skips (model, task) pairs that already have a result."""
    store = BenchStore(ROOT)
    meta = ModelMeta(MODEL_META_PATH)

    with _client(base_url) as c:
        all_models = c.list_models()

        allowlist = load_allowlist(ALLOWLIST_PATH)
        if allowlist:
            console.print(f"[dim]Allowlist active: {len(allowlist)} entries.[/dim]")
            all_models = filter_models(all_models, allowlist=allowlist)

        registry = all_tasks()

        if models_arg:
            ids = [s.strip() for s in models_arg.split(",") if s.strip()]
            by_id = {m.id: m for m in all_models}
            resolved: list = []
            missing: list[str] = []
            for raw in ids:
                if raw in by_id:
                    resolved.append(by_id[raw])
                    continue
                # Variant-Notation "<base>@<quant>" — Default-Variante in LM
                # Studio kennt die ID nicht, also synthesieren wir aus dem
                # Basis-Modell.
                if "@" in raw:
                    base_id, quant = raw.split("@", 1)
                    base = by_id.get(base_id)
                    if base is not None:
                        resolved.append(base.model_copy(update={"id": raw, "quantization": quant}))
                        continue
                missing.append(raw)
            if missing:
                console.print(f"[red]Unknown models: {missing}[/red]")
                raise typer.Exit(1)
            selected_models = resolved
        else:
            selected_models = _select_models_interactive(all_models, meta, c.list_variants())

        if tasks_arg:
            names = [s.strip() for s in tasks_arg.split(",") if s.strip()]
            unknown = [n for n in names if n not in registry]
            if unknown:
                console.print(f"[red]Unknown tasks: {unknown}[/red]")
                raise typer.Exit(1)
            selected_tasks = [registry[n] for n in names]
        else:
            selected_tasks = _select_tasks_interactive(registry)

        rerun_list = (
            [s.strip() for s in rerun_models.split(",") if s.strip()]
            if rerun_models
            else None
        )

        console.print(
            f"[bold]Plan:[/bold] {len(selected_models)} Modelle × {len(selected_tasks)} Tasks "
            f"(skip-existing={'off' if force else 'on'})."
        )
        console.print(f"  Modelle: {', '.join(m.id for m in selected_models)}")
        console.print(f"  Tasks:   {', '.join(t.name for t in selected_tasks)}")
        if not yes and not typer.confirm("Weiter?", default=True):
            raise typer.Abort()

        if auto_judge:
            from .judge import api_available
            if not api_available():
                console.print(
                    "[yellow]--auto-judge braucht OPENROUTER_API_KEY in der Umgebung.[/yellow] "
                    "[dim]Bench läuft ohne Auto-Judge weiter.[/dim]"
                )
                auto_judge = False

        runner = Runner(c, store, live_report_dir=DOCS_DIR, model_meta_path=MODEL_META_PATH)
        runner.run(
            selected_models,
            selected_tasks,
            force=force,
            rerun_models=rerun_list,
            auto_judge=auto_judge,
        )

    console.print(
        "\n[dim]Build the report:[/dim] [bold]owb report[/bold]"
    )


# ----- owb reclassify -----------------------------------------------------


def _reclassify_niah(store: BenchStore) -> None:
    """Re-score stored NIAH results against the current normalization rules.

    Uses the persisted raw_answer / comprehension answers — no model re-run.
    """
    from .core.results import TaskResult
    from .tasks.niah import (
        COMPREHENSION_SCORE_WEIGHT,
        RETRIEVAL_SCORE_WEIGHT,
        SUMMARY_SCORE_WEIGHT,
        NIAHTask,
        effective_comprehension_score,
        effective_summary_score,
    )

    normalize = NIAHTask._normalize_for_match

    # Refresh expected_keywords from the current spec — keyword sets may have
    # been updated since the run. Lookup by needle id.
    spec_path = ROOT / "prompts" / "niah" / "needles.json"
    import json as _json
    spec = _json.loads(spec_path.read_text())
    current_kw = {n["id"]: n["expected_keywords"] for n in spec.get("needles", [])}

    changed = 0
    for model_id in store.model_ids_for_task("niah"):
        path = store.results_dir / "niah" / f"{model_id}.json"
        try:
            r = TaskResult.model_validate_json(path.read_text())
        except Exception:  # noqa: BLE001
            continue
        lengths = r.score_breakdown.get("lengths", [])
        if not lengths:
            continue
        scoring_lengths: list[dict] = []
        for L in lengths:
            answer_norm = normalize(L.get("raw_answer") or "")
            hits = 0
            for n in L.get("needles", []):
                kws = current_kw.get(n.get("id"), n.get("expected_keywords") or [])
                n["expected_keywords"] = kws
                hit = bool(answer_norm) and all(
                    NIAHTask._keyword_matches(kw, answer_norm) for kw in kws
                )
                n["hit"] = hit
                if hit:
                    hits += 1
            total = len(L.get("needles", []))
            L["hits"] = hits
            L["total"] = total
            retrieval_score = hits / total if total else 0.0
            L["retrieval_score"] = retrieval_score

            comp_qs = L.get("comprehension_questions") or []
            comp_score = 0.0
            facts_hits = 0
            facts_total = 0
            traps_passed = 0
            traps_total = 0
            if comp_qs:
                for q in comp_qs:
                    if q.get("type") == "trap":
                        traps_total += 1
                        # Trap verdicts come from a separate classifier; preserve.
                        if q.get("hit"):
                            traps_passed += 1
                        continue
                    facts_total += 1
                    expected = q.get("expected_keywords") or []
                    min_match = int(q.get("min_match", 1))
                    ans_norm = normalize(q.get("answer") or "")
                    matched = [kw for kw in expected if NIAHTask._keyword_matches(kw, ans_norm)]
                    if not expected:
                        s = 0.0
                    elif len(matched) >= min_match:
                        s = 1.0
                    else:
                        s = len(matched) / max(min_match, 1)
                    q["matched_keywords"] = matched
                    q["score"] = s
                    q["hit"] = s >= 0.5
                    if q["hit"]:
                        facts_hits += 1
                comp_score = sum(q.get("score", 0.0) for q in comp_qs) / len(comp_qs)
                L["comprehension_score"] = comp_score
                L["comprehension_facts_hits"] = facts_hits
                L["comprehension_facts_total"] = facts_total
                L["comprehension_traps_passed"] = traps_passed
                L["comprehension_traps_total"] = traps_total

            # Use judge verdict when present, else fall back to deterministic.
            eff_summary = effective_summary_score(L)
            eff_comp = effective_comprehension_score(L) if comp_qs else 0.0
            if comp_qs:
                combined = (
                    eff_summary * SUMMARY_SCORE_WEIGHT
                    + retrieval_score * RETRIEVAL_SCORE_WEIGHT
                    + eff_comp * COMPREHENSION_SCORE_WEIGHT
                )
            else:
                combined = eff_summary * 0.3 + retrieval_score * 0.7
            L["combined_score"] = combined
            if not L.get("skipped") and not L.get("error"):
                scoring_lengths.append(L)

        if scoring_lengths:
            r.score = sum(L["combined_score"] for L in scoring_lengths) / len(scoring_lengths)
        store.save(r)
        changed += 1
        score_str = f"{r.score:.3f}" if r.score is not None else "n/a"
        console.print(f"  ↺ {r.model_id}  score={score_str}")
    console.print(f"[green]Reclassified {changed} niah result(s).[/green]")


@app.command("reclassify")
def reclassify_cmd(
    task: str = typer.Argument(..., help="Task name to re-classify (nonsense / hallucination)."),
) -> None:
    """Re-evaluate stored responses with the current scoring heuristic.

    Useful when prompt patterns get extended — no need to re-run the model.
    Only supports tasks whose stored responses are sufficient to re-score.
    """
    store = BenchStore(ROOT)
    if task == "niah":
        _reclassify_niah(store)
        return
    if task == "nonsense":
        from .tasks.nonsense import _detect_pushback as classify_fn
        verdict_field = "pushback_detected"
    elif task == "hallucination":
        from .tasks.hallucination import classify as classify_fn  # type: ignore
        verdict_field = "verdict"
    else:
        console.print(f"[red]reclassify supports only nonsense/hallucination/niah, not '{task}'.[/red]")
        raise typer.Exit(1)

    changed = 0
    for model_id in store.model_ids_for_task(task):
        # model_id is the safe form here; load via path directly
        from .core.results import TaskResult
        path = store.results_dir / task / f"{model_id}.json"
        try:
            r = TaskResult.model_validate_json(path.read_text())
        except Exception:  # noqa: BLE001
            continue
        questions = r.score_breakdown.get("questions", [])
        if not questions:
            continue
        if task == "nonsense":
            for q in questions:
                q[verdict_field] = classify_fn(q.get("response", ""))
            hits = sum(1 for q in questions if q[verdict_field])
            r.score_breakdown["hits"] = hits
            r.score_breakdown["total"] = len(questions)
            r.score = hits / len(questions) if questions else 0.0
        else:  # hallucination
            for q in questions:
                v = classify_fn(q.get("response", ""))
                q[verdict_field] = v
                q["passed"] = v in {"corrected", "abstained"}
            r.score_breakdown["corrected"] = sum(1 for q in questions if q["verdict"] == "corrected")
            r.score_breakdown["abstained"] = sum(1 for q in questions if q["verdict"] == "abstained")
            r.score_breakdown["fabricated"] = sum(1 for q in questions if q["verdict"] == "fabricated")
            r.score_breakdown["total"] = len(questions)
            good = sum(1 for q in questions if q["passed"])
            r.score = good / len(questions) if questions else 0.0
        # Persist
        store.save(r)
        changed += 1
        console.print(f"  ↺ {r.model_id}  score={r.score:.2f}")
    console.print(f"[green]Reclassified {changed} {task} result(s).[/green]")


# ----- owb report ---------------------------------------------------------


@app.command("report")
def report_cmd(
    out_dir: Path = typer.Option(
        DOCS_DIR, "--out", help="Output directory (default: docs/ for gh-pages)."
    ),
) -> None:
    """Build the static HTML site from the current state of results/."""
    from .report.builder import build_site

    store = BenchStore(ROOT)
    if not store.task_names():
        console.print("[red]No results yet. Run `owb bench` first.[/red]")
        raise typer.Exit(1)

    out = build_site(store, out_dir, MODEL_META_PATH)
    console.print(f"[green]Report:[/green] file://{out}")


@app.command("judge")
def judge_cmd(
    task: str = typer.Argument(..., help="Task name (coding, diagram_to_svg, hallucination, niah)."),
    model_id: Optional[str] = typer.Argument(None, help="Optional: nur dieses Modell bewerten (sonst alle)."),
    redo: bool = typer.Option(False, "--redo", help="Auch Modelle mit existierendem judge neu bewerten."),
    judge_model: Optional[str] = typer.Option(
        None, "--model",
        help="OpenRouter-Modell für den Judge (default anthropic/claude-opus-4.7 oder $OPENROUTER_JUDGE_MODEL).",
    ),
) -> None:
    """Judge-Lauf via OpenRouter — direkter API-Call statt Subprocess.

    Lädt die zugehörige `.claude/skills/<skill>/SKILL.md` als System-Prompt,
    schickt Bilder/Code/Antworten als User-Content, erzwingt JSON-Output via
    response_format. Patcht den Judge-Block direkt in das Result-JSON.
    """
    from .judge import DEFAULT_MODEL, TASK_TO_SKILL_DIR, api_available, run_judge

    if task not in TASK_TO_SKILL_DIR:
        console.print(
            f"[red]Kein Judge für Task '{task}'. "
            f"Verfügbar: {', '.join(sorted(TASK_TO_SKILL_DIR))}[/red]"
        )
        raise typer.Exit(1)
    if not api_available():
        console.print("[red]OPENROUTER_API_KEY env var nicht gesetzt.[/red]")
        raise typer.Exit(1)

    judge_model = judge_model or DEFAULT_MODEL
    targets: list[str]
    if model_id:
        targets = [model_id]
    else:
        store = BenchStore(ROOT)
        targets = []
        results_dir = ROOT / "results" / task
        if results_dir.exists():
            for r in store.all_for_task(task):
                if r.error is None:
                    targets.append(r.model_id)
    if not targets:
        console.print(f"[yellow]Keine bewertbaren Ergebnisse in results/{task}/.[/yellow]")
        return

    failed = 0
    for mid in targets:
        console.print(f"[bold]→ {task}[/bold] · {mid} …", end=" ")
        try:
            judge = run_judge(task, mid, project_root=ROOT, judge_model=judge_model, redo=redo)
            if judge.get("skipped"):
                console.print(f"[dim]{judge.get('reason', 'skip')}[/dim]")
            else:
                js = judge.get("judge_score")
                console.print(f"[magenta]judge_score={js:.2f}[/magenta]" if js is not None else "[magenta]done[/magenta]")
        except Exception as e:  # noqa: BLE001
            failed += 1
            console.print(f"[red]✗ {e}[/red]")

    console.print()
    if failed:
        console.print(f"[yellow]{failed} Lauf/Läufe fehlgeschlagen.[/yellow]")
    console.print("[dim]Tip:[/dim] [bold]owb report[/bold] um den Report neu zu bauen.")


@app.command("reset")
def reset_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete all benchmark outputs (results/, artifacts/, output/, docs/).

    Useful before re-running the full suite from scratch. Inputs (prompts/,
    assets/, data/, src/, models.allowlist.yaml) are never touched.
    """
    import shutil

    targets = [ROOT / "results", ROOT / "artifacts", ROOT / "output", ROOT / "docs"]
    existing = [p for p in targets if p.exists()]
    if not existing:
        console.print("[stone]Nothing to delete — all output directories already empty.[/stone]")
        return

    console.print("[bold]The following directories will be removed:[/bold]")
    for p in existing:
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1024 / 1024
        count = sum(1 for _ in p.rglob("*") if _.is_file())
        console.print(f"  {p.relative_to(ROOT)}/  ({count} files, {size:.1f} MB)")

    if not yes:
        if not questionary.confirm("Delete all of these?", default=False).ask():
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(1)

    for p in existing:
        shutil.rmtree(p)
        console.print(f"  [red]✗[/red] removed {p.relative_to(ROOT)}/")
    console.print("[green]Done.[/green] Run `owb bench` to start fresh.")


if __name__ == "__main__":
    app()
