from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from ..client.lmstudio import LMStudioClient
from ..core.results import BenchStore, ModelInfo, TaskResult


class Task(ABC):
    """Abstract base for all benchmark tasks.

    A task knows:
      - which models it applies to (`applicable`),
      - how to run itself against one model (`run`).
    """

    name: str = ""
    label: str = ""
    requires_vlm: bool = False
    requires_tool_use: bool = False
    min_context_tokens: int = 0

    def applicable(self, model: ModelInfo) -> bool:
        if self.requires_vlm and not model.is_vision:
            return False
        if self.requires_tool_use and not model.supports_tools:
            return False
        if model.max_context_length < self.min_context_tokens:
            return False
        if model.type == "embeddings":
            return False
        return True

    @abstractmethod
    def run(
        self,
        client: LMStudioClient,
        model: ModelInfo,
        store: BenchStore,
    ) -> TaskResult:
        """Execute the task against a single model and return the result.

        Use `store.artifact_dir(self.name, model.id)` for any output files
        and write Artifact paths relative to `store.root`.
        """

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)
