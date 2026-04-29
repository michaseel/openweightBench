# Open Weight Bench

Lokaler Benchmark für Open-Weight-Modelle, die in LM Studio laufen.
Mehrere Test-Kategorien, interaktiver HTML-Report, der nicht nur Zahlen, sondern
auch das tatsächliche Modell-Ergebnis zum Selber-Anschauen zeigt.

## Setup

```bash
uv sync
uv run playwright install chromium
```

LM Studio muss lokal laufen (Default `http://127.0.0.1:1234`) und mindestens ein
Modell verfügbar haben.

## Nutzung

```bash
uv run owb models             # Alle in LM Studio verfügbaren Modelle anzeigen
uv run owb bench              # Interaktives Menü: Modelle und Tests auswählen
uv run owb status             # Anzahl vorhandener Ergebnisse pro Task anzeigen
uv run owb report             # HTML-Report aus vorhandenen Ergebnissen rebuilden
uv run owb judge <task> [<model>] [--redo]  # Judge-Skill headless via `claude -p` aufrufen
                              # `owb bench` triggert die Judges per Default automatisch;
                              # mit --no-auto-judge ausschalten
uv run owb reset [--yes]      # results/, artifacts/, output/, docs/ löschen (Inputs bleiben)
```

## Test-Kategorien

