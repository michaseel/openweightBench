"""LLM-as-Judge via OpenRouter (OpenAI-kompatibles API).

Direkter API-Aufruf statt headless `claude -p` — kein Claude-Code-Spinup,
keine Permission-Tänze, parallelisierbar. SKILL.md bleibt die Wahrheits-
Quelle für die Rubric: wir laden sie als System-Prompt und hängen ein
striktes JSON-Antwortschema an. Strukturierte Outputs via OpenRouters
`response_format: json_schema`.

Erforderlich: `OPENROUTER_API_KEY` env var.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .core.results import safe_model_id

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = os.environ.get("OPENROUTER_JUDGE_MODEL", "anthropic/claude-opus-4.7")

# task name → skill directory (rubric source of truth)
TASK_TO_SKILL_DIR: dict[str, str] = {
    "coding": "coding-judge",
    "diagram_to_svg": "diagram-svg-judge",
    "hallucination": "hallucination-judge",
    "niah": "summary-judge",
}


def has_judge(task: str) -> bool:
    return task in TASK_TO_SKILL_DIR


def api_available() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY env var nicht gesetzt. "
            "Setze einen OpenRouter-Key oder benutze --no-auto-judge."
        )
    return key


def _b64_data_url(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(suffix, "image/png")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_skill(root: Path, skill_dir: str) -> str:
    return (root / ".claude" / "skills" / skill_dir / "SKILL.md").read_text()


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def _post(payload: dict, *, timeout: int = 240) -> dict:
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/openweightBench",
            "X-OpenRouter-Title": "Open Weight Bench",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:600]
        raise RuntimeError(f"OpenRouter HTTP {e.code}: {body}") from e


def _call_json(
    *,
    system: str,
    user: list,
    schema: dict,
    schema_name: str,
    model: str,
    max_tokens: int = 3000,
    retries: int = 2,
) -> dict:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        token_budget = max_tokens * (2 ** attempt)
        payload = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": token_budget,
            "temperature": 0.0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            "plugins": [{"id": "response-healing"}],
        }
        data = _post(payload)
        choice = data["choices"][0]
        if choice.get("error"):
            raise RuntimeError(f"OpenRouter choice error: {choice['error']}")
        text = (choice.get("message") or {}).get("content")
        finish_reason = choice.get("finish_reason")
        if not isinstance(text, str):
            last_error = RuntimeError(
                f"OpenRouter response content ist {type(text).__name__} "
                f"(finish_reason={finish_reason!r}, max_completion_tokens={token_budget})"
            )
            if attempt < retries:
                continue
            break
        if finish_reason == "length":
            last_error = RuntimeError(
                f"OpenRouter response truncated bei max_tokens={token_budget}"
            )
            if attempt < retries:
                continue
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            last_error = e
            messages.extend([
                {"role": "assistant", "content": text[:2000]},
                {
                    "role": "user",
                    "content": (
                        "Die vorige Antwort war kein valides JSON "
                        f"({e}). Antworte jetzt ausschließlich mit einem "
                        "vollständigen JSON-Objekt gemäß Schema."
                    ),
                },
            ])
    raise RuntimeError(f"Judge lieferte kein valides JSON: {last_error}") from last_error


def _score(value: object, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field} muss eine Zahl sein, ist aber {type(value).__name__}")
    out = float(value)
    if not 0 <= out <= 1:
        raise ValueError(f"{field} muss zwischen 0 und 1 liegen, ist aber {out}")
    return out


def _scores(raw: dict, axes: list[str]) -> dict[str, float]:
    return {axis: _score(raw[axis], axis) for axis in axes}


def _nullable_svg_scores(raw: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for axis in _SVG_AXES:
        value = raw[axis]
        if value is None:
            continue
        score = _score(value, axis)
        out[axis] = score
    return out


def _weighted_mean(scores: dict[str, float], weights: dict[str, float]) -> float:
    total_weight = 0.0
    weighted = 0.0
    for key, value in scores.items():
        weight = weights.get(key, 1.0)
        weighted += value * weight
        total_weight += weight
    return weighted / total_weight if total_weight else 0.0


# ---------------------------------------------------------------- coding


_CODING_AXES = [
    "board_renders", "column_completeness", "cards_present", "ui_affordances",
    "design_quality", "code_structure", "dom_safety", "robustness",
    "code_quality", "render_matches_code",
]
_CODING_AXIS_WEIGHTS = {
    "board_renders": 0.5,
    "column_completeness": 0.5,
    "design_quality": 2.0,
}
_CODING_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            "properties": {k: {"type": "number"} for k in _CODING_AXES},
            "required": _CODING_AXES,
            "additionalProperties": False,
        },
        "comment_visual": {"type": "string"},
        "comment_code": {"type": "string"},
        "comment_consistency": {"type": "string"},
    },
    "required": ["scores", "comment_visual", "comment_code", "comment_consistency"],
    "additionalProperties": False,
}


def judge_coding(model_id: str, root: Path, *, judge_model: str = DEFAULT_MODEL) -> dict:
    safe = safe_model_id(model_id)
    art = root / "artifacts" / safe / "coding"
    screenshot = art / "screenshot.png"
    html_path = art / "output.html"
    if not screenshot.exists() or not html_path.exists():
        raise RuntimeError(f"Artefakte fehlen für {model_id} (screenshot/html)")

    system = _read_skill(root, "coding-judge") + (
        "\n\n---\nWICHTIG: Du hast keine Tools. Antworte ausschließlich mit dem JSON-Objekt "
        "gemäß Schema. Schreibe nichts auf Festplatte; das macht der Caller."
    )
    user = [
        {"type": "text", "text": f"Bewertung für Modell `{model_id}`. Screenshot:"},
        {"type": "image_url", "image_url": {"url": _b64_data_url(screenshot)}},
        {"type": "text", "text": "Generiertes HTML/JS:\n\n```html\n" + html_path.read_text(errors="replace") + "\n```"},
    ]
    parsed = _call_json(
        system=system, user=user, schema=_CODING_SCHEMA,
        schema_name="coding_judge", model=judge_model, max_tokens=5000,
    )
    scores = _scores(parsed["scores"], _CODING_AXES)
    judge = {
        "scored_at": _now_iso(),
        "judge_model": judge_model,
        "scores": scores,
        "judge_score": _weighted_mean(scores, _CODING_AXIS_WEIGHTS),
        "comment_visual": parsed["comment_visual"],
        "comment_code": parsed["comment_code"],
        "comment_consistency": parsed["comment_consistency"],
    }
    _patch_result(root / "results" / "coding" / f"{safe}.json", lambda d: d["score_breakdown"].update({"judge": judge}))
    return judge


# -------------------------------------------------------- diagram_to_svg


_SVG_AXES = [
    "completeness",
    "labels",
    "connections",
    "direction",
    "grouping",
    "layout_readability",
    "diagram_kind_match",
    "aesthetic_quality",
]
_SVG_AXIS_WEIGHTS = {"aesthetic_quality": 2.0}
_SVG_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            "properties": {
                k: {"type": ["number", "null"]} for k in _SVG_AXES
            },
            "required": _SVG_AXES,
            "additionalProperties": False,
        },
        "comment": {"type": "string"},
    },
    "required": ["scores", "comment"],
    "additionalProperties": False,
}


def judge_diagram_to_svg(model_id: str, root: Path, *, judge_model: str = DEFAULT_MODEL) -> dict:
    safe = safe_model_id(model_id)
    rj = root / "results" / "diagram_to_svg" / f"{safe}.json"
    d = json.loads(rj.read_text())
    diagrams = d["score_breakdown"].get("diagrams") or []
    system = _read_skill(root, "diagram-svg-judge") + (
        "\n\n---\nWICHTIG: Du hast keine Tools. Bewerte das *eine* gerade gezeigte Diagramm-Paar "
        "und antworte ausschließlich mit dem JSON-Objekt. Scores sind kontinuierlich 0..1; bei N/A null. "
        "Bewerte zusätzlich `aesthetic_quality` für die visuelle Schönheit/Politur des SVG-Renders. "
        "Pro Aufruf bewertest du genau ein Diagramm — der Caller iteriert."
    )

    per_scores: list[float] = []
    for diag in diagrams:
        if diag.get("error") or not diag.get("render_path"):
            continue
        original = root / diag["image_path"]
        rendered = root / diag["render_path"]
        if not (original.exists() and rendered.exists()):
            continue
        user = [
            {"type": "text", "text": f"Diagramm `{diag['id']}` — Bild 1: Original. Bild 2: SVG-Render."},
            {"type": "image_url", "image_url": {"url": _b64_data_url(original)}},
            {"type": "image_url", "image_url": {"url": _b64_data_url(rendered)}},
        ]
        parsed = _call_json(
            system=system, user=user, schema=_SVG_SCHEMA,
            schema_name="diagram_svg_judge", model=judge_model, max_tokens=1500,
        )
        scores = _nullable_svg_scores(parsed["scores"])
        if not scores:
            continue
        js = _weighted_mean(scores, _SVG_AXIS_WEIGHTS)
        diag["judge"] = {
            "scored_at": _now_iso(),
            "judge_model": judge_model,
            "scores": scores,
            "judge_score": js,
            "comment": parsed["comment"],
        }
        per_scores.append(js)

    if per_scores:
        d["score_breakdown"]["judge"] = {
            "scored_at": _now_iso(),
            "judge_model": judge_model,
            "judge_score": sum(per_scores) / len(per_scores),
        }
    _write_json(rj, d)
    return d["score_breakdown"].get("judge", {})


# ------------------------------------------------------------ hallucination


_HALL_VERDICTS = ["korrekt", "ausgewichen", "falsch"]
_HALL_QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "verdict": {"type": "string", "enum": _HALL_VERDICTS},
        "false_premise_caught": {"type": "boolean"},
        "fabrication_detected": {"type": "boolean"},
        "comment": {"type": "string"},
    },
    "required": ["id", "verdict", "false_premise_caught", "fabrication_detected", "comment"],
    "additionalProperties": False,
}
_HALL_SCHEMA = {
    "type": "object",
    "properties": {"judgments": {"type": "array", "items": _HALL_QUESTION_SCHEMA}},
    "required": ["judgments"],
    "additionalProperties": False,
}


def judge_hallucination(model_id: str, root: Path, *, judge_model: str = DEFAULT_MODEL) -> dict:
    safe = safe_model_id(model_id)
    rj = root / "results" / "hallucination" / f"{safe}.json"
    d = json.loads(rj.read_text())
    questions = d["score_breakdown"].get("questions") or []
    if not questions:
        raise RuntimeError("keine Fragen im Result-Breakdown")

    system = _read_skill(root, "hallucination-judge") + (
        "\n\n---\nWICHTIG: Du hast keine Tools. Du bekommst alle Fragen + Antworten in einem Aufruf. "
        "Antworte mit einem JSON-Objekt {\"judgments\": [...]}, eine Bewertung pro Frage. "
        "Verwende Verdicts: korrekt | ausgewichen | falsch."
    )
    payload_qs = [
        {"id": q["id"], "difficulty": q.get("difficulty"), "prompt": q["prompt"],
         "false_premise": q.get("false_premise"), "response": q.get("response", "")}
        for q in questions
    ]
    user = [{"type": "text", "text": "Modell-Antworten zur Bewertung:\n\n" + json.dumps(payload_qs, ensure_ascii=False, indent=2)}]
    parsed = _call_json(
        system=system, user=user, schema=_HALL_SCHEMA,
        schema_name="hallucination_judge", model=judge_model, max_tokens=4000,
    )

    by_id = {j["id"]: j for j in parsed["judgments"]}
    counts = {"korrekt": 0, "ausgewichen": 0, "falsch": 0}
    for q in questions:
        j = by_id.get(q["id"])
        if not j:
            continue
        q["judge"] = {
            "scored_at": _now_iso(),
            "judge_model": judge_model,
            "verdict": j["verdict"],
            "false_premise_caught": j["false_premise_caught"],
            "fabrication_detected": j["fabrication_detected"],
            "comment": j["comment"],
        }
        counts[j["verdict"]] += 1

    total = sum(counts.values()) or 1
    judge_score = (counts["korrekt"] + 0.5 * counts["ausgewichen"]) / total
    d["score_breakdown"]["judge"] = {
        "scored_at": _now_iso(),
        "judge_model": judge_model,
        **counts,
        "total": total,
        "judge_score": judge_score,
    }
    _write_json(rj, d)
    return d["score_breakdown"]["judge"]


# -------------------------------------------------------- niah summary


_NIAH_AXES = [
    "main_characters",
    "setting",
    "plot",
    "themes",
    "code_text_mix_recognized",
    "no_hallucinations",
]
_NIAH_SCHEMA = {
    "type": "object",
    "properties": {
        "judge_score": {"type": "number"},
        "axes": {
            "type": "object",
            "properties": {
                "main_characters": {"type": "number"},
                "setting": {"type": "number"},
                "plot": {"type": "number"},
                "themes": {"type": "number"},
                "code_text_mix_recognized": {"type": "number"},
                "no_hallucinations": {"type": "number"},
            },
            "required": _NIAH_AXES,
            "additionalProperties": False,
        },
        "comment": {"type": "string"},
    },
    "required": ["judge_score", "axes", "comment"],
    "additionalProperties": False,
}
_NIAH_COMP_QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "score": {"type": "number"},
        "verdict": {"type": "string"},
        "comment": {"type": "string"},
    },
    "required": ["id", "score", "verdict", "comment"],
    "additionalProperties": False,
}
_NIAH_COMP_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {"type": "array", "items": _NIAH_COMP_QUESTION_SCHEMA},
        "judge_score": {"type": "number"},
        "comment": {"type": "string"},
    },
    "required": ["questions", "judge_score", "comment"],
    "additionalProperties": False,
}


def judge_niah(model_id: str, root: Path, *, judge_model: str = DEFAULT_MODEL) -> dict:
    safe = safe_model_id(model_id)
    rj = root / "results" / "niah" / f"{safe}.json"
    d = json.loads(rj.read_text())
    lengths = d["score_breakdown"].get("lengths") or []
    system = _read_skill(root, "summary-judge") + (
        "\n\n---\nWICHTIG: Du hast keine Tools. Pro Aufruf bewertest du genau eine Stage-Summary. "
        "Antworte mit dem JSON-Objekt — judge_score 0..1 und sechs Sub-Achsen 0..1, plus Kommentar."
    )
    comp_system = _read_skill(root, "summary-judge") + (
        "\n\n---\nWICHTIG: Du hast keine Tools. Bewerte jetzt nicht die Summary, sondern die "
        "Antworten auf Detailfragen und Halluzinations-Fallen zum Buchinhalt. "
        "Du bekommst pro Frage Prompt, Typ, erwartete Stichwörter bzw. Fallen-Erklärung "
        "und die extrahierte Modellantwort. Vergib pro Frage einen kontinuierlichen "
        "Score 0..1: 1 = sachlich richtig bzw. Falle sauber zurückgewiesen, "
        "0.5 = teilweise richtig/ausweichend, 0 = falsch oder halluziniert. "
        "Antworte ausschließlich mit JSON gemäß Schema."
    )

    stage_scores: list[float] = []
    summary_scores: list[float] = []
    comp_scores: list[float] = []
    for L in lengths:
        if L.get("skipped") or L.get("error"):
            continue
        summary = L.get("raw_summary") or ""
        summary_judge_score: float | None = None
        if summary.strip():
            user = [{
                "type": "text",
                "text": (
                    f"Stage {L.get('length_tokens')} Tokens — "
                    f"Korpus-Summary des Modells:\n\n{summary}"
                ),
            }]
            parsed = _call_json(
                system=system, user=user, schema=_NIAH_SCHEMA,
                schema_name="niah_summary_judge", model=judge_model, max_tokens=1500,
            )
            L["judge"] = {
                "scored_at": _now_iso(),
                "judge_model": judge_model,
                "judge_score": _score(parsed["judge_score"], "judge_score"),
                "axes": _scores(parsed["axes"], _NIAH_AXES),
                "comment": parsed["comment"],
            }
            summary_judge_score = L["judge"]["judge_score"]
            summary_scores.append(summary_judge_score)

        comp_judge_score: float | None = None
        comp_questions = L.get("comprehension_questions") or []
        if comp_questions:
            comp_payload = [
                {
                    "id": q.get("id"),
                    "type": q.get("type"),
                    "prompt": q.get("prompt"),
                    "answer": q.get("answer"),
                    "expected_keywords": q.get("expected_keywords"),
                    "min_match": q.get("min_match"),
                    "trap_explanation": q.get("trap_explanation"),
                }
                for q in comp_questions
            ]
            user = [{
                "type": "text",
                "text": (
                    f"Stage {L.get('length_tokens')} Tokens — Detailfragen/Fallen "
                    "und Modellantworten:\n\n"
                    + json.dumps(comp_payload, ensure_ascii=False, indent=2)
                ),
            }]
            parsed = _call_json(
                system=comp_system,
                user=user,
                schema=_NIAH_COMP_SCHEMA,
                schema_name="niah_comprehension_judge",
                model=judge_model,
                max_tokens=3000,
            )
            by_id = {q["id"]: q for q in parsed["questions"]}
            judged_questions = []
            question_scores = []
            for q in comp_questions:
                qid = q.get("id")
                judged = by_id.get(qid)
                if not judged:
                    continue
                scored = {
                    "id": qid,
                    "score": _score(judged["score"], f"question {qid}"),
                    "verdict": judged["verdict"],
                    "comment": judged["comment"],
                }
                judged_questions.append(scored)
                question_scores.append(scored["score"])
            comp_judge_score = (
                _score(parsed["judge_score"], "comprehension_judge_score")
                if not question_scores
                else sum(question_scores) / len(question_scores)
            )
            L["comprehension_judge"] = {
                "scored_at": _now_iso(),
                "judge_model": judge_model,
                "judge_score": comp_judge_score,
                "questions": judged_questions,
                "comment": parsed["comment"],
            }
            comp_scores.append(comp_judge_score)

        if summary_judge_score is not None or comp_judge_score is not None:
            stage_scores.append(
                sum(v for v in (summary_judge_score, comp_judge_score) if v is not None)
                / sum(1 for v in (summary_judge_score, comp_judge_score) if v is not None)
            )

    if stage_scores:
        d["score_breakdown"]["judge"] = {
            "scored_at": _now_iso(),
            "judge_model": judge_model,
            "judge_score": sum(stage_scores) / len(stage_scores),
            "summary_judge_score": (
                sum(summary_scores) / len(summary_scores) if summary_scores else None
            ),
            "comprehension_judge_score": (
                sum(comp_scores) / len(comp_scores) if comp_scores else None
            ),
        }
    _write_json(rj, d)
    return d["score_breakdown"].get("judge", {})


# ------------------------------------------------------------ dispatch


def _patch_result(rj: Path, mutate: Callable[[dict], None]) -> None:
    d = json.loads(rj.read_text())
    mutate(d)
    _write_json(rj, d)


_DISPATCH = {
    "coding": judge_coding,
    "diagram_to_svg": judge_diagram_to_svg,
    "hallucination": judge_hallucination,
    "niah": judge_niah,
}


def run_judge(
    task: str,
    model_id: str,
    *,
    project_root: Path,
    judge_model: str = DEFAULT_MODEL,
    redo: bool = False,
) -> dict:
    """Judge `task` für `model_id`. Idempotent — wenn ein Aggregat-Judge
    schon vorhanden und nicht `redo`, sofort skippen."""
    fn = _DISPATCH.get(task)
    if fn is None:
        raise ValueError(f"Kein Judge für Task '{task}'")
    safe = safe_model_id(model_id)
    rj = project_root / "results" / task / f"{safe}.json"
    if not redo and rj.exists():
        d = json.loads(rj.read_text())
        if (d.get("score_breakdown") or {}).get("judge", {}).get("judge_score") is not None:
            return {"skipped": True, "reason": "judge_score bereits vorhanden — --redo zum Erzwingen"}
    return fn(model_id, project_root, judge_model=judge_model)
