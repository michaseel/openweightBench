"""Diagram → SVG: VLM produces inline SVG markup for a diagram image.

Deterministic score covers two axes only:
  1. svg_validity — XML parses, has <svg> root, has children, has at least one <text>.
  2. term_coverage — fraction of expected labels found in <text>/<tspan> content.

Structural correctness (correct edges, correct grouping, layout quality) is
deliberately *not* graded here — that's what the diagram-svg-judge skill is
for: it screenshots the rendered SVG and compares it visually against the
source image with anchored metrics.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from ..client.lmstudio import LMStudioClient, image_to_data_url
from ..core.results import Artifact, BenchStore, Metrics, ModelInfo, TaskResult
from .base import Task
from .diagram_to_mermaid import GROUND_TRUTH
from .vision import _ensure_thumbnail

_FENCE = re.compile(r"```(?:svg|xml)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SVG_DOC = re.compile(r"<svg[\s\S]*?</svg>", re.IGNORECASE)


def _glob_diagrams(assets_dir: Path) -> list[Path]:
    out: list[Path] = []
    for ext in ("jpg", "jpeg", "png"):
        out.extend(sorted(assets_dir.glob(f"diagram_*.{ext}")))
    return out


def extract_svg(text: str) -> str:
    """Pull the first <svg>..</svg> block out of the model response.

    Order: prefer raw SVG document; fall back to fenced ```svg blocks.
    """
    m = _SVG_DOC.search(text)
    if m:
        return m.group(0).strip()
    m = _FENCE.search(text)
    if m:
        candidate = m.group(1).strip()
        m2 = _SVG_DOC.search(candidate)
        if m2:
            return m2.group(0).strip()
    return text.strip()


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _all_text(root: ET.Element) -> str:
    """Concatenate every <text>/<tspan>/<title> string in the tree."""
    parts: list[str] = []
    for el in root.iter():
        if _strip_ns(el.tag) in ("text", "tspan", "title"):
            if el.text:
                parts.append(el.text)
    return " ".join(parts)


def _term_present(haystack: str, term: str) -> bool:
    return term.lower() in haystack.lower()


@dataclass
class SvgGrade:
    score: float
    parsed: bool
    has_root: bool
    element_count: int
    text_count: int
    matched_terms: list[str]
    missing_terms: list[str]
    parse_error: str | None


def grade_svg(diagram_id: str, svg: str) -> SvgGrade:
    """Two-axis grade: validity (50%) + label coverage (50%).

    Validity requires: parses, root tag is <svg>, at least one descendant,
    and at least one <text> element. A failed parse hard-caps at 0.
    """
    spec = GROUND_TRUTH.get(diagram_id) or {}
    required_terms = spec.get("required_terms", [])

    if not svg.strip():
        return SvgGrade(0.0, False, False, 0, 0, [], list(required_terms), "leeres SVG")

    try:
        root = ET.fromstring(svg)
        parse_error = None
    except ET.ParseError as e:
        return SvgGrade(0.0, False, False, 0, 0, [], list(required_terms), str(e)[:200])

    has_root = _strip_ns(root.tag) == "svg"
    descendants = list(root.iter())
    element_count = len(descendants)
    text_count = sum(1 for el in descendants if _strip_ns(el.tag) in ("text", "tspan"))
    haystack = _all_text(root)

    if required_terms:
        matched = [t for t in required_terms if _term_present(haystack, t)]
        missing = [t for t in required_terms if t not in matched]
        coverage = len(matched) / len(required_terms)
    else:
        matched, missing, coverage = [], [], 1.0

    valid = has_root and element_count >= 5 and text_count >= 1
    validity_score = 1.0 if valid else 0.0

    score = (validity_score + coverage) / 2.0
    return SvgGrade(
        score=score,
        parsed=True,
        has_root=has_root,
        element_count=element_count,
        text_count=text_count,
        matched_terms=matched,
        missing_terms=missing,
        parse_error=parse_error,
    )


class DiagramToSvgTask(Task):
    name = "diagram_to_svg"
    label = "Diagramm → SVG"
    requires_vlm = True

    def __init__(self, prompt_file: Path, assets_dir: Path) -> None:
        self.spec = json.loads(prompt_file.read_text())
        self.assets_dir = assets_dir

    def run(
        self,
        client: LMStudioClient,
        model: ModelInfo,
        store: BenchStore,
    ) -> TaskResult:
        artifact_dir = store.artifact_dir(self.name, model.id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        thumb_dir = artifact_dir / "_thumbnails"

        diagrams = _glob_diagrams(self.assets_dir)
        started = self.now()

        if not diagrams:
            completed = self.now()
            return TaskResult(
                task=self.name,
                model_id=model.id,
                model_info=model,
                started_at=started,
                completed_at=completed,
                metrics=Metrics(wall_seconds=0.0),
                score=None,
                error=(
                    f"Keine Diagramm-Bilder gefunden unter {self.assets_dir} "
                    "(erwartet: diagram_*.{jpg,jpeg,png})."
                ),
            )

        wall_total = 0.0
        tokens_total = 0
        speeds: list[float] = []
        sub_results: list[dict] = []
        artifacts: list[Artifact] = []

        for src in diagrams:
            thumb = _ensure_thumbnail(src, thumb_dir)
            content = [
                {"type": "text", "text": self.spec["user_prompt"]},
                {"type": "image_url", "image_url": {"url": image_to_data_url(thumb)}},
            ]
            messages = [
                {"role": "system", "content": self.spec.get("system", "")},
                {"role": "user", "content": content},
            ]
            try:
                resp = client.chat(model.id, messages, max_tokens=12000, temperature=0.2, timeout_s=600.0)
                wall_total += resp.metrics.wall_seconds
                tokens_total += resp.metrics.tokens_generated
                if resp.metrics.tokens_per_second:
                    speeds.append(resp.metrics.tokens_per_second)
                raw = resp.effective_text
                err = None
            except Exception as e:  # noqa: BLE001
                raw = ""
                err = str(e)

            svg = extract_svg(raw)
            grade = grade_svg(src.stem, svg)
            score = grade.score

            svg_path = artifact_dir / f"{src.stem}.svg"
            svg_path.write_text(svg)
            raw_path = artifact_dir / f"{src.stem}.raw.txt"
            raw_path.write_text(raw)

            img_target = artifact_dir / src.name
            if not img_target.exists():
                img_target.write_bytes(src.read_bytes())

            # Stable PNG render of the model's SVG so the judge skill always
            # has an apples-to-apples image to compare against the original.
            render_rel = None
            if grade.parsed and grade.has_root:
                try:
                    from ..report.screenshots import screenshot_svg

                    render_path = artifact_dir / f"{src.stem}.render.png"
                    rendered = screenshot_svg(svg, render_path)
                    if rendered is not None:
                        render_rel = str(rendered.relative_to(store.root))
                except Exception:  # noqa: BLE001
                    pass

            sub_results.append(
                {
                    "id": src.stem,
                    "image_name": src.name,
                    "image_path": str(img_target.relative_to(store.root)),
                    "svg_path": str(svg_path.relative_to(store.root)),
                    "render_path": render_rel,
                    "raw_length": len(raw),
                    "svg_length": len(svg),
                    "grade": {
                        "score": grade.score,
                        "parsed": grade.parsed,
                        "has_root": grade.has_root,
                        "element_count": grade.element_count,
                        "text_count": grade.text_count,
                        "matched_terms": grade.matched_terms,
                        "missing_terms": grade.missing_terms,
                        "parse_error": grade.parse_error,
                    },
                    "score": score,
                    "error": err,
                }
            )
            artifacts.append(
                Artifact(
                    kind="image",
                    label=f"Source {src.stem}",
                    path=str(img_target.relative_to(store.root)),
                    mime="image/jpeg",
                )
            )
            artifacts.append(
                Artifact(
                    kind="text",
                    label=f"SVG {src.stem}",
                    path=str(svg_path.relative_to(store.root)),
                    mime="image/svg+xml",
                )
            )
            if render_rel:
                artifacts.append(
                    Artifact(
                        kind="image",
                        label=f"Render {src.stem}",
                        path=render_rel,
                        mime="image/png",
                    )
                )

        completed = self.now()
        scores = [s["score"] for s in sub_results if s.get("error") is None]
        score = sum(scores) / len(scores) if scores else None
        avg = sum(speeds) / len(speeds) if speeds else 0.0

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
                tokens_per_second=avg,
            ),
            score=score,
            score_breakdown={"diagrams": sub_results},
            artifacts=artifacts,
        )
