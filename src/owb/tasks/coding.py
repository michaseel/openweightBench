"""Code generation task. Single-shot prompt; output saved as HTML for inspection."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..client.lmstudio import LMStudioClient
from ..core.results import Artifact, BenchStore, ModelInfo, TaskResult
from .base import Task

_HTML_FENCE = re.compile(r"```(?:html)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_HTML_DOC = re.compile(r"<(?:!doctype|html)[\s\S]*?</html>", re.IGNORECASE)


def extract_html(text: str) -> str:
    m = _HTML_FENCE.search(text)
    if m:
        candidate = m.group(1).strip()
        if "<" in candidate:
            return candidate
    m = _HTML_DOC.search(text)
    if m:
        return m.group(0)
    return text


class CodingTask(Task):
    name = "coding"
    label = "Coding (Kanban-Board)"

    def __init__(self, prompt_file: Path) -> None:
        self.spec = json.loads(prompt_file.read_text())

    def run(
        self,
        client: LMStudioClient,
        model: ModelInfo,
        store: BenchStore,
    ) -> TaskResult:
        artifact_dir = store.artifact_dir(self.name, model.id)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        started = self.now()
        messages = [
            {"role": "system", "content": self.spec.get("system_prompt", "")},
            {"role": "user", "content": self.spec["user_prompt"]},
        ]
        try:
            resp = client.chat(
                model.id, messages,
                temperature=0.6, max_tokens=-1, timeout_s=900.0,
            )
            text = resp.text or resp.reasoning or ""
            metrics = resp.metrics
            err = None
        except Exception as e:  # noqa: BLE001
            text = ""
            from ..core.results import Metrics as _M
            metrics = _M(wall_seconds=900.0)
            err = str(e)
        completed = self.now()

        html = extract_html(text)
        html_path = artifact_dir / "output.html"
        html_path.write_text(html)
        raw_path = artifact_dir / "raw_response.txt"
        raw_path.write_text(text)

        from .coding_lint import lint_kanban, lint_score

        looks_html = bool(_HTML_DOC.search(html))
        e2e_checks: list = []
        e2e_score_v = 0.0
        if err is not None:
            lint_checks: list = []
            lint_score_v = 0.0
        elif not looks_html:
            lint_checks = []
            lint_score_v = 0.0
        else:
            lint_checks = lint_kanban(html)
            lint_score_v = lint_score(lint_checks)

        artifacts = [
            Artifact(
                kind="html",
                label="Generierte App",
                path=str(html_path.relative_to(store.root)),
                mime="text/html",
            ),
            Artifact(
                kind="text",
                label="Raw model response",
                path=str(raw_path.relative_to(store.root)),
                mime="text/plain",
            ),
        ]

        # Optional Playwright screenshot — failure is non-fatal.
        if looks_html:
            try:
                from ..report.screenshots import screenshot_html

                shot_path = artifact_dir / "screenshot.png"
                screenshot_html(html_path, shot_path, extra_wait_ms=2000)
                artifacts.append(
                    Artifact(
                        kind="image",
                        label="Screenshot der gerenderten App",
                        path=str(shot_path.relative_to(store.root)),
                        mime="image/png",
                    )
                )
            except Exception:  # noqa: BLE001
                pass  # screenshot is best-effort

        # Functional E2E checks via Playwright — button clicks only,
        # no DnD, no confetti. Failures inside a single check are local;
        # a Playwright crash skips the whole block.
        if looks_html and err is None:
            try:
                from .coding_e2e import e2e_score as _e2e_score_fn
                from .coding_e2e import run_e2e_checks

                e2e_checks = run_e2e_checks(html_path)
                e2e_score_v = _e2e_score_fn(e2e_checks)
            except Exception:  # noqa: BLE001
                e2e_checks = []
                e2e_score_v = 0.0

        # Deterministic score = mean of available static + functional signals.
        # The qualitative judge blends in later via _effective_score().
        deterministic_parts = []
        if lint_checks:
            deterministic_parts.append(lint_score_v)
        if e2e_checks:
            deterministic_parts.append(e2e_score_v)
        score = (
            sum(deterministic_parts) / len(deterministic_parts)
            if deterministic_parts
            else 0.0
        )

        return TaskResult(
            task=self.name,
            model_id=model.id,
            model_info=model,
            started_at=started,
            completed_at=completed,
            metrics=metrics,
            score=score,
            error=err,
            score_breakdown={
                "looks_like_html": looks_html,
                "html_length": len(html),
                "raw_length": len(text),
                "lint_checks": [
                    {"id": c.id, "label": c.label, "passed": c.passed, "detail": c.detail}
                    for c in lint_checks
                ],
                "lint_passed": sum(1 for c in lint_checks if c.passed),
                "lint_total": len(lint_checks),
                "lint_score": lint_score_v,
                "e2e_checks": [
                    {"id": c.id, "label": c.label, "passed": c.passed, "detail": c.detail}
                    for c in e2e_checks
                ],
                "e2e_passed": sum(1 for c in e2e_checks if c.passed),
                "e2e_total": len(e2e_checks),
                "e2e_score": e2e_score_v,
            },
            raw_response=text[:2000],
            artifacts=artifacts,
        )
