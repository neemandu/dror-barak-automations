"""The design system every page is built from.

One look for the whole product: the admin (dashboard, questionnaire editor,
answers) and the pages clients open (the questionnaire, the signing page). Tokens,
components, icons and the small interactions that make it feel alive (press
feedback, loading and success states, toasts with undo, count-up numbers,
relative times), all inline: the pages are served by Lambda, with no build step
and no static host.

Two page kinds, set on ``<html>``:

* ``app``: Dror's own screens. Dense, follows the system's light/dark setting.
* ``public``: what a client sees. Always light, larger type, the brand band.

Everything that moves respects ``prefers-reduced-motion``.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------- icons

# Lucide (ISC licence) paths, drawn with currentColor at 24x24.
_ICON_PATHS: dict[str, str] = {
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "copy": '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    "arrow-up": '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    "arrow-down": '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "arrow-left": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    "eye": '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    "eye-off": '<path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.53 13.53 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><path d="m2 2 20 20"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "external": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "file": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
    "more": '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "clipboard": '<rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/>',
    "inbox": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    "star": '<path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>',
    "sparkles": '<path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z"/>',
    "send": '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
    "pen": '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    "rotate": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
    "lock": '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "message": '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "check-circle": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>',
    "x-circle": '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    "minus-circle": '<circle cx="12" cy="12" r="10"/><path d="M8 12h8"/>',
    "minus": '<path d="M5 12h14"/>',
    "hourglass": '<path d="M5 22h14"/><path d="M5 2h14"/><path d="M17 22v-4.17a2 2 0 0 0-.59-1.41L12 12l-4.41 4.42A2 2 0 0 0 7 17.83V22"/><path d="M7 2v4.17a2 2 0 0 0 .59 1.41L12 12l4.41-4.42A2 2 0 0 0 17 6.17V2"/>',
    "percent": '<path d="M19 5 5 19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "calendar": '<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/>',
    "grip": '<circle cx="9" cy="12" r="1"/><circle cx="9" cy="5" r="1"/><circle cx="9" cy="19" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="15" cy="5" r="1"/><circle cx="15" cy="19" r="1"/>',
    # question kinds
    "type": '<path d="M4 7V4h16v3"/><path d="M9 20h6"/><path d="M12 4v16"/>',
    "align": '<path d="M21 6H3"/><path d="M15 12H3"/><path d="M17 18H3"/>',
    "mail": '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
    "phone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>',
    "hash": '<path d="M4 9h16"/><path d="M4 15h16"/><path d="M10 3 8 21"/><path d="M16 3l-2 18"/>',
    "circle-dot": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/>',
    "check-square": '<path d="m9 11 3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
    "gauge": '<path d="M12 20V10"/><path d="M18 20V4"/><path d="M6 20v-4"/>',
}

#: The icon for each question kind (see ``questionnaire.KINDS``).
KIND_ICONS = {"text": "type", "textarea": "align", "email": "mail", "tel": "phone", "url": "link",
              "number": "hash", "choice": "circle-dot", "multi": "check-square", "scale": "gauge"}


def icon(name: str, size: int = 16, *, cls: str = "") -> str:
    path = _ICON_PATHS.get(name, "")
    return (f'<svg class="icon{" " + cls if cls else ""}" width="{size}" height="{size}" viewBox="0 0 24 24" '
            f'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{path}</svg>')


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


FAVICON = ("data:image/svg+xml,"
           "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Cdefs%3E%3ClinearGradient "
           "id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%2300e5d0'/%3E%3Cstop "
           "offset='.5' stop-color='%2300a8f0'/%3E%3Cstop offset='1' stop-color='%232f7de1'/%3E"
           "%3C/linearGradient%3E%3C/defs%3E%3Crect width='64' height='64' rx='16' fill='url(%23g)'/%3E"
           "%3Cpath d='M22 16h11a16 16 0 0 1 0 32H22Z' fill='white'/%3E%3C/svg%3E")

# ------------------------------------------------------------------ CSS

CSS = r"""
html.app, html.public {
  --font: "Heebo", system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --brand: #1f6fd6; --brand-hover: #185cb4; --brand-ink: #1552a0; --brand-soft: #eaf2fd;
  --brand-grad: linear-gradient(100deg, #00e5d0 0%, #00a8f0 45%, #2f7de1 100%);
  --ring: #1f6fd6; --ring-soft: rgba(31, 111, 214, .18);
  --bg: #f7f8fa; --surface: #ffffff; --surface-2: #f9fafb; --surface-hover: #f3f5f8;
  --surface-active: #eceff3; --border: #e5e8ed; --border-soft: #eef0f3; --border-strong: #d0d5dd;
  --fg: #101828; --fg-2: #344054; --fg-muted: #667085; --fg-subtle: #98a2b3;
  --success: #12b76a; --success-ink: #067647; --success-soft: #ecfdf3; --success-border: #abefc6;
  --warn: #f79009; --warn-ink: #b54708; --warn-soft: #fffaeb; --warn-border: #fedf89;
  --danger: #f04438; --danger-ink: #b42318; --danger-soft: #fef3f2; --danger-border: #fecdca;
  --toast-bg: #101828; --toast-fg: #ffffff;
  --r-sm: 8px; --r-md: 10px; --r-lg: 14px; --r-xl: 20px;
  --sh-xs: 0 1px 2px rgba(16, 24, 40, .05);
  --sh-sm: 0 1px 3px rgba(16, 24, 40, .08), 0 1px 2px rgba(16, 24, 40, .04);
  --sh-md: 0 4px 8px -2px rgba(16, 24, 40, .08), 0 2px 4px -2px rgba(16, 24, 40, .04);
  --sh-lg: 0 12px 16px -4px rgba(16, 24, 40, .08), 0 4px 6px -2px rgba(16, 24, 40, .03);
  --sh-xl: 0 24px 48px -12px rgba(16, 24, 40, .22);
  --ease: cubic-bezier(.2, .8, .2, 1); --spring: cubic-bezier(.34, 1.4, .64, 1);
  --d1: .12s; --d2: .2s; --d3: .34s;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  html.app {
    --brand: #4d8ff0; --brand-hover: #6aa2f3; --brand-ink: #8fb9f7; --brand-soft: rgba(77, 143, 240, .14);
    --ring: #6aa2f3; --ring-soft: rgba(106, 162, 243, .25);
    --bg: #0c0e12; --surface: #14171c; --surface-2: #181b21; --surface-hover: #1c2027;
    --surface-active: #232830; --border: #242932; --border-soft: #1e232a; --border-strong: #343b46;
    --fg: #f2f4f7; --fg-2: #cfd4dc; --fg-muted: #98a2b3; --fg-subtle: #667085;
    --success-ink: #47cd89; --success-soft: rgba(18, 183, 106, .12); --success-border: rgba(18, 183, 106, .3);
    --warn-ink: #fdb022; --warn-soft: rgba(247, 144, 9, .12); --warn-border: rgba(247, 144, 9, .3);
    --danger-ink: #f97066; --danger-soft: rgba(240, 68, 56, .12); --danger-border: rgba(240, 68, 56, .32);
    --toast-bg: #f2f4f7; --toast-fg: #101828;
    --sh-xs: none; --sh-sm: 0 1px 2px rgba(0, 0, 0, .4); --sh-md: 0 4px 12px rgba(0, 0, 0, .4);
    --sh-lg: 0 12px 24px rgba(0, 0, 0, .45); --sh-xl: 0 24px 48px rgba(0, 0, 0, .55);
    color-scheme: dark;
  }
}

*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
body { margin: 0; font-family: var(--font); font-size: 14.5px; line-height: 1.55; color: var(--fg);
  background: var(--bg); text-rendering: optimizeLegibility; }
html.public body { font-size: 16px; line-height: 1.6; }
h1, h2, h3 { line-height: 1.25; text-wrap: balance; }
a { color: var(--brand); text-decoration: none; }
a:hover { text-decoration: underline; text-underline-offset: 3px; }
:focus-visible { outline: 2px solid var(--ring); outline-offset: 2px; }
::selection { background: var(--ring-soft); }
[hidden] { display: none !important; }
button { font: inherit; color: inherit; }
.icon { flex: none; display: inline-block; vertical-align: -.15em; }
.num { font-variant-numeric: tabular-nums; }
.ltr { direction: ltr; unicode-bidi: isolate; }
.muted { color: var(--fg-muted); }
.subtle { color: var(--fg-subtle); }
.small { font-size: 13px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.kbd { direction: ltr; unicode-bidi: isolate; font-family: var(--mono); font-size: 11px; line-height: 1; padding: 3px 5px; border-radius: 5px;
  border: 1px solid var(--border); border-bottom-width: 2px; background: var(--surface); color: var(--fg-muted); }
.btn-primary .kbd { background: rgba(255, 255, 255, .16); border-color: rgba(255, 255, 255, .25); color: #fff; }

/* ---- buttons */
.btn { --h: 36px; position: relative; display: inline-flex; align-items: center; justify-content: center; gap: 7px;
  height: var(--h); padding: 0 14px; border-radius: var(--r-md); border: 1px solid var(--border);
  background: var(--surface); color: var(--fg); font-size: 14px; font-weight: 500; line-height: 1;
  white-space: nowrap; cursor: pointer; user-select: none; text-decoration: none !important;
  box-shadow: var(--sh-xs); -webkit-tap-highlight-color: transparent;
  transition: background var(--d1) var(--ease), border-color var(--d1) var(--ease), color var(--d1),
    box-shadow var(--d1), transform var(--d1) var(--ease), opacity var(--d1); }
.btn:hover { background: var(--surface-hover); border-color: var(--border-strong); }
.btn:active { transform: scale(.97); }
.btn:disabled, .btn[aria-disabled="true"] { opacity: .5; pointer-events: none; box-shadow: none; }
.btn-primary { --spin: #fff; background: var(--brand); border-color: var(--brand); color: #fff;
  box-shadow: 0 1px 2px rgba(16, 24, 40, .1), inset 0 1px 0 rgba(255, 255, 255, .14); }
.btn-primary:hover { background: var(--brand-hover); border-color: var(--brand-hover); }
.btn-dark { --spin: #fff; background: var(--fg); border-color: var(--fg); color: var(--bg); }
.btn-dark:hover { background: var(--fg-2); border-color: var(--fg-2); }
.btn-ghost { background: transparent; border-color: transparent; box-shadow: none; color: var(--fg-2); }
.btn-ghost:hover { background: var(--surface-hover); border-color: transparent; color: var(--fg); }
.btn-danger { color: var(--danger-ink); }
.btn-danger:hover { background: var(--danger-soft); border-color: var(--danger-border); }
.btn-danger-solid { --spin: #fff; background: var(--danger); border-color: var(--danger); color: #fff; }
.btn-danger-solid:hover { background: var(--danger-ink); border-color: var(--danger-ink); }
.btn-sm { --h: 30px; padding: 0 10px; font-size: 13px; border-radius: var(--r-sm); gap: 6px; }
.btn-lg { --h: 48px; padding: 0 24px; font-size: 16px; font-weight: 600; border-radius: 12px; }
.btn-icon { width: var(--h); padding: 0; }
.btn-block { width: 100%; }
.btn.is-loading { color: transparent !important; pointer-events: none; }
.btn.is-loading > * { visibility: hidden; }
.btn.is-loading::after { content: ""; position: absolute; inset: 0; margin: auto; width: 16px; height: 16px;
  border-radius: 50%; border: 2px solid var(--spin, var(--fg)); border-inline-end-color: transparent;
  animation: spin .6s linear infinite; }
.btn.is-done { animation: pop var(--d3) var(--spring); }
.btn.is-done .icon { color: currentColor; }

/* ---- form controls */
.field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.label { font-size: 13px; font-weight: 500; color: var(--fg-2); }
.hint { font-size: 12.5px; color: var(--fg-muted); }
.input, .select, .textarea { width: 100%; height: 38px; padding: 0 12px; border-radius: var(--r-md);
  border: 1px solid var(--border); background: var(--surface); color: var(--fg); font: inherit; font-size: 14px;
  box-shadow: var(--sh-xs); transition: border-color var(--d1), box-shadow var(--d2) var(--ease), background var(--d1); }
.textarea { height: auto; min-height: 84px; padding: 9px 12px; line-height: 1.55; resize: vertical; }
.input:hover, .select:hover, .textarea:hover { border-color: var(--border-strong); }
.input:focus, .select:focus, .textarea:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 4px var(--ring-soft); }
.input::placeholder, .textarea::placeholder { color: var(--fg-subtle); }
.select { appearance: none; -webkit-appearance: none; padding-inline-end: 34px; cursor: pointer;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%23667085' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: left 11px center; background-size: 16px; }
.input-lg { height: 44px; font-size: 15px; }
.with-icon { position: relative; display: block; }
.with-icon > .icon { position: absolute; inset-inline-start: 11px; top: 50%; transform: translateY(-50%);
  color: var(--fg-subtle); pointer-events: none; }
.with-icon > .input { padding-inline-start: 34px; }
.with-icon > .kbd { position: absolute; inset-inline-end: 10px; top: 50%; transform: translateY(-50%); pointer-events: none; }
.switch { display: inline-flex; align-items: center; gap: 9px; cursor: pointer; user-select: none; font-size: 13.5px;
  color: var(--fg-2); white-space: nowrap; }
.switch input { appearance: none; -webkit-appearance: none; flex: none; position: relative; margin: 0; width: 34px; height: 20px;
  border-radius: 99px; background: var(--border-strong); cursor: pointer; transition: background var(--d2) var(--ease); }
.switch input::after { content: ""; position: absolute; top: 2px; inset-inline-start: 2px; width: 16px; height: 16px;
  border-radius: 50%; background: #fff; box-shadow: 0 1px 2px rgba(16, 24, 40, .25);
  transition: transform var(--d2) var(--spring); }
.switch input:checked { background: var(--brand); }
.switch input:checked::after { transform: translateX(-14px); }
.switch input:focus-visible { box-shadow: 0 0 0 4px var(--ring-soft); outline: none; }

/* ---- surfaces */
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-lg); box-shadow: var(--sh-sm); }
.card-pad { padding: 20px; }
.card-head { display: flex; align-items: center; gap: 10px; padding: 14px 18px; border-bottom: 1px solid var(--border); }
.card-title { margin: 0; font-size: 15px; font-weight: 600; }
.card-sub { margin: 2px 0 0; font-size: 13px; color: var(--fg-muted); }
.alert { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border-radius: var(--r-md);
  border: 1px solid var(--border); background: var(--surface-2); font-size: 14px; }
.alert > .icon { margin-top: 2px; }
.alert-danger { background: var(--danger-soft); border-color: var(--danger-border); color: var(--danger-ink); }
.alert-warn { background: var(--warn-soft); border-color: var(--warn-border); color: var(--warn-ink); }
.alert-info { background: var(--brand-soft); border-color: transparent; color: var(--brand-ink); }
.alert ul { margin: 4px 0 0; padding-inline-start: 18px; }

/* ---- badges */
.badge { display: inline-flex; align-items: center; gap: 6px; height: 22px; padding: 0 9px; border-radius: 99px;
  font-size: 12px; font-weight: 500; line-height: 1; white-space: nowrap; background: var(--surface-active); color: var(--fg-2); }
.badge-dot::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.badge-ok { background: var(--success-soft); color: var(--success-ink); }
.badge-warn { background: var(--warn-soft); color: var(--warn-ink); }
.badge-err { background: var(--danger-soft); color: var(--danger-ink); }
.badge-brand { background: var(--brand-soft); color: var(--brand-ink); }
.badge-outline { background: transparent; box-shadow: inset 0 0 0 1px var(--border); color: var(--fg-muted); }

/* ---- tables */
.table-wrap { overflow-x: auto; background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-lg); box-shadow: var(--sh-sm); }
table.table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 14px; }
.table th { text-align: start; font-size: 12px; font-weight: 500; color: var(--fg-muted); padding: 10px 16px;
  background: var(--surface-2); border-bottom: 1px solid var(--border); white-space: nowrap; }
.table td { padding: 12px 16px; border-bottom: 1px solid var(--border-soft); vertical-align: middle; }
.table tbody tr:last-child td { border-bottom: 0; }
.table tbody tr { transition: background var(--d1); }
.table tbody tr:hover { background: var(--surface-hover); }
.table tr[data-href] { cursor: pointer; }
.table .row-go { opacity: 0; transform: translateX(4px); transition: opacity var(--d2), transform var(--d2) var(--ease); color: var(--fg-subtle); }
.table tr:hover .row-go { opacity: 1; transform: none; }
.cell-strong { font-weight: 600; color: var(--fg); }

/* ---- app shell */
.topbar { position: sticky; top: 0; z-index: 40; border-bottom: 1px solid var(--border);
  background: color-mix(in srgb, var(--bg) 80%, transparent); backdrop-filter: saturate(180%) blur(14px);
  -webkit-backdrop-filter: saturate(180%) blur(14px); }
.topbar-in { max-width: 1180px; margin: 0 auto; padding: 0 20px; height: 60px; display: flex; align-items: center; gap: 22px; }
.brand { display: inline-flex; align-items: center; gap: 10px; color: var(--fg); font-weight: 700; font-size: 15px;
  text-decoration: none !important; white-space: nowrap; }
.brand-mark { width: 30px; height: 30px; border-radius: 9px; background: var(--brand-grad); display: grid; place-items: center;
  box-shadow: 0 4px 10px -3px rgba(47, 125, 225, .55), inset 0 1px 0 rgba(255, 255, 255, .25); flex: none; }
.brand-mark svg { width: 15px; height: 15px; }
.brand small { display: block; font-weight: 500; font-size: 11.5px; color: var(--fg-muted); margin-top: -2px; }
.tabs { position: relative; display: flex; gap: 2px; overflow-x: auto; scrollbar-width: none; }
.tabs::-webkit-scrollbar { display: none; }
.tab { position: relative; z-index: 1; display: inline-flex; align-items: center; gap: 7px; height: 34px; padding: 0 12px;
  border-radius: var(--r-md); color: var(--fg-muted); font-size: 14px; font-weight: 500; white-space: nowrap;
  text-decoration: none !important; transition: color var(--d2); }
.tab:hover, .tab.is-active { color: var(--fg); }
.tab-glider { position: absolute; z-index: 0; top: 0; left: 0; height: 34px; border-radius: var(--r-md); background: var(--surface-active);
  transition: transform var(--d3) var(--ease), width var(--d3) var(--ease), opacity var(--d2); pointer-events: none; }
.topbar .spacer { flex: 1; }
.page { max-width: 1180px; margin: 0 auto; padding: 30px 20px 96px; }
.page-narrow { max-width: 880px; }
.page-head { display: flex; align-items: flex-end; flex-wrap: wrap; gap: 14px 20px; margin-bottom: 24px; }
.page-title { margin: 0; font-size: 26px; font-weight: 700; letter-spacing: -.015em; }
.page-sub { margin: 5px 0 0; color: var(--fg-muted); font-size: 14px; }
.page-actions { margin-inline-start: auto; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.back { display: inline-flex; align-items: center; gap: 4px; color: var(--fg-muted); font-size: 13.5px; font-weight: 500;
  margin-bottom: 14px; text-decoration: none !important; transition: color var(--d1), gap var(--d2) var(--ease); }
.back:hover { color: var(--fg); gap: 7px; }
.section-title { display: flex; align-items: center; gap: 8px; margin: 30px 0 12px; font-size: 13px; font-weight: 600;
  color: var(--fg-muted); text-transform: none; letter-spacing: .01em; }
.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 16px; }
.toolbar .grow { flex: 1 1 220px; }

/* ---- stats */
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 22px; }
.stat { padding: 16px 18px; display: flex; flex-direction: column; gap: 10px; }
.stat-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--fg-muted);
  font-size: 13px; font-weight: 500; }
