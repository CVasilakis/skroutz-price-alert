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
import re

from rich.cells import cell_len
from rich.terminal_theme import TerminalTheme

from ui.catalog._base import BuildResult, Scenario, Surface, SURFACE_INFO
from ui.harness.rendering import make_recording_console, paint

# The eight ANSI slots the panels actually use, in Rich order:
# black, red, green, yellow, blue, magenta, cyan, white.
#
# Light: High-contrast GitHub Light style
LIGHT_THEME = TerminalTheme(
    (255, 255, 255),   # bg
    (36, 41, 46),      # fg
    [
        (36, 41, 46),     # black
        (215, 58, 73),    # red
        (34, 134, 58),    # green
        (176, 136, 0),    # yellow
        (3, 102, 214),    # blue
        (111, 66, 193),   # magenta
        (27, 124, 131),   # cyan
        (106, 115, 125),  # white
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
  overflow-x: hidden;
  -webkit-text-size-adjust: 100%;
}
body.theme-dark {
  --bg:#0f1419; --panel:#1a212b; --panel-2:#222b38; --border:#2e3a4a;
  --text:#e4ebf3; --muted:#a4b1c1; --accent:#5cc8ff; --accent-2:#ffcf5c;
}
body.theme-light {
  --bg:#f6f8fa; --panel:#ffffff; --panel-2:#f0f3f6; --border:#e1e4e8;
  --text:#24292e; --muted:#4a525b; --accent:#0366d6; --accent-2:#b08800;
}
/* Show only the panel copy that matches the active theme. */
body.theme-light .panel--dark, body.theme-dark .panel--light { display: none !important; }

.layout { display: flex; max-width: 1240px; margin: 0 auto; width: 100%; min-width: 0; }

/* ---- sidebar ---------------------------------------------------------- */
.sidebar {
  position: sticky; top: 0; align-self: flex-start;
  width: 264px; flex: 0 0 264px; height: 100vh; overflow-y: auto;
  padding: 26px 16px 48px; border-right: 1px solid var(--border);
  transition: margin-left 0.3s ease;
  z-index: 100;
}
.sidebar-backdrop {
  display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.5); z-index: 90;
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
.tag-filters { display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0 16px; }
.tag-btn {
  background: var(--panel); color: var(--text); border: 1px solid var(--border);
  border-radius: 12px; padding: 3px 10px; font-size: 11px; cursor: pointer; font-family: var(--mono);
  font-weight: 600; box-shadow: 0 1px 2px rgba(0,0,0,0.05); transition: all 0.15s ease;
}
.tag-btn:hover { border-color: var(--accent); color: var(--accent); background: var(--panel-2); }
.tag-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); box-shadow: 0 2px 4px rgba(0,0,0,0.1); }

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
.main-header { display: flex; align-items: flex-start; gap: 16px; margin-bottom: 22px; }
.icon-btn {
  background: var(--panel); border: 1px solid var(--border); color: var(--text);
  border-radius: 8px; padding: 6px 12px; font-size: 20px; cursor: pointer; margin-top: 2px;
  display: flex; align-items: center; justify-content: center;
}
.icon-btn:hover { border-color: var(--accent); color: var(--accent); }
h1 { font-size: 29px; margin: 0 0 6px; line-height: 1.2; }
.sub { color: var(--muted); font-size: 15.5px; }

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

/* Accordion modifications */
.scn { margin: 10px 0; scroll-margin-top: 16px; min-width: 0; max-width: 100%; }
.scn-head {
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 0;
  cursor: pointer; user-select: none; padding: 10px 14px; background: var(--panel);
  border: 1px solid var(--border); border-radius: 8px; transition: border-color .15s, background .15s;
}
.scn-head:hover { border-color: var(--accent); background: var(--panel-2); }
.scn.expanded .scn-head { border-bottom-left-radius: 0; border-bottom-right-radius: 0; }
.scn-name { font-family: var(--mono); font-weight: 700; font-size: 14px; color: var(--accent); }
.scn-desc { color: var(--muted); font-size: 14px; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; box-shadow: 0 0 0 1px rgba(127,127,127,.28); }
.pill {
  font-family: var(--mono); font-size: 10.5px; color: var(--text); font-weight: 600;
  background: var(--panel-2); border: 1px solid var(--border); border-radius: 999px; padding: 1px 9px;
}

