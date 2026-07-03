"""Assemble the UI scenario gallery into a polished, navigable HTML report.

Rich's ``console.save_html`` dumps every scenario into one flat blob with the muddy 16-color
ANSI palette. This module instead renders each scenario panel to its *own* monospace fragment
and wraps the lot in a styled page whose look is borrowed from the project's architecture-guide
docs: a dark navy shell, a sticky left sidebar of surfaces, collapsible per-surface sections,
a live text filter, and a light/dark theme toggle.

Each panel is rendered twice — once through a light terminal palette (Catppuccin Latte) and
once through a dark one tuned to the doc theme — and the inactive copy is hidden via CSS. That
keeps every panel's colors terminal-accurate *and* readable whichever theme the reader picks,
rather than forcing one palette to work on both backgrounds. The panels stay inside a ``<pre>``
so they render exactly like the terminal print, dodging any HTML reflow of the box art.
"""

import html as _html

from rich.cells import cell_len
from rich.terminal_theme import TerminalTheme

from ui.catalog._base import BuildResult, Scenario, Surface
from ui.harness.rendering import make_recording_console, paint

# The eight ANSI slots the panels actually use, in Rich order:
# black, red, green, yellow, blue, magenta, cyan, white.
#
# Light: Catppuccin Latte on a near-white card. Dark: tuned to the navy doc theme
# (bg #1a212b) so each panel blends into its bordered block like the reference guide.
LIGHT_THEME = TerminalTheme(
    (239, 241, 245),   # background — base
    (76, 79, 105),     # foreground — text
    [
        (108, 111, 133),  # black   — overlay0
        (210, 15, 57),    # red
        (64, 160, 43),    # green
        (223, 142, 29),   # yellow
        (30, 102, 245),   # blue
        (136, 57, 239),   # magenta — mauve
        (23, 146, 153),   # cyan    — teal
        (188, 192, 204),  # white   — surface2
    ],
)
DARK_THEME = TerminalTheme(
    (26, 33, 43),      # background — --panel
    (215, 222, 231),   # foreground — --text
    [
        (74, 86, 102),    # black   — muted slate
        (255, 107, 107),  # red     — --red
        (111, 207, 115),  # green   — --green
        (255, 207, 92),   # yellow  — --accent-2
        (130, 170, 255),  # blue
        (199, 146, 234),  # magenta
        (92, 200, 255),   # cyan    — --accent
        (215, 222, 231),  # white   — --text
    ],
)

# Border-color word -> status-dot color. From the doc palette; a faint ring (in CSS) keeps
# them visible on both the dark and light shells.
_DOT = {
    "green": "#6fcf73",
    "yellow": "#ffcf5c",
    "red": "#ff6b6b",
    "blue": "#5cc8ff",
}