.stat-icon { width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center; background: var(--surface-active); color: var(--fg-2); }
.stat-value { font-size: 30px; font-weight: 700; letter-spacing: -.025em; line-height: 1; font-variant-numeric: tabular-nums; }
.stat-foot { font-size: 12.5px; color: var(--fg-muted); }
.stat.tone-ok .stat-icon { background: var(--success-soft); color: var(--success-ink); }
.stat.tone-err .stat-icon { background: var(--danger-soft); color: var(--danger-ink); }
.stat.tone-err.is-hot { border-color: var(--danger-border); }
.stat.tone-warn .stat-icon { background: var(--warn-soft); color: var(--warn-ink); }
.stat.tone-brand .stat-icon { background: var(--brand-soft); color: var(--brand-ink); }
.meter { height: 6px; border-radius: 99px; background: var(--surface-active); overflow: hidden; }
.meter > i { display: block; height: 100%; width: var(--v, 0%); border-radius: inherit; background: var(--brand);
  transform-origin: right; animation: grow .8s var(--ease) both .15s; }

/* ---- segmented control */
.segmented { position: relative; display: inline-flex; gap: 2px; padding: 3px; border-radius: var(--r-md);
  background: var(--surface-active); }
.segmented > a, .segmented > button { position: relative; display: inline-flex; align-items: center; gap: 6px; height: 30px;
  padding: 0 12px; border: 0; border-radius: 8px; background: none; color: var(--fg-muted); font-size: 13px; font-weight: 500;
  cursor: pointer; text-decoration: none !important; white-space: nowrap; transition: color var(--d2), background var(--d2), box-shadow var(--d2); }
