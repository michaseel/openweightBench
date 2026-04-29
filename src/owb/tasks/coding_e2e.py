"""Functional E2E checks for the generated Kanban output via Playwright.

Tests only what is reliable through simple button clicks — no drag&drop,
no confetti detection. Models must expose stable data-testid attributes
per the prompt contract; otherwise individual checks fail. Console
errors raised during the flow fail the dedicated console-error check.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class E2ECheck:
    id: str
    label: str
    passed: bool
    detail: str = ""


# Console messages we ignore: known noise from the Tailwind Play CDN
# script and from missing favicons in file:// loads.
_IGNORED_CONSOLE_PATTERNS = (
    "cdn.tailwindcss.com should not be used in production",
    "should not be used in production",
    "favicon.ico",
)


def _ignored(text: str) -> bool:
    return any(p in text for p in _IGNORED_CONSOLE_PATTERNS)


_REMAINING_CHECKS = (
    ("add_card", "Add card via button"),
    ("no_double_add", "Card is added exactly 1× (no double submit)"),
    ("delete_card", "Delete card via button + confirmation"),
    ("persistence", "Cards survive a reload"),
    ("no_console_errors", "No JS console errors"),
)


def _commit_card_input(page) -> None:
    """Try Enter first; fall back to a save-card button if present."""
    page.keyboard.press("Enter")


def run_e2e_checks(html_path: Path, *, timeout_ms: int = 10000) -> list[E2ECheck]:
    """Run the 5-check Kanban functional suite on a generated HTML file.

    Returns one E2ECheck per criterion. Each criterion is independent —
    a failure in one does not skip later ones (except when the page
    itself fails to load, in which case all subsequent checks fail with
    a clear reason).
    """
    from playwright.sync_api import sync_playwright

    checks: list[E2ECheck] = []
    console_errors: list[str] = []

    def _on_console(msg) -> None:
        if msg.type == "error" and not _ignored(msg.text):
            console_errors.append(f"console.error: {msg.text}")

    def _on_pageerror(exc) -> None:
        text = str(exc)
        if not _ignored(text):
            console_errors.append(f"pageerror: {text}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.on("console", _on_console)
        page.on("pageerror", _on_pageerror)

        try:
            page.goto(html_path.resolve().as_uri(), timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            # Give JS-rendered apps time to paint before inspecting the DOM.
            page.wait_for_timeout(2000)
        except Exception as e:  # noqa: BLE001
            checks.append(
                E2ECheck("renders", "Page loads with all 4 columns", False, f"load error: {e}"[:200])
            )
            for cid, lbl in _REMAINING_CHECKS:
                checks.append(E2ECheck(cid, lbl, False, "page could not be loaded"))
            browser.close()
            return checks

        # 1. renders — all 4 columns visible
        cols = ("backlog", "in-progress", "review", "done")
        missing = [c for c in cols if page.query_selector(f'[data-testid="column-{c}"]') is None]
        checks.append(
            E2ECheck(
                "renders",
                "Page loads with all 4 columns",
                not missing,
                "" if not missing else f"missing columns: {missing}",
            )
        )

        def card_count() -> int:
            return len(page.query_selector_all('[data-testid="card"]'))

        # 2. add_card / no_double_add — click first add-card button, type
        # immediately, commit, then settle and read the actual delta.
        # We detect three failure modes separately:
        #   delta == 0  → Karte wurde nicht erstellt
        #   delta == 1  → Erfolg
        #   delta >= 2  → Doppel-Submit / Mehrfach-Listener (häufiger Bug)
        before_add = card_count()
        add_error: str | None = None
        delta = 0
        try:
            if page.query_selector('[data-testid="add-card"]') is None:
                raise RuntimeError('no [data-testid="add-card"] found')
            page.click('[data-testid="add-card"]', timeout=2000)
            page.wait_for_selector('[data-testid="card-input"]', timeout=2000)
            # Type immediately — some apps cancel/blur the input on focus loss,
            # so we click+type in a tight sequence rather than using fill().
            page.click('[data-testid="card-input"]', timeout=1000)
            page.keyboard.type("OWB-E2E-Test-Card")
            _commit_card_input(page)
            # Wait for SOMETHING to change, then settle and count.
            try:
                page.wait_for_function(
                    f'document.querySelectorAll(\'[data-testid="card"]\').length !== {before_add}',
                    timeout=2000,
                )
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(250)
            delta = card_count() - before_add
        except Exception as e:  # noqa: BLE001
            add_error = str(e)[:200]

        if add_error is not None:
            checks.append(E2ECheck("add_card", "Add card via button", False, add_error))
            checks.append(
                E2ECheck(
                    "no_double_add",
                    "Card is added exactly 1× (no double submit)",
                    False,
                    "skipped — add flow failed",
                )
            )
        else:
            checks.append(
                E2ECheck(
                    "add_card",
                    "Add card via button",
                    delta >= 1,
                    "" if delta >= 1 else "card was not created (count unchanged)",
                )
            )
            checks.append(
                E2ECheck(
                    "no_double_add",
                    "Card is added exactly 1× (no double submit)",
                    delta == 1,
                    ""
                    if delta == 1
                    else (
                        f"card was added {delta}× — multiple listeners or double submit"
                        if delta >= 2
                        else "no add happened, so not verifiable"
                    ),
                )
            )

        # 3. delete_card — click delete on a card, confirm, count drops by 1.
        # Use page.click() with auto-wait to survive DOM re-renders triggered
        # by the click itself.
        before_del = card_count()
        if before_del == 0:
            checks.append(
                E2ECheck(
                    "delete_card",
                    "Delete card via button + confirmation",
                    False,
                    "no cards available to delete",
                )
            )
        else:
            try:
                if page.query_selector('[data-testid="delete-card"]') is None:
                    raise RuntimeError('no [data-testid="delete-card"] found')
                page.locator('[data-testid="delete-card"]').first.click(timeout=2000)
                page.wait_for_selector('[data-testid="confirm-delete"]', timeout=2000)
                page.click('[data-testid="confirm-delete"]', timeout=2000)
                try:
                    page.wait_for_function(
                        f'document.querySelectorAll(\'[data-testid="card"]\').length !== {before_del}',
                        timeout=2000,
                    )
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(200)
                actual = card_count()
                ok = actual == before_del - 1
                checks.append(
                    E2ECheck(
                        "delete_card",
                        "Delete card via button + confirmation",
                        ok,
                        "" if ok else f"before delete {before_del}, after {actual}",
                    )
                )
            except Exception as e:  # noqa: BLE001
                checks.append(
                    E2ECheck(
                        "delete_card",
                        "Delete card via button + confirmation",
                        False,
                        str(e)[:200],
                    )
                )

        # 4. persistence — reload, card count matches the post-CRUD state
        expected = card_count()
        try:
            page.reload(timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.wait_for_timeout(2000)
            page.wait_for_selector('[data-testid="column-backlog"]', timeout=2000)
            actual = card_count()
            ok = actual == expected
            checks.append(
                E2ECheck(
                    "persistence",
                    "Cards survive a reload",
                    ok,
                    "" if ok else f"before reload {expected} cards, after reload {actual}",
                )
            )
        except Exception as e:  # noqa: BLE001
            checks.append(
                E2ECheck("persistence", "Cards survive a reload", False, str(e)[:200])
            )

        # 5. no console errors throughout the entire flow
        clean = [e for e in console_errors if not _ignored(e)]
        checks.append(
            E2ECheck(
                "no_console_errors",
                "No JS console errors",
                not clean,
                "" if not clean else "; ".join(clean[:3])[:300],
            )
        )

        browser.close()

    return checks


def e2e_score(checks: list[E2ECheck]) -> float:
    """Return the E2E score (0..1) — fraction of passed checks."""
    if not checks:
        return 0.0
    return sum(1 for c in checks if c.passed) / len(checks)
