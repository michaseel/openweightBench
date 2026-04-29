"""Generate the six benchmark teaser images via OpenRouter.

Uses Google Nano Banana 2 (gemini-3.1-flash-image-preview).
Reads OPENROUTER_API_KEY from .env. Saves PNGs to assets/landing/<name>.png.
"""

from __future__ import annotations

import base64
import os
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
OUT_DIR = ROOT / "assets" / "landing"
MODEL = "google/gemini-3.1-flash-image-preview"

STYLE = (
    "Editorial flat illustration, muted warm palette of cream, sand, "
    "stone, sage and a single accent of deep terracotta. Clean geometric "
    "shapes, generous negative space, subtle paper texture. No text, no "
    "letters, no UI mockups, no logos. Square 1:1 framing. Minimalist, "
    "calm, slightly abstract — feels like it belongs next to a serif "
    "headline on a research page."
)

PROMPTS: dict[str, str] = {
    "coding": (
        "A minimalist illustration of a kanban board: three vertical "
        "lanes with simple rounded rectangles as task cards, a couple of "
        "cards mid-flight between lanes. " + STYLE
    ),
    "vision": (
        "A minimalist illustration of an open notebook with handwritten "
        "scribbles abstracted into wavy lines, partly overlapped by a "
        "soft magnifying-glass shape that reveals crisper geometry "
        "beneath — symbolizing OCR and visual understanding. " + STYLE
    ),
    "niah": (
        "A minimalist illustration of a single bright thin needle "
        "resting on a textured field of straw-like horizontal strokes. "
        "The needle is the only crisp object; the haystack recedes. "
        + STYLE
    ),
    "tool_use": (
        "A minimalist illustration of three abstract tools (a wrench, "
        "a small cog, and a dotted line connecting them) arranged like "
        "a constellation around a central pause-shaped node. Suggests "
        "function calling and orchestration. " + STYLE
    ),
    "diagram_to_svg": (
        "A minimalist illustration: on the left, a hand-sketched "
        "flowchart with rough boxes and arrows; on the right, the same "
        "shapes redrawn in crisp vector geometry. A soft gradient "
        "connects the two halves. " + STYLE
    ),
    "hallucination": (
        "A minimalist illustration of a thought bubble whose outline "
        "fades into dotted lines and dissolves into small floating "
        "particles — symbolizing fabricated, ungrounded answers. "
        + STYLE
    ),
}


def load_env() -> str:
    if not ENV_FILE.exists():
        sys.exit(f"missing .env at {ENV_FILE}")
    for line in ENV_FILE.read_text().splitlines():
        m = re.match(r"^\s*OPENROUTER_API_KEY\s*=\s*(.+?)\s*$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    sys.exit("OPENROUTER_API_KEY not found in .env")


def generate(name: str, prompt: str, api_key: str) -> Path:
    out = OUT_DIR / f"{name}.png"
    if out.exists() and "--force" not in sys.argv:
        print(f"  skip {name} — already exists ({out.relative_to(ROOT)})")
        return out

    print(f"  generating {name} …", flush=True)
    resp = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/innoq/openweightBench",
            "X-Title": "openweightBench",
        },
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["image", "text"],
        },
        timeout=180,
    )
    if resp.status_code != 200:
        sys.exit(f"OpenRouter error {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    msg = data["choices"][0]["message"]
    images = msg.get("images") or []
    if not images:
        sys.exit(f"no image returned for {name}: {data}")

    url = images[0]["image_url"]["url"]
    if not url.startswith("data:image/"):
        sys.exit(f"unexpected image url for {name}: {url[:80]}")

    payload = url.split(",", 1)[1]
    out.write_bytes(base64.b64decode(payload))
    print(f"  wrote {out.relative_to(ROOT)} ({out.stat().st_size // 1024} kB)")
    return out


def main() -> None:
    api_key = load_env()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    targets = {k: v for k, v in PROMPTS.items() if not only or k in only}
    print(f"generating {len(targets)} image(s) → {OUT_DIR.relative_to(ROOT)}/")
    for name, prompt in targets.items():
        generate(name, prompt, api_key)


if __name__ == "__main__":
    main()