.segmented > a:hover, .segmented > button:hover { color: var(--fg); }
.segmented > .is-active { background: var(--surface); color: var(--fg); box-shadow: var(--sh-sm); }
.segmented .count { font-size: 11.5px; color: var(--fg-subtle); font-variant-numeric: tabular-nums; }

/* ---- menus */
details.menu { position: relative; }
details.menu > summary { list-style: none; }
details.menu > summary::-webkit-details-marker { display: none; }
.menu-list { position: absolute; z-index: 30; top: calc(100% + 6px); inset-inline-end: 0; min-width: 200px; padding: 5px;
  background: var(--surface); border: 1px solid var(--border); border-radius: 12px; box-shadow: var(--sh-lg);
  transform-origin: top left; animation: menu-in var(--d2) var(--ease); }
.menu-item { display: flex; width: 100%; align-items: center; gap: 9px; padding: 8px 10px; border: 0; border-radius: 8px;
  background: none; color: var(--fg-2); font-size: 13.5px; text-align: start; cursor: pointer; text-decoration: none !important; }
.menu-item:hover { background: var(--surface-hover); color: var(--fg); }
.menu-item.danger { color: var(--danger-ink); }
.menu-item.danger:hover { background: var(--danger-soft); }
.menu-sep { height: 1px; background: var(--border); margin: 4px 2px; }

