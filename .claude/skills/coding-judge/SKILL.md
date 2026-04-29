---
name: coding-judge
description: Bewertet die Kanban-Coding-Outputs auf zwei Ebenen — visuell (gerenderter Screenshot) und im Code (HTML/JS-Qualität) — und schreibt einen strukturierten Verdict in die jeweilige results/coding/<safe-id>.json. Ergänzt den deterministischen Linter um qualitative Aspekte. Wird manuell aufgerufen, da Bewertung subjektiv ist und Bildkontext braucht.
---

# coding-judge — visuelle + Code-Qualitätsbewertung der Kanban-Outputs

Diese Skill bewertet einen oder mehrere generierte Kanban-Boards aus dem Coding-Benchmark anhand **zweier Quellen**:
- **Screenshot** der gerenderten App (`artifacts/<safe-id>/coding/screenshot.png`)
- **Generierter HTML/JS-Code** (`artifacts/<safe-id>/coding/output.html`)

Manche Fehler sieht man nur visuell (z.B. Modell schreibt korrekten Code, JS bricht aber beim Init und der Bildschirm bleibt leer). Manche nur im Code (XSS-Lücken, kaputte Error-Behandlung). Beide Sichten liefern unterschiedliche Signale.

---

## Eingaben pro Modell

1. `artifacts/<safe-model-id>/coding/screenshot.png` — gerenderte App (Read-Tool, multimodal)
2. `artifacts/<safe-model-id>/coding/output.html` — generiertes HTML mit JS+CSS inline
3. `results/coding/<safe-model-id>.json` — vorhandene Daten

---

## Bewertungs-Achsen (10, jede 0..1)

### Visuelle Bewertung (aus Screenshot — 5 Achsen)

1. **board_renders** — Wird tatsächlich ein Kanban-Board gerendert? Oder nur Header / leere Page / 1×1-Pixel-Output? Scoring:
   - 1.0: voll funktionierendes Board mit Spalten + Karten sichtbar
   - 0.5: Header + leere Spalten ohne Inhalt
   - 0.0: leere/abgebrochene Seite (nur Titel oder gar nichts)

2. **column_completeness** — Alle 4 erwarteten Spalten *Backlog / In Progress / Review / Done* visuell erkennbar?
   - 1.0: alle vier klar beschriftet, sauber abgegrenzt
   - 0.5: zwei oder drei sichtbar, oder Labels falsch
   - 0.0: keine Spalten erkennbar

3. **cards_present** — Sind Beispiel-/Dummy-Karten sichtbar in mind. zwei Spalten? Lesbar (kein Overflow / Pixel-Müll)?
   - 1.0: mehrere Karten in mehreren Spalten, gut lesbar
   - 0.5: nur 1-2 Karten oder nur in einer Spalte
   - 0.0: keine Karten zu sehen

4. **ui_affordances** — Sieht der User Bedienelemente: Add-Card-Button, Drag-Cursor, Edit-Hinweise?
   - 1.0: alle drei klar erkennbar
   - 0.5: nur Add-Button oder nur Drag-Affordance
   - 0.0: keine UI-Hinweise

5. **design_quality** — Wirkt das Resultat optisch professionell? Konsistente Abstände, lesbare Typografie, sinnvolle Farben, klare visuelle Hierarchie?
   - 1.0: produktreif, modern, vibrant
   - 0.5: OK aber roh, Abstände/Farben suboptimal
   - 0.0: chaotisch, unleserlich, störendes Layout

### Code-Qualität (aus HTML — 4 Achsen)

6. **code_structure** — JS-Code logisch organisiert in Funktionen, klare Trennung State / Render / Events?
   - 1.0: saubere Modul-Struktur, kleine Funktionen, klarer Datenfluss
   - 0.5: alles in main() / IIFE, aber lesbar
   - 0.0: Spaghetti, alles im global scope

7. **dom_safety** — Wie wird User-Text in die DOM gesetzt? `textContent` (sicher), `innerHTML` mit String-Concat (XSS), Sanitizer?
   - 1.0: durchgängig `textContent` oder Sanitizer
   - 0.5: gemischt, mit Disclaimer / Trust-Annahme
   - 0.0: rohes `innerHTML` mit User-Eingaben

8. **robustness** — Error-Handling um `localStorage`, `JSON.parse`, Event-Listener? Fallback bei kaputtem State?
   - 1.0: try/catch + Default-State + null-checks
   - 0.5: einfache Defaults, kein try/catch
   - 0.0: blind angenommen alles klappt

9. **code_quality** — Variablen-/Funktionsnamen, Kommentare, keine Toten-Code-Reste, keine offensichtlichen Bugs?
   - 1.0: durchdacht, gut kommentiert
   - 0.5: passt aber kantig
   - 0.0: schlecht benannt, offensichtliche Bugs (z.B. unverwendete Listener)

### Konsistenz-Check (1 Achse)

10. **render_matches_code** — Entspricht das gerenderte Bild dem, was der Code verspricht? Steht im HTML "renderCards()" aber das Bild zeigt nichts? Oder fehlt im Code etwas, das das Bild zeigt? **Critical-Failure-Detector**.
    - 1.0: Render und Code in Einklang — was im Code steht, sieht man auch
    - 0.5: leichte Abweichungen (z.B. Confetti im Code, nicht statisch sichtbar — OK)
    - 0.0: drastische Diskrepanz (z.B. komplettes Board-JS im Code, gerendert aber nur leerer Header → JS bricht zur Laufzeit)

