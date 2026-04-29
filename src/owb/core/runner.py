from __future__ import annotations

import platform
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Iterable

import psutil
from rich.console import Console

from ..client.lmstudio import LMStudioClient
from ..tasks.base import Task
from .results import BenchStore, Metrics, ModelInfo, TaskResult

console = Console()


def hardware_info() -> dict:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "ram_total_gb": round(psutil.virtual_memory().total / 1024**3, 1),
        "cpu_count": psutil.cpu_count(),
    }


class Runner:
    """Iterates models × tasks, persists each result via BenchStore.

    Skip-existing is the default: if `store.has_result(task, model.id)` is
    true, that pair is skipped unless `force` or `rerun_models` says otherwise.
    """

    def __init__(
        self,
        client: LMStudioClient,
        store: BenchStore,
        live_report_dir: "Path | None" = None,
        model_meta_path: "Path | None" = None,
    ) -> None:
        self.client = client
        self.store = store
        self.hw = hardware_info()
        self.live_report_dir = live_report_dir
        self.model_meta_path = model_meta_path
        self._report_lock = Lock()

    def _run_auto_judge(self, result: TaskResult) -> None:
        """Fire the matching judge for `result` via direct OpenRouter call.

        Best-effort: if the API key is missing or the judge fails, the
        bench continues. Patches result JSON in place; rebuilds the
        report on success.
        """
        from ..judge import has_judge, run_judge

        if not has_judge(result.task):
            return
        try:
            judge = run_judge(
                result.task,
                result.model_id,
                project_root=self.store.root,
                redo=True,  # bench just produced this result, always score it fresh
            )
            if judge.get("skipped"):
                console.print(
                    f"  [dim]→ judge ({result.task}) skipped: "
                    f"{judge.get('reason', 'skip')}[/dim]"
                )
            else:
                js = judge.get("judge_score")
                msg = f"judge_score={js * 100:.0f}%" if js is not None else "done"
                console.print(f"  [magenta]→ judge ({result.task}) {msg}[/magenta]")
                self._rebuild_report()
        except Exception as e:  # noqa: BLE001
            console.print(f"  [yellow]→ judge ({result.task}) failed: {e}[/yellow]")

    def _queue_auto_judge(
        self,
        result: TaskResult,
        executor: ThreadPoolExecutor,
        futures: list[Future],
    ) -> None:
        from ..judge import has_judge

        if not has_judge(result.task):
            return
        futures.append(executor.submit(self._run_auto_judge, result))
        console.print(f"  [dim]→ judge queued ({result.task})[/dim]")

    def _rebuild_report(self) -> None:
        """Rebuild the static HTML report from current state. Best-effort."""
        if self.live_report_dir is None or self.model_meta_path is None:
            return
        try:
            from ..report.builder import build_site
            with self._report_lock:
                build_site(self.store, self.live_report_dir, self.model_meta_path)
        except Exception as e:  # noqa: BLE001
            console.print(f"[dim]  (live-report skipped: {e})[/dim]")

    def run(
        self,
        models: list[ModelInfo],
        tasks: list[Task],
        *,
        force: bool = False,
        rerun_models: list[str] | None = None,
        auto_judge: bool = False,
    ) -> list[TaskResult]:
        rerun = set(rerun_models or [])

        # Build the work list before unloading anything, so the user sees the plan.
        work: list[tuple[ModelInfo, list[Task], list[Task]]] = []
        for m in models:
            applicable = [t for t in tasks if t.applicable(m)]
            todo = [
                t
                for t in applicable
                if force or m.id in rerun or not self.store.has_result(t.name, m.id)
            ]
            skipped = [t for t in applicable if t not in todo]
            work.append((m, todo, skipped))

        total_runs = sum(len(todo) for _, todo, _ in work)
        if total_runs == 0:
            console.print("[green]Alles aktuell — keine Runs nötig.[/green]")
            console.print(
                "[dim]  --force für komplette Neu-Berechnung, "
                "oder --rerun-models <id,id> für selektives Nachtesten.[/dim]"
            )
            return []

        console.print(
            f"[bold]Plan:[/bold] {total_runs} Runs (skipping bereits vorhandene)."
        )

        console.print("[dim]Unloading any currently loaded models…[/dim]")
        self.client.unload_all()
        time.sleep(0.5)

        produced: list[TaskResult] = []
        judge_executor = ThreadPoolExecutor(max_workers=1) if auto_judge else None
        judge_futures: list[Future] = []

        try:
            for i, (model, todo, skipped) in enumerate(work, 1):
                console.rule(f"[bold cyan]({i}/{len(work)}) {model.id}[/bold cyan]")
                if skipped:
                    done = [t for t in skipped if self.store.has_result(t.name, model.id)]
                    na = [t for t in skipped if t not in done]
                    if done:
                        console.print(
                            f"[dim]  ↺ skip (existing): {', '.join(t.name for t in done)}[/dim]"
                        )
                    if na:
                        console.print(
                            f"[dim]  ✗ not applicable: {', '.join(t.name for t in na)}[/dim]"
                        )
                if not todo:
                    continue

                for t in todo:
                    console.print(f"  ▶ [bold]{t.label or t.name}[/bold] …", end=" ")
                    t0 = time.monotonic()
                    try:
                        result = t.run(self.client, model, self.store)
                        result.hardware = self.hw
                    except Exception as e:  # noqa: BLE001
                        elapsed = time.monotonic() - t0
                        console.print(f"[red]✗ {e}[/red] ({elapsed:.1f}s)")
                        result = TaskResult(
                            task=t.name,
                            model_id=model.id,
                            model_info=model,
                            started_at=datetime.now(timezone.utc),
                            completed_at=datetime.now(timezone.utc),
                            metrics=Metrics(wall_seconds=elapsed),
                            error=str(e),
                            hardware=self.hw,
                        )

                    # Capture loaded-model RAM (lms ps reports the GGUF/MLX weight size).
                    if result.error is None and result.metrics.peak_rss_mb is None:
                        ram = self.client.loaded_size_mb(model.id)
                        if ram is not None:
                            result.metrics.peak_rss_mb = ram

                    self.store.save(result)
                    produced.append(result)
                    self._rebuild_report()  # live update after every task

                    if judge_executor is not None and result.error is None:
                        self._queue_auto_judge(result, judge_executor, judge_futures)

                    if result.error is None:
                        score_str = (
                            f"{result.score * 100:.0f}%" if result.score is not None else "—"
                        )
                        console.print(
                            f"[green]✓[/green] score={score_str} "
                            f"{result.metrics.wall_seconds:.1f}s "
                            f"@ {result.metrics.tokens_per_second:.0f} tok/s"
                        )

                console.print(f"  unloading {model.id}…")
                self.client.unload(model.id)
        finally:
            if judge_executor is not None:
                if judge_futures:
                    console.print(
                        f"[dim]Waiting for {len(judge_futures)} background judge job(s)…[/dim]"
                    )
                    for future in judge_futures:
                        future.result()
                judge_executor.shutdown(wait=True)

        console.rule("[bold green]Bench complete[/bold green]")
        return produced


def find_models(
    available: list[ModelInfo],
    selected_ids: Iterable[str],
) -> list[ModelInfo]:
    by_id = {m.id: m for m in available}
    return [by_id[i] for i in selected_ids if i in by_id]