/* ---- empty & skeleton */
.empty { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 6px; padding: 56px 20px; }
.empty-icon { width: 52px; height: 52px; border-radius: 16px; display: grid; place-items: center; margin-bottom: 10px;
  background: var(--surface-2); border: 1px solid var(--border); color: var(--fg-subtle); box-shadow: var(--sh-xs); }
.empty-title { font-size: 15.5px; font-weight: 600; color: var(--fg); }
.empty-text { font-size: 14px; color: var(--fg-muted); max-width: 380px; }
.skeleton { border-radius: 6px; background: linear-gradient(90deg, var(--surface-active) 25%, var(--surface-hover) 50%, var(--surface-active) 75%);
  background-size: 200% 100%; animation: shimmer 1.3s infinite linear; }

/* ---- toasts */
.toasts { position: fixed; z-index: 100; bottom: 22px; inset-inline: 0; display: flex; flex-direction: column; align-items: center;
  gap: 8px; pointer-events: none; padding: 0 16px; }
.toast { pointer-events: auto; display: flex; align-items: center; gap: 10px; min-height: 44px; max-width: 520px;
  padding: 10px 16px; border-radius: 12px; background: var(--toast-bg); color: var(--toast-fg); font-size: 14px; font-weight: 500;
  box-shadow: var(--sh-xl); animation: toast-in var(--d3) var(--spring) both; }
