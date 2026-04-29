"""Static linter for the Kanban-Coding output.

Runs without external dependencies. Each check returns pass/fail + detail.
The combined linter score is contribution to the overall coding score; a
separate Claude-Code-Skill ('/coding-judge') provides the qualitative
visual verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class LintCheck:
    id: str
    label: str
    passed: bool
    detail: str = ""


def _has(html: str, needle: str) -> bool:
    return needle.lower() in html.lower()


def _count(html: str, regex: str) -> int:
    return len(re.findall(regex, html, re.IGNORECASE))


def lint_kanban(html: str) -> list[LintCheck]:
    """Return a per-criterion pass/fail list for a Kanban HTML output.

    Each check covers a *concrete* requirement from the prompt. The Score
    is mean(passed) — i.e. each check has equal weight. Subjective things
    (visual polish, layout) are NOT in this linter; they belong in the
    judge skill.
    """
    h = html.lower()
    checks: list[LintCheck] = []

    # -- Required column labels ---------------------------------------
    for col in ("backlog", "in progress", "review", "done"):
        checks.append(
            LintCheck(
                id=f"col_{col.replace(' ', '_')}",
                label=f"Spalte '{col.title()}'",
                passed=col in h,
                detail="" if col in h else "Spalten-Label fehlt im HTML",
            )
        )

    # -- Drag & Drop primitives ---------------------------------------
    has_dnd = (
        'draggable="true"' in h
        or "ondragstart" in h
        or 'addeventlistener("dragstart"' in h
        or "addeventlistener('dragstart'" in h
        or "@dragstart" in h
        or "sortablejs" in h
        or "interact.js" in h
    )
    checks.append(
        LintCheck(
            id="drag_drop",
            label="HTML5 drag & drop or sortable lib",
            passed=has_dnd,
            detail="" if has_dnd else "no draggable / dragstart / sortable.js found",
        )
    )

    # -- localStorage persistence -------------------------------------
    has_ls = "localstorage" in h
    checks.append(
        LintCheck(
            id="local_storage",
            label="localStorage persistence",
            passed=has_ls,
            detail="" if has_ls else "no localStorage call found",
        )
    )

    # -- Tailwind via CDN ---------------------------------------------
    has_tailwind = "cdn.tailwindcss.com" in h or "tailwindcss" in h
    checks.append(
        LintCheck(
            id="tailwind_cdn",
            label="Tailwind via CDN",
            passed=has_tailwind,
            detail="" if has_tailwind else "tailwindcss CDN not loaded",
        )
    )

    # -- Confetti animation -------------------------------------------
    has_confetti = "confetti" in h
    checks.append(
        LintCheck(
            id="confetti",
            label="Confetti animation on 'Done'",
            passed=has_confetti,
            detail="" if has_confetti else "no 'confetti' in the code",
        )
    )

    # -- Add-card UI affordance ---------------------------------------
    has_add = bool(
        re.search(r"add\s*card", h)
        or re.search(r"karte\s*hinzu", h)
        or re.search(r">\s*\+\s*<", h)
        or re.search(r"new[-_ ]card", h)
    )
    checks.append(
        LintCheck(
            id="add_card",
            label="Add card button",
            passed=has_add,
            detail="" if has_add else "no add-button trigger found",
        )
    )

    # -- No frameworks (vanilla JS required) ---------------------------
    forbidden_libs = ["react.production", "react.development", "vue@", "angular", "svelte"]
    blockers = [lib for lib in forbidden_libs if lib in h]
    checks.append(
        LintCheck(
            id="vanilla_js",
            label="Vanilla JS (no React/Vue/Angular/Svelte)",
            passed=not blockers,
            detail=f"detected: {blockers}" if blockers else "",
        )
    )

    # -- No window.alert / window.prompt ------------------------------
    uses_alert = bool(re.search(r"window\.alert|window\.prompt|^alert\(|^prompt\(", h, re.MULTILINE))
    checks.append(
        LintCheck(
            id="no_alert_prompt",
            label="No window.alert/prompt for CRUD",
            passed=not uses_alert,
            detail="alert/prompt used" if uses_alert else "",
        )
    )

    # -- Editable cards (contenteditable / input field on click) -------
    editable = (
        'contenteditable="true"' in h
        or "contenteditable=" in h
        or "input " in h
        or "<textarea" in h
    )
    checks.append(
        LintCheck(
            id="editable_cards",
            label="Editable cards (contenteditable / input)",
            passed=editable,
            detail="" if editable else "no contenteditable / input found",
        )
    )

    return checks


def lint_score(checks: list[LintCheck]) -> float:
    """Return the linter score (0..1) — fraction of passed checks."""
    if not checks:
        return 0.0
    return sum(1 for c in checks if c.passed) / len(checks)
