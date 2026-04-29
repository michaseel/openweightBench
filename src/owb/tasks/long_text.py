"""Two long-context tasks built on the same book corpus:

  - ComprehensionTask: 5 plot/character questions answered from the text.
  - SummarizationTask:  exact 4-sentence summary of the whole book.

Both share corpus loading + context-aware loading via `client.ensure_context`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..client.lmstudio import LMStudioClient
from ..core.results import Artifact, BenchStore, Metrics, ModelInfo, TaskResult
from .base import Task

CHARS_PER_TOKEN = 2.5


def _trim_to_tokens(text: str, target: int) -> str:
    target_chars = int(target * CHARS_PER_TOKEN)
    return text if len(text) <= target_chars else text[:target_chars]


def _load_book(corpus_files: list[Path], target_tokens: int) -> str:
    parts = []
    for f in corpus_files:
        parts.append(f"\n\n=== {f.name} ===\n\n")
        parts.append(f.read_text())
    joined = "".join(parts)
    return _trim_to_tokens(joined, target_tokens)


def _ensure_corpus_file(text: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size != len(text.encode()):
        path.write_text(text)
    return path


class ComprehensionTask(Task):
    name = "comprehension"
    label = "Long-Context Fragen (Standalone)"

    def __init__(
        self,
        prompt_file: Path,
        corpus_files: list[Path],
        corpus_path: Path,
    ) -> None:
        self.spec = json.loads(prompt_file.read_text())
        self.corpus_files = corpus_files
        self.target_tokens = int(self.spec.get("context_target_tokens", 150_000))
        self.corpus_path = corpus_path
        self.min_context_tokens = int(self.target_tokens * 1.3)

    def applicable(self, model: ModelInfo) -> bool:
        if not super().applicable(model):
            return False
        return model.max_context_length >= self.min_context_tokens

    def _build_corpus(self) -> str:
        text = _load_book(self.corpus_files, self.target_tokens)
        _ensure_corpus_file(text, self.corpus_path)
        return text

    @staticmethod
    def _score_factual(
        answer: str,
        expected: list[str],
        min_match: int = 1,
    ) -> tuple[float, list[str]]:
        """Any-of-keywords scoring. min_match required for full credit."""
        text = answer.lower()
        matched = [kw for kw in expected if kw.lower() in text]
        if not expected:
            return 0.0, []
        if len(matched) >= min_match:
            return 1.0, matched
        # partial credit if at least 1 keyword found but below threshold
        return len(matched) / max(min_match, 1), matched

    @staticmethod
    def _score_trap(answer: str) -> tuple[float, str]:
        """Did the model refuse / admit ignorance instead of inventing?

        Reuses the hallucination heuristic. Returns (score, verdict).
        """
        from .hallucination import classify

        verdict = classify(answer)
        score = 1.0 if verdict in {"corrected", "abstained"} else 0.0
        return score, verdict

    def run(
        self,
        client: LMStudioClient,
        model: ModelInfo,
        store: BenchStore,
    ) -> TaskResult:
        artifact_dir = store.artifact_dir(self.name, model.id)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        started = self.now()
        corpus = self._build_corpus()
        questions_block = "\n".join(
            f"{i+1}. {q['prompt']}" for i, q in enumerate(self.spec["questions"])
        )
        user = (
            "Du bekommst gleich einen langen Text aus einer deutschen Erzählung. "
            "Beantworte anschließend ausschließlich auf Basis dieses Textes alle "
            "Fragen — keine Allgemeinwissen-Annahmen, keine Erfindungen.\n\n"
            "===== TEXT BEGINN =====\n"
            f"{corpus}\n"
            "===== TEXT ENDE =====\n\n"
            "Fragen:\n"
            f"{questions_block}\n\n"
            "Antworte als nummerierte Liste 1., 2., 3. … mit jeweils 1–2 Sätzen."
        )

        client.ensure_context(model.id, self.min_context_tokens)
        try:
            resp = client.chat(
                model.id,
                [
                    {"role": "system", "content": self.spec.get("system", "")},
                    {"role": "user", "content": user},
                ],
                max_tokens=3000,
                temperature=0.2,
                timeout_s=900.0,
            )
            answer = resp.effective_text
            metrics = resp.metrics
            err = None
        except Exception as e:  # noqa: BLE001
            answer = ""
            metrics = Metrics(wall_seconds=900)
            err = str(e)

        completed = self.now()

        # Heuristic per-question scoring: split numbered answer.
        per_q = self._split_numbered(answer, len(self.spec["questions"]))
        breakdown: list[dict] = []
        for i, q in enumerate(self.spec["questions"]):
            ans_part = per_q[i] if i < len(per_q) else answer
            qtype = q.get("type", "factual")
            entry: dict = {
                "id": q["id"],
                "type": qtype,
                "prompt": q["prompt"],
                "answer": ans_part,
            }
            if qtype == "trap":
                s, verdict = self._score_trap(ans_part or answer)
                entry["verdict"] = verdict
                entry["trap_explanation"] = q.get("trap_explanation", "")
                entry["score"] = s
                entry["hit"] = s >= 0.5
            else:
                expected = q.get("expected_keywords", [])
                min_match = int(q.get("min_match", 1))
                s, matched = self._score_factual(ans_part or answer, expected, min_match)
                entry["expected_keywords"] = expected
                entry["matched_keywords"] = matched
                entry["min_match"] = min_match
                entry["score"] = s
                entry["hit"] = s >= 0.5
            breakdown.append(entry)

        hits = sum(1 for b in breakdown if b["hit"])
        if err is not None:
            score = 0.0  # any chat error => 0 points, no partial credit
        elif breakdown:
            score = sum(b["score"] for b in breakdown) / len(breakdown)
        else:
            score = 0.0

        ans_path = artifact_dir / "answer.txt"
        ans_path.write_text(answer)
        bd_path = artifact_dir / "breakdown.json"
        bd_path.write_text(json.dumps(breakdown, indent=2, ensure_ascii=False))

        return TaskResult(
            task=self.name,
            model_id=model.id,
            model_info=model,
            started_at=started,
            completed_at=completed,
            metrics=metrics,
            score=score,
            score_breakdown={
                "questions": breakdown,
                "hits": hits,
                "total": len(breakdown),
                "raw_answer": answer,
                "context_tokens_target": self.target_tokens,
            },
            error=err,
            artifacts=[
                Artifact(
                    kind="text",
                    label=f"Korpus (~{self.target_tokens // 1000}k Tokens)",
                    path=str(self.corpus_path.relative_to(store.root)),
                    mime="text/plain",
                ),
                Artifact(
                    kind="text",
                    label="Antwort des Modells",
                    path=str(ans_path.relative_to(store.root)),
                    mime="text/plain",
                ),
            ],
        )

    @staticmethod
    def _split_numbered(text: str, n: int) -> list[str]:
        """Split `1. ... 2. ... 3. ...` style answer into segments."""
        if not text:
            return [""] * n
        pattern = re.compile(r"^\s*(\d+)[\.\)]\s+", re.MULTILINE)
        positions = [m.start() for m in pattern.finditer(text)]
        if len(positions) < 2:
            return [text] + [""] * (n - 1)
        positions.append(len(text))
        return [text[positions[i]:positions[i + 1]].strip() for i in range(len(positions) - 1)]


# -------------------------------------------------------------------- summary


class SummarizationTask(Task):
    name = "summarization"
    label = "Long-Context Zusammenfassung (Standalone)"

    def __init__(
        self,
        prompt_file: Path,
        corpus_files: list[Path],
        corpus_path: Path,
    ) -> None:
        self.spec = json.loads(prompt_file.read_text())
        self.corpus_files = corpus_files
        self.target_tokens = int(self.spec.get("context_target_tokens", 150_000))
        self.corpus_path = corpus_path
        self.min_context_tokens = int(self.target_tokens * 1.3)
        self.expected_keywords: list[str] = self.spec.get("expected_keywords", [])
        self.required_sentences = int(self.spec.get("required_sentences", 4))
        self.max_words = int(self.spec.get("max_words", 200))

    def applicable(self, model: ModelInfo) -> bool:
        if not super().applicable(model):
            return False
        return model.max_context_length >= self.min_context_tokens

    def _build_corpus(self) -> str:
        text = _load_book(self.corpus_files, self.target_tokens)
        _ensure_corpus_file(text, self.corpus_path)
        return text

    @staticmethod
    def _count_sentences(text: str) -> int:
        # Split on . ! ? followed by whitespace or end-of-string.
        # Trim trailing whitespace / quote marks.
        text = text.strip().strip('"').strip("'")
        # Avoid counting "z.B." etc. as sentence enders by requiring whitespace+capital
        # after the punctuation. This is a heuristic.
        parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])", text)
        # Filter empty
        return sum(1 for p in parts if p.strip())

    @staticmethod
    def _word_count(text: str) -> int:
        return len(re.findall(r"\b\w+\b", text, re.UNICODE))

    def run(
        self,
        client: LMStudioClient,
        model: ModelInfo,
        store: BenchStore,
    ) -> TaskResult:
        artifact_dir = store.artifact_dir(self.name, model.id)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        started = self.now()
        corpus = self._build_corpus()
        user = (
            "Im Folgenden ein langer deutscher Erzähltext. Fasse den GESAMTEN Inhalt "
            f"in EXAKT {self.required_sentences} Sätzen zusammen.\n"
            "- Jeder Satz steht für sich (keine Aufzählung, keine Bullet Points).\n"
            "- Decke Hauptfiguren, Schauplatz und Hauptkonflikt ab.\n"
            "- Maximal "
            f"{self.max_words} Wörter insgesamt.\n"
            "- Keine Vorbemerkung, keine Überschrift, nur die vier Sätze.\n\n"
            "===== TEXT BEGINN =====\n"
            f"{corpus}\n"
            "===== TEXT ENDE ====="
        )

        client.ensure_context(model.id, self.min_context_tokens)
        try:
            resp = client.chat(
                model.id,
                [
                    {"role": "system", "content": self.spec.get("system", "")},
                    {"role": "user", "content": user},
                ],
                max_tokens=2500,
                temperature=0.2,
                timeout_s=900.0,
            )
            answer = resp.effective_text.strip()
            metrics = resp.metrics
            err = None
        except Exception as e:  # noqa: BLE001
            answer = ""
            metrics = Metrics(wall_seconds=900)
            err = str(e)

        completed = self.now()

        sent_count = self._count_sentences(answer) if answer else 0
        words = self._word_count(answer) if answer else 0
        keyword_hits = sum(
            1 for kw in self.expected_keywords if kw.lower() in answer.lower()
        )
        keyword_total = len(self.expected_keywords) or 1

        checks = [
            {
                "id": "sentence_count",
                "label": f"Exactly {self.required_sentences} sentences",
                "passed": sent_count == self.required_sentences,
                "detail": f"{sent_count} sentences counted",
            },
            {
                "id": "max_words",
                "label": f"At most {self.max_words} words",
                "passed": words <= self.max_words,
                "detail": f"{words} words",
            },
            {
                "id": "keyword_coverage",
                "label": f"Plot keywords ({keyword_hits}/{keyword_total})",
                "passed": keyword_hits >= max(1, int(keyword_total * 0.6)),
                "detail": ", ".join(
                    f"{'✓' if kw.lower() in answer.lower() else '✗'} {kw}"
                    for kw in self.expected_keywords
                ),
            },
        ]
        passed = sum(1 for c in checks if c["passed"])
        if err is not None:
            score = 0.0  # any chat error => 0 points (no partial credit for empty answers passing length checks)
        else:
            score = passed / len(checks) if checks else None

        ans_path = artifact_dir / "summary.txt"
        ans_path.write_text(answer)

        return TaskResult(
            task=self.name,
            model_id=model.id,
            model_info=model,
            started_at=started,
            completed_at=completed,
            metrics=metrics,
            score=score,
            score_breakdown={
                "summary": answer,
                "checks": checks,
                "passed": passed,
                "total": len(checks),
                "sentence_count": sent_count,
                "word_count": words,
                "keyword_hits": keyword_hits,
                "keyword_total": keyword_total,
                "context_tokens_target": self.target_tokens,
            },
            error=err,
            artifacts=[
                Artifact(
                    kind="text",
                    label=f"Korpus (~{self.target_tokens // 1000}k Tokens)",
                    path=str(self.corpus_path.relative_to(store.root)),
                    mime="text/plain",
                ),
                Artifact(
                    kind="text",
                    label="Zusammenfassung",
                    path=str(ans_path.relative_to(store.root)),
                    mime="text/plain",
                ),
            ],
        )


# ----- Combined long-context task: summary + comprehension in one chat ------


class LongContextTask(Task):
    """Single chat that combines summary + comprehension on the same corpus.

    Reuses the comprehension prompt's question pool and the summarization
    formatting checks. Saves one full prompt-processing cycle (~150k tokens)
    per model — the biggest practical wall-time win on Apple Silicon.
    """

    name = "long_context"
    label = "Long-Context Kombi (Summary + Fragen)"

    def __init__(
        self,
        comprehension_prompt: Path,
        summarization_prompt: Path,
        corpus_files: list[Path],
        corpus_path: Path,
    ) -> None:
        self.comp_spec = json.loads(comprehension_prompt.read_text())
        self.sum_spec = json.loads(summarization_prompt.read_text())
        self.corpus_files = corpus_files
        self.corpus_path = corpus_path
        self.target_tokens = int(
            self.comp_spec.get(
                "context_target_tokens",
                self.sum_spec.get("context_target_tokens", 150_000),
            )
        )
        self.required_sentences = int(self.sum_spec.get("required_sentences", 4))
        self.max_words = int(self.sum_spec.get("max_words", 200))
        self.expected_summary_keywords: list[str] = self.sum_spec.get(
            "expected_keywords", []
        )
        self.min_context_tokens = int(self.target_tokens * 1.3)

    def applicable(self, model: ModelInfo) -> bool:
        if not super().applicable(model):
            return False
        return model.max_context_length >= self.min_context_tokens

    def _build_corpus(self) -> str:
        text = _load_book(self.corpus_files, self.target_tokens)
        _ensure_corpus_file(text, self.corpus_path)
        return text

    def _build_prompt(self, corpus: str) -> str:
        questions = "\n".join(
            f"{i + 1}. {q['prompt']}"
            for i, q in enumerate(self.comp_spec["questions"])
        )
        return (
            "Du bekommst gleich einen sehr langen deutschen Erzähltext "
            f"(~{self.target_tokens // 1000}k Tokens). Bearbeite anschließend "
            "ZWEI Aufgaben in einer einzigen Antwort.\n\n"
            "===== TEXT BEGINN =====\n"
            f"{corpus}\n"
            "===== TEXT ENDE =====\n\n"
            f"AUFGABE 1: Fasse den GESAMTEN Inhalt in EXAKT "
            f"{self.required_sentences} Sätzen zusammen. Maximal "
            f"{self.max_words} Wörter. Decke Hauptfiguren, Schauplatz und "
            "Hauptkonflikt ab. Keine Aufzählung, keine Vorbemerkung — nur die "
            "Sätze.\n\n"
            "AUFGABE 2: Beantworte folgende Fragen ausschließlich auf Basis "
            "des oben stehenden Textes. Wenn die Antwort im Text nicht steht, "
            "sag das ausdrücklich — erfinde nichts.\n"
            f"{questions}\n\n"
            "Format deiner Antwort EXAKT:\n"
            "[ZUSAMMENFASSUNG]\n"
            "<deine Sätze hier>\n\n"
            "[FRAGEN]\n"
            "1. <Antwort 1>\n"
            "2. <Antwort 2>\n"
            "..."
        )

    @staticmethod
    def _split_sections(text: str) -> tuple[str, str]:
        """Return (summary, qa) splitting on [ZUSAMMENFASSUNG] / [FRAGEN]."""
        m_q = re.search(r"\[FRAGEN\]", text, re.IGNORECASE)
        if not m_q:
            return text, ""
        summary = text[: m_q.start()]
        qa = text[m_q.end():]
        m_s = re.search(r"\[ZUSAMMENFASSUNG\]", summary, re.IGNORECASE)
        if m_s:
            summary = summary[m_s.end():]
        return summary.strip(), qa.strip()

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

    def run(
        self,
        client: LMStudioClient,
        model: ModelInfo,
        store: BenchStore,
    ) -> TaskResult:
        artifact_dir = store.artifact_dir(self.name, model.id)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        started = self.now()
        corpus = self._build_corpus()
        prompt = self._build_prompt(corpus)
        client.ensure_context(model.id, self.min_context_tokens)
        try:
            resp = client.chat(
                model.id,
                [
                    {"role": "system", "content": self.comp_spec.get("system", "")},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4000,
                temperature=0.2,
                timeout_s=900.0,
            )
            answer = resp.effective_text
            metrics = resp.metrics
            err = None
        except Exception as e:  # noqa: BLE001
            answer = ""
            metrics = Metrics(wall_seconds=900)
            err = str(e)
        completed = self.now()

        summary, qa_section = self._split_sections(answer)

        # ---- Summarization checks
        from .long_text import SummarizationTask  # for static methods

        sent_count = SummarizationTask._count_sentences(summary) if summary else 0
        words = SummarizationTask._word_count(summary) if summary else 0
        kw_hits = sum(
            1 for kw in self.expected_summary_keywords if kw.lower() in summary.lower()
        )
        kw_total = len(self.expected_summary_keywords) or 1
        sum_checks = [
            {
                "id": "sentence_count",
                "label": f"Exactly {self.required_sentences} sentences",
                "passed": sent_count == self.required_sentences,
                "detail": f"{sent_count} sentences",
            },
            {
                "id": "max_words",
                "label": f"≤{self.max_words} words",
                "passed": 0 < words <= self.max_words,
                "detail": f"{words} words",
            },
            {
                "id": "keyword_coverage",
                "label": f"Plot keywords ({kw_hits}/{kw_total})",
                "passed": kw_hits >= max(1, int(kw_total * 0.6)),
                "detail": ", ".join(
                    f"{'✓' if kw.lower() in summary.lower() else '✗'} {kw}"
                    for kw in self.expected_summary_keywords
                ),
            },
        ]

        # ---- Comprehension scoring (factual OR-keywords + trap detection)
        per_q = self._split_numbered(qa_section, len(self.comp_spec["questions"]))
        comp_breakdown: list[dict] = []
        for i, q in enumerate(self.comp_spec["questions"]):
            ans_part = per_q[i] if i < len(per_q) else qa_section
            qtype = q.get("type", "factual")
            entry: dict = {
                "id": q["id"],
                "type": qtype,
                "prompt": q["prompt"],
                "answer": ans_part,
            }
            if qtype == "trap":
                s, verdict = ComprehensionTask._score_trap(ans_part or qa_section)
                entry["verdict"] = verdict
                entry["trap_explanation"] = q.get("trap_explanation", "")
                entry["score"] = s
                entry["hit"] = s >= 0.5
            else:
                expected = q.get("expected_keywords", [])
                min_match = int(q.get("min_match", 1))
                s, matched = ComprehensionTask._score_factual(
                    ans_part or qa_section, expected, min_match
                )
                entry["expected_keywords"] = expected
                entry["matched_keywords"] = matched
                entry["min_match"] = min_match
                entry["score"] = s
                entry["hit"] = s >= 0.5
            comp_breakdown.append(entry)

        sum_passed = sum(1 for c in sum_checks if c["passed"])
        comp_score_avg = (
            sum(b["score"] for b in comp_breakdown) / len(comp_breakdown)
            if comp_breakdown
            else 0
        )
        sum_score_avg = sum_passed / len(sum_checks) if sum_checks else 0
        if err is not None:
            score = 0.0
        else:
            # weighted: equal weight summary vs questions
            score = (sum_score_avg + comp_score_avg) / 2

        ans_path = artifact_dir / "answer.txt"
        ans_path.write_text(answer)
        bd_path = artifact_dir / "breakdown.json"
        bd_path.write_text(
            json.dumps(
                {
                    "summary": summary,
                    "summary_checks": sum_checks,
                    "questions": comp_breakdown,
                    "raw_answer": answer,
                },
                indent=2,
                ensure_ascii=False,
            )
        )

        return TaskResult(
            task=self.name,
            model_id=model.id,
            model_info=model,
            started_at=started,
            completed_at=completed,
            metrics=metrics,
            score=score,
            score_breakdown={
                "summary": summary,
                "summary_checks": sum_checks,
                "summary_passed": sum_passed,
                "summary_total": len(sum_checks),
                "sentence_count": sent_count,
                "word_count": words,
                "keyword_hits": kw_hits,
                "keyword_total": kw_total,
                "questions": comp_breakdown,
                "comp_hits": sum(1 for b in comp_breakdown if b["hit"]),
                "comp_total": len(comp_breakdown),
                "context_tokens_target": self.target_tokens,
            },
            error=err,
            raw_response=answer[:2000],
            artifacts=[
                Artifact(
                    kind="text",
                    label=f"Korpus (~{self.target_tokens // 1000}k Tokens)",
                    path=str(self.corpus_path.relative_to(store.root)),
                    mime="text/plain",
                ),
                Artifact(
                    kind="text",
                    label="Antwort des Modells",
                    path=str(ans_path.relative_to(store.root)),
                    mime="text/plain",
                ),
            ],
        )