.toast.is-leaving { animation: toast-out var(--d2) var(--ease) both; }
.toast .icon { color: #47cd89; }
.toast.toast-error .icon { color: #f97066; }
.toast button { margin-inline-start: 6px; padding: 5px 9px; border: 0; border-radius: 7px; background: rgba(127, 127, 127, .18);
  color: inherit; font-weight: 600; font-size: 13px; cursor: pointer; }
.toast button:hover { background: rgba(127, 127, 127, .3); }

/* ---- modal */
dialog.modal { width: min(460px, calc(100vw - 32px)); padding: 0; border: 1px solid var(--border); border-radius: var(--r-xl);
  background: var(--surface); color: var(--fg); box-shadow: var(--sh-xl); }
dialog.modal[open] { animation: pop-in var(--d3) var(--spring); }
dialog.modal::backdrop { background: rgba(10, 13, 18, .45); backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px); }
dialog.modal[open]::backdrop { animation: fade-in var(--d2) var(--ease); }
.modal-body { padding: 24px 24px 6px; }
.modal-icon { width: 44px; height: 44px; border-radius: 50%; display: grid; place-items: center; margin-bottom: 14px;
  background: var(--brand-soft); color: var(--brand-ink); box-shadow: 0 0 0 8px color-mix(in srgb, var(--brand-soft) 50%, transparent); }
.modal-icon.danger { background: var(--danger-soft); color: var(--danger-ink); box-shadow: 0 0 0 8px color-mix(in srgb, var(--danger-soft) 50%, transparent); }
.modal-title { margin: 0 0 6px; font-size: 17px; font-weight: 600; }
.modal-text { margin: 0; color: var(--fg-muted); font-size: 14px; }
.modal-fields { display: grid; gap: 14px; margin-top: 18px; }
.modal-foot { display: flex; gap: 8px; padding: 20px 24px 22px; }
.modal-foot .btn { flex: 1; }

/* ---- tooltips */
[data-tip] { position: relative; }
[data-tip]:hover::after, [data-tip]:focus-visible::after { content: attr(data-tip); position: absolute; z-index: 50;
  bottom: calc(100% + 7px); left: 50%; transform: translateX(-50%); padding: 5px 8px; border-radius: 7px;
  background: #101828; color: #fff; font-size: 12px; font-weight: 500; line-height: 1.3; white-space: nowrap;
  pointer-events: none; animation: tip-in var(--d2) var(--ease) .35s both; }

/* ---- motion */
.reveal { animation: rise .42s var(--ease) both; animation-delay: calc(var(--i, 0) * 45ms); }
.flash { animation: flash 1s var(--ease); }
.shake { animation: shake .42s var(--ease); }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes pop { 0% { transform: scale(.94); } 60% { transform: scale(1.04); } 100% { transform: none; } }
@keyframes rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@keyframes fade-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes pop-in { from { opacity: 0; transform: translateY(8px) scale(.96); } to { opacity: 1; transform: none; } }
@keyframes menu-in { from { opacity: 0; transform: translateY(-4px) scale(.97); } to { opacity: 1; transform: none; } }
@keyframes tip-in { from { opacity: 0; transform: translate(-50%, 3px); } to { opacity: 1; transform: translate(-50%, 0); } }
@keyframes toast-in { from { opacity: 0; transform: translateY(14px) scale(.95); } to { opacity: 1; transform: none; } }
@keyframes toast-out { to { opacity: 0; transform: translateY(8px) scale(.97); } }
@keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
@keyframes grow { from { transform: scaleX(0); } to { transform: none; } }
@keyframes flash { 0% { box-shadow: 0 0 0 0 var(--ring-soft); background-color: var(--brand-soft); } 100% { box-shadow: 0 0 0 12px transparent; } }
@keyframes shake { 20% { transform: translateX(-6px); } 40% { transform: translateX(5px); } 60% { transform: translateX(-3px); } 80% { transform: translateX(2px); } }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important;
    animation-delay: 0s !important; transition-duration: .01ms !important; scroll-behavior: auto !important; }
}
@media (max-width: 720px) {
  .topbar-in { gap: 12px; padding: 0 14px; }
  .brand small { display: none; }
  .brand-name { display: none; }
  .page { padding: 22px 16px 88px; }
  .page-title { font-size: 22px; }
  .page-actions { margin-inline-start: 0; width: 100%; }
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .stat { padding: 14px; gap: 8px; }
  .stat-value { font-size: 24px; }
  .tab { padding: 0 10px; }
  .tab .icon { display: none; }
  .table th, .table td { padding: 10px 12px; }
}
"""

# ------------------------------------------------------------------ JS

JS = r"""
(function () {
  var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var UI = window.UI = {};
  var ICONS = window.ICONS || {};

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  UI.el = el;
  UI.icon = function (name) { return ICONS[name] || ''; };

  // Toasts, with an optional action (undo).
  var host;
  UI.toast = function (text, opts) {
    opts = opts || {};
    if (!host) { host = el('div', 'toasts'); host.setAttribute('role', 'status'); host.setAttribute('aria-live', 'polite'); document.body.appendChild(host); }
    var t = el('div', 'toast' + (opts.kind === 'error' ? ' toast-error' : ''));
    t.innerHTML = UI.icon(opts.icon || (opts.kind === 'error' ? 'alert' : 'check-circle'));
    t.appendChild(el('span', '', text));
    var timer;
    function close() { clearTimeout(timer); t.classList.add('is-leaving'); setTimeout(function () { t.remove(); }, 220); }
    if (opts.action) { var b = el('button', '', opts.action.label); b.type = 'button';
      b.onclick = function () { opts.action.run(); close(); }; t.appendChild(b); }
    host.appendChild(t);
    timer = setTimeout(close, opts.duration || (opts.action ? 6000 : 3000));
    return close;
  };

  // Busy and success states for buttons.
  UI.busy = function (btn, on) { if (!btn) return; btn.classList.toggle('is-loading', !!on); btn.disabled = !!on; };
  UI.done = function (btn, label) {
    if (!btn) return; var old = btn.innerHTML;
    btn.innerHTML = UI.icon('check') + '<span></span>'; btn.lastChild.textContent = label || '';
    btn.classList.add('is-done');
    setTimeout(function () { btn.innerHTML = old; btn.classList.remove('is-done'); }, 1500);
  };
  UI.copy = function (text, btn) {
    var p = navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject();
    p.then(function () { btn ? UI.done(btn, 'הועתק') : UI.toast('הועתק'); },
           function () { UI.toast('לא הצלחתי להעתיק', {kind: 'error'}); });
  };

  // A modal, instead of the browser's confirm(): resolves true/false.
  UI.confirm = function (o) {
    return new Promise(function (resolve) {
      var d = el('dialog', 'modal');
      d.innerHTML = '<form method="dialog"><div class="modal-body"><div class="modal-icon' + (o.danger ? ' danger' : '') + '">' +
        UI.icon(o.icon || (o.danger ? 'trash' : 'info')) + '</div><h2 class="modal-title"></h2><p class="modal-text"></p></div>' +
        '<div class="modal-foot"><button value="ok" class="btn ' + (o.danger ? 'btn-danger-solid' : 'btn-primary') + '"></button>' +
        '<button value="cancel" class="btn">ביטול</button></div></form>';
      d.querySelector('.modal-title').textContent = o.title || '';
      d.querySelector('.modal-text').textContent = o.text || '';
      d.querySelector('[value=ok]').textContent = o.ok || 'אישור';
      document.body.appendChild(d);
      d.addEventListener('close', function () { resolve(d.returnValue === 'ok'); setTimeout(function () { d.remove(); }, 50); });
      d.addEventListener('click', function (e) { if (e.target === d) d.close('cancel'); });
      d.showModal(); d.querySelector('[value=ok]').focus();
    });
  };
  UI.openModal = function (id) { var d = document.getElementById(id); if (d) { d.showModal();
    var f = d.querySelector('[autofocus],input,select'); if (f) f.focus(); } };
  document.addEventListener('click', function (e) {
    var open = e.target.closest('[data-modal]'); if (open) { e.preventDefault(); UI.openModal(open.getAttribute('data-modal')); }
    var shut = e.target.closest('[data-close]'); if (shut) { var d = shut.closest('dialog'); if (d) d.close(); }
    var dlg = e.target.tagName === 'DIALOG' ? e.target : null; if (dlg) dlg.close();
  });

  // JSON API with the header a cross-site form cannot send.
  UI.api = function (path, body) {
    return fetch(window.BASE + '/admin/api' + path, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-Requested-With': 'dashboard'},
      body: JSON.stringify(body || {})}).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) { d._status = r.status; return d; });
      }, function () { return {_status: 0, errors: ['אין חיבור לשרת']}; });
  };

  // Numbers that count up once, on first paint.
  function countUp(node) {
    var to = parseFloat(node.getAttribute('data-count')); if (isNaN(to)) return;
    var suffix = node.getAttribute('data-suffix') || '';
    if (reduce || to === 0) { node.textContent = to.toLocaleString('he-IL') + suffix; return; }
    var start = null, dur = 700;
    function step(ts) { if (!start) start = ts; var p = Math.min(1, (ts - start) / dur), e = 1 - Math.pow(1 - p, 3);
      node.textContent = Math.round(to * e).toLocaleString('he-IL') + suffix; if (p < 1) requestAnimationFrame(step); }
    requestAnimationFrame(step);
  }

  // "לפני 5 דקות", with the exact time on hover.
  var rtf = window.Intl && Intl.RelativeTimeFormat ? new Intl.RelativeTimeFormat('he', {numeric: 'auto'}) : null;
  UI.ago = function (iso) {
    var t = new Date(iso).getTime(); if (!t || !rtf) return '';
    var s = Math.round((t - Date.now()) / 1000), a = Math.abs(s);
    if (a < 45) return 'עכשיו';
    if (a < 3600) return rtf.format(Math.round(s / 60), 'minute');
    if (a < 86400) return rtf.format(Math.round(s / 3600), 'hour');
    if (a < 86400 * 30) return rtf.format(Math.round(s / 86400), 'day');
    return new Date(iso).toLocaleDateString('he-IL', {day: 'numeric', month: 'short', year: 'numeric'});
  };
  function times(root) {
    (root || document).querySelectorAll('[data-time]').forEach(function (n) {
      var iso = n.getAttribute('data-time'); var txt = UI.ago(iso); if (!txt) return;
      if (!n.title) n.title = n.textContent.trim(); n.textContent = txt;
    });
  }
  UI.times = times;

  // The tab indicator glides to the hovered tab and back to the active one.
  function glider() {
    var tabs = document.querySelector('.tabs'); if (!tabs) return;
    var g = el('span', 'tab-glider'); tabs.prepend(g);
    var active = tabs.querySelector('.tab.is-active');
    function to(t, instant) { if (!t) { g.style.opacity = 0; return; } if (instant) g.style.transition = 'none';
      g.style.opacity = 1; g.style.width = t.offsetWidth + 'px';
      g.style.transform = 'translateX(' + (t.offsetLeft) + 'px)';
      if (instant) { g.offsetWidth; g.style.transition = ''; } }
    to(active, true);
    tabs.addEventListener('mouseover', function (e) { var t = e.target.closest('.tab'); if (t) to(t); });
    tabs.addEventListener('mouseleave', function () { to(active); });
    window.addEventListener('resize', function () { to(active, true); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-count]').forEach(countUp);
    times(); glider();
    // Whole rows are links; inner links and buttons keep their own click.
    document.addEventListener('click', function (e) {
      var row = e.target.closest('tr[data-href]');
      if (row && !e.target.closest('a,button,input,select,summary,details')) {
        if (e.metaKey || e.ctrlKey) window.open(row.getAttribute('data-href')); else location.href = row.getAttribute('data-href');
      }
      document.querySelectorAll('details.menu[open]').forEach(function (m) { if (!m.contains(e.target)) m.open = false; });
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') document.querySelectorAll('details.menu[open]').forEach(function (m) { m.open = false; });
      var s = document.querySelector('[data-search]');
      if (e.key === '/' && s && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); s.focus(); }
    });
    document.querySelectorAll('form[data-busy]').forEach(function (f) {
      f.addEventListener('submit', function () { UI.busy(f.querySelector('[type=submit]'), true); });
    });
    document.querySelectorAll('[data-autosubmit]').forEach(function (n) {
      n.addEventListener('change', function () { n.form.requestSubmit ? n.form.requestSubmit() : n.form.submit(); });
    });
  });
})();
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;600;700;800&display=swap">')


