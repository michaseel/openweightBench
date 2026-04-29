"""Render an HTML file via headless Chromium and save a screenshot."""

from __future__ import annotations

import html as _html
from pathlib import Path


_MERMAID_WRAPPER = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html,body{margin:0;background:#fff;font-family:system-ui,-apple-system,sans-serif}
  .wrap{padding:24px;display:inline-block;background:#fff}
  .wrap svg{display:block;height:auto;max-width:none}
  .err{color:#b91c1c;font-family:ui-monospace,monospace;white-space:pre-wrap;font-size:13px;max-width:900px;padding:16px}
</style>
<script type="module">
  import m from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
  m.initialize({startOnLoad:false,theme:'neutral',securityLevel:'loose'});
  const el = document.querySelector('.mermaid');
  const code = el.textContent.trim();
  try {
    const {svg} = await m.render('owb-m', code);
    el.innerHTML = svg;
    // Force the SVG to render at a comfortable preview size.
    const inner = el.querySelector('svg');
    if (inner) {
      const w = inner.viewBox && inner.viewBox.baseVal ? inner.viewBox.baseVal.width : null;
      const h = inner.viewBox && inner.viewBox.baseVal ? inner.viewBox.baseVal.height : null;
      if (w && h) {
        const targetW = Math.min(1100, Math.max(720, w * 1.4));
        inner.removeAttribute('style');
        inner.setAttribute('width', String(Math.round(targetW)));
        inner.setAttribute('height', String(Math.round(targetW * h / w)));
      }
    }
    document.body.dataset.rendered = '1';
  } catch (e) {
    el.outerHTML = '<pre class="err">' + (e && e.message ? e.message : String(e)) + '</pre>';
    document.body.dataset.rendered = 'error';
  }
</script>
</head><body><div class="wrap"><div class="mermaid">__CODE__</div></div></body></html>
"""


def screenshot_html(
    html_path: Path,
    out_path: Path,
    *,
    width: int = 1280,
    height: int = 800,
    full_page: bool = True,
    timeout_ms: int = 8000,
    wait_selector: str | None = None,
    crop_selector: str | None = None,
    extra_wait_ms: int = 0,
) -> Path:
    """Open `html_path` in headless Chromium, save PNG to `out_path`.

    `crop_selector`: if given, screenshot only the matching element's
    bounding box instead of the full page — useful when the rendered
    content sits inside a much larger viewport.
    """
    from playwright.sync_api import sync_playwright

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": width, "height": height})
        page = ctx.new_page()
        page.goto(html_path.resolve().as_uri(), timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:  # noqa: BLE001
            pass
        if extra_wait_ms > 0:
            page.wait_for_timeout(extra_wait_ms)
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=timeout_ms)
            except Exception:  # noqa: BLE001
                pass
        if crop_selector:
            elem = page.query_selector(crop_selector)
            if elem is not None:
                elem.screenshot(path=str(out_path))
                browser.close()
                return out_path
        page.screenshot(path=str(out_path), full_page=full_page)
        browser.close()
    return out_path


_SVG_WRAPPER = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html,body{margin:0;background:#fff;font-family:system-ui,-apple-system,sans-serif}
  .wrap{padding:24px;display:inline-block;background:#fff}
  /* Force a concrete render box: many model-emitted SVGs only set viewBox.
     Without a width here, an inline-block parent collapses the SVG to its
     intrinsic ~300x150 (or smaller) and the screenshot becomes a tiny
     blank tile. Fixed width + auto height uses viewBox aspect-ratio. */
  .wrap svg{display:block;width:1200px;height:auto;max-width:none}
</style></head><body><div class="wrap">__SVG__</div></body></html>
"""


def screenshot_svg(
    svg_text: str,
    out_path: Path,
    *,
    width: int = 1400,
    height: int = 1200,
    timeout_ms: int = 8000,
) -> Path | None:
    """Render raw inline SVG via headless Chromium, save PNG.

    Returns the PNG path on success, or None if the SVG is empty / not
    rendered to a non-zero box. Best-effort — caller treats absence as
    'no preview available'.
    """
    code = (svg_text or "").strip()
    if not code:
        return None
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_path.with_suffix(".render.html")
    html_path.write_text(_SVG_WRAPPER.replace("__SVG__", code))
    try:
        screenshot_html(
            html_path,
            out_path,
            width=width,
            height=height,
            full_page=True,
            timeout_ms=timeout_ms,
            crop_selector=".wrap",
        )
    except Exception:  # noqa: BLE001
        return None
    return out_path if out_path.exists() else None


def screenshot_mermaid(
    mermaid_code: str,
    out_dir: Path,
    stem: str,
    *,
    width: int = 1400,
    height: int = 1200,
    timeout_ms: int = 15000,
) -> Path | None:
    """Render mermaid code via mermaid.js + headless Chromium, save PNG.

    Returns the PNG path on success, or None if rendering failed (empty
    code, mermaid syntax error, no internet for the CDN fetch). Best-
    effort — caller should treat absence as "no preview available".
    """
    code = (mermaid_code or "").strip()
    if not code:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{stem}.render.html"
    png_path = out_dir / f"{stem}.render.png"
    html_path.write_text(_MERMAID_WRAPPER.replace("__CODE__", _html.escape(code)))
    try:
        screenshot_html(
            html_path,
            png_path,
            width=width,
            height=height,
            full_page=True,
            timeout_ms=timeout_ms,
            wait_selector="body[data-rendered]",
            crop_selector=".wrap",
        )
    except Exception:  # noqa: BLE001
        return None
    return png_path if png_path.exists() else None
