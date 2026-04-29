---
name: summary-judge
description: Bewertet die Korpus-Zusammenfassungen aus dem NIAH-Benchmark (Turn 1) auf Korrektheit, Vollständigkeit, Erfindungen. Der Skill kennt das Buch durch eine eingebettete Detail-Zusammenfassung — er muss den 120k-Korpus nicht selbst lesen. Schreibt einen `judge`-Block in die NIAH-Breakdown.
---

# summary-judge — semantische Bewertung der NIAH-Korpus-Zusammenfassung

Der NIAH-Benchmark fragt das Modell in Turn 1, den Korpus zu summarisieren. Der bisherige Score (`_score_summary` in `src/owb/tasks/niah.py`) prüft nur drei Dinge: 3–5 Sätze, ≤220 Wörter, mind. 2 von 4 Stichwörtern (Gottlieb, Malineken, Schmied, Bonaparte). Das ist mechanisch und übersieht:
- ob die genannten Figuren *in der richtigen Rolle* erscheinen (z.B. Malineken als Sohn statt Mädchen)
- erfundene Plot-Elemente (ein Onkel, eine Tante, eine Königin Luise als Hauptfigur etc.)
- Standortverwechslungen (Bayern statt Mark)

Dieser Skill ist die LLM-as-Judge-Ergänzung. Er hat die Buchwahrheit eingebettet und bewertet jede Zusammenfassung dagegen.

---

## Eingebettetes Buchwissen — „Im Blumentalwald" (5 Kapitel, ~86k Wörter)

