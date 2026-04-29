"""Instruction-following: 8 constraints in one prompt, each auto-validated."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..client.lmstudio import LMStudioClient
from ..core.results import Artifact, BenchStore, ModelInfo, TaskResult
from .base import Task

_JSON_TAIL = re.compile(r"\{[^{}]*\"absaetze\"\s*:\s*(-?\d+)[^{}]*?\"werkzeuge\"\s*:\s*(\[[^\]]*\])\s*\}", re.DOTALL | re.IGNORECASE)
_MD_RE = re.compile(r"(\*\*|^#+\s|^-\s+|^\*\s+)", re.MULTILINE)


def _strip_json_tail(text: str) -> tuple[str, dict | None]:
    """Return (text without trailing JSON, parsed JSON dict or None)."""
    m = _JSON_TAIL.search(text)
    if not m:
        return text, None
    try:
        import json as _json
        full = m.group(0)
        parsed = _json.loads(full)
    except Exception:  # noqa: BLE001
        return text, None
    cleaned = text[: m.start()].rstrip()
    return cleaned, parsed


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text, re.UNICODE))


def _check(text: str) -> list[dict]:
    body, json_obj = _strip_json_tail(text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    p1 = paragraphs[0] if len(paragraphs) > 0 else ""
    p2 = paragraphs[1] if len(paragraphs) > 1 else ""
    p3 = paragraphs[2] if len(paragraphs) > 2 else ""
    p4 = paragraphs[3] if len(paragraphs) > 3 else ""

    # 3) multiplication 137*24 = 3288
    mult_re = re.compile(r"137\s*[×x*]\s*24\s*=\s*(\d+)")
    mult_match = mult_re.search(p2 + " " + body)
    mult_correct = bool(mult_match) and int(mult_match.group(1)) == 3288

    # 4) alphabetically sorted comma-separated list of 5 tools in p3
    tools_line = ""
    for line in p3.splitlines():
        if line.count(",") >= 4:
            tools_line = line
            break
    tools = [t.strip() for t in tools_line.split(",") if t.strip()]
    tools_correct = (
        len(tools) == 5
        and tools == sorted(tools, key=str.lower)
    )

    # 5) p4 has exactly 7 words
    p4_words = _word_count(p4)
    p4_correct = p4_words == 7

    # 6) "Stahl" in body (case-insensitive but NOT in tools list)
    body_no_tools = body.replace(tools_line, "")
    stahl_correct = "stahl" in body_no_tools.lower()

    # 7) no markdown
    has_md = bool(_MD_RE.search(body))

    # 8) trailing JSON consistency
    json_ok = (
        json_obj is not None
        and json_obj.get("absaetze") == 4
        and isinstance(json_obj.get("werkzeuge"), list)
        and len(json_obj.get("werkzeuge", [])) == 5
        and [w.lower() for w in json_obj.get("werkzeuge", [])]
            == [t.lower() for t in tools]
    )

    return [
        {
            "id": "four_paragraphs",
            "label": "Exactly four paragraphs",
            "passed": len(paragraphs) == 4,
            "detail": f"{len(paragraphs)} paragraphs found",
        },
        {
            "id": "start_listen",
            "label": "First paragraph starts with 'Listen:'",
            "passed": p1.startswith("Listen:"),
            "detail": "" if p1.startswith("Listen:") else f"start: {p1[:30]!r}",
        },
        {
            "id": "multiplication",
            "label": "137 × 24 = 3288 correct",
            "passed": mult_correct,
            "detail": (
                f"found: {mult_match.group(0)}" if mult_match else "multiplication not found in expected format"
            ),
        },
        {
            "id": "tools_sorted",
            "label": "Five alphabetically sorted tools",
            "passed": tools_correct,
            "detail": f"{len(tools)} tools: {tools}" if tools else "no list detected in paragraph 3",
        },
        {
            "id": "p4_seven_words",
            "label": "Fourth paragraph: exactly 7 words",
            "passed": p4_correct,
            "detail": f"{p4_words} words in paragraph 4",
        },
        {
            "id": "mentions_stahl",
            "label": "Mentions 'Stahl' (in body text)",
            "passed": stahl_correct,
            "detail": "",
        },
        {
            "id": "no_markdown",
            "label": "No Markdown",
            "passed": not has_md,
            "detail": "",
        },
        {
            "id": "trailing_json_consistent",
            "label": "Trailing JSON consistent with tools",
            "passed": json_ok,
            "detail": "" if json_ok else f"JSON: {json_obj}",
        },
    ]


class InstructionFollowingTask(Task):
    name = "instruction_following"
    label = "Instruction Following"

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
            {"role": "system", "content": self.spec.get("system", "")},
            {"role": "user", "content": self.spec["user_prompt"]},
        ]
        try:
            resp = client.chat(model.id, messages, max_tokens=6000, temperature=0.2, timeout_s=600.0)
            text = resp.effective_text
            metrics = resp.metrics
            if not text and resp.truncated_reasoning:
                err = "Reasoning ohne Antwort abgebrochen (max_tokens erreicht)"
            else:
                err = None
        except Exception as e:  # noqa: BLE001
            text = ""
            from ..core.results import Metrics as _M
            metrics = _M(wall_seconds=600.0)
            err = str(e)
        completed = self.now()

        checks = _check(text)
        passed = sum(1 for c in checks if c["passed"])
        if err is not None:
            score = 0.0
        else:
            score = passed / len(checks)

        # Persist the full response + per-check verdict for the report.
        out = {"prompt": self.spec["user_prompt"], "response": text, "checks": checks, "error": err}
        out_path = artifact_dir / "result.json"
        out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))

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
                "checks": checks,
                "passed": passed,
                "total": len(checks),
                "response": text,
            },
            raw_response=text[:2000],
            artifacts=[
                Artifact(
                    kind="json",
                    label="Prompt + Antwort + Check-Ergebnisse",
                    path=str(out_path.relative_to(store.root)),
                    mime="application/json",
                )
            ],
        )