.panel {
  margin: 0; padding: 15px 18px; border: 1px solid var(--border); border-radius: 10px;
  font-family: var(--mono); font-size: 13px; line-height: 1.25; white-space: pre; tab-size: 4;
  overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100%; box-sizing: border-box;
}
.panel--light { background: #ffffff; color: #24292e; }
.panel--dark  { background: #1a212b; color: #d7dee7; }

.scn pre.panel { border-top-left-radius: 0; border-top-right-radius: 0; border-top: none; margin-top: 0; }
.scn:not(.expanded) pre.panel { display: none !important; }

/* Pin terminal double-width glyphs (emoji) to two character cells so the box-drawing
   borders stay on the terminal's fixed column grid instead of drifting per row. */
.panel .w2 { display: inline-block; width: 2ch; text-align: center; }

/* Box-drawing character width fix specifically applied ONLY on Android via JS.
   This prevents subpixel AA degradation (muddiness) on Windows while fixing the alignment on Android. */
body.is-android .panel .bd { display: inline-block; width: 1ch; text-align: center; }

.empty { color: var(--muted); padding: 40px 4px; font-size: 15px; display: none; }

/* Responsive adjustments */
@media (min-width: 801px) {
  body.sidebar-toggled .sidebar { margin-left: -264px; }
}
@media (max-width: 800px) {
  .sidebar {
    position: fixed; background: var(--bg); z-index: 100; margin-left: -264px;
  }
  body.sidebar-toggled .sidebar {
    margin-left: 0; box-shadow: 2px 0 10px rgba(0,0,0,0.2);
  }
  body.sidebar-toggled .sidebar-backdrop { display: block; }
  .main { padding: 16px 12px 80px; width: 100vw; overflow: hidden; }
  .panel { font-size: 11px; padding: 12px; margin: 0; width: 100%; border-radius: 0; border-left: none; border-right: none; }
  h1 { font-size: 22px; }
  .icon-btn { font-size: 18px; padding: 4px 10px; margin-top: 0; z-index: 95; }
}
"""

_SCRIPT = """\
(function () {
  var body = document.body;
  var KEY = "gallery-theme";
  
  // Detect Android for specific font-rendering fixes
  if (/Android/i.test(navigator.userAgent)) {
    body.classList.add("is-android");
    
    // Apply box-drawing fix dynamically ONLY on Android so Windows rendering isn't ruined
    var boxRegex = /[\\u2500-\\u259F]/g;
    document.querySelectorAll('.panel').forEach(function(panel) {
      panel.innerHTML = panel.innerHTML.replace(boxRegex, '<span class="bd">$&</span>');
    });
  }
  
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

  // Sidebar toggling
  document.getElementById("sidebar-toggle").addEventListener("click", function() {
    body.classList.toggle("sidebar-toggled");
  });
  var backdrop = document.getElementById("sidebar-backdrop");
  if (backdrop) {
    backdrop.addEventListener("click", function() {
      body.classList.remove("sidebar-toggled");
    });
  }

  // Collapsible surface sections.
  document.querySelectorAll(".surface-head").forEach(function (h) {
    h.addEventListener("click", function () {
      var s = h.closest(".surface");
      s.classList.toggle("collapsed");
      h.setAttribute("aria-expanded", s.classList.contains("collapsed") ? "false" : "true");
    });
  });

  // Accordion logic for scenarios
  document.querySelectorAll(".scn-head").forEach(function(h) {
    h.addEventListener("click", function(e) {
      h.closest(".scn").classList.toggle("expanded");
    });
  });

  // Following any sidebar link auto-expands the section & scenario it lands in.
  document.querySelectorAll('.sidebar a[href^="#"]').forEach(function (a) {
    a.addEventListener("click", function () {
      var href = a.getAttribute("href");
      var el = document.querySelector(href);
      if (el) {
        var s = el.closest(".surface");
        if (s) s.classList.remove("collapsed");
        if (el.classList.contains("scn")) {
          el.classList.add("expanded");
        }
      }
      // On mobile, automatically hide the sidebar after clicking a link
      if (window.innerWidth <= 800) {
         body.classList.remove("sidebar-toggled");
      }
    });
  });

  var filter = document.getElementById("filter");
  var scns = [].slice.call(document.querySelectorAll(".scn"));
  var navItems = [].slice.call(document.querySelectorAll(".nav-item"));
  var surfaces = [].slice.call(document.querySelectorAll(".surface"));
  var navSurfaces = [].slice.call(document.querySelectorAll(".nav-surface"));
  var empty = document.getElementById("empty");
  var activeTag = null;

  var tagBtns = [].slice.call(document.querySelectorAll(".tag-btn"));
  tagBtns.forEach(function(btn) {
    btn.addEventListener("click", function() {
      if (activeTag === btn.getAttribute("data-tag")) {
         activeTag = null;
         btn.classList.remove("active");
      } else {
         tagBtns.forEach(function(b) { b.classList.remove("active"); });
         activeTag = btn.getAttribute("data-tag");
         btn.classList.add("active");
      }
      match(filter.value);
    });
  });

  function match(q) {
    q = q.trim().toLowerCase();
    var hits = 0;
    scns.forEach(function (c) {
      var textMatches = !q || c.getAttribute("data-text").indexOf(q) !== -1;
      var tagMatches = !activeTag || c.getAttribute("data-tags").indexOf(" " + activeTag + " ") !== -1;
      var on = textMatches && tagMatches;
      c.style.display = on ? "" : "none";
      if (on) hits++;
    });
    navItems.forEach(function (n) {
      var textMatches = !q || n.getAttribute("data-text").indexOf(q) !== -1;
      var tagMatches = !activeTag || n.getAttribute("data-tags").indexOf(" " + activeTag + " ") !== -1;
      var on = textMatches && tagMatches;
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
    """Force terminal double-width glyphs to 2ch, and wrap box-drawing characters.

    Rich pads each panel line to a fixed cell count. Browsers render double-width glyphs
    at fractional advances, breaking the right border. We pin them to `2ch`.
    
    (Note: Box drawing alignment for Android is handled dynamically via JS to avoid
    disrupting crisp subpixel rendering on desktop Windows/macOS).
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


def _search_text(sc: Scenario) -> str:
    """The lowercased haystack behind the sidebar filter — one definition shared by a
    scenario's card and its nav item, so the two can never match differently."""
    return " ".join([sc.snapshot_key, sc.name, sc.description, " ".join(sc.tags),
                     sc.surface.value, SURFACE_INFO[sc.surface].label]).lower()


def _scenario(sc: Scenario, result: BuildResult) -> str:
    key = sc.snapshot_key
    text = _search_text(sc)
    tags = "".join(f'<span class="pill">{_html.escape(t)}</span>' for t in sc.tags)
    if result.exit_code is not None:
        tags += f'<span class="pill">exit {result.exit_code}</span>'
    return (
        f'<article class="scn" id="{key}" data-text="{_html.escape(text, quote=True)}" data-tags=" {" ".join(sc.tags)} ">'
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

    # Collect all unique tags for the tag filters
    all_tags = set()
    for sc in scenarios:
        all_tags.update(sc.tags)
    
    tag_html = '<div class="tag-filters">'
    for tag in sorted(all_tags):
        tag_html += f'<button class="tag-btn" data-tag="{_html.escape(tag)}">{_html.escape(tag)}</button>'
    tag_html += '</div>'

    nav, main = [], []
    for surface, items in groups.items():
        info = SURFACE_INFO[surface]
        # Anchors keep the stable surface *value* (snapshot-key prefix); only the
        # visible label is the human-readable one.
        anchor = f"surface-{surface.value}"
        nav.append(
            f'<a class="nav-surface" href="#{anchor}">{_html.escape(info.label)}'
            f'<span class="nav-count">{len(items)}</span></a>'
        )
        main.append(f'<section class="surface" id="{anchor}">')
        main.append(
            f'<button class="surface-head" type="button" aria-expanded="true">'
            f'<span class="chev">&#9662;</span>'
            f'<span class="surface-name">{_html.escape(info.label)}</span>'
            f'<span class="surface-count">{len(items)}</span></button>'
        )
        main.append('<div class="surface-body">')
        # One-line section subtitle, reusing the existing muted description style so
        # no new CSS is introduced; collapses along with the section body.
        main.append(f'<div class="scn-desc">{_html.escape(info.blurb)}</div>')
        
        # Build all items so we can sort them by color
        built_items = [(sc, sc.build()) for sc in items]
        
        # Sort order: green(0), yellow(1), red(2), blue(3)
        color_order = {"green": 0, "yellow": 1, "red": 2, "blue": 3}
        built_items.sort(key=lambda x: color_order.get(x[1].border_color, 99))
        
        for sc, result in built_items:
            tags_str = " ".join(sc.tags)
            data = _search_text(sc)
            nav.append(
                f'<a class="nav-item" href="#{sc.snapshot_key}" data-text="{_html.escape(data, quote=True)}" data-tags=" {tags_str} ">'
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
        '<div class="sidebar-backdrop" id="sidebar-backdrop"></div>\n'
        '<div class="layout">\n'
        '<nav class="sidebar">'
        '<div class="brand-title">UI Gallery</div>'
        f'<div class="brand-sub">Scrooge Alert &middot; {total} scenarios</div>'
        '<div class="controls">'
        '<input id="filter" type="search" placeholder="Filter scenarios…" autocomplete="off">'
        '<button id="theme-toggle" type="button"></button></div>'
        f'{tag_html}'
        '<div class="nav-title">Surfaces</div>'
        f'{"".join(nav)}</nav>\n'
        '<main class="main">'
        '<div class="main-header">'
        '<button id="sidebar-toggle" class="icon-btn" title="Toggle Sidebar">&#9776;</button>'
        '<div>'
        '<h1>UI Scenario Gallery</h1>'
        '<div class="sub">Every catalogued terminal panel, rendered with full color for review.</div>'
        '</div></div>'
        f'{"".join(main)}'
        '<div class="empty" id="empty">No scenarios match your filter.</div>'
        '</main>\n</div>\n'
        f"<script>\n{_SCRIPT}</script>\n</body>\n</html>\n"
    )


def write_report(scenarios: list[Scenario], path: str) -> None:
    """Render ``scenarios`` to a styled, navigable HTML report at ``path``."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_report(scenarios))
