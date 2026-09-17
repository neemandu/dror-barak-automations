"""HTML → PDF via headless Chromium (Playwright).

The campaign report is design-heavy — full-bleed gradient banner, zero page
margins, crisp charts — and Google Drive's HTML→Docs conversion (:mod:`src.lib.pdf`)
cannot do any of that: it applies fixed page margins and resamples images. A real
browser renders the CSS exactly, so the report uses this path instead.

Locally, Chromium is the one Playwright bundles (``playwright install chromium``).
**On Lambda it comes from a layer** built by ``src/tools/publish_chromium_layer``:
sparticuz/chromium's arm64 pack under ``/opt/chromium`` — four Brotli archives
(the ``headless_shell`` binary, the AL2023 shared libraries it needs, SwiftShader,
a ``fonts.conf``) — plus DejaVu Sans under ``/opt/fonts`` for Hebrew. ``/opt`` is
read-only and Chromium wants a writable profile, so the pack is inflated into
``/tmp`` once per container (a few seconds) and reused on warm starts.

The contract stays on the Drive path: it is mostly text, needs no browser, and
keeping it there avoids making the signing flow depend on Chromium.
"""

from __future__ import annotations

import io
import os
import shutil
import tarfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional

PACK_DIR_DEFAULT = "/opt/chromium"
TMP = Path("/tmp")

# sparticuz/chromium's defaults, minus the headless flag Playwright adds itself.
# --single-process / --no-zygote: Lambda forbids the sandbox's prctl calls;
# --disable-setuid-sandbox: the function runs without the setuid helper.
LAMBDA_ARGS = [
    "--ash-no-nudges", "--disable-domain-reliability", "--disable-print-preview",
    "--disk-cache-size=33554432", "--no-default-browser-check", "--no-pings",
    "--single-process", "--font-render-hinting=none",
    "--disable-features=AudioServiceOutOfProcess,IsolateOrigins,site-per-process",
    "--enable-features=SharedArrayBuffer", "--ignore-gpu-blocklist", "--in-process-gpu",
    "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
    "--allow-running-insecure-content", "--disable-setuid-sandbox",
    "--disable-site-isolation-trials", "--disable-web-security",
    "--no-sandbox", "--no-zygote", "--disable-dev-shm-usage",
]


class ChromiumError(RuntimeError):
    """Raised when the browser could not render the document."""


def _pack_dir() -> Optional[Path]:
    """Where the layer put the pack, or None when running on a normal machine."""
    raw = os.environ.get("PLAYWRIGHT_CHROMIUM_PACK") or PACK_DIR_DEFAULT
    path = Path(raw)
    return path if (path / "chromium.br").exists() else None


def _inflate(pack: Path, tmp: Path = TMP) -> str:
    """Unpack the layer into ``tmp`` (once per container) and return the binary path.

    Mirrors what the sparticuz npm package does for Node: Brotli-decompress the
    binary, untar the libraries, fonts and SwiftShader, and point the loader and
    fontconfig at them. A warm container already has ``tmp/chromium`` and skips
    all of it.
    """
    exe = tmp / "chromium"
    if not exe.exists():
        try:
            import brotli
        except ImportError as exc:  # pragma: no cover - requirements.txt has it
            raise ChromiumError("the brotli package is missing; add it to requirements.txt") from exc

        def inflate(name: str) -> bytes:
            return brotli.decompress((pack / name).read_bytes())

        for name, dest in (("al2023.tar.br", tmp / "al2023"), ("fonts.tar.br", tmp / "fonts"),
                           ("swiftshader.tar.br", tmp)):
            dest.mkdir(parents=True, exist_ok=True)
            with tarfile.open(fileobj=io.BytesIO(inflate(name))) as tar:
                tar.extractall(dest, filter="data")
        partial = tmp / "chromium.partial"
        partial.write_bytes(inflate("chromium.br"))
        partial.chmod(0o700)
        partial.rename(exe)  # atomic: a second invocation never sees a half-written binary

    lib = next((p for p in (tmp / "al2023").rglob("lib") if p.is_dir()), tmp / "al2023" / "lib")
    os.environ["LD_LIBRARY_PATH"] = f"{lib}:{os.environ.get('LD_LIBRARY_PATH', '')}".rstrip(":")
    os.environ.setdefault("FONTCONFIG_PATH", str(tmp / "fonts"))
    os.environ.setdefault("HOME", str(tmp))
    _ensure_node_executable(tmp)
    return str(exe)


