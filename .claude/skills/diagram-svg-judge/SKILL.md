---
name: diagram-svg-judge
description: Bewertet die SVG-Outputs aus dem Diagram-→-SVG-Benchmark visuell. Vergleicht den gerenderten SVG-Screenshot pro Diagramm gegen das Original-Bild entlang acht Achsen mit kontinuierlichen Scores von 0 bis 1. Schreibt einen `judge`-Block pro Diagramm in results/diagram_to_svg/<safe-id>.json.
---

# diagram-svg-judge — visuelle Bewertung der SVG-Diagramm-Repräsentationen

Diese Skill bewertet, wie gut ein VLM ein Original-Diagramm in inline-SVG übersetzt hat. Der deterministische Score (in `score_breakdown.diagrams[i].grade`) deckt nur SVG-Validität und Begriffs-Abdeckung ab — **strukturelle Korrektheit** (sind die richtigen Boxen verbunden? stimmt die Pfeilrichtung? ist die Gruppierung erkennbar?) erkennt nur ein menschen- oder Modell-Auge im Visualvergleich.

Das ist genau die Aufgabe dieses Skills: zwei Bilder vergleichen — Original und SVG-Render — und entlang **acht Achsen** bewerten. Die genannten Werte sind Kalibrierungsbeispiele; Zwischenwerte sind ausdrücklich erlaubt.

---

## Eingaben pro Modell × Diagramm

Für jedes Modell in `results/diagram_to_svg/`:

1. `results/diagram_to_svg/<safe-model-id>.json` — Liste der Diagramme in `score_breakdown.diagrams[]`
2. Pro Diagramm `d`:
   - `artifacts/<safe-model-id>/diagram_to_svg/<d.image_name>` — **Original-Bild** (Read tool, multimodal)
   - `artifacts/<safe-model-id>/diagram_to_svg/<d.id>.render.png` — **SVG-Render** des Modells (Read tool, multimodal)
   - Wenn kein `render_path` da ist (SVG war nicht renderbar): überspringe dieses Diagramm und vermerke „kein Render verfügbar".

Beide Bilder *gemeinsam* anschauen. Die SVG-Datei selbst (`d.svg_path`) ist nicht entscheidend für diese Bewertung — sie ist nur Backup-Kontext.

---

## Bewertungs-Achsen (8, jeweils kontinuierlich 0..1)

Vergib pro Achse einen realistischen Score zwischen 0 und 1. Nutze die Beispiele unten als Orientierung, aber vergib Zwischenwerte wie 0.2, 0.65 oder 0.85, wenn sie die beobachtete Qualität besser treffen. 1.0 bleibt sehr guten Outputs vorbehalten; 0.0 ist ein Totalausfall.

### 1. `completeness` — Sind alle wichtigen Knoten/Boxen vorhanden?

