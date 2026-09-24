"""HTML → PDF via headless Chromium.

The campaign report is design-heavy — full-bleed gradient banner, zero page
margins, crisp charts — and Google Drive's HTML→Docs conversion (:mod:`src.lib.pdf`)
cannot do any of that: it applies fixed page margins and resamples images. A real
browser renders the CSS exactly, so the report uses this path instead.

Two engines, one function:

* **On Lambda** Chromium comes from a layer built by
  ``src/tools/publish_chromium_layer`` — sparticuz/chromium's arm64 pack under
  ``/opt/chromium`` (four Brotli archives: the ``headless_shell`` binary, the
  AL2023 libraries it needs, SwiftShader, a ``fonts.conf``) plus DejaVu Sans under
  ``/opt/fonts`` for Hebrew. The pack is inflated into ``/tmp`` once per
  container, and this module drives it directly over the **DevTools protocol**
  (``--remote-debugging-pipe`` → ``Page.printToPDF``) — the same thing Playwright
  does, in a hundred lines of standard library. (This ``headless_shell`` build
  ignores the ``--print-to-pdf`` switch; it hangs.) No Playwright on Lambda: its
  bundled Node is 118 MB, which put code + layer within 6 MB of Lambda's 250 MB
  limit, lost its execute bit in ``sam build``, and tied every deploy to a
  Playwright↔Chromium version pair.
* **Locally** there is no pack, so Playwright's bundled Chromium renders
  (``pip install -r requirements-dev.txt`` and ``playwright install chromium``).

Both print with backgrounds, the CSS page size, and zero margins.

The signed contract prints here too (:func:`src.sign_page.file_contract`), so the PDF
is the document the client read. It prints in a background task after the client
has already been answered, and falls back to Drive's converter if Chromium fails,
so signing itself never depends on the browser.
"""

from __future__ import annotations

import base64
import json
import os
import select
import shutil
import signal
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

PACK_DIR_DEFAULT = "/opt/chromium"
TMP = Path("/tmp")