| Kategorie | Was wird getestet |
|---|---|
| `coding` | Quality vs Speed: Kanban-Board als Single-Shot |
| `niah` | Needle-in-Haystack + Korpus-Summary + Comprehension/Trap-Fragen aus großem Kontext (bis 120k Tokens) |
| `vision` | OCR + Diagramm + 3-Screenshots-Vergleich (nur VLMs) |
| `diagram_to_mermaid` | Whiteboard-Foto → Mermaid-Code; Render-Vergleich |
| `tool_use` | Agentic-Workflow-Simulation: 7 Szenarien (leicht/mittel/schwer) — Datei-Editing, Multi-File-Refactor, JSON-Komposition aus mehreren Quellen. Outcome-basiertes Scoring (validierter Diff, valides JSON, korrekte Werte) statt Wort-Matching |
| `diagram_to_svg` | Foto eines Diagramms → Modell gibt direkt inline-SVG zurück. Score = SVG-Validität + Begriffs-Abdeckung; qualitativ via `diagram-svg-judge`-Skill (Visualvergleich Original ↔ Render) |
| `hallucination` | 12 Best-of-Fallen über drei Schwierigkeitsstufen (3 leicht / 5 mittel / 4 schwer). Mischung aus subtilen falschen Prämissen und Kategorienfehlern (z.B. „In welcher Tonart steht das Wort 'Donnerstag'?") |

Optional (nicht im Default-Set, nur via `--tasks <name>`):

| Kategorie | Was wird getestet |
|---|---|
| `diagram_to_mermaid` | Älterer Diagramm-Bench: Modell gibt Mermaid-Code aus, mermaid.js rendert live. Strukturelles Scoring (required_edges, required_groups). Durch `diagram_to_svg` ersetzt |
| `nonsense` | Reine Kategorienfehler-Sammlung — die meisten guten Beispiele wurden in `hallucination` integriert |
| `instruction_following` | 8 Constraints in einem Prompt, automatisch validiert |
| `context_growth` | Echo-Bot über 40 Turns — Token-Speed + Prefill-Zeit bei wachsendem Kontext |
| `niah_deep` | NIAH über alle 4 Stufen (32k/64k/120k/200k) statt nur Top-Stage |

## Skills (LLM-as-Judge)

Drei Tasks haben einen **deterministischen Score** (Regex / Linter / Keyword-Matching), der für die Tabellen-Übersicht reicht — aber nicht alle Nuancen erfasst. Dafür gibt es **manuell aufrufbare Judge-Skills** in `.claude/skills/`, die Claude (oder ein anderer LLM-Client mit Skill-Support) als zweite Bewertungsebene nutzt. Sobald ein Judge gelaufen ist, ersetzt/blendet er den Score in `_effective_score()`; ohne Judge wird die Zeile als `preliminary` markiert.

| Skill | Aufruf | Was es bewertet |
|---|---|---|
| `coding-judge` | `/coding-judge [<model>] [--redo]` | Visuelle (Screenshot) + Code-Qualität (HTML/JS) des Kanban-Boards. Score = Mittel aus Statisch (Linter) · Funktional (Playwright-E2E) · Qualitativ (Judge) |
| `diagram-svg-judge` | `/diagram-svg-judge [<model>] [--redo]` | Visualvergleich SVG-Render ↔ Original-Diagramm entlang sieben fester Achsen mit verankerten 0/0.5/1.0-Stufen (completeness, labels, connections, direction, grouping, layout_readability, diagram_kind_match). Schreibt pro Diagramm + Aggregat |
| `hallucination-judge` | `/hallucination-judge [<model>] [--redo]` | Erkennt implizit-akzeptierte falsche Prämissen, die der Regex-Klassifizierer durchrutschen lässt (z.B. „Ich kenne die Sterne-Zahl von Helene Fischers Restaurant nicht" — akzeptiert dass es eines gibt). 3-Stufen-Verdict: korrekt / ausgewichen / falsch. Judge ersetzt Score |
| `summary-judge` | `/summary-judge [<model>] [--redo]` | Bewertet die NIAH-Korpus-Zusammenfassung (Turn 1) inhaltlich gegen eine eingebettete Buchwahrheit. Achsen: Hauptfiguren, Schauplatz, Plot, Halluzinationen, Themen, Mischtext-Erkennung. Judge ersetzt den `summary_score`-Anteil pro Stage, combined wird neu berechnet |

Die Skills tragen ihre Verdicts direkt in `results/<task>/<model>.json → score_breakdown.judge` ein (Aggregat) und pro Item in `score_breakdown.{questions,lengths}[i].judge`. Nach einem Judge-Lauf einfach `uv run owb report` neu bauen, der HTML-Report zeigt dann die aktualisierten Werte ohne `preliminary`-Marker.

### Headless / automatischer Aufruf

Die Skills laufen auch direkt als API-Call gegen **OpenRouter** — keine Claude-Code-Session, kein Subprocess-Spinup, parallelisierbar. SKILL.md bleibt die Wahrheits-Quelle für die Rubric (wird als System-Prompt geladen); strikte JSON-Outputs via OpenRouters `response_format: json_schema` werden in `score_breakdown.judge` gepatcht.

Setup:
1. OpenRouter-Key besorgen: https://openrouter.ai/keys
2. `cp .env.example .env` und Key eintragen (`.env` ist gitignored)
3. Optional `OPENROUTER_JUDGE_MODEL` setzen (Default: `anthropic/claude-opus-4.7`)

Aufruf:
- `owb judge <task> [<model>] [--redo]` — manuell für `coding | diagram_to_svg | hallucination | niah`
- `owb bench` triggert die Judges **per Default** nach jedem Modell-Task automatisch; `--no-auto-judge` schaltet's aus

`hallucination-judge` und `summary-judge` haben die Wahrheit (false-premise-Tabelle bzw. detaillierte Buchzusammenfassung) **direkt in der `SKILL.md` eingebettet** — Claude muss kein externes Wissen oder den 120k-Korpus selbst lesen, um zu bewerten.

## Architektur

```
src/owb/
├── client/lmstudio.py    REST-Wrapper (list, chat, vision); strip_reasoning() für <think>-Tags + Prosa-Reasoning
├── core/                 discovery, runner, metrics, results
├── tasks/                eine Datei pro Test-Kategorie
└── report/               Jinja-Templates + HTML-Builder

.claude/skills/
├── coding-judge/         visuelle + Code-Qualitäts-Bewertung der Kanban-Outputs
├── diagram-svg-judge/    Visualvergleich SVG-Render ↔ Original-Diagramm
├── hallucination-judge/  semantische Bewertung der false-premise Antworten
└── summary-judge/        inhaltliche Bewertung der NIAH-Korpus-Summaries
```