Das Buch ist eine **erbauliche Kindergeschichte aus der Zeit um 1850–1880**, spielt während der **napoleonischen Besatzung Preußens (1810–1813)** und vereint deutsch-patriotische, christliche und Abenteuer-Elemente. Schreibweise altdeutsch („giebt", „daß", „Rußland", „infolge", „Wimpern feucht").

### Schauplatz
- **„Blumental"** — ein dichter Mischwald (Eichen, Buchen, Edeltannen, Birken, Espen) nahe der Stadt **Wriezen** in der **Mark Brandenburg**, der **preußischen Provinz**. Im Wald liegen mehrere **Seen**, einer davon enthält eine **Insel**, die später dramatische Bedeutung bekommt.
- **Finkenwalde** — benachbartes Dorf mit Wirtshaus, Versammlungsort des heimlichen Widerstands.
- **Wriezen** — Kreisstadt, dort einquartierte französische Truppen, dort sitzt **Kapitän Etienne de Beaumont**.

### Hauptfiguren

- **Gottlieb Lasso** (auch „Haßlo" geschrieben — vermutlich Setzfehler, beide Schreibweisen kommen vor; einmal „Gottlieb Lasso", später „Gottlieb Haßlo"): junger Schmiedelehrling, **Waisenkind**, lebt bei Schmied Lebbin. Seine Eltern wurden vom französischen Kapitän Etienne de Beaumont mit einem Holzpantoffel erschlagen. Im Buch ringt er zwischen Rachegedanken und christlichem Vergebungsethos. Wird wegen seiner Mutigkeit „junger Goliath" von einem französischen Soldaten genannt.
- **Malineken**: junges **Mädchen** (sehr wichtig — verwechseln Modelle gerne mit „Sohn" oder „Bruder"), Tochter aus dem Fischerhaus an der Insel, Vater ist Fischer auf der Insel. Sie ist Gottliebs Freundin, eulenruft („Du hu, ju hu!") als geheimes Signal an Gottlieb.
- **Michael Lebbin**: der **Schmied vom Blumental**, Gottliebs Lehrmeister. Patriotisch, am heimlichen Widerstand beteiligt, geht zur Versammlung in Finkenwalde.
- **Die Meisterin**: Lebbins Frau, mütterliche Figur für Gottlieb, fromme Christin, betet mit ihm den Abendsegen.
- **Schneider Hägelin**: Antagonist, **Verräter** an seinen Landsleuten, wird im Verlauf von der preußischen Seite getötet (Kapitel 4 endet mit seiner Leiche, Oberst sagt „Er hat seinen Lohn empfangen — christliche Soldaten sollen sich nicht damit beflecken, einen Verräter nur anzurühren").
- **Die Gräfin**: patriotische Adlige, deren **Bräutigam** unter Schill kämpfte und in **Wesel** erschossen wurde. Sie agitiert für den **Tugendbund**, erzählt den Kindern Schills Geschichte (siehe historische Bezüge), reist von Ort zu Ort. Erscheint zu Besuch im Blumental.
- **Oberst Alexander von Hatzrow** (Achtung Inkonsistenz im Originaltext: in Kap. 4 als „Hatzrow" eingeführt, in Kap. 5 als „Satzrow" referenziert — beide gelten): preußischer Offizier, der zunächst **als Schweinetreiber verkleidet** im Blumental auftritt, am Ende seine wahre Identität enthüllt und **drei preußische Kriegskassen** rettet, die auf der Insel versteckt waren. Lobt Gottlieb für Mut und Umsicht.
- **Kapitän Etienne de Beaumont**: französischer Offizier, Antagonist, **Mörder von Gottliebs Eltern** mit einem Holzpantoffel, einquartiert in Wriezen. Im Buch das personifizierte Übel der Besatzung.
- **Bonaparte / Napoleon**: Hintergrundfigur, „feuriger Irrstern, der die Welt in Schrecken versetzt" (Zitat der Meisterin); kommt persönlich im Text **nicht vor** — wird nur als Ursache des Elends erwähnt.

### Handlung pro Kapitel

**Kap. 1 — „Gottlieb und Malineken" (~15.000 Wörter)**
Idyllische Beschreibung des Blumentals. Gottlieb erfährt von der Meisterin, dass seine Eltern vom Kapitän Etienne de Beaumont getötet wurden. Er schwört Rache, die Meisterin mahnt ihn an „Die Rache ist mein, ich will vergelten" (5. Mose 32,35) und dass er sich Gottes Willen unterwerfen solle.

**Kap. 2 — „Das Geheimnis der Insel" (~17.000 Wörter)**
Schmied Lebbin geht zur heimlichen Patrioten-Versammlung in Finkenwalde. Politischer Hintergrund: Hass gegen Napoleon. Gottlieb lernt die Insel im See kennen — dort versteckt der Tugendbund preußische Kriegskassen vor den Franzosen. Gottlieb wird von französischen Soldaten gefangen genommen, soll erschossen werden, beruhigt sich im Gebet, denkt an „Jesus meine Zuversicht". Malineken signalisiert ihm mit Eulenruf.

**Kap. 3 — „Die Prinzessin vom See" (~19.000 Wörter)**
Gottlieb wird begnadigt / freigelassen (Wendepunkt). Die Gräfin trifft Malineken am See und erzählt ausführlich die Geschichte von **Major Ferdinand von Schill** — preußischer Offizier, der 1809 eigenmächtig gegen Napoleon zog, in **Stralsund** fiel, dessen Offiziere in **Wesel** erschossen wurden, dessen Gemeine in **Braunschweig** standrechtlich exekutiert wurden. Die Gräfin selbst hat ihren Bräutigam dort verloren. Patriotische Kindheits-Inspiration.

**Kap. 4 — „Der Schneider Hägelin" (~21.000 Wörter)**
Konfrontation mit dem Verräter Hägelin, der die Insel und die Kriegskassen an die Franzosen verraten will. Gottlieb gerät erneut in Gefahr. Der vermeintliche Schweinetreiber stellt sich als preußischer Oberst **von Hatzrow** heraus, fängt mit seinen Soldaten den Plan ab. Hägelin wird getötet, der Oberst rettet die Kriegskassen mit Gottliebs Hilfe.

**Kap. 5 — „Die Heimat des Schweinetreibers" (~14.000 Wörter)**
Ein Brief vom Obersten von Satzrow trifft ein (mit Wappen: entwurzelter Eichenbaum). Erzählung des nahenden Ende der Besatzung. Erwähnung des **Großen Kometen 1811** als Vorbote des Krieges gegen Russland. Schluss-Stimmung: „Es wird Friede!" Schnitter mähen den zweiten Klee-Schnitt. Erzählung schließt mit fragender Reflexion: „Was ist's mit Gottlieb und Malineken … geworden?" — und einem christlichen Trost-Zitat, dass alle Treuen Christus wiedersehen werden.

### Zentrale Themen
1. **Christlicher Glaube als Trost** und Halt in Notzeiten — sehr viele Bibelzitate, Lieder („Jesus meine Zuversicht"), Gebete.
2. **Patriotischer Widerstand** gegen die napoleonische Besatzung — Tugendbund, Schill, preußische Kriegskassen.
3. **Rache vs. Vergebung** — Gottliebs innerer Kampf, schließlich Sieg der christlichen Vergebungsethik.
4. **Kinder als Vorbild** — die Erzählung richtet sich explizit an junge Leser, will sie patriotisch und fromm bilden.
5. **Verrat als Sünde** — Hägelin wird kompromisslos verurteilt, sein Tod als „Lohn" gerechtfertigt.

### Was im Buch NICHT vorkommt (häufige Halluzinationen)
- **Kein Onkel von Gottlieb** — er ist Waise.
- **Keine Tante oder andere bekannte Verwandte**.
- **Königin Luise von Preußen** wird nicht erwähnt (lebt zwar im historischen Kontext, kommt aber im Text nicht vor).
- **Napoleon persönlich** tritt nicht auf — er ist nur Hintergrundfigur.
- **Kein Schloss** im Blumental, **keine Burg**, keine andere klassische Märchenkulisse.
- **Keine Drachen, keine Magie** — es ist realistisch, kein Märchen.
- **Kein Text-Setting in Süddeutschland / München / Bayern** — alles spielt in der Mark Brandenburg.
- **Kein militärischer Sieg Gottliebs gegen Napoleon persönlich** — nur Rettung von Kriegskassen.

---

## Bewertungsschema

Für jede Zusammenfassung liefert der Judge:

```json
{
  "judge": {
    "scored_at": "2026-04-28T10:00:00Z",
    "judge_model": "claude-opus-4.7",
    "scores": {
      "main_characters_correct": 0.0,
      "setting_correct": 0.0,
      "plot_correct": 0.0,
      "no_hallucinations": 0.0,
      "themes_captured": 0.0
    },
    "judge_score": 0.0,
    "comment": "1-3 Sätze deutsch — Stärken/Schwächen der Zusammenfassung."
  }
}
```

### Bewertungs-Achsen (jede 0..1)

1. **main_characters_correct** — Werden die zentralen Figuren in *richtiger Rolle* genannt?
   - 1.0: Gottlieb (Schmiedelehrling/Waise), Malineken (Mädchen), Schmied Lebbin, Kap. de Beaumont oder die Gräfin in passenden Rollen.
   - 0.7: zwei Figuren korrekt, eine vage/fehlend.
   - 0.4: Hauptfigur erwähnt, Rollen aber falsch (Malineken als Junge, Lebbin als Bauer).
   - 0.0: keine Figuren oder vollständig falsche Rollen.

2. **setting_correct** — Schauplatz und Epoche?
   - 1.0: Blumental / Mark Brandenburg / nahe Wriezen / napoleonische Besatzung.
   - 0.7: Wald + napoleonische Zeit, ohne Ortsdetails.
   - 0.4: nur „im Wald" oder „im 19. Jahrhundert", ohne politischen Kontext.
   - 0.0: völlig falsch (Süddeutschland, Mittelalter, Märchenwelt).

3. **plot_correct** — Wird der grobe Handlungsbogen erfasst?
   - 1.0: Eltern getötet → Rache erwogen → Insel-Geheimnis (Kriegskassen / Tugendbund) → Verräter Hägelin → Befreiung.
   - 0.7: zwei zentrale Plot-Punkte korrekt.
   - 0.4: nur ein Plot-Element.
   - 0.0: erfundener Plot.

4. **no_hallucinations** — Gibt es erfundene Elemente?
   - 1.0: keine Erfindungen.
   - 0.5: kleine Ungenauigkeiten (z.B. falsches Jahr, falscher Onkel-Name *als beiläufig*).
   - 0.0: zentrale Erfindungen (erfundener Antagonist, falsche Rolle, erfundener Schauplatz).

5. **themes_captured** — Werden zumindest 1–2 zentrale Themen erfasst (christlicher Glaube, patriotischer Widerstand, Rache/Vergebung, Erbauungsliteratur)?
   - 1.0: explizit benannt.
   - 0.5: angedeutet aber nicht explizit.
   - 0.0: Themen ignoriert.

### Aggregierter judge_score

Gewichteter Mittelwert (zentralere Achsen wiegen mehr):

```
judge_score = (
    0.25 * main_characters_correct +
    0.15 * setting_correct +
    0.25 * plot_correct +
    0.25 * no_hallucinations +
    0.10 * themes_captured
)
```

---

## Wo der Judge schreibt — Single Source of Truth

**Ausschließlich** in `results/niah/<safe-model-id>.json`. Die Datei `artifacts/<safe-model-id>/niah/breakdown.json` ist nur eine vom Bench-Lauf erzeugte **Kopie** desselben Breakdowns — der Report-Builder (`src/owb/report/builder.py`) liest aus `results/`. Schreibe niemals in `artifacts/...` — die Werte würden im Report nicht erscheinen und divergieren.

Konvention identisch zum bestehenden `coding-judge`: Per-Stage-Detail in `score_breakdown.lengths[i].judge`, Aggregat in `score_breakdown.judge`. Das Aggregat-Feld matcht den Pfad, den `_effective_score()` bei `coding` ausliest.

### Eingaben

`results/niah/<safe-model-id>.json` — Top-Level-Form:

```json
{
  "task": "niah",
  "model_id": "...",
  "score": 0.42,
  "score_breakdown": {
    "lengths": [
      {
        "length_tokens": 120000,
        "skipped": false,
        "raw_summary": "Die Geschichte spielt in einem Wald ...",
        "raw_answer": "...",
        "summary_score": 0.67,
        "retrieval_score": 0.4,
        "comprehension_score": 0.5,
        "combined_score": 0.46,
        ...
      }
    ],
    "targets": [...],
    "score_components": {...}
  }
}
```

Pro Stage steht die zu bewertende Zusammenfassung unter `lengths[i].raw_summary`. Du brauchst **keine** Files aus `artifacts/` zu lesen — alles liegt schon in `results/...`.

### Ausgabe nach dem Judge-Lauf

Erweiterung **in derselben Datei**:

```json
{
  ...,
  "score_breakdown": {
    "lengths": [
      {
        "length_tokens": 120000,
        ...,
        "judge": {
          "scored_at": "2026-04-28T10:00:00Z",
          "judge_model": "claude-opus-4.7",
          "scores": {
            "main_characters_correct": 1.0,
            "setting_correct": 0.7,
            "plot_correct": 0.7,
            "no_hallucinations": 0.5,
            "themes_captured": 1.0
          },
          "judge_score": 0.755,
          "comment": "Erfasst Gottlieb/Malineken/Schmied korrekt und nennt Napoleon-Kontext. Kleine Erfindung: Modell führt 'Onkel' ein, der nicht existiert."
        }
      }
    ],
    ...,
    "judge": {
      "scored_at": "2026-04-28T10:00:00Z",
      "judge_model": "claude-opus-4.7",
      "by_stage": {
        "120000": 0.745
      },
      "judge_score": 0.745
    }
  }
}
```

`judge_score` aggregiert ist der **arithmetische Mittelwert über alle Stages**, sodass bei `top_stage_only=True` einfach der Stage-Wert übernommen wird. Pro Stage wird `judge_score` als gewichteter Mittelwert der 6 Achsen berechnet (Gewichte siehe oben).

---

## Ablauf

1. **Eingabe lesen**: `results/niah/<safe-model-id>.json`. Gehe `score_breakdown.lengths[]` durch (idR 1 Stage bei `niah`, bis zu 4 bei `niah_deep`).

2. **Pro Stage bewerten**: Lies `lengths[i].raw_summary`, bewerte die 6 Achsen gegen die eingebettete Buchwahrheit.

3. **Judge-Block schreiben**: Über ein Python-Skript via Bash, um JSON-Integrität zu sichern:

   ```python
   # python3 << 'EOF'
   import json
   p = "results/niah/<model>.json"
   d = json.load(open(p))
   per_stage_judges = {
       120000: {
           "scores": {
               "main_characters_correct": 1.0,
               "setting_correct": 0.7,
               "plot_correct": 0.7,
               "no_hallucinations": 0.5,
               "themes_captured": 1.0,
           },
           "comment": "...",
       },
   }
   weights = {
       "main_characters_correct": 0.25,
       "setting_correct": 0.15,
       "plot_correct": 0.25,
       "no_hallucinations": 0.25,
       "themes_captured": 0.10,
   }
   by_stage = {}
   for L in d["score_breakdown"]["lengths"]:
       j = per_stage_judges[L["length_tokens"]]
       js = sum(j["scores"][k] * weights[k] for k in weights)
       L["judge"] = {
           "scored_at": "...",
           "judge_model": "claude-opus-4.7",
           "scores": j["scores"],
           "judge_score": round(js, 3),
           "comment": j["comment"],
       }
       by_stage[L["length_tokens"]] = round(js, 3)
   mean = sum(by_stage.values()) / len(by_stage)
   d["score_breakdown"]["judge"] = {
       "scored_at": "...",
       "judge_model": "claude-opus-4.7",
       "by_stage": by_stage,
       "judge_score": round(mean, 3),
   }
   json.dump(d, open(p, "w"), indent=2, ensure_ascii=False)
   # EOF
   ```

4. **Andere Felder unverändert lassen** — insbesondere die bestehenden `summary_score`, `retrieval_score`, `comprehension_score`, `combined_score`. Der Judge ergänzt, ersetzt nicht.

5. **Strikt sequentiell — ein Modell nach dem anderen**: Bei mehreren Modellen ein Modell **vollständig** abarbeiten (alle Stages lesen → 6 Achsen pro Stage scoren → Python-Update → Bestätigung), erst dann zum nächsten wechseln.
   - **Kein Agent-Tool, keine Parallelisierung**, kein Batching mehrerer Modelle in einem Reasoning-Schritt.
   - Begründung: Zusammenfassungen verschiedener Modelle erwähnen häufig dieselben Figuren (Gottlieb, Malineken, Schmied) und können oberflächlich sehr ähnlich aussehen — paralleles Bewerten führt schnell zu Verwechslungen, wo Plot-Erfindungen oder Halluzinationen einem falschen Modell zugeschrieben werden.
   - Nach jedem geschriebenen Aggregat-`judge`-Block: kurze Bestätigung an den User („✓ <model-id> @ <stage>k: judge_score=0.NN, [Hauptfindings]"), bevor das nächste Modell startet.

6. **Nach Abschluss**: User hinweisen, `uv run owb report` neu zu bauen.

---

## Aufruf-Modi

- `/summary-judge` (ohne Argumente): liste alle Modelle in `artifacts/*/niah/`, bewerte pro Modell jede Stage; falls ein Stage-Judge existiert, erst nachfragen.
- `/summary-judge <model-id>`: nur dieses Modell (alle Stages).
- `/summary-judge --redo`: alle Modelle inkl. derer mit existierendem Judge neu bewerten.

---

## Wichtige Regeln

- **Die eingebettete Buchzusammenfassung oben ist die Quelle**. Verlasse dich nicht auf eigenes Trainings-Wissen — das Buch ist obskur, du würdest es nicht zuverlässig kennen.
- **Sei strikt-kalibriert**: 1.0 ist eine sehr gute Zusammenfassung, 0.5 ist mittelmäßig (ein paar wichtige Elemente, einige Lücken), 0.0 ist ein Total-Daneben (erfundener Plot, falscher Schauplatz). 
- **Erfindungen sind harte Punkt-Killer** — selbst eine hübsch geschriebene Zusammenfassung mit erfundener Tante / erfundenem Schloss bekommt bei `no_hallucinations` höchstens 0.4.
- **Stilkontrolle ist NICHT Aufgabe des Judge** — Sätze zählen, Wörter zählen, Stichwörter prüfen erledigt der Regex-Score in `_score_summary`. Der Judge bewertet *Inhalt*.
- **Einbeziehen der Inkonsistenz Hatzrow/Satzrow**: das Originalbuch hat diesen Setzfehler — wenn ein Modell einen der beiden nennt, ist das **richtig**, ebenso wenn es beide oder keinen erwähnt.
- **Sprache des Kommentars**: deutsch, max. 3 Sätze.
- **Schreibe NUR in den `judge`-Subkey** der Eingabedateien — andere Felder unverändert lassen.
