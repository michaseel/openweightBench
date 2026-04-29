"""Nonsense resistance: ask absurd questions, see if the model pushes back."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..client.lmstudio import LMStudioClient
from ..core.results import Artifact, BenchStore, Metrics, ModelInfo, TaskResult
from .base import Task

_PUSHBACK_DE = [
    r"\bunsinn\b",
    r"\bquatsch\b",
    r"\bnicht\s+sinnvoll\b",
    r"\bnicht\s+beantwortbar\b",
    r"\bnicht\s+zu\s+beantworten\b",
    r"\bkeine\s+sinnvolle\s+antwort\b",
    r"\bergibt\s+keinen\s+sinn\b",
    r"\bnicht\s+messbar\b",
    r"\bsinnlos\w*\b",
    r"\bist\s+(eine\s+|die\s+)?frage\s+(ist\s+)?sinnlos\b",
    r"\bdie\s+frage\s+ist\s+(sinnlos|absurd|unsinnig|nicht\s+(beantwortbar|sinnvoll))\b",
    r"\bist\s+falsch\s+gestellt\b",
    r"\bkategorienfehler\b",
    r"\bist\s+keine\s+(richtige\s+|sinnvolle\s+)?frage\b",
    r"\bweder\s+sinnvoll\s+noch\b",
    r"\bist\s+ein\s+witz\b",
    r"\bist\s+humorvoll\b",
    r"\bironisch\s+(formuliert|gemeint)\b",
    r"\b(scherzhaft|scherzfrage)\b",
    # broader negation patterns: "X benötigt keinen Y", "X hat keine Y", etc.
    # Optional ein Adverb ("daher", "doch", "jedoch") zwischen Verb und "kein".
    r"\b(hat|haben|hatte|hatten|benötigt|benötigen|braucht|brauchen|kennt|kennen|"
    r"spricht|sprechen|wachsen?|wächst|kocht|kochen|isst|essen|trinkt|trinken|"
    r"liest|lesen|atmet|atmen|schläft|schlafen|sieht|sehen|hört|hören|fühlt|fühlen)\s+"
    r"(?:\w+\s+){0,2}kein(en|e|er|es)?\s+\w+\b",
    r"\b(spricht|kennt|hat|wächst|wachsen|kocht|trägt|trinkt|isst)\s+"
    r"keine\s+(muttersprache|sprache|farbe|geschmack|tonart|temperatur|meinung)\b",
    r"\bist\s+kein(e|en|er|es)?\s+(kochtopf|substanz|objekt|gegenstand|lebewesen|"
    r"ding|physische[srn]?|materielle[srn]?|farbe|tonart|mensch|person|raum|gefühl)\b",
    r"\b(ist|wäre)\s+(ein\s+|eine\s+)?(mathematisches?|abstraktes?|sprachliches?|"
    r"rhetorisches?|geometrisches?|metaphorisches?|theoretisches?)\s+konzept\b",
    r"\b(metapher|metaphorisch|abstrakt(e|er|es)?\s+begriff)\b",
    r"\bnicht\s+(physikalisch|materiell|kochbar|essbar|hörbar|sichtbar|messbar)\b",
    r"\bnicht\s+rund\b",
    r"\bsind\s+(nie|nicht)\s+rund\b",
    r"\bvier\s+ecken\b",
    r"\bkeine\s+eigene\s+(tonart|farbe|sprache|temperatur)\b",
    r"\b0\s+(pfund|kg|kilo|gramm|zentimeter|liter)\b",
    # bare verb-not patterns for "X wachsen/kocht/spricht/... nicht"
    r"\b(wachsen|wächst|kocht|spricht|liest|fließt|isst|trinkt|atmet|schläft|"
    r"träumt|hört|sieht|fährt|geht|kommt|läuft|denkt|kann)\s+(nicht|nie)\s+lesen\b",
    r"\b(wachsen|wächst|kocht|spricht|liest|fließt|isst|trinkt|atmet|schläft|"
    r"träumt|hört|sieht|fährt|geht|kommt|läuft|denkt)\s+(nicht|nie)\b",
    r"\bkein\s+elektrischer\s+strom\b",
    r"\b(weder|keine)\s+(spannung|strom|farbe|geschmack|gefühl|emotion)\b",
    # "X ist ein Konzept / Gefühl / abstrakter Begriff / mathematisches Konzept"
    # Erlaubt mehrere Adjektive vor dem Schlüsselbegriff:
    # "ist ein abstraktes mathematisches Konzept".
    r"\bist\s+(ein(e)?|kein(e)?)?\s*(\w+\s+){0,3}(konzept|gefühl|emotion|absicht|begriff|"
    r"vorstellung|abstraktion|hypothese|theorie|idee|wert|zustand)\b",
    r"\bist\s+ein\s+\w+\s+oder\s+(eine?|ein)\s+\w+\b",
    # "X benötigen kein Y", "X benötigt kein Y" — verb-conjugations
    r"\b(benötigen|benötigt|brauchen|braucht|kennen|kennt|sprechen|spricht|"
    r"haben|hat|wachsen|wächst|fließen|fließt)\s+kein(en|e|er|es)?\s+\w+",
    r"\b(benötigen|brauchen|kennen|sprechen|wachsen|haben)\s+kein\s+\w+",
    # "kann nicht in Zentimetern gemessen werden", "lässt sich nicht messen"
    r"\bkann\s+nicht\s+(in|mit)\s+\w+\s+gemessen\b",
    r"\b(lässt|lassen)\s+sich\s+nicht\s+(in|mit)?\s*\w*\s*(messen|gemessen|wiegen|gewogen|"
    r"zählen|gezählt|quantifizieren|quantifiziert|ausgedrückt|berechnen|berechnet)\b",
    r"\bnicht\s+in\s+\w+\s+(messbar|ausdrückbar|quantifizierbar|zählbar)\b",
    r"\bkein\s+physischer\s+gegenstand\b",
    r"\bkein\s+(sprecher|mensch|tier|gegenstand|objekt)\b",
    # Ich kann nicht … / Ich sehe keine
    r"\bich\s+(sehe|kann|habe)\s+(keine?|nicht)\b",
    # Meinen Sie vielleicht … (clarification request)
    r"\bmein(en|st)\s+sie\s+(vielleicht|eventuell|möglicherweise)\b",
    r"\bbitte\s+präzisieren\s+sie\b",
]

_PUSHBACK_EN = [
    r"\bnonsense\b",
    r"\bcategory\s+(error|mistake)\b",
    r"\bdoesn'?t\s+make\s+sense\b",
    r"\bnot\s+a\s+meaningful\s+question\b",
    r"\bill[-\s]formed\b",
    r"\bcannot\s+be\s+answered\b",
]

_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _PUSHBACK_DE + _PUSHBACK_EN]


def _detect_pushback(text: str) -> bool:
    if not text or not text.strip():
        return False
    clean = re.sub(r"\*+", "", text)
    return any(p.search(clean) for p in _PATTERNS)


class NonsenseTask(Task):
    name = "nonsense"
    label = "Nonsense-Resistenz"

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
        breakdown: list[dict] = []
        wall_total = 0.0
        tokens_total = 0
        speeds: list[float] = []

        system_msg = {"role": "system", "content": self.spec.get("system", "")}

        for q in self.spec["questions"]:
            messages = [system_msg, {"role": "user", "content": q["prompt"]}]
            try:
                resp = client.chat(model.id, messages, max_tokens=4000)
                text = resp.effective_text
                wall_total += resp.metrics.wall_seconds
                tokens_total += resp.metrics.tokens_generated
                if resp.metrics.tokens_per_second:
                    speeds.append(resp.metrics.tokens_per_second)
                if not text and resp.truncated_reasoning:
                    detected = False
                    error = "Reasoning ohne Antwort abgebrochen (max_tokens erreicht)"
                else:
                    detected = _detect_pushback(text)
                    error = None
            except Exception as e:  # noqa: BLE001
                text = ""
                detected = False
                error = str(e)

            breakdown.append(
                {
                    "id": q["id"],
                    "prompt": q["prompt"],
                    "response": text,
                    "pushback_detected": detected,
                    "error": error,
                }
            )

        completed = self.now()
        hits = sum(1 for b in breakdown if b["pushback_detected"])
        score = hits / len(breakdown) if breakdown else 0.0

        artifact_path = artifact_dir / "responses.json"
        artifact_path.write_text(json.dumps(breakdown, indent=2, ensure_ascii=False))

        avg_speed = sum(speeds) / len(speeds) if speeds else 0.0

        return TaskResult(
            task=self.name,
            model_id=model.id,
            model_info=model,
            started_at=started,
            completed_at=completed,
            metrics=Metrics(
                wall_seconds=wall_total,
                tokens_generated=tokens_total,
                tokens_per_second=avg_speed,
            ),
            score=score,
            score_breakdown={
                "questions": breakdown,
                "hits": hits,
                "total": len(breakdown),
            },
            artifacts=[
                Artifact(
                    kind="json",
                    label="Alle Fragen + Antworten",
                    path=str(artifact_path.relative_to(store.root)),
                    mime="application/json",
                )
            ],
        )
