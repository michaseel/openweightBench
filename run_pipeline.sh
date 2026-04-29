#!/usr/bin/env bash
# Continuous benchmark pipeline — fresh start after cleanup.
set -euo pipefail

TASKS="coding,diagram_to_svg,hallucination,niah,tool_use,vision"
RUN="uv run owb bench --tasks $TASKS --yes"

echo "=== Pipeline started at $(date) ==="

# ── Restore: Gemma 3 small artifacts (lost in git mishap) ──────────────────
echo "--- Restore: Gemma 3 small (--force to regenerate artifacts) ---"
uv run owb bench --models "google/gemma-3-4b,google/gemma-3n-e4b" --tasks "$TASKS" --force --yes || true

# ── Coding re-runs with E2E timing fix ─────────────────────────────────────
echo "--- Coding re-runs (E2E fix) ---"
uv run owb bench --models "google/gemma-3-12b,google/gemma-3-27b,qwen/qwen3.6-27b,qwen3.5-27b-claude-4.6-opus-distilled-mlx,google/gemma-4-e4b@q8_0" --tasks "coding" --force --yes || true

# ── Phase 1: Complete partial models ───────────────────────────────────────
echo "--- Phase 1: Fill missing tasks for partial models ---"
$RUN --models "glm-4.5-air-mlx" || true
$RUN --models "google/gemma-4-e2b" || true
$RUN --models "liquid/lfm2-24b-a2b" || true
$RUN --models "microsoft/phi-4-reasoning-plus" || true
$RUN --models "nvidia/nemotron-3-nano-4b" || true
$RUN --models "openai/gpt-oss-120b" || true
$RUN --models "openai/gpt-oss-20b" || true
$RUN --models "qwen/qwen2.5-coder-32b" || true
$RUN --models "qwen/qwen3-coder-30b" || true
$RUN --models "qwen/qwen3-coder-next" || true
$RUN --models "zai-org/glm-4.7-flash" || true
$RUN --models "zai-org/glm-4.7-flash@q4_k_m" || true

# ── Phase 2: New small/fast models ─────────────────────────────────────────
echo "--- Phase 2: Small new models ---"
$RUN --models "ouro-2.6b" || true
$RUN --models "ibm/granite-4-h-tiny" || true
$RUN --models "liquid/lfm2.5-1.2b" || true
$RUN --models "qwen3.5-9b-mlx" || true
$RUN --models "qwen/qwen3-4b-2507" || true
$RUN --models "qwen/qwen3-4b-thinking-2507" || true

# ── Phase 3: New medium models ──────────────────────────────────────────────
echo "--- Phase 3: Medium new models ---"
$RUN --models "zai-org/glm-4.6v-flash" || true
$RUN --models "qwen/qwen3-8b" || true
$RUN --models "mistralai/ministral-3-14b-reasoning" || true
$RUN --models "nvidia/nemotron-3-nano" || true
$RUN --models "qwen/qwen2.5-coder-14b" || true

# ── Phase 4: 8-bit variants + qwen3-vl ────────────────────────────────────
echo "--- Phase 4: 8-bit variants + VL models ---"
$RUN --models "google/gemma-4-31b@q8_0" || true
$RUN --models "qwen/qwen3.6-27b@q8_0" || true
$RUN --models "nvidia/nemotron-3-nano-omni@q8_0" || true
$RUN --models "qwen/qwen3-vl-8b" || true
$RUN --models "qwen/qwen3-vl-30b" || true

# ── Phase 5: New large models ───────────────────────────────────────────────
echo "--- Phase 5: Large new models ---"
$RUN --models "meta/llama-3.3-70b" || true
$RUN --models "nvidia/nemotron-3-super" || true
$RUN --models "mistralai/devstral-small-2-2512" || true
$RUN --models "qwen/qwen3-30b-a3b-2507" || true

# ── Phase 6: Very large models ──────────────────────────────────────────────
echo "--- Phase 6: Very large models ---"
$RUN --models "allenai/olmo-3-32b-think" || true
$RUN --models "bytedance/seed-oss-36b" || true

echo "=== Pipeline completed at $(date) ==="