---

## Ablauf

1. **Eingaben sammeln**: Read tool auf Screenshot (PNG) + HTML-Datei.

2. **Visuell bewerten**: Du bist multimodal, du siehst das Bild direkt. Bewerte die 5 visuellen Achsen.

3. **Code lesen**: lies das HTML. Skim die JS-Logik. Prüfe DOM-Manipulation, Error-Handling, Struktur.

4. **Konsistenz prüfen**: passt das, was du visuell siehst, zu dem, was der Code beschreibt? Nutze diese Achse besonders, um Failure-Cases wie "Code ok, Render leer" zu identifizieren — das ist der Hauptzweck dieser Skill.

5. **JSON-Block schreiben** in `results/coding/<safe-id>.json`. Füge unter `score_breakdown` einen Key `judge` hinzu (oder ersetze ihn):

   ```json
   "judge": {
     "scored_at": "2026-04-28T10:00:00Z",
     "judge_model": "claude-opus-4.7",
     "scores": {
       "board_renders": 0.0,
       "column_completeness": 0.0,
       "cards_present": 0.0,
       "ui_affordances": 0.0,
       "design_quality": 0.0,
       "code_structure": 0.8,
       "dom_safety": 0.7,
       "robustness": 0.6,
       "code_quality": 0.8,
       "render_matches_code": 0.0
     },
     "judge_score": 0.37,
     "comment_visual": "Nur Header sichtbar, alle Spalten leer — JS-Init bricht offenbar.",
     "comment_code": "HTML deklariert sauber 4 Spalten via JS-Render-Funktion mit guter Struktur, aber irgendwas verhindert das Initial-Render. Code-Qualität an sich solide.",
     "comment_consistency": "Drastische Diskrepanz: 487 Zeilen JS-Logik im Code, gerendertes Bild zeigt nur Header. Klassischer Init-Bug."
   }
   ```

   `judge_score` = gewichteter Mittelwert der zehn Einzel-Scores; `board_renders` und `column_completeness` zählen jeweils halb, `design_quality` zählt doppelt. Drei Kommentare (visual / code / consistency) je 1-3 Sätze deutsch.

6. **Datei zurückschreiben**: nutze Edit-Tool. Andere Felder unverändert lassen.

7. **Strikt sequentiell — niemals parallel oder gebatcht**: Ein Coding-Output pro Bewertungs-Durchlauf.
   - **Kein Agent-Tool** für Coding-Bewertung. Nicht parallelisieren, nicht in Sub-Agenten auslagern, nicht 2+ Modelle in einem Reasoning-Schritt vergleichen.
   - Bei mehreren Modellen: ein Modell vollständig durchbewerten (Screenshot lesen → Code lesen → 10 Achsen scoren → Kommentare schreiben → JSON-Update zurückschreiben), **erst dann** zum nächsten Modell übergehen.
   - Begründung: Coding-Outputs sind visuell und im Code so unterschiedlich, dass paralleles Bewerten oder mehrere im Kontext gleichzeitig zu Verwechslungen führt — z.B. Screenshot von Modell A wird mit Code von Modell B kombiniert, oder Achsenwerte werden zwischen Modellen versehentlich gemischt. Ein Modell auf einmal, klar abgegrenzt, ist hier wichtiger als Geschwindigkeit.
   - Nach jedem geschriebenen `judge`-Block: kurze Bestätigung an den User („✓ <model-id> bewertet — judge_score 0.XX"), bevor das nächste Modell gestartet wird.

8. **Nach Abschluss**: User hinweisen, `uv run owb report` auszuführen.

---

## Aufruf-Modi

- `/coding-judge` (ohne Argumente): liste alle Modelle in `results/coding/`, bewerte jedes; falls `judge`-Block existiert, erst nachfragen.
- `/coding-judge <model-id>`: nur dieses Modell bewerten.
- `/coding-judge --redo`: alle Modelle inkl. derer mit existierendem judge neu bewerten.

---

## Wichtige Regeln

- **Ein Modell auf einmal — nie batchen, nie parallelisieren** (siehe Schritt 7). Das ist die Kardinalregel dieses Skills.
- **Sei strikt-kalibriert**: 1.0 ist Spitze; 0.7 ist gut; 0.5 ist Mittelmaß; 0.3 ist schwach; 0.0 ist Totalausfall. Ein Output mit 4 Spalten und Karten aber miserablem Design verdient *nicht* überall 0.7. Sei mutig mit niedrigen Werten.
- **`render_matches_code` ist die wichtigste Achse**: sie entlarvt die häufigsten Fehler-Modi. Setze sie auf 0, wenn Code groß/komplex aber Render leer/abgebrochen ist.
- **Schreibe NUR in den `judge`-Subkey**, andere Felder der `result.json` nicht anfassen.
- **Wenn der Screenshot fehlt** (0-Byte oder kein File): kein judge-Block erzeugen, dem User berichten "kein Screenshot für <model-id>".
- **Wenn das HTML fehlt** oder nicht lesbar: Code-Achsen 0, comment_code "kein Code lesbar".
- **Sprache**: Kommentare deutsch.