_STYLE = """\
:root { color-scheme: light dark; --mono: "DejaVu Sans Mono", "SFMono-Regular", Consolas, Menlo, monospace; }
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  transition: background .15s, color .15s;
}
body.theme-dark {
  --bg:#0f1419; --panel:#1a212b; --panel-2:#222b38; --border:#2e3a4a;
  --text:#d7dee7; --muted:#8c9aab; --accent:#5cc8ff; --accent-2:#ffcf5c;
}
body.theme-light {
  --bg:#eef1f5; --panel:#ffffff; --panel-2:#e7ecf2; --border:#d5dce4;
  --text:#20272f; --muted:#5d6b7a; --accent:#0d76a8; --accent-2:#9a6b00;
}
/* Show only the panel copy that matches the active theme. */
body.theme-light .panel--dark, body.theme-dark .panel--light { display: none; }

.layout { display: flex; max-width: 1240px; margin: 0 auto; }

/* ---- sidebar ---------------------------------------------------------- */
.sidebar {
  position: sticky; top: 0; align-self: flex-start;
  width: 264px; flex: 0 0 264px; height: 100vh; overflow-y: auto;
  padding: 26px 16px 48px; border-right: 1px solid var(--border);
}
.brand-title { font-size: 17px; font-weight: 700; letter-spacing: .2px; }
.brand-sub { font-size: 12.5px; color: var(--muted); margin-top: 2px; }
.controls { display: flex; gap: 8px; margin: 18px 0 6px; }
#filter {
  flex: 1 1 auto; min-width: 0; padding: 8px 11px; font-size: 13px;
  color: var(--text); background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
}
#filter:focus { outline: none; border-color: var(--accent); }
#theme-toggle {
  flex: 0 0 auto; padding: 8px 10px; font-size: 12.5px; cursor: pointer; white-space: nowrap;
  color: var(--text); background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
}
#theme-toggle:hover { border-color: var(--accent); color: var(--accent); }
.nav-title { font-size: 12px; text-transform: uppercase; letter-spacing: .09em; color: var(--muted); margin: 22px 6px 6px; }
.nav-surface {
  display: flex; justify-content: space-between; align-items: center;
  margin: 14px 4px 3px; padding: 5px 8px; border-radius: 7px;
  font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
  color: var(--accent); text-decoration: none;
}
.nav-surface:hover { background: var(--panel-2); }
.nav-surface .nav-count {
  font-weight: 600; color: var(--muted); background: var(--panel);
  border: 1px solid var(--border); border-radius: 20px; padding: 0 8px; font-size: 10.5px; letter-spacing: 0;
}
.nav-item {
  display: flex; align-items: center; gap: 8px;
  padding: 4px 10px; margin: 1px 4px; border-radius: 6px;
  font-family: var(--mono); font-size: 12px; color: var(--text); text-decoration: none;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; opacity: .8;
}
.nav-item:hover { background: var(--panel-2); color: var(--accent); opacity: 1; }
.nav-item .dot { flex: 0 0 auto; }

/* ---- main ------------------------------------------------------------- */
.main { flex: 1 1 auto; min-width: 0; padding: 34px 44px 120px; }
h1 { font-size: 29px; margin: 0 0 6px; line-height: 1.2; }
h1 + .sub { color: var(--muted); font-size: 15.5px; margin-bottom: 22px; }

.surface { margin-top: 12px; }
.surface-head {
  display: flex; align-items: center; gap: 11px; width: 100%;
  margin: 40px 0 4px; padding: 0 0 9px; border: none; border-bottom: 1px solid var(--border);
  background: none; color: var(--text); font: inherit; font-size: 22px; font-weight: 600;
  text-align: left; cursor: pointer;
}
.surface-head:hover { color: var(--accent); }
.surface-head .chev {
  color: var(--muted); font-size: 13px; transition: transform .15s; width: 14px; flex: 0 0 auto;
}
.surface.collapsed .surface-head .chev { transform: rotate(-90deg); }
.surface-head .surface-name { flex: 1 1 auto; }
.surface-count {
  font-size: 12px; font-weight: 600; color: var(--muted);
  background: var(--panel); border: 1px solid var(--border); border-radius: 20px; padding: 1px 10px;
}
.surface.collapsed .surface-body { display: none; }

.scn { margin: 22px 0 26px; scroll-margin-top: 16px; }
.scn-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 9px; }
.scn-name { font-family: var(--mono); font-weight: 700; font-size: 14px; color: var(--accent); }
.scn-desc { color: var(--muted); font-size: 14px; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 1px rgba(127,127,127,.28); }
.pill {
  font-family: var(--mono); font-size: 10.5px; color: var(--muted);
  background: var(--panel-2); border: 1px solid var(--border); border-radius: 999px; padding: 1px 9px;
}

.panel {
  margin: 0; padding: 15px 18px; border: 1px solid var(--border); border-radius: 10px;
  font-family: var(--mono); font-size: 13px; line-height: 1.25; white-space: pre; tab-size: 4;
  overflow-x: auto;
}
.panel--light { background: #eff1f5; color: #4c4f69; }
.panel--dark  { background: #1a212b; color: #d7dee7; }
/* Pin terminal double-width glyphs (emoji) to two character cells so the box-drawing
   borders stay on the terminal's fixed column grid instead of drifting per row. */
.panel .w2 { display: inline-block; width: 2ch; text-align: center; }

.empty { color: var(--muted); padding: 40px 4px; font-size: 15px; display: none; }
"""