def icons_json(names: tuple[str, ...] = ()) -> str:
    """The icons the page's script draws, as a JSON object (all by default)."""
    wanted = names or tuple(_ICON_PATHS)
    data = {n: icon(n) for n in wanted}
    return json.dumps(data).replace("</", "<\\/")


def document(title: str, body: str, *, kind: str = "app", css: str = "", script: str = "",
             base: str = "", head: str = "") -> str:
    """A whole page: fonts, tokens, components, the shared script, then ``body``."""
    boot = f"window.BASE={json.dumps(base)};window.ICONS={icons_json()};"
    return (
        f'<!doctype html><html lang="he" dir="rtl" class="{kind}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="robots" content="noindex,nofollow">'
        f'<meta name="theme-color" content="{"#f7f8fa" if kind == "app" else "#00a8f0"}">'
        f'<title>{esc(title)}</title><link rel="icon" href="{FAVICON}">{FONTS}'
        f"<style>{CSS}{css}</style>{head}</head><body>{body}"
        f"<script>{boot}{JS}</script>"
        + (f"<script>{script}</script>" if script else "")
        + "</body></html>"
    )


# ------------------------------------------------------------ components

BRAND_MARK = ('<span class="brand-mark"><svg viewBox="0 0 24 24" aria-hidden="true">'
              '<path d="M6 3h6.5a9 9 0 0 1 0 18H6Z" fill="#fff"/></svg></span>')

