"""Tool-use benchmark: agentic-workflow simulation with mocked tools.

System-Prompt beschreibt den Werkzeugkasten, User-Prompt enthält eine Aufgabe.
Modell muss selbst planen, Tools wählen, Ergebnisse zusammensetzen.

Score per Szenario aggregiert:
  - Tool-Nutzung: wurden die richtigen Tools aufgerufen?
  - Argumente: stimmt der Pfad/die Stadt?
  - Outcome: validiert der Diff strukturell? Ist das JSON korrekt? Stimmen die
    inhaltlichen Werte (count, sets, bool-Flags) mit den Fixtures überein?
  - Soft-Mention: leichter Bonus, wenn die Antwort 3 von 4 erwähnten Konzepten
    enthält — kein Punktabzug für fehlendes technisches Vokabular.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..client.lmstudio import LMStudioClient
from ..core.results import Artifact, BenchStore, Metrics, ModelInfo, TaskResult
from ..tools import default_tools, execute_tool
from .base import Task

MAX_ITERATIONS_DEFAULT = 10


def _arg_check(call_args: dict, expected: dict) -> tuple[bool, str]:
    """Soft check on tool arguments. Returns (ok, detail)."""
    if "path_contains" in expected:
        path = (call_args.get("path") or "").lower()
        ok = expected["path_contains"].lower() in path
        return ok, f"path={call_args.get('path')!r}"
    if "city_match" in expected:
        city = (call_args.get("city") or "").lower()
        ok = expected["city_match"].lower() in city
        return ok, f"city={call_args.get('city')!r}"
    return True, ""


def _extract_json(text: str) -> dict | list | None:
    """Best-effort: parse a JSON object or array from the model's final text.
    Tolerates ```json fences and surrounding prose."""
    if not text:
        return None
    s = text.strip()
    # Strip markdown fences if present
    fence = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", s, re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()
    # Try whole-string parse first
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        pass
    # Fall back to first {...} or [...] block
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        i = s.find(open_ch)
        j = s.rfind(close_ch)
        if i != -1 and j > i:
            try:
                return json.loads(s[i : j + 1])
            except Exception:  # noqa: BLE001
                continue
    return None


class ToolUseTask(Task):
    name = "tool_use"
    label = "Tool Use"
    requires_tool_use = True

    def __init__(self, prompt_file: Path, fixtures_dir: Path) -> None:
        self.spec = json.loads(prompt_file.read_text())
        self.fixtures_dir = fixtures_dir
        self.tools = default_tools()
        self.tool_schemas = [t.schema() for t in self.tools]
        self.max_iterations = int(self.spec.get("max_tool_iterations", MAX_ITERATIONS_DEFAULT))

    # ---- per-scenario execution ----------------------------------------

    def _run_scenario(
        self,
        client: LMStudioClient,
        model_id: str,
        scenario: dict,
    ) -> dict:
        messages: list[dict] = [
            {"role": "system", "content": self.spec.get("system", "")},
            {"role": "user", "content": scenario["user_prompt"]},
        ]
        history: list[dict] = []
        wall_total = 0.0
        tokens_total = 0
        speeds: list[float] = []
        final_text = ""
        last_resp = None

        for it in range(self.max_iterations):
            try:
                resp = client.chat(
                    model_id,
                    messages,
                    max_tokens=6000,
                    temperature=0.2,
                    tools=self.tool_schemas,
                    timeout_s=400.0,
                )
            except Exception as e:  # noqa: BLE001
                return {
                    "id": scenario["id"],
                    "difficulty": scenario.get("difficulty", "?"),
                    "error": f"chat failed at iteration {it}: {e}",
                    "history": history,
                    "final_text": "",
                    "score": 0.0,
                    "metrics": {"wall_seconds": wall_total, "tokens": tokens_total, "tps": 0.0},
                }

            last_resp = resp
            wall_total += resp.metrics.wall_seconds
            tokens_total += resp.metrics.tokens_generated
            if resp.metrics.tokens_per_second:
                speeds.append(resp.metrics.tokens_per_second)

            if resp.tool_calls:
                # Append assistant turn (must include the tool_calls payload).
                messages.append(
                    {
                        "role": "assistant",
                        "content": resp.text or "",
                        "tool_calls": resp.tool_calls,
                    }
                )
                # Execute each call and feed result back as tool message.
                for call in resp.tool_calls:
                    fn = call.get("function", {})
                    name = fn.get("name", "")
                    raw_args = fn.get("arguments", "{}")
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:  # noqa: BLE001
                        args = {}
                    result = execute_tool(self.tools, name, args, self.fixtures_dir)
                    history.append({"tool": name, "args": args, "result": result[:600]})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id", ""),
                            "content": result,
                        }
                    )
                continue  # next iteration -> let model use the tool result

            # No tool calls: this is the final answer.
            final_text = resp.text
            break
        else:
            final_text = last_resp.text if last_resp else ""

        avg_speed = sum(speeds) / len(speeds) if speeds else 0.0

        return {
            "id": scenario["id"],
            "difficulty": scenario.get("difficulty", "?"),
            "user_prompt": scenario["user_prompt"],
            "history": history,
            "final_text": final_text,
            "metrics": {
                "wall_seconds": wall_total,
                "tokens": tokens_total,
                "tps": avg_speed,
                "iterations": len(history) // max(1, len(history))
                if not history
                else sum(1 for _ in history),
            },
            **self._score_scenario(scenario, history, final_text),
        }

    # ---- scoring -------------------------------------------------------

    @staticmethod
    def _score_scenario(scenario: dict, history: list, final_text: str) -> dict:
        checks: list[dict] = []
        called = [h["tool"] for h in history]

        # 1. Tool-Aufruf — ein Check pro erwartetes Tool
        for t in scenario.get("expected_tools", []):
            ok = t in called
            checks.append(
                {
                    "id": f"called_{t}",
                    "label": f"Tool '{t}' aufgerufen",
                    "passed": ok,
                    "detail": f"history: {', '.join(called) or '—'}",
                }
            )

        # 2. Tool-Argumente — Pfad/Stadt müssen passen
        for tool_name, expected in (scenario.get("expected_args") or {}).items():
            calls = [h for h in history if h["tool"] == tool_name]
            if not calls:
                checks.append(
                    {
                        "id": f"args_{tool_name}",
                        "label": f"Argumente für '{tool_name}'",
                        "passed": False,
                        "detail": "Tool nicht aufgerufen",
                    }
                )
                continue
            ok, det = _arg_check(calls[0]["args"], expected)
            checks.append(
                {
                    "id": f"args_{tool_name}",
                    "label": f"Argumente für '{tool_name}'",
                    "passed": ok,
                    "detail": det,
                }
            )

        # 3. Diff-Outcome — apply_diff muss strukturell ok=True liefern
        diff_target = scenario.get("expect_diff_validates")
        if diff_target:
            diff_calls = [
                h for h in history
                if h["tool"] == "apply_diff"
                and diff_target.lower() in (h["args"].get("path") or "").lower()
            ]
            ok = False
            applied_total = 0
            for dc in diff_calls:
                try:
                    res = json.loads(dc["result"]) if isinstance(dc["result"], str) else dc["result"]
                except Exception:  # noqa: BLE001
                    res = {}
                if res.get("ok"):
                    ok = True
                    applied_total = max(applied_total, int(res.get("applied", 0)))
            checks.append(
                {
                    "id": "diff_validates",
                    "label": f"Diff für {diff_target} validiert strukturell",
                    "passed": ok,
                    "detail": f"applied={applied_total}, calls={len(diff_calls)}",
                }
            )
            min_changes = int(scenario.get("expect_diff_min_changes", 0) or 0)
            if min_changes > 0:
                checks.append(
                    {
                        "id": "diff_min_changes",
                        "label": f"Diff ändert ≥{min_changes} Zeilen",
                        "passed": applied_total >= min_changes,
                        "detail": f"angewendet: {applied_total}",
                    }
                )
            for required in scenario.get("expect_diff_contains", []):
                found = any(required in (dc["args"].get("diff") or "") for dc in diff_calls)
                checks.append(
                    {
                        "id": f"diff_contains_{required}",
                        "label": f"Diff enthält '{required}'",
                        "passed": found,
                        "detail": "" if found else "Token im Diff-Body nicht gefunden",
                    }
                )

        # 4. JSON-Output — strikte Struktur- und Wertprüfung
        if scenario.get("expect_json"):
            parsed = _extract_json(final_text)
            json_ok = parsed is not None
            checks.append(
                {
                    "id": "json_format",
                    "label": "Antwort enthält gültiges JSON",
                    "passed": json_ok,
                    "detail": "" if json_ok else "kein parsbares JSON in Antwort",
                }
            )
            if json_ok:
                expected_keys = scenario.get("expect_json_keys") or []
                if expected_keys:
                    have = parsed.keys() if isinstance(parsed, dict) else set()
                    missing = [k for k in expected_keys if k not in have]
                    checks.append(
                        {
                            "id": "json_keys",
                            "label": f"JSON enthält Felder {expected_keys}",
                            "passed": not missing,
                            "detail": "fehlt: " + ", ".join(missing) if missing else "alle Felder vorhanden",
                        }
                    )
                ev = scenario.get("expect_json_eval") or {}
                for key, expected_val in ev.items():
                    if key.endswith("_equals") and isinstance(parsed, dict):
                        field = key[: -len("_equals")]
                        actual = parsed.get(field)
                        ok = actual == expected_val
                        checks.append(
                            {
                                "id": f"json_eq_{field}",
                                "label": f"{field} == {expected_val!r}",
                                "passed": ok,
                                "detail": f"actual={actual!r}",
                            }
                        )
                    elif key == "users_ids_set" and isinstance(parsed, dict):
                        users = parsed.get("users") or []
                        ids = sorted(u.get("id") for u in users if isinstance(u, dict))
                        ok = ids == sorted(expected_val)
                        checks.append(
                            {
                                "id": "json_users_ids",
                                "label": f"users.ids == {sorted(expected_val)}",
                                "passed": ok,
                                "detail": f"actual={ids}",
                            }
                        )
                    elif key == "admin_emails_set" and isinstance(parsed, dict):
                        emails = parsed.get("admin_emails") or []
                        norm = sorted(str(e).lower() for e in emails)
                        exp = sorted(str(e).lower() for e in expected_val)
                        ok = norm == exp
                        checks.append(
                            {
                                "id": "json_admin_emails",
                                "label": f"admin_emails == {exp}",
                                "passed": ok,
                                "detail": f"actual={emails}",
                            }
                        )

        # 5. Soft-Mention — partial credit, nur wenn min unterschritten
        soft = scenario.get("answer_should_mention") or []
        if soft:
            text_l = (final_text or "").lower()
            hits = [w for w in soft if w.lower() in text_l]
            min_hits = int(scenario.get("answer_should_mention_min", len(soft)))
            checks.append(
                {
                    "id": "soft_mention",
                    "label": f"Antwort erwähnt {min_hits}/{len(soft)} der Schlüssel-Begriffe",
                    "passed": len(hits) >= min_hits,
                    "detail": f"erwähnt: {hits}, fehlt: {[w for w in soft if w not in hits]}",
                }
            )

        passed = sum(1 for c in checks if c["passed"])
        score = passed / len(checks) if checks else 0.0
        return {"checks": checks, "score": score, "passed": passed, "total": len(checks)}

    # ---- entrypoint ----------------------------------------------------

    def run(
        self,
        client: LMStudioClient,
        model: ModelInfo,
        store: BenchStore,
    ) -> TaskResult:
        artifact_dir = store.artifact_dir(self.name, model.id)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        started = self.now()
        scenarios_out: list[dict] = []
        wall_total = 0.0
        tokens_total = 0
        speeds: list[float] = []

        for scenario in self.spec["scenarios"]:
            sr = self._run_scenario(client, model.id, scenario)
            scenarios_out.append(sr)
            wall_total += sr.get("metrics", {}).get("wall_seconds", 0)
            tokens_total += sr.get("metrics", {}).get("tokens", 0)
            t = sr.get("metrics", {}).get("tps", 0)
            if t:
                speeds.append(t)

        completed = self.now()
        scores = [s.get("score", 0) for s in scenarios_out if not s.get("error")]
        score = sum(scores) / len(scores) if scores else None
        avg = sum(speeds) / len(speeds) if speeds else 0.0

        bd_path = artifact_dir / "scenarios.json"
        bd_path.write_text(json.dumps(scenarios_out, indent=2, ensure_ascii=False))

        return TaskResult(
            task=self.name,
            model_id=model.id,
            model_info=model,
            started_at=started,
            completed_at=completed,
            metrics=Metrics(
                wall_seconds=wall_total,
                tokens_generated=tokens_total,
                tokens_per_second=avg,
            ),
            score=score,
            score_breakdown={"scenarios": scenarios_out},
            artifacts=[
                Artifact(
                    kind="json",
                    label="Scenarios + Tool-Call-Verlauf",
                    path=str(bd_path.relative_to(store.root)),
                    mime="application/json",
                )
            ],
        )