_SCRIPT = """\
(function () {
  var body = document.body;
  var KEY = "gallery-theme";
  function apply(theme) {
    body.classList.remove("theme-light", "theme-dark");
    body.classList.add("theme-" + theme);
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.textContent = theme === "dark" ? "\\u2600\\ufe0f Light" : "\\u{1F319} Dark";
  }
  var saved = localStorage.getItem(KEY);
  if (!saved) saved = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  apply(saved);
  document.getElementById("theme-toggle").addEventListener("click", function () {
    var next = body.classList.contains("theme-dark") ? "light" : "dark";
    localStorage.setItem(KEY, next);
    apply(next);
  });

  // Collapsible surface sections.
  document.querySelectorAll(".surface-head").forEach(function (h) {
    h.addEventListener("click", function () {
      var s = h.closest(".surface");
      s.classList.toggle("collapsed");
      h.setAttribute("aria-expanded", s.classList.contains("collapsed") ? "false" : "true");
    });
  });
  // Following any sidebar link auto-expands the section it lands in.
  document.querySelectorAll('.sidebar a[href^="#"]').forEach(function (a) {
    a.addEventListener("click", function () {
      var el = document.querySelector(a.getAttribute("href"));
      var s = el && el.closest(".surface");
      if (s) s.classList.remove("collapsed");
    });
  });

  var filter = document.getElementById("filter");
  var scns = [].slice.call(document.querySelectorAll(".scn"));
  var navItems = [].slice.call(document.querySelectorAll(".nav-item"));
  var surfaces = [].slice.call(document.querySelectorAll(".surface"));
  var navSurfaces = [].slice.call(document.querySelectorAll(".nav-surface"));
  var empty = document.getElementById("empty");
  function match(q) {
    q = q.trim().toLowerCase();
    var hits = 0;
    scns.forEach(function (c) {
      var on = !q || c.getAttribute("data-text").indexOf(q) !== -1;
      c.style.display = on ? "" : "none";
      if (on) hits++;
    });
    navItems.forEach(function (n) {
      var on = !q || n.getAttribute("data-text").indexOf(q) !== -1;
      n.style.display = on ? "" : "none";
    });
    surfaces.forEach(function (s) {
      var any = [].slice.call(s.querySelectorAll(".scn")).some(function (c) {
        return c.style.display !== "none";
      });
      s.style.display = any ? "" : "none";
      if (q && any) s.classList.remove("collapsed");
    });
    navSurfaces.forEach(function (ns) {
      var s = document.getElementById(ns.getAttribute("href").slice(1));
      ns.style.display = (s && s.style.display !== "none") ? "" : "none";
    });
    empty.style.display = hits ? "none" : "block";
  }
  filter.addEventListener("input", function () { match(filter.value); });
})();
"""


def _pin_wide_glyphs(frag: str) -> str:
    """Force every terminal-double-width glyph to occupy exactly ``2ch`` in the browser.

    Rich pads each panel line to a fixed cell count treating emoji (✅ 🎉 …) as two cells.
    Browsers render those glyphs at some fractional advance instead, so any line carrying one
    stops short of column 75 and the right border zig-zags. Wrapping each wide glyph in a
    fixed ``2ch`` inline-block restores the exact terminal column grid, so borders line up.

    Wide glyphs never appear inside the emitted ``<span style=…>`` tags (those are ASCII), so
    a plain per-character replace over the fragment is safe.
    """
    for ch in {c for c in set(frag) if cell_len(c) == 2}:
        frag = frag.replace(ch, f'<span class="w2">{ch}</span>')
    return frag


def _fragment(result: BuildResult, theme: TerminalTheme) -> str:
    """One scenario panel as inline-styled HTML spans (no wrapper, padding stripped)."""
    console = make_recording_console()
    paint(console, result)
    frag = console.export_html(theme=theme, inline_styles=True, code_format="{code}")
    lines = [line.rstrip() for line in frag.split("\n")]
    return _pin_wide_glyphs("\n".join(lines).strip("\n"))


