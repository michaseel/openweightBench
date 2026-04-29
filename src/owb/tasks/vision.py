"""Vision tasks (handwriting OCR / Fraktur OCR). VLM only."""

from __future__ import annotations

import base64
import difflib
import json
import re
from pathlib import Path

from PIL import Image

from ..client.lmstudio import LMStudioClient, image_to_data_url
from ..core.results import Artifact, BenchStore, Metrics, ModelInfo, TaskResult
from .base import Task


# ---- OCR scoring ---------------------------------------------------------


_WORD_RE = re.compile(r"[\wäöüÄÖÜß]+", re.UNICODE)
_COMMON_RELOCATED_WORDS = {
    "aber",
    "auch",
    "auf",
    "aus",
    "das",
    "daß",
    "dem",
    "den",
    "der",
    "die",
    "doch",
    "du",
    "ein",
    "eine",
    "einem",
    "einen",
    "einer",
    "eines",
    "er",
    "es",
    "hat",
    "hatte",
    "haben",
    "ich",
    "ihm",
    "ihnen",
    "ihr",
    "im",
    "in",
    "ist",
    "man",
    "mit",
    "nicht",
    "noch",
    "nur",
    "oder",
    "sie",
    "sich",
    "um",
    "und",
    "von",
    "vor",
    "war",
    "wenn",
    "wie",
    "wohl",
    "zu",
    "über",
}


def _normalize_word(w: str) -> str:
    return w.lower().strip(".,;:!?\"'„“()[]")


def _tokenize(text: str) -> list[str]:
    return [_normalize_word(w) for w in _WORD_RE.findall(text or "") if w.strip()]


def _append_opcode(opcodes: list[dict], tag: str, gt: str = "", pred: str = "") -> None:
    if not gt and not pred:
        return
    if opcodes and opcodes[-1]["tag"] == tag:
        if gt:
            opcodes[-1]["gt"] = f"{opcodes[-1]['gt']} {gt}".strip()
        if pred:
            opcodes[-1]["pred"] = f"{opcodes[-1]['pred']} {pred}".strip()
        return
    opcodes.append({"tag": tag, "gt": gt, "pred": pred})


def _fuzzy_threshold(word: str) -> float | None:
    """Return the similarity threshold for fuzzy OCR credit.

    Short tokens are too easy to over-match. Longer tokens can tolerate a
    single OCR slip without turning the benchmark into exact string matching.
    """
    n = len(word)
    if n < 5:
        return None
    if n <= 7:
        return 0.88
    return 0.84


def _can_match_relocated(word: str) -> bool:
    if word.isdigit():
        return True
    return len(word) >= 4 and word not in _COMMON_RELOCATED_WORDS


def _can_match_fuzzy(gt_word: str, pred_word: str) -> bool:
    if len(gt_word) <= 6 and gt_word[:1] != pred_word[:1]:
        return False
    if gt_word in _COMMON_RELOCATED_WORDS or pred_word in _COMMON_RELOCATED_WORDS:
        return False
    return True