def _ensure_node_executable(tmp: Path = TMP) -> None:
    """Playwright drives the browser through its bundled Node binary, and
    ``sam build --use-container`` copies packages out of the build container
    without their execute bits — so on Lambda ``playwright/driver/node`` is a
    122 MB file nobody may run. ``deploy_stack`` restores the bit before
    packaging; this is the belt to that suspender: copy it somewhere writable and
    tell Playwright, so an unfixed package still renders."""
    try:
        from playwright._impl._driver import compute_driver_executable
    except Exception:  # noqa: BLE001 - no playwright, nothing to fix
        return
    node, _cli = compute_driver_executable()

    def runnable(path: str) -> bool:
        # Mode bits, not os.access(): as root, access(X_OK) says yes to a file
        # with no execute bit at all, and then exec fails anyway.
        return os.path.exists(path) and bool(os.stat(path).st_mode & 0o111)

    if runnable(node):
        return
    copy = tmp / "pw-node"
    if not runnable(str(copy)):
        partial = tmp / "pw-node.partial"
        shutil.copyfile(node, partial)
        partial.chmod(0o755)
        partial.rename(copy)
    os.environ["PLAYWRIGHT_NODEJS_PATH"] = str(copy)


def _launch_options() -> dict[str, Any]:
    launch: dict[str, Any] = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH")  # an explicit binary always wins
    pack = None if exe else _pack_dir()
    if pack:
        exe = _inflate(pack)
        launch["args"] = list(LAMBDA_ARGS)
    if exe:
        launch["executable_path"] = exe
    return launch


def render(html: str, *, page_format: str = "A4") -> bytes:
    """Return PDF bytes for ``html``, full-bleed (zero margins, backgrounds on)."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001 - a clear message beats an ImportError
        raise ChromiumError(
            "Playwright is not installed. `pip install playwright` and "
            "`playwright install chromium`."
        ) from exc

    launch = _launch_options()
    # A fresh profile per render, removed afterwards: on a warm Lambda the
    # default profile dir in /tmp grows until the 512 MB disk is full.
    profile = TMP / f"pw-{uuid.uuid4().hex}" if "executable_path" in launch else None
    if profile:
        launch["args"] = launch["args"] + [f"--user-data-dir={profile}"]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch)
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                return page.pdf(
                    format=page_format,
                    print_background=True,               # render the gradient/colours
                    prefer_css_page_size=True,           # honour @page size/margin
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                )
            finally:
                browser.close()
    except ChromiumError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ChromiumError(f"Chromium could not render the PDF: {exc}") from exc
    finally:
        if profile:
            shutil.rmtree(profile, ignore_errors=True)


def self_check() -> dict[str, Any]:
    """Render a small Hebrew page and report how; for proving a deploy, not for Dror."""
    started = time.time()
    html = ('<html dir="rtl"><body style="font-family:DejaVu Sans,sans-serif">'
            "<h1>בדיקת Chromium</h1><p>אם זה PDF, הדוח החודשי יכול להתרנדר כאן.</p></body></html>")
    try:
        pdf = render(html)
    except ChromiumError as exc:
        return {"ok": False, "error": str(exc), "pack": str(_pack_dir()), "ms": int((time.time() - started) * 1000)}
    return {"ok": pdf[:4] == b"%PDF", "pdf_bytes": len(pdf), "pack": str(_pack_dir()),
            "executable": os.environ.get("PLAYWRIGHT_CHROMIUM_PATH") or (str(TMP / "chromium") if _pack_dir() else "playwright-bundled"),
            "ms": int((time.time() - started) * 1000)}
