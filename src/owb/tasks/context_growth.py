"""Context-Growth Bench.

Misst, wie sich die Generierungs-Geschwindigkeit (Tokens/s) und die
Antwortqualität verändern, während der Konversationskontext durch
wiederholtes Echo-Spielen wächst.

Ablauf:
  - System-Prompt: "Du bist ein Echo-Bot."
  - Pro Turn schickt der Benchmark einen Chunk aus 3-4 Buchsätzen, das
    Modell soll exakt diesen Text zurückgeben.
  - Die Antwort wird als Assistant-Message in die Historie übernommen,
    der nächste Chunk geht im selben Chat raus — der Kontext wächst
    monoton.
  - Pro Turn werden tok/s, Wall-Time, prompt/completion-Tokens,
    TTFT und Match-Quality (exakt + whitespace-normalisiert) erfasst.

Score = Anteil der Turns mit normalisiertem Match. Erfolg + Speed-Verlauf
landen im score_breakdown, damit der Report einen Verlaufs-Chart rendern
kann.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from ..client.lmstudio import LMStudioClient
from ..core.results import Artifact, BenchStore, Metrics, ModelInfo, TaskResult
from .base import Task

DEFAULT_NUM_CHUNKS = 40
DEFAULT_WORDS_PER_CHUNK = 35
# Reasoning/Thinking abschalten, soweit das jeweilige Modell auf einen der
# gängigen Schalter reagiert. Der Echo-Job profitiert nicht von Reasoning,
# und Thinking-Tokens verzerren tokens_per_second.
#   - 'detailed thinking off' → NVIDIA Nemotron
#   - '/no_think'            → Qwen3
#   - reasoning_effort: low   → OpenAI-style Modelle (gpt-oss, o-Modelle)
SYSTEM_PROMPT = (
    "Du bist ein Echo-Bot. Deine einzige Aufgabe ist es, den Text zwischen "
    "den Markern '=== TEXT BEGIN ===' und '=== TEXT END ===' wortwörtlich, "
    "Zeichen für Zeichen zurückzugeben. Keine Erklärung, kein Kommentar, "
    "keine Anführungszeichen, keine Markdown-Formatierung, keine Marker. "
    "Nur den reinen Text — exakt wie er kam.\n\n"
    "detailed thinking off"
)
USER_TEMPLATE = (
    "/no_think\n"
    "Gib den folgenden Text exakt und unverändert zurück:\n\n"
    "=== TEXT BEGIN ===\n{chunk}\n=== TEXT END ==="
)
THINKING_OFF_EXTRA = {"reasoning_effort": "low"}
# Großzügig dimensioniert, damit Thinking-Modelle nicht abgeschnitten werden,
# bevor sie zur eigentlichen Antwort kommen (Bug bei nemotron-3-nano-4b).
ECHO_MAX_TOKENS = 8000


_WORD_RE = re.compile(r"\S+")


def _tokenize_words(text: str) -> list[str]:
    """Whitespace-getrennte Tokens — alles, was kein Whitespace ist, zählt
    als Wort (inkl. Satzzeichen). So ist 'gleich viele Wörter' robust und
    deterministisch über alle Chunks."""
    return _WORD_RE.findall(text)


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFC", s).strip()
    return re.sub(r"\s+", " ", s)


def _strip_wrapping(s: str) -> str:
    """Remove a single layer of surrounding quotes / code fences if present."""
    s = s.strip()
    if s.startswith("```"):
        # ```...\n<body>\n```
        m = re.match(r"^```[^\n]*\n(.*?)\n```\s*$", s, re.DOTALL)
        if m:
            s = m.group(1).strip()
    if len(s) >= 2:
        opener = s[0]
        closer = s[-1]
        pairs = {'"': '"', "'": "'", "`": "`", "„": "“", "«": "»", "‹": "›"}
        if opener in pairs and closer == pairs[opener]:
            s = s[1:-1].strip()
    return s


class ContextGrowthTask(Task):
    """Echo-Loop, der den Konversationskontext schrittweise wachsen lässt."""

    name = "context_growth"
    label = "Context Growth (Echo, 40 Turns)"
    min_context_tokens = 12_000

    def __init__(
        self,
        corpus_files: list[Path],
        num_chunks: int = DEFAULT_NUM_CHUNKS,
        words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK,
    ) -> None:
        self.corpus_files = corpus_files
        self.num_chunks = num_chunks
        self.words_per_chunk = words_per_chunk

    def _load_chunks(self) -> list[str]:
        """Liefert `num_chunks` Chunks mit JEWEILS exakt `words_per_chunk`
        Wörtern aus dem Korpus — strikt gleich lange Eingaben für jede
        Iteration, damit nur die wachsende Konversationshistorie variiert."""
        words: list[str] = []
        for f in self.corpus_files:
            words.extend(_tokenize_words(f.read_text()))
        n = self.words_per_chunk
        chunks: list[str] = []
        i = 0
        while len(chunks) < self.num_chunks and i + n <= len(words):
            chunks.append(" ".join(words[i : i + n]))
            i += n
        return chunks

    def run(
        self,
        client: LMStudioClient,
        model: ModelInfo,
        store: BenchStore,
    ) -> TaskResult:
        artifact_dir = store.artifact_dir(self.name, model.id)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        started = self.now()
        chunks = self._load_chunks()
        if len(chunks) < self.num_chunks:
            return TaskResult(
                task=self.name,
                model_id=model.id,
                model_info=model,
                started_at=started,
                completed_at=self.now(),
                metrics=Metrics(wall_seconds=0.0),
                error=f"Korpus liefert nur {len(chunks)} Chunks, brauche {self.num_chunks}.",
            )

        # Kontext-Window einmal vorab fix laden, damit es während aller
        # 20 Turns gleich bleibt.
        approx_total_chars = sum(len(c) for c in chunks) * 2 + 4000
        target_ctx = max(self.min_context_tokens, int(approx_total_chars / 2.5) + 4000)
        if target_ctx > model.max_context_length:
            target_ctx = model.max_context_length
        client.ensure_context(model.id, target_ctx)

        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        steps: list[dict] = []
        speeds: list[float] = []
        wall_total = 0.0
        tokens_total = 0
        exact_hits = 0
        norm_hits = 0
        any_error: str | None = None

        for i, chunk in enumerate(chunks, 1):
            messages.append({"role": "user", "content": USER_TEMPLATE.format(chunk=chunk)})

            try:
                resp = client.chat(
                    model.id,
                    messages,
                    max_tokens=ECHO_MAX_TOKENS,
                    temperature=0.0,
                    timeout_s=300.0,
                    extra=THINKING_OFF_EXTRA,
                )
                answer = resp.text or ""
            except Exception as e:  # noqa: BLE001
                any_error = f"turn {i}: {e}"
                steps.append(
                    {
                        "turn": i,
                        "input_chunk": chunk,
                        "model_output": "",
                        "exact_match": False,
                        "normalized_match": False,
                        "wall_seconds": 0.0,
                        "tokens_per_second": 0.0,
                        "completion_tokens": 0,
                        "prompt_tokens": 0,
                        "ttft_ms": None,
                        "prefill_seconds": None,
                        "prefill_tps": None,
                        "generation_time": None,
                        "error": str(e),
                    }
                )
                break

            wall_total += resp.metrics.wall_seconds
            tokens_total += resp.metrics.tokens_generated
            if resp.metrics.tokens_per_second:
                speeds.append(resp.metrics.tokens_per_second)

            stripped = _strip_wrapping(answer)
            exact = stripped == chunk.strip()
            norm = _normalize(stripped) == _normalize(chunk)
            if exact:
                exact_hits += 1
            if norm:
                norm_hits += 1

            usage = resp.raw.get("usage", {}) or {}
            stats = resp.raw.get("stats", {}) or {}
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            # `time_to_first_token` ist die Wall-Time vom Senden des Requests
            # bis zum ersten generierten Token — bei nicht-streamender Inferenz
            # praktisch komplett Prefill (Prompt-Processing).
            prefill_s = stats.get("time_to_first_token")
            prefill_s = float(prefill_s) if prefill_s is not None else None
            prefill_tps = (
                prompt_tokens / prefill_s if prefill_s and prefill_s > 0 and prompt_tokens else None
            )
            steps.append(
                {
                    "turn": i,
                    "input_chunk": chunk,
                    "model_output": answer,
                    "exact_match": exact,
                    "normalized_match": norm,
                    "wall_seconds": resp.metrics.wall_seconds,
                    "tokens_per_second": resp.metrics.tokens_per_second,
                    "completion_tokens": resp.metrics.tokens_generated,
                    "prompt_tokens": prompt_tokens,
                    "ttft_ms": resp.metrics.time_to_first_token_ms,
                    "prefill_seconds": prefill_s,
                    "prefill_tps": prefill_tps,
                    "generation_time": stats.get("generation_time"),
                    "error": None,
                }
            )

            # Antwort in den Verlauf packen, damit der Kontext echt wächst.
            messages.append({"role": "assistant", "content": answer})

        completed = self.now()

        completed_turns = sum(1 for s in steps if s["error"] is None)
        score = (norm_hits / completed_turns) if completed_turns else 0.0

        avg_speed = sum(speeds) / len(speeds) if speeds else 0.0
        first_speed = speeds[0] if speeds else 0.0
        last_speed = speeds[-1] if speeds else 0.0
        slowdown_pct = (
            (1.0 - last_speed / first_speed) * 100.0 if first_speed else 0.0
        )
        prefill_seconds_series = [
            s.get("prefill_seconds")
            for s in steps
            if s.get("prefill_seconds") is not None
        ]
        first_prefill = prefill_seconds_series[0] if prefill_seconds_series else None
        last_prefill = prefill_seconds_series[-1] if prefill_seconds_series else None
        prefill_tps_series = [
            s.get("prefill_tps") for s in steps if s.get("prefill_tps") is not None
        ]
        first_prefill_tps = prefill_tps_series[0] if prefill_tps_series else None
        last_prefill_tps = prefill_tps_series[-1] if prefill_tps_series else None

        bd_path = artifact_dir / "steps.json"
        bd_path.write_text(json.dumps(steps, indent=2, ensure_ascii=False))

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
                "steps": steps,
                "num_chunks": self.num_chunks,
                "words_per_chunk": self.words_per_chunk,
                "completed_turns": completed_turns,
                "exact_hits": exact_hits,
                "normalized_hits": norm_hits,
                "first_speed": first_speed,
                "last_speed": last_speed,
                "slowdown_pct": slowdown_pct,
                "first_prefill_seconds": first_prefill,
                "last_prefill_seconds": last_prefill,
                "first_prefill_tps": first_prefill_tps,
                "last_prefill_tps": last_prefill_tps,
                "context_window_loaded": target_ctx,
            },
            error=any_error,
            artifacts=[
                Artifact(
                    kind="json",
                    label="Schritt-für-Schritt Metriken",
                    path=str(bd_path.relative_to(store.root)),
                    mime="application/json",
                ),
            ],
        )