# sparticuz/chromium's defaults for running inside Lambda.
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
    raw = os.environ.get("CHROMIUM_PACK_DIR") or PACK_DIR_DEFAULT
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

        def inflate(name: str, out: Path) -> None:
            # Streamed, never whole-in-memory: the binary is ~250 MB decompressed,
            # and this account caps a function at 512 MB — holding it in RAM got
            # the runtime OOM-killed.
            decoder = brotli.Decompressor()
            with open(pack / name, "rb") as src, open(out, "wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(decoder.process(chunk))

        for name, dest in (("al2023.tar.br", tmp / "al2023"), ("fonts.tar.br", tmp / "fonts"),
                           ("swiftshader.tar.br", tmp)):
            dest.mkdir(parents=True, exist_ok=True)
            archive = tmp / name[:-3]
            inflate(name, archive)
            with tarfile.open(archive) as tar:
                tar.extractall(dest, filter="data")
            archive.unlink()
        partial = tmp / "chromium.partial"
        inflate("chromium.br", partial)
        partial.chmod(0o700)
        partial.rename(exe)  # atomic: a second invocation never sees a half-written binary

    lib = next((p for p in (tmp / "al2023").rglob("lib") if p.is_dir()), tmp / "al2023" / "lib")
    os.environ["LD_LIBRARY_PATH"] = f"{lib}:{os.environ.get('LD_LIBRARY_PATH', '')}".rstrip(":")
    os.environ.setdefault("FONTCONFIG_PATH", str(tmp / "fonts"))
    os.environ.setdefault("HOME", str(tmp))
    return str(exe)


PAGE_SIZES = {"A4": "A4", "Letter": "letter"}


def _with_page_css(html: str, page_format: str) -> str:
    """Chromium's own printer takes the page box from CSS. Give it one when the
    document has none, so the PDF is full-bleed whichever engine printed it."""
    if "@page" in html:
        return html
    rule = f"<style>@page{{size:{PAGE_SIZES.get(page_format, page_format)};margin:0}}</style>"
    return html.replace("</head>", rule + "</head>", 1) if "</head>" in html else rule + html


class _Cdp:
    """A minimal DevTools-protocol client over ``--remote-debugging-pipe``.

    Chromium reads commands on fd 3 and writes replies and events on fd 4, as
    NUL-terminated JSON. ``posix_spawn`` wires those two descriptors up without
    the fd-closing that ``subprocess`` does behind a ``preexec_fn``.
    """

    def __init__(self, exe: str, args: list[str], log_path: Path):
        import fcntl

        def high(fd: int) -> int:  # keep our ends clear of 3 and 4
            new = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10)
            os.close(fd)
            return new

        chrome_in, ours_out = os.pipe()
        ours_in, chrome_out = os.pipe()
        chrome_in, ours_out, ours_in, chrome_out = map(high, (chrome_in, ours_out, ours_in, chrome_out))
        actions = [
            (os.POSIX_SPAWN_DUP2, chrome_in, 3),
            (os.POSIX_SPAWN_DUP2, chrome_out, 4),
            (os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0),
            (os.POSIX_SPAWN_OPEN, 2, str(log_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
        ]
        self.pid = os.posix_spawn(exe, [exe, *args, "--remote-debugging-pipe"], dict(os.environ),
                                  file_actions=actions)
        os.close(chrome_in)
        os.close(chrome_out)
        self._w, self._r = ours_out, ours_in
        self._buf, self._id = b"", 0
        self._events: list[dict[str, Any]] = []

    def _next(self, deadline: float) -> dict[str, Any]:
        while b"\0" not in self._buf:
            left = deadline - time.time()
            if left <= 0:
                raise ChromiumError("Chromium stopped answering")
            ready, _, _ = select.select([self._r], [], [], left)
            if ready:
                chunk = os.read(self._r, 1 << 20)
                if not chunk:
                    raise ChromiumError("Chromium exited before finishing")
                self._buf += chunk
        raw, _, self._buf = self._buf.partition(b"\0")
        return dict(json.loads(raw))

    def call(self, method: str, params: Optional[dict[str, Any]] = None, *,
             session: Optional[str] = None, timeout: float = 60) -> dict[str, Any]:
        self._id += 1
        message: dict[str, Any] = {"id": self._id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        os.write(self._w, json.dumps(message).encode("utf-8") + b"\0")
        deadline = time.time() + timeout
        while True:
            reply = self._next(deadline)
            if reply.get("id") == self._id:
                if "error" in reply:
                    raise ChromiumError(f"{method}: {reply['error'].get('message')}")
                return dict(reply.get("result") or {})
            self._events.append(reply)  # an event; someone may be waiting for it

    def wait_event(self, name: str, *, timeout: float = 60) -> None:
        deadline = time.time() + timeout
        while True:
            for event in self._events:
                if event.get("method") == name:
                    self._events.remove(event)
                    return
            self._events.append(self._next(deadline))

    def close(self) -> None:
        try:
            self.call("Browser.close", timeout=5)
        except Exception:  # noqa: BLE001 - it is going away either way
            pass
        for fd in (self._w, self._r):
            try:
                os.close(fd)
            except OSError:
                pass
        for _ in range(50):  # up to 5 s to leave on its own, then insist
            if os.waitpid(self.pid, os.WNOHANG)[0]:
                return
            time.sleep(0.1)
        try:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        except OSError:
            pass


def _render_cdp(exe: str, html: str, page_format: str) -> bytes:
    """Print through the DevTools protocol: backgrounds on, CSS page size, zero
    margins — the same request Playwright's ``page.pdf`` makes."""
    work = Path(tempfile.mkdtemp(prefix="pdf-", dir=str(TMP) if TMP.exists() else None))
    cdp: Optional[_Cdp] = None
    try:
        source, log = work / "report.html", work / "chromium.log"
        source.write_text(_with_page_css(html, page_format), encoding="utf-8")
        cdp = _Cdp(exe, [*LAMBDA_ARGS, "--headless", f"--user-data-dir={work / 'profile'}", "about:blank"], log)
        try:
            target = cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
            session = cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
            cdp.call("Page.enable", session=session)
            cdp.call("Page.navigate", {"url": source.as_uri()}, session=session)
            cdp.wait_event("Page.loadEventFired")
            result = cdp.call("Page.printToPDF", {
                "printBackground": True, "preferCSSPageSize": True,
                "marginTop": 0, "marginBottom": 0, "marginLeft": 0, "marginRight": 0,
            }, session=session, timeout=120)
        except ChromiumError as exc:
            tail = " | ".join(log.read_text(errors="replace").strip().splitlines()[-3:]) if log.exists() else ""
            raise ChromiumError(f"{exc}" + (f" - {tail}" if tail else "")) from exc
        return base64.b64decode(result["data"])
    finally:
        if cdp:
            cdp.close()
        shutil.rmtree(work, ignore_errors=True)  # /tmp is 512 MB and survives warm starts


def _render_playwright(html: str, page_format: str) -> bytes:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001 - a clear message beats an ImportError
        raise ChromiumError(
            "No Chromium layer here and Playwright is not installed. Locally: "
            "`pip install -r requirements-dev.txt` and `playwright install chromium`. "
            "On Lambda: attach the layer (ChromiumLayerArn)."
        ) from exc

    launch: dict[str, Any] = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH")
    if exe:
        launch["executable_path"] = exe
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
    except Exception as exc:  # noqa: BLE001
        raise ChromiumError(f"Chromium could not render the PDF: {exc}") from exc


def engine() -> str:
    """Which renderer this environment will use: ``cdp`` (the layer) or ``playwright``."""
    return "cdp" if _pack_dir() else "playwright"


def render(html: str, *, page_format: str = "A4") -> bytes:
    """Return PDF bytes for ``html``, full-bleed (zero margins, backgrounds on)."""
    pack = _pack_dir()
    if pack:
        return _render_cdp(_inflate(pack), html, page_format)
    return _render_playwright(html, page_format)


def self_check() -> dict[str, Any]:
    """Render a small Hebrew page and report how; for proving a deploy, not for Dror."""
    started = time.time()
    html = ('<html dir="rtl"><head><meta charset="utf-8"></head>'
            '<body style="font-family:DejaVu Sans,sans-serif;background:#0f6b5c;color:#fff">'
            "<h1>בדיקת Chromium</h1><p>אם זה PDF, הדוח החודשי יכול להתרנדר כאן.</p></body></html>")
    out: dict[str, Any] = {"engine": engine(), "pack": str(_pack_dir())}
    try:
        pdf = render(html)
    except ChromiumError as exc:
        return {**out, "ok": False, "error": str(exc), "ms": int((time.time() - started) * 1000)}
    return {**out, "ok": pdf[:4] == b"%PDF", "pdf_bytes": len(pdf), "ms": int((time.time() - started) * 1000)}