NAV = (("dashboard", "/dashboard", "פעילות", "activity"),
       ("questionnaires", "/admin/questionnaires", "שאלונים", "clipboard"),
       ("responses", "/admin/responses", "תשובות", "inbox"))


def topbar(base: str, active: str) -> str:
    tabs = "".join(
        f'<a class="tab{" is-active" if key == active else ""}" href="{base}{path}"'
        f'{" aria-current=page" if key == active else ""}>{icon(ic, 15)}<span>{label}</span></a>'
        for key, path, label, ic in NAV)
    return (f'<header class="topbar"><div class="topbar-in">'
            f'<a class="brand" href="{base}/dashboard">{BRAND_MARK}'
            f'<span class="brand-name">דרור ברק<small>אוטומציות</small></span></a>'
            f'<nav class="tabs" aria-label="ניווט">{tabs}</nav><span class="spacer"></span>'
            f'<a class="btn btn-ghost btn-sm" href="{base}/logout" data-tip="יציאה">{icon("logout", 15)}'
            f'<span class="sr-only">יציאה</span></a></div></header>')


def app_page(base: str, active: str, title: str, body: str, *, script: str = "",
             css: str = "", narrow: bool = False) -> str:
    """Dror's screens: the top bar, then the page."""
    return document(title, topbar(base, active)
                    + f'<main class="page{" page-narrow" if narrow else ""}">{body}</main>',
                    kind="app", css=css, script=script, base=base)


