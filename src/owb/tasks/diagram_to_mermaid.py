"""Diagram → Mermaid: VLM produces mermaid syntax from a diagram image.

Score is structural and content-based: valid Mermaid prelude, expected diagram
kind, required labels/entities, and a small set of required relationships. The
report renders the produced code through mermaid.js so the user can compare
visually with the source image.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..client.lmstudio import LMStudioClient, image_to_data_url
from ..core.results import Artifact, BenchStore, Metrics, ModelInfo, TaskResult
from .base import Task
from .vision import _ensure_thumbnail

_FENCE = re.compile(r"```(?:mermaid)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SUBGRAPH = re.compile(r"^(\s*)subgraph\s+(.+?)\s*$", re.IGNORECASE)
_STYLE = re.compile(
    r"^(\s*)style\s+(.+?)\s+((?:fill|stroke|color|background|opacity|font)[^ ]*:.*)$",
    re.IGNORECASE,
)
_NODE_LABEL = re.compile(r"(?<![\[(])\b([A-Za-z][A-Za-z0-9_]*)\[([^\]\n]+)\]")
GRADING_VERSION = 2

GROUND_TRUTH: dict[str, dict] = {
    "diagram_service_architecture": {
        "label": "System Architecture",
        "preferred_kinds": {"graph", "flowchart"},
        "required_terms": [
            "Frontend",
            "React",
            "API Gateway",
            "Kong",
            "Auth Service",
            "JWT",
            "User DB",
            "Backend",
            "FastAPI",
            "Database",
            "External API",
            "Payment Provider",
            "Message Queue",
            "RabbitMQ",
            "Worker Service",
            "File Storage",
            "S3",
            "Monitoring",
            "Prometheus",
            "Grafana",
        ],
        "required_edges": [
            ("Frontend", "API Gateway"),
            ("API Gateway", "Auth Service"),
            ("Auth Service", "User DB"),
            ("API Gateway", "Backend"),
            ("Backend", "Database"),
            ("API Gateway", "External API"),
            ("API Gateway", "Message Queue"),
            ("Backend", "Message Queue"),
            ("Message Queue", "Worker Service"),
            ("Worker Service", "File Storage"),
            ("Message Queue", "Monitoring"),
            ("Worker Service", "Monitoring"),
        ],
        "required_groups": {
            "Frontend/API": ["Frontend", "React", "API Gateway", "Kong"],
            "Auth/Data": ["Auth Service", "JWT", "User DB", "Database"],
            "Backend/Async": ["Backend", "FastAPI", "Message Queue", "RabbitMQ"],
            "Worker/Storage/Ops": ["Worker Service", "File Storage", "S3", "Monitoring"],
            "External": ["External API", "Payment Provider"],
        },
    },
    "diagram_sso_sequence": {
        "label": "SSO-Ablaufdiagramm",
        "preferred_kinds": {"sequenceDiagram", "graph", "flowchart"},
        "ideal_kinds": {"sequenceDiagram"},
        "required_terms": [
            "Benutzer",
            "App",
            "Service Provider",
            "Identity Provider",
            "IdP",
            "Login-Seite",
            "Anmeldedaten",
            "Authentifizierung",
            "Token",
            "Assertion",
            "SSO-Response",
            "Zugriff",
            "Ressourcen",
            "Nein",
            "Ja",
        ],
        "required_edges": [
            ("Benutzer", "App"),
            ("App", "Identity Provider"),
            ("Identity Provider", "Login-Seite"),
            ("Login-Seite", "Anmeldedaten"),
            ("Login-Seite", "Authentifizierung"),
            ("Authentifizierung", "Token"),
            ("Token", "App"),
            ("App", "Zugriff"),
        ],
        "required_groups": {
            "Actors": ["Benutzer", "App", "Service Provider", "Identity Provider", "IdP"],
            "Login": ["Login-Seite", "Anmeldedaten", "Authentifizierung"],
            "Success": ["Ja", "Token", "Assertion", "SSO-Response", "Zugriff", "Ressourcen"],
            "Failure": ["Nein"],
        },
    },
    "diagram_eisenhower": {
        "label": "Eisenhower-Matrix Marketing",
        "preferred_kinds": {"graph", "flowchart", "quadrantChart"},
        "required_terms": [
            "Wichtig",
            "Dringend",
            "Nicht wichtig",
            "Nicht dringend",
            "Content-Strategie",
            "Brand schärfen",
            "SEO",
            "Zielgruppen",
            "Kampagne retten",
            "Shitstorm",
            "Newsletter",
            "falschem Link",
            "viral gehen",
            "Merch-Ideen",
            "Glitzer",
            "Buzzword-Bingo",
            "TikTok",
            "Maskottchen",
            "Hashtag",
            "Last-Minute-Meeting",
            "Logo größer",
            "LinkedIn-Post",
            "Weltag der Büroklammer",
            "Folie 27",
        ],
        "required_edges": [],
        "required_groups": {
            "Wichtig + Nicht dringend": [
                "Wichtig",
                "Nicht dringend",
                "Content-Strategie",
                "Brand schärfen",
                "SEO",
                "Zielgruppen",
            ],
            "Wichtig + Dringend": [
                "Wichtig",
                "Dringend",
                "Kampagne retten",
                "Shitstorm",
                "Newsletter",
                "falschem Link",
                "viral gehen",
            ],
            "Nicht wichtig + Nicht dringend": [
                "Nicht wichtig",
                "Nicht dringend",
                "Merch-Ideen",
                "Glitzer",
                "Buzzword-Bingo",
                "TikTok",
                "Maskottchen",
                "Hashtag",
            ],
            "Nicht wichtig + Dringend": [
                "Nicht wichtig",
                "Dringend",
                "Last-Minute-Meeting",
                "Logo größer",
                "LinkedIn-Post",
                "Weltag der Büroklammer",
                "Folie 27",
            ],
        },
        "forbidden_terms": [
            "Keine spezifischen Punkte",
            "nur Quadrant-Label",
        ],
    },
}


def strip_fence(text: str) -> str:
    m = _FENCE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _quote_label(label: str) -> str:
    return label.replace('"', "&quot;")


def _subgraph_needs_id(raw: str) -> bool:
    title = raw.strip()
    if "[" in title or "]" in title:
        return False
    return not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", title)


def _normalize_flowchart_mermaid(text: str) -> tuple[str, list[str]]:
    """Fix common model-generated flowchart syntax that breaks Mermaid.

    Mermaid needs stable identifiers separate from human labels. VLMs often
    emit lines like `subgraph Wichtig + Dringend (Q1)` or `style Wichtig + ...`;
    those read naturally but are not parseable. Keep the visible labels and
    replace them with generated IDs.
    """
    lines = text.splitlines()
    out: list[str] = []
    subgraph_ids: dict[str, str] = {}
    warnings: list[str] = []
    subgraph_count = 0
    subgraph_depth = 0

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.lower() == "end" and subgraph_depth > 0:
            subgraph_depth -= 1

        if stripped.endswith(","):
            line = line.rstrip().removesuffix(",")
            stripped = line.strip()
            warnings.append("trailing_comma_removed")

        if subgraph_depth == 0 and re.match(r"^direction\s+(TB|TD|BT|RL|LR)$", stripped):
            warnings.append("root_direction_removed")
            continue

        m_sub = _SUBGRAPH.match(line)
        if m_sub and _subgraph_needs_id(m_sub.group(2)):
            indent, title = m_sub.groups()
            subgraph_count += 1
            sg_id = f"SG{subgraph_count}"
            clean_title = title.strip()
            subgraph_ids[clean_title] = sg_id
            short_title = re.sub(r"\s*\([^)]*\)\s*$", "", clean_title).strip()
            if short_title:
                subgraph_ids[short_title] = sg_id
            line = f'{indent}subgraph {sg_id}["{_quote_label(clean_title)}"]'
            warnings.append("subgraph_title_quoted")
        if m_sub:
            subgraph_depth += 1

        m_style = _STYLE.match(line)
        if m_style:
            indent, target, rest = m_style.groups()
            fixed_target = subgraph_ids.get(target.strip())
            if fixed_target is not None:
                line = f"{indent}style {fixed_target} {rest}"
                warnings.append("style_target_rewritten")
            elif not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", target.strip()):
                warnings.append("invalid_style_removed")
                continue

        def repl_node(match: re.Match[str]) -> str:
            node_id = match.group(1)
            label = match.group(2)
            if label.startswith('"') and label.endswith('"'):
                return match.group(0)
            if '"' not in label and not re.search(r"[(){}+/]", label):
                return match.group(0)
            warnings.append("node_label_quoted")
            return f'{node_id}["{_quote_label(label)}"]'

        line = _NODE_LABEL.sub(repl_node, line)
        out.append(line)

    return "\n".join(out).strip(), sorted(set(warnings))


def normalize_mermaid(text: str) -> tuple[str, list[str]]:
    stripped = strip_fence(text)
    first = stripped.lstrip().split("\n", 1)[0].strip().lower()
    if first.startswith(("graph", "flowchart")):
        return _normalize_flowchart_mermaid(stripped)
    return stripped, []


def _glob_diagrams(assets_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for ext in ("jpg", "jpeg", "png"):
        paths.extend(assets_dir.glob(f"diagram_*.{ext}"))
    return sorted(paths)


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]+", "", text.lower())


def _term_present(text: str, term: str) -> bool:
    compact_text = _compact(text)
    compact_term = _compact(term)
    if compact_term in compact_text:
        return True
    words = [w for w in re.split(r"[^A-Za-z0-9ÄÖÜäöüß]+", term) if len(w) >= 3]
    if not words:
        return False
    return all(_compact(w) in compact_text for w in words)


def _node_label_map(text: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for match in re.finditer(
        r"\b([A-Za-z][A-Za-z0-9_]*)\s*(?:\[\"?([^\"\]\n]+)\"?\]|\(([^)\n]+)\)|\{([^}\n]+)\})",
        text,
    ):
        labels[match.group(1)] = next(g for g in match.groups()[1:] if g)
    return labels


def _line_with_labels(line: str, labels: dict[str, str]) -> str:
    found = []
    for node_id, label in labels.items():
        if re.search(rf"\b{re.escape(node_id)}\b", line):
            found.append(label)
    return " ".join([line, *found])


def _edge_present(text: str, src: str, dst: str) -> bool:
    labels = _node_label_map(text)
    lines = [line for line in text.splitlines() if "--" in line or "->" in line]
    for line in lines:
        expanded = _line_with_labels(line, labels)
        if _term_present(expanded, src) and _term_present(expanded, dst):
            return True
    return False


def _score_ratio(hit_count: int, total: int) -> float:
    return hit_count / total if total else 1.0


def grade_mermaid(
    diagram_id: str,
    mermaid: str,
    diagram_kind: str | None,
    starts_with_keyword: bool,
) -> dict:
    """Return a deterministic, per-diagram quality grade.

    This intentionally stays stricter than a syntax smoke test but simpler than
    visual judgment. It catches the common failure mode where a model emits a
    syntactically plausible diagram that omits half of the whiteboard.
    """
    spec = GROUND_TRUTH.get(diagram_id)
    if spec is None:
        score = 1.0 if starts_with_keyword else 0.0
        return {
            "version": GRADING_VERSION,
            "score": score,
            "checks": [
                {
                    "id": "syntax_prelude",
                    "label": "Gültige Mermaid-Einleitung",
                    "passed": starts_with_keyword,
                    "score": score,
                    "detail": diagram_kind or "keine",
                }
            ],
        }

    kind = diagram_kind or ""
    syntax_score = 1.0 if starts_with_keyword else 0.0
    if not starts_with_keyword:
        kind_score = 0.0
    elif kind in spec.get("ideal_kinds", set()):
        kind_score = 1.0
    elif kind in spec["preferred_kinds"]:
        kind_score = 0.7 if spec.get("ideal_kinds") else 1.0
    else:
        kind_score = 0.3

    required_terms = spec["required_terms"]
    matched_terms = [term for term in required_terms if _term_present(mermaid, term)]
    missing_terms = [term for term in required_terms if term not in matched_terms]
    content_score = _score_ratio(len(matched_terms), len(required_terms))

    required_edges = spec.get("required_edges", [])
    matched_edges = [
        {"from": src, "to": dst}
        for src, dst in required_edges
        if _edge_present(mermaid, src, dst)
    ]
    missing_edges = [
        {"from": src, "to": dst}
        for src, dst in required_edges
        if {"from": src, "to": dst} not in matched_edges
    ]
    edge_score = _score_ratio(len(matched_edges), len(required_edges))

    required_groups = spec.get("required_groups", {})
    group_breakdown = []
    for label, terms in required_groups.items():
        matched = [term for term in terms if _term_present(mermaid, term)]
        missing = [term for term in terms if term not in matched]
        group_breakdown.append(
            {
                "label": label,
                "score": _score_ratio(len(matched), len(terms)),
                "matched": matched,
                "missing": missing,
            }
        )
    if group_breakdown:
        group_average = sum(g["score"] for g in group_breakdown) / len(group_breakdown)
        group_min = min(g["score"] for g in group_breakdown)
        group_score = group_average * 0.6 + group_min * 0.4
    else:
        group_score = 1.0
    relationship_score = edge_score if required_edges else group_score

    forbidden_terms = spec.get("forbidden_terms", [])
    forbidden_hits = [term for term in forbidden_terms if _term_present(mermaid, term)]
    hallucination_score = (
        1.0 - _score_ratio(len(forbidden_hits), len(forbidden_terms))
        if forbidden_terms
        else 1.0
    )

    checks = [
        {
            "id": "syntax_prelude",
            "label": "Gültige Mermaid-Einleitung",
            "passed": starts_with_keyword,
            "score": syntax_score,
            "detail": diagram_kind or "keine",
        },
        {
            "id": "diagram_kind",
            "label": "Passender Diagrammtyp",
            "passed": kind_score >= 0.7,
            "score": kind_score,
            "detail": f"{kind or 'unbekannt'}; erwartet: {', '.join(sorted(spec['preferred_kinds']))}",
        },
        {
            "id": "content_terms",
            "label": "Erwartete Beschriftungen/Entitäten",
            "passed": content_score >= 0.75,
            "score": content_score,
            "detail": f"{len(matched_terms)}/{len(required_terms)} getroffen",
            "missing": missing_terms,
        },
        {
            "id": "relationships",
            "label": "Erwartete Beziehungen/Pfeile",
            "passed": relationship_score >= 0.65,
            "score": relationship_score,
            "detail": f"{len(matched_edges)}/{len(required_edges)} getroffen",
            "missing": missing_edges,
        },
        {
            "id": "section_completeness",
            "label": "Vollständigkeit der Diagramm-Teilbereiche",
            "passed": group_score >= 0.75,
            "score": group_score,
            "detail": f"{sum(1 for g in group_breakdown if g['score'] >= 0.75)}/{len(group_breakdown)} Bereiche ausreichend",
            "groups": group_breakdown,
        },
        {
            "id": "hallucination_penalty",
            "label": "Keine offensichtlichen Platzhalter/Halluzinationen",
            "passed": not forbidden_hits,
            "score": hallucination_score,
            "detail": ", ".join(forbidden_hits) if forbidden_hits else "",
        },
    ]

    weights = {
        "syntax_prelude": 0.05,
        "diagram_kind": 0.05,
        "content_terms": 0.30,
        "relationships": 0.25,
        "section_completeness": 0.25,
        "hallucination_penalty": 0.10,
    }
    score = sum(c["score"] * weights[c["id"]] for c in checks)
    caps: list[tuple[float, str]] = []
    if not starts_with_keyword:
        caps.append((0.20, "missing_mermaid_prelude"))
    if content_score < 0.75:
        caps.append((0.70, "content_below_75_percent"))
    if required_edges and edge_score < 0.70:
        caps.append((0.75, "relationships_below_70_percent"))
    if group_breakdown and group_score < 0.75:
        caps.append((0.75, "section_completeness_below_75_percent"))
    if forbidden_hits:
        caps.append((0.65, "forbidden_placeholders_present"))
    applied_caps = [reason for cap, reason in caps if score > cap]
    if caps:
        score = min(score, *(cap for cap, _ in caps))
    return {
        "version": GRADING_VERSION,
        "score": score,
        "score_caps": applied_caps,
        "label": spec["label"],
        "checks": checks,
        "matched_terms": matched_terms,
        "missing_terms": missing_terms,
        "matched_edges": matched_edges,
        "missing_edges": missing_edges,
        "section_groups": group_breakdown,
        "forbidden_hits": forbidden_hits,
    }


class DiagramToMermaidTask(Task):
    name = "diagram_to_mermaid"
    label = "Diagramm → Mermaid"
    requires_vlm = True

    def __init__(self, prompt_file: Path, assets_dir: Path) -> None:
        self.spec = json.loads(prompt_file.read_text())
        self.assets_dir = assets_dir

    def _starts_with_keyword(self, text: str) -> tuple[bool, str | None]:
        first_line = text.lstrip().split("\n", 1)[0].strip().lower()
        for kw in self.spec["diagram_keywords"]:
            if first_line.startswith(kw.lower()):
                return True, kw
        return False, None

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
                resp = client.chat(model.id, messages, max_tokens=8000, temperature=0.2, timeout_s=400.0)
                wall_total += resp.metrics.wall_seconds
                tokens_total += resp.metrics.tokens_generated
                if resp.metrics.tokens_per_second:
                    speeds.append(resp.metrics.tokens_per_second)
                raw = resp.effective_text
                err = None
            except Exception as e:  # noqa: BLE001
                raw = ""
                err = str(e)

            mermaid, normalization_warnings = normalize_mermaid(raw)
            ok, kw = self._starts_with_keyword(mermaid)
            grade = grade_mermaid(src.stem, mermaid, kw, ok)
            score = grade["score"]

            mm_path = artifact_dir / f"{src.stem}.mmd"
            mm_path.write_text(mermaid)
            raw_path = artifact_dir / f"{src.stem}.raw.txt"
            raw_path.write_text(raw)

            # copy source image into artifact dir for the report
            img_target = artifact_dir / src.name
            if not img_target.exists():
                img_target.write_bytes(src.read_bytes())

            sub_results.append(
                {
                    "id": src.stem,
                    "image_name": src.name,
                    "image_path": str(img_target.relative_to(store.root)),
                    "mermaid_path": str(mm_path.relative_to(store.root)),
                    "mermaid": mermaid,
                    "raw": raw,
                    "diagram_kind": kw,
                    "starts_with_keyword": ok,
                    "normalization_warnings": normalization_warnings,
                    "grade": grade,
                    "score": score,
                    "error": err,
                }
            )
            artifacts.append(
                Artifact(
                    kind="image",
                    label=f"Quelle {src.stem}",
                    path=str(img_target.relative_to(store.root)),
                    mime="image/jpeg",
                )
            )
            artifacts.append(
                Artifact(
                    kind="text",
                    label=f"Mermaid {src.stem}",
                    path=str(mm_path.relative_to(store.root)),
                    mime="text/plain",
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
