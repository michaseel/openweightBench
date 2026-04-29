"""Thin REST wrapper around LM Studio's local API.

Endpoints used:
  GET  /api/v0/models                  -> model catalog
  POST /api/v0/chat/completions        -> chat with `stats` (tok/s, TTFT, gen_time)
  CLI: `lms ps --json`                 -> currently loaded models with sizeBytes
  CLI: `lms unload <model>`            -> explicit unload (no REST equivalent)

LM Studio JIT-loads models on first chat call.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..core.results import Metrics, ModelInfo

DEFAULT_BASE_URL = "http://127.0.0.1:1234"


# Reasoning-Tags, die manche Modelle direkt im `content`-Feld emittieren statt
# über das separate `reasoning_content`-Feld (z.B. Qwen3, DeepSeek-R1).
_THINK_TAG_RE = re.compile(
    r"<\s*think(?:ing)?\s*>.*?<\s*/\s*think(?:ing)?\s*>", re.DOTALL | re.IGNORECASE
)
# Einzelnes (unbalanciertes) öffnendes <think> ohne Schließung — Cutoff-Fall:
# Modell wurde mitten im Thinking abgeschnitten. Schneide alles ab `<think>`.
_THINK_OPEN_RE = re.compile(r"<\s*think(?:ing)?\s*>", re.IGNORECASE)
# Marker, die in nicht-getaggter Prosa-Reasoning-Antwort den finalen Output
# einleiten ("Final answer:", "```mermaid", "Endgültig:", "---"-Trenner gefolgt
# von Inhalt, …). Wird nur ausgewertet, wenn der Anfang nach Reasoning aussieht.
_PROSE_THINKING_HEAD_RE = re.compile(
    r"^\s*(?:"
    r"(?:here'?s|here\s+is)\s+(?:a\s+|the\s+|my\s+)?(?:thinking|reasoning|thought|plan|analysis|approach)"
    r"|let'?s\s+think|let\s+me\s+(?:think|check|recall|see|verify|consider|analyze|analyse)"
    r"|thinking\s+process"
    # Reasoning-Modelle leaken oft als Drittpersonen-Erzählung der User-Frage:
    # "User asks: …", "User question: …", "The user is asking …",
    # "We need to …", "We must …", "Wait,", "Okay,(\s+let)" als Eröffnung.
    r"|user\s+(?:asks|asked|is\s+asking|wants|question|says)\b"
    r"|the\s+user\s+(?:is\s+asking|wants|asks|question)"
    r"|we\s+(?:need\s+to|must|should|have\s+to)\b"
    r"|wait[,.\s]"
    r"|okay[,\s]+(?:so|let|we|the)"
    r"|\*\s+(?:input|task|topic|analysis|constraint|step|draft)\b"
    r"|\d+\.\s+\*\*(?:analyze|analyse|understand|identify|read|determine|plan|draft)"
    r")",
    re.IGNORECASE,
)
_FINAL_ANSWER_MARKER_RE = re.compile(
    r"(?im)^[\s\->*]*(?:final\s+(?:answer|response|version|draft|output|summary)"
    r"|finale?\s+(?:antwort|version|zusammenfassung|ausgabe|fassung)"
    r"|abschließende?\s+(?:antwort|fassung|zusammenfassung)"
    r"|endgültige?\s+(?:antwort|fassung)"
    r"|answer|antwort|output|ausgabe|result|ergebnis|response"
    r"|summary|zusammenfassung)\s*[:\-—]\s*$"
)
_MERMAID_FENCE_RE = re.compile(r"^```\s*mermaid\s*\n", re.MULTILINE | re.IGNORECASE)


# Wenn nicht-getaggter Reasoning-Prosa-Anfang erkannt wurde und ab dieser
# Länge KEIN Final-Answer-Marker folgt, gehen wir davon aus, dass der gesamte
# Text Reasoning-Leak ist (kein abgesetzter Antwort-Block kommt mehr) — und
# unterdrücken ihn komplett. Unterhalb der Schwelle bleibt der Text stehen
# (z.B. eine knappe Antwort, die tatsächlich mit "Let me check…" anfängt).
_PROSE_THINKING_LEAK_THRESHOLD = 1500


def strip_reasoning(text: str) -> str:
    """Remove reasoning/thinking content emitted directly in the assistant
    message text (not via the separate `reasoning_content` channel).

    Strategy, in order:
    1. Remove balanced `<think>...</think>` / `<thinking>...</thinking>` blocks.
    2. If only an opening `<think>` is present (cutoff mid-thinking), return
       the prefix before it (often empty → caller falls back to `reasoning`).
    3. If text starts with prose-thinking markers ("Here's a thinking process:",
       "User asks:", "We need to …", "Let me check …"), look for a final-answer
       marker further down and return only the tail. If no marker found and the
       text is long, treat it as full reasoning leak and return "". Below the
       threshold the text is left as-is (could be a legitimate short answer
       that happens to start with such a phrase).
    """
    if not text:
        return text
    # 1. Balanced think tags
    cleaned = _THINK_TAG_RE.sub("", text).strip()
    if cleaned != text.strip():
        text = cleaned
    # 2. Unbalanced opening <think> — model was cut off mid-thinking
    m = _THINK_OPEN_RE.search(text)
    if m:
        prefix = text[: m.start()].strip()
        return prefix
    # 3. Prose-thinking detection
    head = text.lstrip()
    if _PROSE_THINKING_HEAD_RE.match(head):
        # 3a. explicit "Final answer:" / "Antwort:" / "Endgültig:" marker
        markers = list(_FINAL_ANSWER_MARKER_RE.finditer(text))
        if markers:
            tail = text[markers[-1].end():].strip()
            if tail:
                return tail
        # 3b. Mermaid fence — extract last fenced ```mermaid block
        mermaid_starts = list(_MERMAID_FENCE_RE.finditer(text))
        if mermaid_starts:
            tail = text[mermaid_starts[-1].start():].strip()
            if tail:
                return tail
        # 3c. No marker, but text is long → treat as full reasoning leak.
        if len(text) >= _PROSE_THINKING_LEAK_THRESHOLD:
            return ""
        # 3d. Short text with reasoning-ish opener — keep as-is.
    return text


@dataclass
class ChatResponse:
    text: str
    metrics: Metrics
    raw: dict[str, Any]
    tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str | None = None
    reasoning: str = ""
    truncated_reasoning: bool = False

    @property
    def effective_text(self) -> str:
        """Bevorzugt `text` (mit entferntem Inline-Reasoning).

        Bei `truncated_reasoning` (Modell hat das Token-Budget mitten im
        Thinking aufgebraucht und nie eine eigentliche Antwort erzeugt)
        wird ein leerer String zurückgegeben — Tasks sollen das als Abbruch
        werten statt das halbgare Reasoning als Antwort zu klassifizieren.
        """
        if self.truncated_reasoning:
            return ""
        if self.text and self.text.strip():
            stripped = strip_reasoning(self.text)
            if stripped and stripped.strip():
                return stripped
        return self.reasoning or ""


class LMStudioClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 1200.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout_s)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LMStudioClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ----- discovery -------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        r = self._client.get("/api/v0/models")
        r.raise_for_status()
        data = r.json().get("data", [])
        out: list[ModelInfo] = []
        for raw in data:
            try:
                out.append(ModelInfo.model_validate(raw))
            except Exception:  # noqa: BLE001
                # Skip unknown shapes rather than crashing the whole list
                continue
        return out

    def get_model(self, model_id: str) -> ModelInfo:
        r = self._client.get(f"/api/v0/models/{model_id}")
        r.raise_for_status()
        return ModelInfo.model_validate(r.json())

    # ----- generation ------------------------------------------------------

    def chat(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.6,
        max_tokens: int = -1,
        seed: int | None = None,
        extra: dict[str, Any] | None = None,
        timeout_s: float = 300.0,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
    ) -> ChatResponse:
        """Send a chat completion request. Returns text + metrics from `stats`.

        `messages` follows OpenAI format, including multimodal content arrays
        with `{"type": "image_url", "image_url": {"url": "data:..."}}`.

        `timeout_s` is a per-call hard cap (default 300s). The httpx client is
        always created with a higher upper bound so we can override per call.
        Use a higher value for very long-context tasks if needed.
        """
        body: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            # Default für Reasoning-Modelle (gpt-oss / o-Reihe / kompatible).
            # Hält das Thinking moderat, sodass es selten das max_tokens-Budget
            # auffrisst. Modelle ohne Reasoning ignorieren den Parameter.
            "reasoning_effort": "medium",
        }
        if seed is not None:
            body["seed"] = seed
        if tools:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if extra:
            body.update(extra)

        t0 = time.monotonic()
        try:
            r = self._client.post(
                "/api/v0/chat/completions",
                json=body,
                timeout=httpx.Timeout(timeout_s, connect=10.0),
            )
        except (httpx.TimeoutException, httpx.ReadTimeout, httpx.WriteTimeout) as e:
            wall = time.monotonic() - t0
            raise TimeoutError(
                f"timeout nach {timeout_s:.0f}s (httpx: {type(e).__name__})"
            ) from e
        wall = time.monotonic() - t0
        if wall >= timeout_s:
            raise TimeoutError(f"timeout nach {timeout_s:.0f}s")
        r.raise_for_status()
        data = r.json()

        msg = data["choices"][0]["message"]
        text = msg.get("content", "") or ""
        # LM Studio hat zwei Feldnamen-Generationen für den Reasoning-Channel:
        # `reasoning_content` (DeepSeek-R1, ältere Builds) und `reasoning`
        # (gpt-oss, neuere Builds, alignt mit o3-mini).
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
        if not isinstance(reasoning, str):
            reasoning = ""
        tool_calls = msg.get("tool_calls") or None
        finish_reason = data["choices"][0].get("finish_reason")
        usage = data.get("usage", {})
        stats = data.get("stats", {})

        # Truncated-Reasoning erkennen: Modell hat sein Token-Budget im
        # Thinking-Channel verbraucht und nie zur eigentlichen Antwort
        # gefunden. finish_reason="length" + leerer content + reasoning
        # vorhanden ist das eindeutige Signal. Tasks werten das als Abbruch.
        truncated = bool(
            finish_reason == "length"
            and not (text and text.strip())
            and (reasoning and reasoning.strip())
        )

        metrics = Metrics(
            wall_seconds=wall,
            tokens_generated=usage.get("completion_tokens", 0),
            tokens_per_second=stats.get("tokens_per_second", 0.0) or 0.0,
            time_to_first_token_ms=(stats.get("time_to_first_token") or 0.0) * 1000.0
            if stats.get("time_to_first_token") is not None
            else None,
        )
        return ChatResponse(
            text=text,
            metrics=metrics,
            raw=data,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            reasoning=reasoning,
            truncated_reasoning=truncated,
        )

    # ----- model lifecycle -------------------------------------------------

    def load(
        self,
        model_id: str,
        *,
        context_length: int | None = None,
        gpu: str = "max",
        ttl_seconds: int = 3600,
    ) -> bool:
        """Explicitly load a model with a specific context size.

        Returns True on success. Use this before tasks that need a large
        context window — JIT loading defaults to a small context (~20k).

        Variant-spezifische Modelle (z.B. ``…@q8_0``) gehen über das
        ``lmstudio``-Python-SDK, weil ``lms load`` nur die Default-Variante
        akzeptiert. Standard-IDs gehen weiter über die ``lms``-CLI.
        """
        if "@" in model_id:
            return self._load_via_sdk(
                model_id,
                context_length=context_length,
                gpu=gpu,
                ttl_seconds=ttl_seconds,
            )
        cmd = ["lms", "load", model_id, "--gpu", gpu, "--ttl", str(ttl_seconds), "-y"]
        if context_length is not None:
            cmd.extend(["--context-length", str(context_length)])
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            return res.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _load_via_sdk(
        self,
        model_id: str,
        *,
        context_length: int | None,
        gpu: str,
        ttl_seconds: int,
    ) -> bool:
        try:
            import lmstudio as lms  # type: ignore
        except ImportError:
            return False
        cfg_kwargs: dict[str, Any] = {}
        if context_length is not None:
            cfg_kwargs["context_length"] = context_length
        try:
            with lms.Client() as c:
                cfg = lms.LlmLoadModelConfig(**cfg_kwargs) if cfg_kwargs else None
                kwargs: dict[str, Any] = {"model_key": model_id, "ttl": ttl_seconds * 1000}
                if cfg is not None:
                    kwargs["config"] = cfg
                m = c.llm.load_new_instance(**kwargs)
                return bool(m and m.identifier)
        except Exception:  # noqa: BLE001
            return False

    def loaded_context_length(self, model_id: str) -> int | None:
        for m in self.loaded_models():
            ident = m.get("identifier") or ""
            if (
                m.get("modelKey") == model_id
                or ident == model_id
                or ident.startswith(model_id + ":")
            ):
                return m.get("contextLength")
        return None

    def ensure_context(self, model_id: str, min_context: int) -> bool:
        """Ensure the model is loaded with at least `min_context` tokens of ctx.

        If already loaded with a larger context, no-op. Otherwise unload and
        reload with the requested context size.
        """
        current = self.loaded_context_length(model_id)
        if current is not None and current >= min_context:
            return True
        # Unload first if currently loaded with smaller context.
        if current is not None:
            self.unload(model_id)
        return self.load(model_id, context_length=min_context)

    def list_variants(self) -> dict[str, list[str]]:
        """Map ``modelKey`` → list of *non-default* variant IDs (e.g. ``…@q8_0``).

        ``/api/v0/models`` zeigt nur die Default-Variante. ``lms ls --json``
        kennt aber auch Alternativ-Quants. Für jedes Modell mit ≥2 Varianten
        liefern wir die nicht-selektierten zurück; den Default-Eintrag deckt
        weiterhin der bare ``modelKey`` ab. Leeres Dict, falls ``lms`` fehlt.
        """
        try:
            res = subprocess.run(
                ["lms", "ls", "--json"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if res.returncode != 0:
                return {}
            data = json.loads(res.stdout) or []
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return {}

        out: dict[str, list[str]] = {}
        for entry in data:
            key = entry.get("modelKey")
            variants = entry.get("variants") or []
            selected = entry.get("selectedVariant")
            if not key or len(variants) < 2:
                continue
            alt = [v for v in variants if v != selected]
            if alt:
                out[key] = alt
        return out

    def loaded_models(self) -> list[dict[str, Any]]:
        """Return the list of currently loaded models with their RAM footprint.

        Each entry has at least `modelKey`, `sizeBytes`, `paramsString`,
        `quantization`, `contextLength`. Returns [] if no models are loaded
        or `lms` CLI is unavailable.
        """
        try:
            res = subprocess.run(
                ["lms", "ps", "--json"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if res.returncode != 0:
                return []
            return json.loads(res.stdout) or []
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return []

    def loaded_size_mb(self, model_id: str) -> float | None:
        """Convenience: RAM size in MB for a single loaded model."""
        for m in self.loaded_models():
            ident = m.get("identifier") or ""
            if (
                m.get("modelKey") == model_id
                or ident == model_id
                or ident.startswith(model_id + ":")
            ):
                size = m.get("sizeBytes")
                if size:
                    return round(size / 1024**2, 1)
        return None

    def unload(self, model_id: str) -> bool:
        """Unload a model via the `lms` CLI. Returns True on success.

        Variant-IDs (``…@quant``) gehen via SDK, weil ``lms unload`` sie
        nicht kennt — die geladene Instanz heißt z.B. ``model@q8_0:2``.
        """
        if "@" in model_id:
            return self._unload_via_sdk(model_id)
        try:
            res = subprocess.run(
                ["lms", "unload", model_id],
                capture_output=True,
                text=True,
                timeout=60,
            )
            return res.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _unload_via_sdk(self, model_id: str) -> bool:
        try:
            import lmstudio as lms  # type: ignore
        except ImportError:
            return False
        try:
            with lms.Client() as c:
                for m in c.list_loaded_models():
                    ident = getattr(m, "identifier", "") or ""
                    if ident == model_id or ident.startswith(model_id + ":"):
                        m.unload()
                        return True
            return False
        except Exception:  # noqa: BLE001
            return False

    def unload_all(self) -> None:
        try:
            subprocess.run(
                ["lms", "unload", "--all"],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass


# ----- helpers for vision content -----------------------------------------


def image_to_data_url(path: Path) -> str:
    """Encode an image file as a data: URL for the chat API."""
    suffix = path.suffix.lower().lstrip(".")
    mime = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(suffix, "application/octet-stream")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def make_vision_message(text: str, image_paths: list[Path]) -> dict[str, Any]:
    """Build a single user-message with text + images for VLM chat."""
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for p in image_paths:
        content.append(
            {"type": "image_url", "image_url": {"url": image_to_data_url(p)}}
        )
    return {"role": "user", "content": content}