def score_ocr(answer: str, ground_truth: str) -> dict:
    """Word-level OCR scoring with flexible matching.

    Returns a dict with:
      - recall, precision, f1
      - matched / missed / extra word counts
      - ordered_matched: exact words in the expected order
      - relocated_matched: exact words found at a different position
      - fuzzy_matched: very similar words counted as OCR-near-matches
      - opcodes: list of (tag, gt_text, pred_text) suitable for side-by-side render
    """
    gt = _tokenize(ground_truth)
    pr = _tokenize(answer)
    if not gt:
        return {
            "recall": 0.0, "precision": 0.0, "f1": 0.0,
            "matched": 0, "gt_total": 0, "pred_total": len(pr),
            "missed": 0, "extra": len(pr), "opcodes": [],
        }
    sm = difflib.SequenceMatcher(a=gt, b=pr, autojunk=False)
    opcodes_raw = sm.get_opcodes()

    gt_matches: dict[int, tuple[int, str]] = {}
    pred_matches: dict[int, int] = {}
    ordered_matched = 0
    for tag, i1, i2, j1, _j2 in opcodes_raw:
        if tag != "equal":
            continue
        for offset, gt_i in enumerate(range(i1, i2)):
            pred_i = j1 + offset
            gt_matches[gt_i] = (pred_i, "equal")
            pred_matches[pred_i] = gt_i
            ordered_matched += 1

    unmatched_pred_by_word: dict[str, list[int]] = {}
    for pred_i, word in enumerate(pr):
        if pred_i not in pred_matches:
            unmatched_pred_by_word.setdefault(word, []).append(pred_i)

    relocated_matched = 0
    for gt_i, word in enumerate(gt):
        if gt_i in gt_matches:
            continue
        if not _can_match_relocated(word):
            continue
        candidates = unmatched_pred_by_word.get(word) or []
        while candidates and candidates[0] in pred_matches:
            candidates.pop(0)
        if not candidates:
            continue
        pred_i = candidates.pop(0)
        gt_matches[gt_i] = (pred_i, "moved")
        pred_matches[pred_i] = gt_i
        relocated_matched += 1

    fuzzy_matched = 0
    for gt_i, word in enumerate(gt):
        if gt_i in gt_matches:
            continue
        threshold = _fuzzy_threshold(word)
        if threshold is None:
            continue
        best_pred_i = None
        best_ratio = 0.0
        for pred_i, pred_word in enumerate(pr):
            if pred_i in pred_matches:
                continue
            if not _can_match_fuzzy(word, pred_word):
                continue
            if abs(len(word) - len(pred_word)) > max(2, len(word) // 3):
                continue
            ratio = difflib.SequenceMatcher(a=word, b=pred_word, autojunk=False).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_pred_i = pred_i
        if best_pred_i is not None and best_ratio >= threshold:
            gt_matches[gt_i] = (best_pred_i, "fuzzy")
            pred_matches[best_pred_i] = gt_i
            fuzzy_matched += 1

    matched = ordered_matched + relocated_matched + fuzzy_matched
    missed = len(gt) - matched
    extra = len(pr) - matched
    recall = matched / len(gt) if gt else 0.0
    precision = matched / len(pr) if pr else 0.0
    f1 = (2 * recall * precision / (recall + precision)) if (recall + precision) else 0.0

    opcodes: list[dict] = []
    for tag, i1, i2, j1, j2 in opcodes_raw:
        if tag == "equal":
            _append_opcode(opcodes, "equal", " ".join(gt[i1:i2]), " ".join(pr[j1:j2]))
            continue

        if tag in ("delete", "replace"):
            for gt_i in range(i1, i2):
                matched_pred = gt_matches.get(gt_i)
                if matched_pred is None:
                    _append_opcode(opcodes, "delete", gt[gt_i], "")
                    continue
                pred_i, match_tag = matched_pred
                if match_tag == "moved":
                    _append_opcode(opcodes, "moved", gt[gt_i], pr[pred_i])
                elif match_tag == "fuzzy":
                    _append_opcode(opcodes, "fuzzy", gt[gt_i], pr[pred_i])

        if tag in ("insert", "replace"):
            for pred_i in range(j1, j2):
                if pred_i not in pred_matches:
                    _append_opcode(opcodes, "insert", "", pr[pred_i])

    return {
        "recall": recall,
        "precision": precision,
        "f1": f1,
        "matched": matched,
        "ordered_matched": ordered_matched,
        "relocated_matched": relocated_matched,
        "fuzzy_matched": fuzzy_matched,
        "missed": missed,
        "extra": extra,
        "gt_total": len(gt),
        "pred_total": len(pr),
        "opcodes": opcodes,
    }

# Vision images can be very large; downscale to keep prompt reasonable.
MAX_IMAGE_SIDE = 1600


def _ensure_thumbnail(src: Path, work_dir: Path) -> Path:
    """Return a max-side-MAX_IMAGE_SIDE JPEG of `src`, cached in `work_dir`."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / src.with_suffix(".jpg").name
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    img = Image.open(src).convert("RGB")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    img.save(out, "JPEG", quality=85)
    return out


class VisionTask(Task):
    """Runs all configured vision sub-tasks for one model."""

    name = "vision"
    label = "Vision"
    requires_vlm = True

    def __init__(
        self,
        prompt_dir: Path,
        assets_dir: Path,
        sub_specs: list[str] | None = None,
    ) -> None:
        self.prompt_dir = prompt_dir
        self.assets_dir = assets_dir
        names = sub_specs or [
            "handwriting_easy",
            "handwriting_medium",
            "handwriting_hard",
            "fraktur_ocr",
        ]
        self.sub_specs = [
            json.loads((prompt_dir / f"{n}.json").read_text()) for n in names
        ]

    def _score_sub(self, text: str, expected: list[str]) -> float:
        """Fallback keyword-based score when no ground_truth is provided."""
        if not expected:
            return 0.0
        text_l = text.lower()
        hits = sum(1 for kw in expected if kw.lower() in text_l)
        return hits / len(expected)

    def run(
        self,
        client: LMStudioClient,
        model: ModelInfo,
        store: BenchStore,
    ) -> TaskResult:
        artifact_dir = store.artifact_dir(self.name, model.id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        thumb_dir = artifact_dir / "_thumbnails"

        started = self.now()
        wall_total = 0.0
        tokens_total = 0
        speeds: list[float] = []
        sub_results: list[dict] = []
        artifacts: list[Artifact] = []

        for spec in self.sub_specs:
            sub_id = spec["name"]
            images = []
            for rel in spec["images"]:
                src = self.assets_dir / rel
                if not src.exists():
                    sub_results.append(
                        {
                            "id": sub_id,
                            "label": spec.get("label", sub_id),
                            "error": f"Asset fehlt: {rel}",
                            "score": 0.0,
                        }
                    )
                    images = None
                    break
                images.append(_ensure_thumbnail(src, thumb_dir))
            if images is None:
                continue

            content: list[dict] = [{"type": "text", "text": spec["user_prompt"]}]
            for p in images:
                content.append(
                    {"type": "image_url", "image_url": {"url": image_to_data_url(p)}}
                )
            messages = [
                {"role": "system", "content": spec.get("system", "")},
                {"role": "user", "content": content},
            ]

            sub_wall = 0.0
            sub_tps = 0.0
            try:
                resp = client.chat(model.id, messages, max_tokens=6000, temperature=0.2, timeout_s=400.0)
                wall_total += resp.metrics.wall_seconds
                sub_wall = resp.metrics.wall_seconds
                tokens_total += resp.metrics.tokens_generated
                if resp.metrics.tokens_per_second:
                    speeds.append(resp.metrics.tokens_per_second)
                    sub_tps = resp.metrics.tokens_per_second
                answer = resp.effective_text
                err = None
            except Exception as e:  # noqa: BLE001
                answer = ""
                err = str(e)
                sub_wall = 0.0

            # OCR Word-Level-Diff falls Ground-Truth vorhanden, sonst Stichwort-Match
            ocr = None
            gt = spec.get("ground_truth")
            if gt:
                ocr = score_ocr(answer or "", gt)
                score = ocr["f1"]
            else:
                score = self._score_sub(answer, spec.get("expected_keywords", []))

            answer_path = artifact_dir / f"{sub_id}.txt"
            answer_path.write_text(answer or "")

            # Copy each source image into artifact dir + collect their relative paths
            sub_image_paths: list[str] = []
            for img_p in images:
                rel_target = artifact_dir / img_p.name
                if not rel_target.exists():
                    rel_target.write_bytes(img_p.read_bytes())
                sub_image_paths.append(str(rel_target.relative_to(store.root)))

            sub_results.append(
                {
                    "id": sub_id,
                    "label": spec.get("label", sub_id),
                    "prompt": spec["user_prompt"],
                    "image_paths": sub_image_paths,
                    "answer": answer,
                    "ground_truth": gt or "",
                    "expected_keywords": spec.get("expected_keywords", []),
                    "ocr": ocr,  # None if no ground_truth, else recall/precision/f1/opcodes
                    "score": score,
                    "error": err,
                    "wall_seconds": sub_wall,
                    "tokens_per_second": sub_tps,
                }
            )
            artifacts.append(
                Artifact(
                    kind="text",
                    label=f"{spec.get('label', sub_id)} – Antwort",
                    path=str(answer_path.relative_to(store.root)),
                    mime="text/plain",
                )
            )
            for ip in sub_image_paths:
                artifacts.append(
                    Artifact(
                        kind="image",
                        label=f"{spec.get('label', sub_id)} – Bild",
                        path=ip,
                        mime="image/jpeg",
                    )
                )

        completed = self.now()
        scores = [s["score"] for s in sub_results if s.get("error") is None]
        score = sum(scores) / len(scores) if scores else 0.0
        avg_speed = sum(speeds) / len(speeds) if speeds else 0.0

        bd_path = artifact_dir / "breakdown.json"
        bd_path.write_text(json.dumps(sub_results, indent=2, ensure_ascii=False))

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
            score_breakdown={"subtasks": sub_results},
            artifacts=artifacts,
        )
