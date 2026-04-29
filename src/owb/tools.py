"""Mock tools used by the tool-use benchmark.

All execution is local — no network, no real filesystem outside the fixture
directory. The bench feeds the OpenAI-compatible JSON schema to the model and
calls the matching Python function when the model emits a `tool_calls`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[Path, dict[str, Any]], str]

    def schema(self) -> dict[str, Any]:
        """OpenAI tools-schema entry."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---- handlers --------------------------------------------------------------


def _safe_path(fixtures: Path, requested: str) -> Path:
    """Reject absolute paths or anything that escapes the fixtures dir."""
    p = (fixtures / requested).resolve()
    if not str(p).startswith(str(fixtures.resolve())):
        raise ValueError(f"path escapes fixtures dir: {requested!r}")
    return p


def _list_files(fixtures: Path, _args: dict[str, Any]) -> str:
    files = sorted(p.name for p in fixtures.iterdir() if p.is_file())
    return json.dumps(files)


def _read_file(fixtures: Path, args: dict[str, Any]) -> str:
    path = args.get("path", "")
    if not path:
        return json.dumps({"error": "missing 'path' argument"})
    try:
        p = _safe_path(fixtures, path)
        if not p.exists():
            return json.dumps({"error": f"file not found: {path}"})
        return p.read_text()
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": str(e)})


_DIFF_HUNK = re.compile(r"@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")


def _apply_diff(fixtures: Path, args: dict[str, Any]) -> str:
    """Apply a unified diff to a file (in-memory check, no actual write).

    Returns JSON: {ok: bool, applied: int, errors: list}. Path validation is
    strict — we never modify real files; this is a structural check only.
    """
    path = args.get("path", "")
    diff = args.get("diff", "")
    if not path or not diff:
        return json.dumps({"ok": False, "errors": ["missing path or diff"]})
    try:
        p = _safe_path(fixtures, path)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "errors": [str(e)]})
    if not p.exists():
        return json.dumps({"ok": False, "errors": [f"file not found: {path}"]})
    if not _DIFF_HUNK.search(diff):
        return json.dumps(
            {
                "ok": False,
                "errors": [
                    "diff does not contain a valid '@@ ... @@' hunk header"
                ],
            }
        )
    plus = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    minus = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    return json.dumps(
        {
            "ok": True,
            "applied": plus + minus,
            "added_lines": plus,
            "removed_lines": minus,
            "note": "structural-validation only; no real write performed",
        }
    )


_WEATHER_DB = {
    "berlin": {"temp_c": 12, "condition": "leichter Regen", "wind_kmh": 18},
    "münchen": {"temp_c": 9, "condition": "bewölkt", "wind_kmh": 7},
    "hamburg": {"temp_c": 11, "condition": "Schauer", "wind_kmh": 22},
    "köln": {"temp_c": 13, "condition": "wolkig", "wind_kmh": 12},
    "frankfurt": {"temp_c": 14, "condition": "sonnig", "wind_kmh": 8},
    "stuttgart": {"temp_c": 10, "condition": "neblig", "wind_kmh": 4},
}


def _get_weather(_fixtures: Path, args: dict[str, Any]) -> str:
    city = (args.get("city") or "").strip().lower()
    if not city:
        return json.dumps({"error": "missing 'city' argument"})
    data = _WEATHER_DB.get(city)
    if data is None:
        return json.dumps({"error": f"city not in mock DB: {args.get('city')!r}"})
    return json.dumps({"city": args.get("city"), **data})


# ---- registry --------------------------------------------------------------


def default_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="list_files",
            description=(
                "List the available files in the fixture directory. Returns a "
                "JSON array of filenames."
            ),
            parameters={"type": "object", "properties": {}},
            handler=_list_files,
        ),
        ToolSpec(
            name="read_file",
            description=(
                "Read a text file from the fixture directory and return its "
                "raw content. Path is relative to the fixture root, no '..'."
            ),
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=_read_file,
        ),
        ToolSpec(
            name="apply_diff",
            description=(
                "Validate a unified diff against a file (structural check, no "
                "real write). Returns {ok, added_lines, removed_lines}."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "diff": {
                        "type": "string",
                        "description": "Unified diff with @@ ... @@ hunks.",
                    },
                },
                "required": ["path", "diff"],
            },
            handler=_apply_diff,
        ),
        ToolSpec(
            name="get_weather",
            description=(
                "Get current weather for a German city (mock data). Available: "
                "Berlin, München, Hamburg, Köln, Frankfurt, Stuttgart."
            ),
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            handler=_get_weather,
        ),
    ]


def execute_tool(
    tools: list[ToolSpec],
    name: str,
    args: dict[str, Any],
    fixtures: Path,
) -> str:
    for t in tools:
        if t.name == name:
            try:
                return t.handler(fixtures, args)
            except Exception as e:  # noqa: BLE001
                return json.dumps({"error": f"tool '{name}' raised: {e}"})
    return json.dumps({"error": f"unknown tool: {name}"})
