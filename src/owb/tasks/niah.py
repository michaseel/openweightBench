"""Needle-in-a-Haystack: insert distinctive facts into a long corpus, ask the
model to retrieve them. Three context lengths: 32k, 64k, 128k tokens."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..client.lmstudio import LMStudioClient
from ..core.results import Artifact, BenchStore, Metrics, ModelInfo, TaskResult
from .base import Task

# Conservative chars-per-token ratio so we don't *exceed* the target token
# count when slicing the corpus. German-on-BPE often hits ~2.5 chars/token, so
# we slice short on purpose — the actual token count matches the target tighter.
CHARS_PER_TOKEN = 2.5

STANDARD_TARGET_LENGTHS = [32_000, 64_000, 120_000]
DEEP_TARGET_LENGTHS = [*STANDARD_TARGET_LENGTHS, 200_000]
TARGET_LENGTHS = DEEP_TARGET_LENGTHS
# Absoluter Headroom für Prompt-Overhead, Antwort und Tokenizer-Variabilität.
# Damit qualifiziert sich ein 128k-Kontextmodell (131072 Tokens) für die 120k-Stage.
CONTEXT_HEADROOM_TOKENS = 8_000
SUMMARY_EXPECTED_KEYWORDS = ["Gottlieb", "Malineken", "Schmied", "Bonaparte"]
SUMMARY_MIN_SENTENCES = 3
SUMMARY_MAX_SENTENCES = 5
SUMMARY_MAX_WORDS = 220
# Drei Sub-Benchmarks: Korpus-Zusammenfassung, Needle-Retrieval, Verstehen
# (Faktenfragen + Halluzinations-Fallen aus dem Buchtext).
SUMMARY_SCORE_WEIGHT = 0.2
RETRIEVAL_SCORE_WEIGHT = 0.5
COMPREHENSION_SCORE_WEIGHT = 0.3


def effective_summary_score(length_entry: dict) -> float:
    """Prefer the LLM-judge verdict over the deterministic checks.

    The deterministic summary score only counts sentence/word/keyword presence,
    so it can't see invented characters or distorted plot. When a judge has
    scored the summary we use that score instead.
    """
    j = length_entry.get("judge")
    if isinstance(j, dict) and "judge_score" in j:
        return float(j["judge_score"])
    return float(length_entry.get("summary_score", 0.0))


def effective_comprehension_score(length_entry: dict) -> float:
    """Prefer the LLM-judge verdict over keyword-regex matching."""
    j = length_entry.get("comprehension_judge")
    if isinstance(j, dict) and "judge_score" in j:
        return float(j["judge_score"])
    return float(length_entry.get("comprehension_score", 0.0))


def _approx_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def _trim_to_tokens(text: str, target: int) -> str:
    """Trim text to approximately `target` tokens (using char heuristic)."""
    target_chars = int(target * CHARS_PER_TOKEN)
    if len(text) <= target_chars:
        return text
    return text[:target_chars]


def _insert_at_depth(text: str, needle: str, depth_pct: float) -> str:
    """Insert `needle` (with surrounding line-breaks) at a relative depth."""
    target_idx = int(len(text) * depth_pct)
    # Snap to next paragraph boundary so we don't split a sentence.
    nl = text.find("\n", target_idx)
    if nl == -1:
        nl = target_idx
    return text[:nl] + "\n\n" + needle + "\n\n" + text[nl:]


def _count_sentences(text: str) -> int:
    text = text.strip().strip('"').strip("'")
    if not text:
        return 0
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])", text)
    return sum(1 for p in parts if p.strip())


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text, re.UNICODE))


def _score_summary(summary: str) -> dict:
    sentence_count = _count_sentences(summary)
    words = _word_count(summary)
    keyword_hits = sum(
        1 for kw in SUMMARY_EXPECTED_KEYWORDS if kw.lower() in summary.lower()
    )
    keyword_total = len(SUMMARY_EXPECTED_KEYWORDS)
    checks = [
        {
            "id": "sentence_count",
            "label": f"{SUMMARY_MIN_SENTENCES}-{SUMMARY_MAX_SENTENCES} sentences",
            "passed": SUMMARY_MIN_SENTENCES <= sentence_count <= SUMMARY_MAX_SENTENCES,
            "detail": f"{sentence_count} sentences counted",
        },
        {
            "id": "max_words",
            "label": f"At most {SUMMARY_MAX_WORDS} words",
            "passed": 0 < words <= SUMMARY_MAX_WORDS,
            "detail": f"{words} words",
        },
        {
            "id": "keyword_coverage",
            "label": f"Corpus keywords ({keyword_hits}/{keyword_total})",
            "passed": keyword_hits >= max(1, int(keyword_total * 0.5)),
            "detail": ", ".join(
                f"{'✓' if kw.lower() in summary.lower() else '✗'} {kw}"
                for kw in SUMMARY_EXPECTED_KEYWORDS
            ),
        },
    ]
    passed = sum(1 for c in checks if c["passed"])
    return {
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "score": passed / len(checks) if checks else 0.0,
        "sentence_count": sentence_count,
        "word_count": words,
        "keyword_hits": keyword_hits,
        "keyword_total": keyword_total,
    }


def needle_count_for_target(target: int) -> int:
    """Konstante Needle-Anzahl pro Stage (10) — erlaubt Vergleich, wie sich
    Modelle über einen längeren Kontext verhalten, statt verzerrt durch
    unterschiedliche Stichprobengrößen pro Tiefe."""
    return 10


class NIAHTask(Task):
    """Needle-in-a-Haystack.

    `top_stage_only=True` (Default): nur die jeweils größte applicable
    Kontext-Stufe für ein Modell ausführen — schneller & repräsentativer.
    `top_stage_only=False` (`niah_deep`): alle 4 Stufen für eine vollständige
    Heatmap.
    """

    name = "niah"
    label = "Needle in a Haystack"
    min_context_tokens = TARGET_LENGTHS[0] + CONTEXT_HEADROOM_TOKENS

    def __init__(
        self,
        needles_file: Path,
        corpus_files: list[Path],
        targets: list[int] | None = None,
        haystack_dir: Path | None = None,
        top_stage_only: bool = True,
        name: str | None = None,
        label: str | None = None,
        comprehension_prompt: Path | None = None,
    ) -> None:
        if name is not None:
            self.name = name
        if label is not None:
            self.label = label
        self.spec = json.loads(needles_file.read_text())
        self.all_targets = targets or TARGET_LENGTHS
        self.targets = self.all_targets  # may be filtered per-model in run()
        self.top_stage_only = top_stage_only
        self.haystack_dir = needles_file.parent.parent.parent / "assets" / "niah" if haystack_dir is None else haystack_dir
        # Comprehension/trap questions aus dem Long-Context-Bench übernommen
        # — laufen als 3. Turn im selben Chat, profitieren vom bereits
        # geladenen Korpus (Prefill ist die teure Phase bei 120k Tokens).
        self.comprehension_spec: dict | None = None
        if comprehension_prompt is not None and comprehension_prompt.exists():
            self.comprehension_spec = json.loads(comprehension_prompt.read_text())

        parts: list[str] = []
        for f in corpus_files:
            parts.append(f"\n\n=== {f.name} ===\n\n")
            parts.append(f.read_text())
        joined = "".join(parts)
        max_target_chars = int(self.all_targets[-1] * CHARS_PER_TOKEN * 1.2)
        while len(joined) < max_target_chars:
            joined = joined + "\n\n" + "".join(parts)
        self._corpus = joined

    def _haystack_path(self, target: int) -> Path:
        return self.haystack_dir / f"haystack_{target // 1000}k.txt"

    def applicable(self, model: ModelInfo) -> bool:
        if not super().applicable(model):
            return False
        return model.max_context_length >= self.all_targets[0] + CONTEXT_HEADROOM_TOKENS

    def _targets_for(self, model: ModelInfo) -> list[int]:
        """Pick which target lengths to actually run for this model."""
        eligible = [
            t for t in self.all_targets
            if model.max_context_length >= t + CONTEXT_HEADROOM_TOKENS
        ]
        if not eligible:
            return []
        if self.top_stage_only:
            return [eligible[-1]]  # only the largest the model can handle
        return eligible

    def _needles_for_target(self, target: int) -> list[dict]:
        """Pick first N needles (sorted by depth_pct) where N scales with target."""
        all_needles = sorted(self.spec["needles"], key=lambda n: n["depth_pct"])
        n = min(needle_count_for_target(target), len(all_needles))
        # Take an even sample by depth: pick every k-th to spread coverage
        if n >= len(all_needles):
            return all_needles
        step = len(all_needles) / n
        return [all_needles[int(i * step)] for i in range(n)]

    def _build_haystack(self, target: int) -> str:
        """Trim corpus to ~target tokens and inject the needles for this target."""
        base = _trim_to_tokens(self._corpus, target)
        chosen = self._needles_for_target(target)
        ordered = sorted(chosen, key=lambda n: n["depth_pct"], reverse=True)
        for n in ordered:
            base = _insert_at_depth(base, n["text"], n["depth_pct"])
        return base

    def ensure_haystack(self, target: int) -> Path:
        """Return path to the haystack file for `target`, building it if missing."""
        p = self._haystack_path(target)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(self._build_haystack(target))
        return p

    def _build_for_target(self, target: int) -> tuple[str, list[dict]]:
        """Return (haystack-text, needle-records) for one target length."""
        haystack = self.ensure_haystack(target).read_text()
        records = []
        for n in sorted(self._needles_for_target(target), key=lambda n: n["depth_pct"]):
            records.append({
                "id": n["id"],
                "depth_label": n["depth_label"],
                "depth_pct": n["depth_pct"],
                "text": n["text"],
                "question": n["question"],
                "expected_keywords": n["expected_keywords"],
            })
        return haystack, records

    def _build_summary_message(self, context: str) -> dict:
        return {
            "role": "user",
            "content": (
                "Im folgenden Abschnitt befindet sich ein längerer Mischtext "
                "aus deutschsprachiger Erzählung und Quellcode.\n\n"
                "===== TEXT BEGINN =====\n"
                f"{context}\n"
                "===== TEXT ENDE =====\n\n"
                "Fasse den Inhalt des Textes in 3-5 Sätzen zusammen. "
                "Nenne Hauptfiguren, Schauplatz und die wichtigsten Themen."
            ),
        }

    def _build_questions_message(self, needles: list[dict]) -> dict:
        questions_block = "\n".join(
            f"{i + 1}. {n['question']}" for i, n in enumerate(needles)
        )
        return {
            "role": "user",
            "content": (
                "Beantworte jetzt die folgenden Fragen ausschließlich anhand "
                "des oben gezeigten Textes — erfinde nichts, ergänze nichts "
                "und übernehme keine Allgemeinwissen-Annahmen.\n\n"
                "Fragen:\n"
                f"{questions_block}\n\n"
                "Antworte als nummerierte Liste 1., 2., 3. mit jeweils einem "
                "kurzen Satz."
            ),
        }

    def _build_comprehension_message(self, questions: list[dict]) -> dict:
        block = "\n".join(
            f"{i + 1}. {q['prompt']}" for i, q in enumerate(questions)
        )
        return {
            "role": "user",
            "content": (
                "Beantworte jetzt diese Verständnisfragen zum oben gezeigten "
                "Buchtext — wenn die Frage im Text NICHT beantwortet wird, sag "
                "das ausdrücklich, erfinde nichts.\n\n"
                "Fragen:\n"
                f"{block}\n\n"
                "Antworte als nummerierte Liste 1., 2., 3. mit jeweils 1–2 "
                "kurzen Sätzen."
            ),
        }

    @staticmethod
    def _normalize_for_match(text: str) -> str:
        # LLMs often emit typographic hyphen/dash variants and NBSP when
        # rendering identifiers like "Lübeck-1907" or "7-Bravo-12". Fold them
        # to ASCII so substring matching against the expected keywords works.
        translation = str.maketrans({
            "‐": "-",  # HYPHEN
            "‑": "-",  # NON-BREAKING HYPHEN
            "‒": "-",  # FIGURE DASH
            "–": "-",  # EN DASH
            "—": "-",  # EM DASH
            "―": "-",  # HORIZONTAL BAR
            "−": "-",  # MINUS SIGN
            " ": " ",  # NO-BREAK SPACE
            " ": " ",  # NARROW NO-BREAK SPACE
            " ": " ",  # THIN SPACE
        })
        return text.translate(translation).lower()

    @classmethod
    def _keyword_matches(cls, keyword: str, normalized_text: str) -> bool:
        # A keyword may list `|`-separated alternatives — any one matches.
        alternatives = [a for a in keyword.split("|") if a]
        return any(cls._normalize_for_match(a) in normalized_text for a in alternatives)

    def _score_answer(self, answer: str, needle: dict) -> bool:
        text = self._normalize_for_match(answer)
        return all(self._keyword_matches(kw, text) for kw in needle["expected_keywords"])

    @staticmethod
    def _split_numbered(text: str, n: int) -> list[str]:
        if not text:
            return [""] * n
        pattern = re.compile(r"^\s*(\d+)[\.\)]\s+", re.MULTILINE)
        positions = [m.start() for m in pattern.finditer(text)]
        if len(positions) < 2:
            return [text] + [""] * (n - 1)
        positions.append(len(text))
        return [text[positions[i]:positions[i + 1]].strip() for i in range(len(positions) - 1)]

    @classmethod
    def _score_factual(cls, answer: str, expected: list[str], min_match: int) -> tuple[float, list[str]]:
        text = cls._normalize_for_match(answer)
        matched = [kw for kw in expected if cls._keyword_matches(kw, text)]
        if not expected:
            return 0.0, []
        if len(matched) >= min_match:
            return 1.0, matched
        return len(matched) / max(min_match, 1), matched

    @staticmethod
    def _score_trap(answer: str) -> tuple[float, str]:
        from .hallucination import classify
        verdict = classify(answer)
        return (1.0 if verdict in {"corrected", "abstained"} else 0.0), verdict

    def _score_comprehension(self, answer: str) -> dict:
        """Bewerte Faktenfragen + Halluzinations-Fallen. Liefert per-Frage-
        Breakdown plus aggregierte Werte."""
        spec = self.comprehension_spec or {}
        questions: list[dict] = spec.get("questions", [])
        if not questions:
            return {"score": 0.0, "breakdown": [], "facts_hits": 0, "facts_total": 0, "traps_passed": 0, "traps_total": 0}
        per_q = self._split_numbered(answer or "", len(questions))
        breakdown: list[dict] = []
        facts_total = 0
        facts_hits = 0
        traps_total = 0
        traps_passed = 0
        for i, q in enumerate(questions):
            ans_part = per_q[i] if i < len(per_q) else (answer or "")
            qtype = q.get("type", "factual")
            entry: dict = {"id": q["id"], "type": qtype, "prompt": q["prompt"], "answer": ans_part}
            if qtype == "trap":
                traps_total += 1
                s, verdict = self._score_trap(ans_part or (answer or ""))
                entry["verdict"] = verdict
                entry["trap_explanation"] = q.get("trap_explanation", "")
                entry["score"] = s
                entry["hit"] = s >= 0.5
                if entry["hit"]:
                    traps_passed += 1
            else:
                facts_total += 1
                expected = q.get("expected_keywords", [])
                min_match = int(q.get("min_match", 1))
                s, matched = self._score_factual(ans_part or (answer or ""), expected, min_match)
                entry["expected_keywords"] = expected
                entry["matched_keywords"] = matched
                entry["min_match"] = min_match
                entry["score"] = s
                entry["hit"] = s >= 0.5
                if entry["hit"]:
                    facts_hits += 1
            breakdown.append(entry)
        comp_score = sum(b["score"] for b in breakdown) / len(breakdown)
        return {
            "score": comp_score,
            "breakdown": breakdown,
            "facts_hits": facts_hits,
            "facts_total": facts_total,
            "traps_passed": traps_passed,
            "traps_total": traps_total,
        }

    def run(
        self,
        client: LMStudioClient,
        model: ModelInfo,
        store: BenchStore,
    ) -> TaskResult:
        artifact_dir = store.artifact_dir(self.name, model.id)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        started = self.now()
        per_length: list[dict] = []
        wall_total = 0.0
        tokens_total = 0
        speeds: list[float] = []
        any_run = False

        haystack_artifacts: list[Artifact] = []
        targets_to_run = self._targets_for(model)

        for target in targets_to_run:
            any_run = True

            context, needles = self._build_for_target(target)
            haystack_path = self._haystack_path(target)
            haystack_artifacts.append(
                Artifact(
                    kind="text",
                    label=f"Haystack {target // 1000}k Tokens",
                    path=str(haystack_path.relative_to(store.root)),
                    mime="text/plain",
                )
            )
            # Load model with enough headroom for the haystack + answer + slack
            # for tokenizer variance. Default JIT load is only ~20k.
            client.ensure_context(model.id, target + CONTEXT_HEADROOM_TOKENS)

            # Turn 1: ask for a summary of the long context. This forces the
            # model to actually read & process the corpus once.
            messages = [self._build_summary_message(context)]
            summary = ""
            answer = ""
            err = None
            try:
                resp_sum = client.chat(
                    model.id,
                    messages,
                    max_tokens=8000,
                    temperature=0.1,
                    timeout_s=780.0,
                )
                wall_total += resp_sum.metrics.wall_seconds
                tokens_total += resp_sum.metrics.tokens_generated
                if resp_sum.metrics.tokens_per_second:
                    speeds.append(resp_sum.metrics.tokens_per_second)
                summary = resp_sum.effective_text
            except Exception as e:  # noqa: BLE001
                err = f"summary turn: {e}"

            # Turn 2: in the same chat, ask the NIAH retrieval questions. The
            # full corpus is still part of the conversation history.
            comp_answer = ""
            if err is None:
                messages.append({"role": "assistant", "content": summary})
                messages.append(self._build_questions_message(needles))
                try:
                    resp = client.chat(
                        model.id,
                        messages,
                        max_tokens=8000,
                        temperature=0.1,
                        timeout_s=780.0,
                    )
                    wall_total += resp.metrics.wall_seconds
                    tokens_total += resp.metrics.tokens_generated
                    if resp.metrics.tokens_per_second:
                        speeds.append(resp.metrics.tokens_per_second)
                    answer = resp.effective_text
                except Exception as e:  # noqa: BLE001
                    err = f"questions turn: {e}"

            # Turn 3: Verstehensfragen + Halluzinations-Fallen aus dem
            # Buchkorpus. Nutzt denselben Chat-Kontext, kein zweiter Prefill.
            if err is None and self.comprehension_spec:
                messages.append({"role": "assistant", "content": answer})
                messages.append(
                    self._build_comprehension_message(
                        self.comprehension_spec.get("questions", [])
                    )
                )
                try:
                    resp_comp = client.chat(
                        model.id,
                        messages,
                        max_tokens=8000,
                        temperature=0.1,
                        timeout_s=780.0,
                    )
                    wall_total += resp_comp.metrics.wall_seconds
                    tokens_total += resp_comp.metrics.tokens_generated
                    if resp_comp.metrics.tokens_per_second:
                        speeds.append(resp_comp.metrics.tokens_per_second)
                    comp_answer = resp_comp.effective_text
                except Exception as e:  # noqa: BLE001
                    err = f"comprehension turn: {e}"

            scored = []
            for n in needles:
                hit = bool(answer) and self._score_answer(answer, n)
                scored.append(
                    {
                        "id": n["id"],
                        "depth_label": n["depth_label"],
                        "depth_pct": n["depth_pct"],
                        "expected_keywords": n["expected_keywords"],
                        "hit": hit,
                    }
                )
            summary_score = _score_summary(summary) if summary else {
                "checks": [],
                "passed": 0,
                "total": 0,
                "score": 0.0,
                "sentence_count": 0,
                "word_count": 0,
                "keyword_hits": 0,
                "keyword_total": len(SUMMARY_EXPECTED_KEYWORDS),
            }
            retrieval_hits = sum(1 for s in scored if s["hit"])
            retrieval_total = len(scored)
            retrieval_score = retrieval_hits / retrieval_total if retrieval_total else 0.0
            comp_result = self._score_comprehension(comp_answer) if self.comprehension_spec else None
            comp_score_value = comp_result["score"] if comp_result else 0.0
            if self.comprehension_spec:
                # Initial run: judge hasn't scored yet, use deterministic. Once
                # the judge writes its block, `owb reclassify niah` recomputes
                # combined_score using the judge verdicts.
                combined_score = (
                    summary_score["score"] * SUMMARY_SCORE_WEIGHT
                    + retrieval_score * RETRIEVAL_SCORE_WEIGHT
                    + comp_score_value * COMPREHENSION_SCORE_WEIGHT
                )
            else:
                # Backward-compat: ohne comprehension nur Summary+Retrieval (alte Gewichtung).
                combined_score = (
                    summary_score["score"] * 0.3
                    + retrieval_score * 0.7
                )

            entry = {
                "length_tokens": target,
                "skipped": False,
                "needles": scored,
                "hits": retrieval_hits,
                "total": retrieval_total,
                "retrieval_score": retrieval_score,
                "summary_checks": summary_score["checks"],
                "summary_passed": summary_score["passed"],
                "summary_total": summary_score["total"],
                "summary_score": summary_score["score"],
                "summary_sentence_count": summary_score["sentence_count"],
                "summary_word_count": summary_score["word_count"],
                "summary_keyword_hits": summary_score["keyword_hits"],
                "summary_keyword_total": summary_score["keyword_total"],
                "combined_score": combined_score,
                "raw_summary": summary,
                "raw_answer": answer,
                "approx_corpus_tokens": _approx_tokens(context),
                "error": err,
            }
            if comp_result is not None:
                entry.update(
                    {
                        "comprehension_questions": comp_result["breakdown"],
                        "comprehension_score": comp_result["score"],
                        "comprehension_facts_hits": comp_result["facts_hits"],
                        "comprehension_facts_total": comp_result["facts_total"],
                        "comprehension_traps_passed": comp_result["traps_passed"],
                        "comprehension_traps_total": comp_result["traps_total"],
                        "raw_comprehension_answer": comp_answer,
                    }
                )
            per_length.append(entry)

            # Persist all turns so users can read what the model actually said.
            (artifact_dir / f"{target // 1000}k_summary.txt").write_text(summary or "")
            (artifact_dir / f"{target // 1000}k_answer.txt").write_text(answer or "")
            if comp_answer:
                (artifact_dir / f"{target // 1000}k_comprehension.txt").write_text(comp_answer)

        completed = self.now()

        # Score = average combined summary/retrieval score across completed lengths.
        scoring = [L for L in per_length if not L.get("skipped") and not L.get("error")]
        if scoring:
            score = sum(L["combined_score"] for L in scoring) / len(scoring)
        else:
            score = 0.0 if not any_run else None

        # Persist full breakdown.
        bd_path = artifact_dir / "breakdown.json"
        bd_path.write_text(json.dumps(per_length, indent=2, ensure_ascii=False))

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
                "lengths": per_length,
                "targets": self.targets,
                "score_components": (
                    {
                        "summary": SUMMARY_SCORE_WEIGHT,
                        "needle_retrieval": RETRIEVAL_SCORE_WEIGHT,
                        "comprehension": COMPREHENSION_SCORE_WEIGHT,
                    }
                    if self.comprehension_spec
                    else {"summary": 0.3, "needle_retrieval": 0.7}
                ),
            },
            artifacts=[
                Artifact(
                    kind="json",
                    label="Breakdown pro Kontextlänge",
                    path=str(bd_path.relative_to(store.root)),
                    mime="application/json",
                ),
                *haystack_artifacts,
            ],
        )
