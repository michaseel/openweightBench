from __future__ import annotations

from pathlib import Path

from .base import Task
from .coding import CodingTask
from .context_growth import ContextGrowthTask
from .diagram_to_mermaid import DiagramToMermaidTask
from .diagram_to_svg import DiagramToSvgTask
from .hallucination import HallucinationTask
from .instruction_following import InstructionFollowingTask
from .niah import NIAHTask, STANDARD_TARGET_LENGTHS
from .nonsense import NonsenseTask
from .tool_use import ToolUseTask
from .vision import VisionTask

ROOT = Path(__file__).resolve().parents[3]
PROMPTS = ROOT / "prompts"
ASSETS = ROOT / "assets"


def all_tasks() -> dict[str, Task]:
    """Registry of every task instance, keyed by short name.

    Default-Set ist auf Wall-Time getrimmt:
      - `niah` läuft nur die größte applicable Stufe (statt 4 Stufen)
      - `long_context`, `comprehension`, `summarization` und `niah_deep` sind
        nur manuell anzuwählen via `--tasks long_context` etc.
    """
    niah_corpus = sorted((ASSETS / "niah").glob("kapitel_*.txt"))
    code_files = sorted((ASSETS / "niah").glob("*.cpp"))
    return {
        "coding": CodingTask(PROMPTS / "coding" / "kanban_board.json"),
        "vision": VisionTask(
            prompt_dir=PROMPTS / "vision",
            assets_dir=ASSETS / "vision",
        ),
        "niah": NIAHTask(
            PROMPTS / "niah" / "needles.json",
            corpus_files=niah_corpus + code_files,
            targets=STANDARD_TARGET_LENGTHS,
            top_stage_only=True,
            label="NIAH (schnell, max. 120k)",
            comprehension_prompt=PROMPTS / "comprehension.json",
        ),
        "context_growth": ContextGrowthTask(corpus_files=niah_corpus),
        "tool_use": ToolUseTask(
            prompt_file=PROMPTS / "tool_use.json",
            fixtures_dir=ASSETS / "tool_use",
        ),
        "diagram_to_svg": DiagramToSvgTask(
            prompt_file=PROMPTS / "vision" / "diagram_to_svg.json",
            assets_dir=ASSETS / "vision",
        ),
        "hallucination": HallucinationTask(PROMPTS / "hallucination.json"),
        # Optional / nur via --tasks anwählbar:
        "diagram_to_mermaid": DiagramToMermaidTask(
            prompt_file=PROMPTS / "vision" / "diagram_to_mermaid.json",
            assets_dir=ASSETS / "vision",
        ),
        "nonsense": NonsenseTask(PROMPTS / "nonsense.json"),
        "instruction_following": InstructionFollowingTask(
            PROMPTS / "instruction_following.json"
        ),
        "niah_deep": NIAHTask(
            PROMPTS / "niah" / "needles.json",
            corpus_files=niah_corpus + code_files,
            top_stage_only=False,
            name="niah_deep",
            label="NIAH Deep (32k-200k Heatmap)",
            comprehension_prompt=PROMPTS / "comprehension.json",
        ),
    }


# Reihenfolge bestimmt sowohl die Vorauswahl im interaktiven Menü als auch
# die Reihenfolge, in der `owb bench` die Tasks pro Modell ausführt.
# Optional/manuell: `nonsense` (in Halluzination integriert), `instruction_following`,
# `context_growth`, `niah_deep` — nur via `--tasks <name>`.
DEFAULT_TASKS = [
    "coding",
    "vision",
    "niah",
    "tool_use",
    "diagram_to_svg",
    "hallucination",
]


__all__ = ["Task", "all_tasks", "DEFAULT_TASKS", "PROMPTS", "ASSETS"]