def _dot(color: str) -> str:
    return f'<span class="dot" style="background:{_DOT.get(color, "#8c9aab")}"></span>'


def _scenario(sc: Scenario, result: BuildResult) -> str:
    key = sc.snapshot_key
    text = " ".join([key, sc.name, sc.description, " ".join(sc.tags), sc.surface.value]).lower()
    tags = "".join(f'<span class="pill">{_html.escape(t)}</span>' for t in sc.tags)
    return (
        f'<article class="scn" id="{key}" data-text="{_html.escape(text, quote=True)}">'
        f'<div class="scn-head">{_dot(result.border_color)}'
        f'<span class="scn-name">{_html.escape(sc.name)}</span>'
        f'<span class="scn-desc">{_html.escape(sc.description)}</span>{tags}</div>'
        f'<pre class="panel panel--light">{_fragment(result, LIGHT_THEME)}</pre>'
        f'<pre class="panel panel--dark">{_fragment(result, DARK_THEME)}</pre>'
        f'</article>'
    )


def _group(scenarios: list[Scenario]) -> dict[Surface, list[Scenario]]:
    """Scenarios grouped by surface, preserving first-seen order."""
    groups: dict[Surface, list[Scenario]] = {}
    for sc in scenarios:
        groups.setdefault(sc.surface, []).append(sc)
    return groups


def render_report(scenarios: list[Scenario]) -> str:
    """Return the complete self-contained HTML page for ``scenarios``."""
    groups = _group(scenarios)

    nav, main = [], []
    for surface, items in groups.items():
        label = surface.value
        anchor = f"surface-{label}"
        nav.append(
            f'<a class="nav-surface" href="#{anchor}">{_html.escape(label)}'
            f'<span class="nav-count">{len(items)}</span></a>'
        )
        main.append(f'<section class="surface" id="{anchor}">')
        main.append(
            f'<button class="surface-head" type="button" aria-expanded="true">'
            f'<span class="chev">&#9662;</span>'
            f'<span class="surface-name">{_html.escape(label)} surface</span>'
            f'<span class="surface-count">{len(items)}</span></button>'
        )
        main.append('<div class="surface-body">')
        for sc in items:
            result = sc.build()
            data = " ".join([sc.snapshot_key, sc.name, sc.description, " ".join(sc.tags)]).lower()
            nav.append(
                f'<a class="nav-item" href="#{sc.snapshot_key}" data-text="{_html.escape(data, quote=True)}">'
                f'{_dot(result.border_color)}<span>{_html.escape(sc.name)}</span></a>'
            )
            main.append(_scenario(sc, result))
        main.append('</div></section>')

    total = len(scenarios)
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"UTF-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>Scrooge Alert — UI Gallery</title>\n"
        f"<style>\n{_STYLE}</style>\n</head>\n<body class=\"theme-dark\">\n"
        '<div class="layout">\n'
        '<nav class="sidebar">'
        '<div class="brand-title">UI Gallery</div>'
        f'<div class="brand-sub">Scrooge Alert &middot; {total} scenarios</div>'
        '<div class="controls">'
        '<input id="filter" type="search" placeholder="Filter scenarios…" autocomplete="off">'
        '<button id="theme-toggle" type="button"></button></div>'
        '<div class="nav-title">Surfaces</div>'
        f'{"".join(nav)}</nav>\n'
        '<main class="main">'
        '<h1>UI Scenario Gallery</h1>'
        '<div class="sub">Every catalogued terminal panel, rendered with full color for review.</div>'
        f'{"".join(main)}'
        '<div class="empty" id="empty">No scenarios match your filter.</div>'
        '</main>\n</div>\n'
        f"<script>\n{_SCRIPT}</script>\n</body>\n</html>\n"
    )


def write_report(scenarios: list[Scenario], path: str) -> None:
    """Render ``scenarios`` to a styled, navigable HTML report at ``path``."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_report(scenarios))