def page_head(title: str, sub: str = "", actions: str = "", *, extra: str = "") -> str:
    sub_html = f'<p class="page-sub">{sub}</p>' if sub else ""
    return (f'<div class="page-head reveal"><div><h1 class="page-title">{esc(title)}{extra}</h1>{sub_html}</div>'
            f'<div class="page-actions">{actions}</div></div>')


def stat(label: str, value: int, *, ico: str, tone: str = "", foot: str = "", suffix: str = "",
         i: int = 0, hot: bool = False) -> str:
    cls = f"card stat reveal{' tone-' + tone if tone else ''}{' is-hot' if hot else ''}"
    foot_html = f'<div class="stat-foot">{foot}</div>' if foot else ""
    return (f'<div class="{cls}" style="--i:{i}"><div class="stat-top"><span>{esc(label)}</span>'
            f'<span class="stat-icon">{icon(ico, 16)}</span></div>'
            f'<div class="stat-value" data-count="{value}" data-suffix="{esc(suffix)}">{value}{esc(suffix)}</div>'
            f"{foot_html}</div>")


def empty(title: str, text: str = "", *, ico: str = "inbox", action: str = "") -> str:
    return (f'<div class="empty reveal"><div class="empty-icon">{icon(ico, 22)}</div>'
            f'<div class="empty-title">{esc(title)}</div>'
            + (f'<div class="empty-text">{text}</div>' if text else "")
            + (f'<div style="margin-top:14px">{action}</div>' if action else "") + "</div>")


def local_time(iso: Any) -> str:
    """ISO UTC → ``DD.MM.YYYY HH:MM`` in Israel time (fixed +3 if no tz database)."""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        dt = dt.astimezone(ZoneInfo("Asia/Jerusalem"))
    except Exception:  # noqa: BLE001 - no tz database on this host
        dt = dt.astimezone(timezone(timedelta(hours=3)))
    return dt.strftime("%d.%m.%Y %H:%M")


def when(iso: Any, fallback: str = "") -> str:
    """An absolute Israel-time stamp that the page turns into "לפני 3 שעות"."""
    text = local_time(iso) if iso else ""
    if not text:
        return esc(fallback)
    return f'<time class="num" data-time="{esc(iso)}" datetime="{esc(iso)}">{esc(text)}</time>'


def initials(name: str) -> str:
    words = [w for w in str(name or "").replace("-", " ").split() if w]
    return "".join(w[0] for w in words[:2]) or "?"
