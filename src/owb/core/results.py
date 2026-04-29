from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(protected_namespaces=())


class ModelInfo(_Base):
    """Metadata for a single LM Studio model, as returned by /api/v0/models."""

    id: str
    type: Literal["llm", "vlm", "embeddings"]
    publisher: str | None = None
    arch: str | None = None
    compatibility_type: str | None = None
    quantization: str | None = None
    state: Literal["loaded", "not-loaded"] = "not-loaded"
    max_context_length: int = 0
    capabilities: list[str] = Field(default_factory=list)
    loaded_context_length: int | None = None

    @property
    def is_vision(self) -> bool:
        return self.type == "vlm"

    @property
    def supports_tools(self) -> bool:
        return "tool_use" in self.capabilities

    @property
    def is_mlx(self) -> bool:
        return self.compatibility_type == "mlx"


class Metrics(_Base):
    """Performance metrics captured for a single chat completion (or aggregated)."""

    wall_seconds: float
    tokens_generated: int = 0
    tokens_per_second: float = 0.0
    time_to_first_token_ms: float | None = None
    peak_rss_mb: float | None = None


class Artifact(_Base):
    """Pointer to an artefact on disk. Paths are relative to the project root."""

    kind: Literal["html", "image", "text", "json"]
    label: str
    path: str
    mime: str | None = None


class TaskResult(_Base):
    """Result of a single task run for a single model.

    Stored as JSON at `results/<task>/<safe_model_id>.json`.
    Artifact paths inside are relative to the project root so the report
    can resolve them no matter where it lives.
    """

    task: str
    model_id: str
    model_info: ModelInfo
    started_at: datetime
    completed_at: datetime
    metrics: Metrics
    score: float | None = None
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    raw_response: str = ""
    artifacts: list[Artifact] = Field(default_factory=list)
    error: str | None = None
    hardware: dict[str, Any] = Field(default_factory=dict)
    bench_version: str = "0.1.0"


def safe_model_id(model_id: str) -> str:
    """Filesystem-safe version of a model id (no slashes, no spaces)."""
    return model_id.replace("/", "_").replace(" ", "_")


class BenchStore:
    """Per-(model, task) JSON store on disk.

    Layout:
      <root>/results/<task>/<safe_model_id>.json
      <root>/artifacts/<safe_model_id>/<task>/...
    """

    def __init__(self, project_root: Path) -> None:
        self.root = project_root
        self.results_dir = project_root / "results"
        self.artifacts_dir = project_root / "artifacts"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    # ----- paths --------------------------------------------------------

    def result_path(self, task: str, model_id: str) -> Path:
        return self.results_dir / task / f"{safe_model_id(model_id)}.json"

    def artifact_dir(self, task: str, model_id: str) -> Path:
        return self.artifacts_dir / safe_model_id(model_id) / task

    # ----- queries ------------------------------------------------------

    def has_result(self, task: str, model_id: str) -> bool:
        """A stored result counts as 'done' only if it has a top-level
        score and no top-level error. Tasks where every sub-stage errored
        out store score=None; those should be retried automatically."""
        p = self.result_path(task, model_id)
        if not p.exists():
            return False
        try:
            r = TaskResult.model_validate_json(p.read_text())
        except Exception:  # noqa: BLE001
            return False
        lengths = r.score_breakdown.get("lengths") if r.score_breakdown else None
        if r.task in {"niah", "niah_deep"} and lengths and all(L.get("error") for L in lengths):
            return False
        if r.task in {"niah", "niah_deep"} and lengths and any(
            not L.get("skipped") and not L.get("error") and "combined_score" not in L
            for L in lengths
        ):
            return False
        diagrams = r.score_breakdown.get("diagrams") if r.score_breakdown else None
        if r.task == "diagram_to_mermaid" and diagrams and any(
            "normalization_warnings" not in d for d in diagrams
        ):
            return False
        if r.task == "diagram_to_mermaid" and diagrams and any(
            (d.get("grade") or {}).get("version") != 2 for d in diagrams
        ):
            return False
        return r.error is None and r.score is not None

    def load(self, task: str, model_id: str) -> TaskResult | None:
        p = self.result_path(task, model_id)
        if not p.exists():
            return None
        try:
            return TaskResult.model_validate_json(p.read_text())
        except Exception:  # noqa: BLE001
            return None

    def save(self, result: TaskResult) -> Path:
        p = self.result_path(result.task, result.model_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(result.model_dump_json(indent=2))
        return p

    # ----- iteration ----------------------------------------------------

    def task_names(self) -> list[str]:
        return sorted(
            d.name for d in self.results_dir.iterdir() if d.is_dir()
        )

    def model_ids_for_task(self, task: str) -> list[str]:
        d = self.results_dir / task
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.json"))

    def all_for_task(self, task: str) -> list[TaskResult]:
        d = self.results_dir / task
        if not d.exists():
            return []
        out: list[TaskResult] = []
        for p in sorted(d.glob("*.json")):
            try:
                out.append(TaskResult.model_validate_json(p.read_text()))
            except Exception:  # noqa: BLE001
                continue
        return out

    def all_for_model(self, model_id: str) -> list[TaskResult]:
        out: list[TaskResult] = []
        sm = safe_model_id(model_id)
        for task in self.task_names():
            p = self.results_dir / task / f"{sm}.json"
            if p.exists():
                try:
                    out.append(TaskResult.model_validate_json(p.read_text()))
                except Exception:  # noqa: BLE001
                    continue
        return out

    def all_known_models(self) -> list[str]:
        """Every model that appears in at least one results file."""
        seen: dict[str, str] = {}
        for task in self.task_names():
            for p in (self.results_dir / task).glob("*.json"):
                try:
                    r = TaskResult.model_validate_json(p.read_text())
                except Exception:  # noqa: BLE001
                    continue
                seen[r.model_id] = r.model_id
        return sorted(seen)

    def model_info(self, model_id: str) -> ModelInfo | None:
        """Return the latest ModelInfo we saved for a model, if any."""
        for r in self.all_for_model(model_id):
            return r.model_info
        return None

    def filter_pending(
        self,
        models: Iterable[ModelInfo],
        task: str,
        force: bool = False,
        rerun_models: list[str] | None = None,
    ) -> list[ModelInfo]:
        """Drop models that already have a successful result for this task.

        `force=True` returns all models (rerun everything).
        `rerun_models` overrides skip-logic for specific ids.
        """
        rerun = set(rerun_models or [])
        out: list[ModelInfo] = []
        for m in models:
            if force or m.id in rerun:
                out.append(m)
                continue
            if not self.has_result(task, m.id):
                out.append(m)
        return out