- **nahe 1.0**: Jede Box / jeder Knoten / jede Spalte aus dem Original ist im Render erkennbar. Maximal 1 unwichtiges Element fehlt.
- **um 0.5**: 2–3 Boxen/Knoten fehlen, aber die Hauptstruktur ist erkennbar.
- **nahe 0.0**: Mehr als 3 Boxen fehlen ODER zentrale Elemente (z.B. der zentrale „API Gateway"-Knoten in der Architektur) fehlen ODER der Render ist leer/fast leer.

### 2. `labels` — Stimmen die Beschriftungen?

Bewerte semantisch, nicht zeichenweise: „Authentifizierung" und „Auth" sind beide OK, „Authetifikation" auch (Tippfehler). „User DB" statt „Datenbank" ist OK. Aber „Frontend" verwechselt mit „Backend" zählt als Fehler.

- **nahe 1.0**: Alle Beschriftungen semantisch korrekt; höchstens eine kleine Verwechslung.
- **um 0.5**: 2–3 Beschriftungen falsch oder fehlen, der Rest passt.
- **nahe 0.0**: Mehr als 3 Beschriftungen falsch ODER mehrere Boxen sind komplett unbeschriftet ODER Texte sind unleserlich (zu klein, überlappt, abgeschnitten).

### 3. `connections` — Sind die richtigen Knoten miteinander verbunden?

Topologisch: Wer zeigt auf wen? Pfeile/Linien zwischen den Knoten.

- **nahe 1.0**: Alle wichtigen Verbindungen aus dem Original sind im Render vorhanden und verbinden die richtigen Endpunkte. Höchstens 1 Verbindung fehlt oder ist falsch verbunden.
- **um 0.5**: 2–3 Verbindungen falsch verbunden oder fehlen, der Großteil stimmt.
- **nahe 0.0**: Mehr als 3 Verbindungen falsch oder fehlen ODER es gibt gar keine Verbindungen ODER die Boxen sind zwar da, aber wahllos verkabelt.

### 4. `direction` — Stimmt die Pfeilrichtung?

Nur relevant, wenn das Original gerichtete Pfeile zeigt (Sequenz, Flowchart, Architektur mit Datenfluss). Bei rein assoziativen Diagrammen (Quadrant-Matrix ohne Pfeile) → **N/A** und nicht einbeziehen (siehe Aggregation unten).

- **nahe 1.0**: Alle Pfeilrichtungen entsprechen dem Original. Markers/Pfeilspitzen sind sichtbar.
- **um 0.5**: 1–2 Pfeile haben falsche Richtung oder es fehlen Pfeilspitzen (nur Linien).
- **nahe 0.0**: Mehrere Richtungen falsch ODER gar keine Pfeile/Spitzen, obwohl das Original welche hat.

### 5. `grouping` — Sind Subsysteme/Container/Quadranten erkennbar?

Wenn das Original Boxen-um-Gruppen, Hintergrund-Farbe pro Subsystem, Quadrant-Aufteilung o.ä. zeigt — wird das im Render reproduziert?

- **nahe 1.0**: Alle Gruppen sind klar abgegrenzt (Container-Box, Hintergrund, Trennlinien) UND mit dem Gruppen-Label versehen.
- **um 0.5**: Gruppen sind erkennbar (z.B. räumlich zusammengeschoben) aber ohne sichtbare Box/Trennung; oder Gruppen-Labels fehlen.
- **nahe 0.0**: Keine Gruppierung erkennbar — alle Knoten gleichberechtigt im Raum verstreut.

Bei Diagrammen ohne Gruppierung im Original (z.B. einfache Flowchart): **N/A**.

### 6. `layout_readability` — Ist der Render lesbar?

Subjektiv, aber konkret: Überlappen Boxen? Kreuzen Pfeile chaotisch? Ragen Texte aus ihren Boxen?

- **nahe 1.0**: Sauberes Layout, kaum Überlappungen, Pfeile vermeiden Kollisionen, Texte bleiben in ihren Boxen.
- **um 0.5**: Lesbar, aber unschön — einige Pfeile kreuzen, Texte hängen am Rand, Abstände unregelmäßig.
- **nahe 0.0**: Chaos — Boxen überlappen, Texte unleserlich, Pfeile gehen ins Leere oder kreuzen wahllos.

### 7. `diagram_kind_match` — Passt der Diagrammtyp?

- **nahe 1.0**: SVG-Render hat denselben Diagrammtyp wie das Original (Flowchart bleibt Flowchart, Sequenz bleibt Sequenz mit Lifelines, Quadrant bleibt 2×2-Matrix).
- **um 0.5**: Verwandt, aber falsch repräsentiert (z.B. Sequenz als Flowchart, Quadrant als Liste).
- **nahe 0.0**: Komplett anderer Typ (z.B. Architektur als Tortendiagramm).

### 8. `aesthetic_quality` — Ist der Render schön und visuell poliert?

Bewerte die visuelle Qualität des SVG-Renders selbst: Proportionen, Abstände, Farbwahl, Typografie, Linienführung, Konsistenz und Gesamteindruck. Diese Achse ist bewusst zusätzlich zur reinen Lesbarkeit; ein lesbares, aber sehr rohes Diagramm kann hier niedriger abschneiden.

- **nahe 1.0**: Visuell sauber und professionell: harmonische Abstände, konsistente Formen/Linien, angenehme Farben, gute Schriftgrößen, ausgewogene Gesamtkomposition.
- **um 0.5**: Zweckmäßig, aber sichtbar roh oder unausgewogen: unruhige Abstände, mäßige Farbwahl, inkonsistente Größen, wenig gestalterische Sorgfalt.
- **nahe 0.0**: Unansehnlich oder amateurhaft: chaotische Komposition, störende Farben, stark inkonsistente Elemente, visuelle Artefakte oder kaum gestalterische Ausarbeitung.

---

## Aggregation

`judge_score` = gewichteter Mittelwert über die anwendbaren Achsen. `aesthetic_quality` zählt doppelt; alle anderen Achsen zählen einfach. `direction` und `grouping` bei „N/A" → ausgeschlossen aus Mittelwert und aus dem `scores`-Dict weglassen.

---

## Ablauf

1. **Eingaben pro Diagramm sammeln**: Read tool auf `image_path` (Original) und `render_path` (SVG-Render). Beide Bilder sind PNG/JPG und werden multimodal gelesen.

2. **Pro Diagramm 8 Achsen scoren**: Vergleiche die zwei Bilder. Vergib kontinuierliche Scores 0..1; nutze Zwischenwerte, wenn sie genauer sind.

3. **Pro Diagramm einen `judge`-Block ergänzen** in `results/diagram_to_svg/<safe-id>.json` unter `score_breakdown.diagrams[i]`:

   ```json
   "judge": {
     "scored_at": "2026-04-28T10:00:00Z",
     "judge_model": "claude-opus-4.7",
     "scores": {
       "completeness": 0.5,
       "labels": 1.0,
       "connections": 0.5,
       "direction": 1.0,
       "grouping": 0.0,
       "layout_readability": 0.5,
       "diagram_kind_match": 1.0,
       "aesthetic_quality": 0.5
     },
     "judge_score": 0.64,
     "comment": "Alle vier Hauptboxen vorhanden mit korrekten Labels. Worker→Storage und Backend→DB fehlen. Pfeilrichtungen passen. Kein Container/keine Gruppe für „Backend/Async" — alles wirkt gleichberechtigt verstreut. Layout lesbar, leichte Überlappung Frontend↔Gateway."
   }
   ```

   `comment` ist 2–4 Sätze deutsch, **konkret und beobachtungsbasiert**. Keine Allgemeinplätze („das Diagramm könnte besser sein"). Schreibe, *was du siehst*: welche Box fehlt, welcher Pfeil falsch herum, welcher Text unleserlich.

4. **Aggregat-Block auf Result-Ebene**: nach allen Diagrammen einen `judge`-Block direkt unter `score_breakdown` schreiben:

   ```json
   "judge": {
     "scored_at": "...",
     "judge_model": "...",
     "judge_score": <mittelwert über alle diagrams[i].judge.judge_score>
   }
   ```

5. **Strikt sequentiell**:
   - Ein Diagramm vollständig durchbewerten (Original + Render anschauen → 7 Achsen → JSON-Update), **erst dann** zum nächsten.
   - Ein Modell vollständig fertig, bevor das nächste Modell beginnt.
   - **Kein Agent-Tool**, keine Parallelisierung. Diagramme sind visuell unterschiedlich genug, dass paralleles Scoring zu Bildverwechslungen führt.
   - Nach jedem Modell kurze Bestätigung: „✓ <model-id>: <n> Diagramme bewertet, judge_score 0.XX".

6. **Nach Abschluss**: User auf `uv run owb report` hinweisen.

---

## Aufruf-Modi

- `/diagram-svg-judge` (ohne Argumente): alle Modelle in `results/diagram_to_svg/` durchgehen; übersprungen werden Modelle, deren Aggregat-`judge` schon existiert (außer mit `--redo`).
- `/diagram-svg-judge <model-id>`: nur dieses Modell bewerten.
- `/diagram-svg-judge --redo`: alle Modelle inkl. derer mit existierendem judge neu bewerten.

---

## Wichtige Regeln

- **Kontinuierliche Scores** — nutze die volle 0..1-Skala. Kalibriere streng, aber erzwinge keine festen Stufen.
- **N/A-Achsen weglassen** — `direction` bei pfeillosem Quadrant, `grouping` bei einfachem Flowchart. Nicht mit 0 oder 1 raten.
- **Nichts erfinden** — wenn du nicht sicher bist, ob eine bestimmte Box im Render ist, schreib es in den Kommentar („vermutlich Frontend, beschriftet mit unleserlichem Text") statt einer Halb-Punktzahl.
- **Schreibe nur in den `judge`-Subkey** pro Diagramm und in den Aggregat-`judge` direkt unter `score_breakdown`. Andere Felder unverändert lassen.
- **Wenn `render_path` fehlt** (SVG war nicht renderbar): kein judge-Block, im Kommentar des Aggregats vermerken: „<n> von <m> Diagrammen ohne Render — übersprungen".
- **Sprache**: Kommentare deutsch.
