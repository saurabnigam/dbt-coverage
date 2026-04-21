"""SPEC-05 §5.3 — marker-based line mapping (rendered -> source).

We inject `-- DBTCOV_LINE:N` before every source line prior to rendering.
After rendering, we scan the output, strip markers, and record which source
line each rendered line originated from. Lines with no preceding marker
inherit the last seen source line (useful for macro expansions).
"""

from __future__ import annotations

import re

_MARKER_RE = re.compile(r"^\s*--\s*DBTCOV_LINE:(\d+)\s*$")


def _ends_inside_jinja_block(line: str, starts_inside: bool) -> bool:
    """Return True if the position after this line is still inside an open {{ or {% block.

    Performs a simple character-scan: tracks open/close tokens for both
    print-blocks (``{{ }}``) and tag-blocks (``{% %}``) as well as Jinja
    comment blocks (``{# #}``).  String literals inside blocks are NOT
    modelled; this is intentional (false negatives are safe — we simply skip
    a marker injection, which is conservative and correct).
    """
    depth = 1 if starts_inside else 0
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == "{" and i + 1 < n and line[i + 1] in ("{", "%", "#"):
            depth += 1
            i += 2
        elif ch == "}" and i + 1 < n and line[i + 1] == "}" or ch == "%" and i + 1 < n and line[i + 1] == "}" or ch == "#" and i + 1 < n and line[i + 1] == "}":
            depth = max(0, depth - 1)
            i += 2
        else:
            i += 1
    return depth > 0


def inject_line_markers(source_sql: str) -> str:
    """Prepend each source line N with ``-- DBTCOV_LINE:N`` on its own line.

    Skips injection inside:

    * ``{% raw %}...{% endraw %}`` regions (the markers would render
      literally, not survive as comments).
    * Multi-line ``{{ }}`` / ``{% %}`` Jinja blocks — injecting a SQL
      ``-- comment`` inside an open expression block causes a
      ``TemplateSyntaxError`` because Jinja2 parses ``-- DBTCOV_LINE:N``
      as ``-(-(DBTCOV_LINE))`` followed by the illegal ``:`` token.
    """
    lines = source_sql.splitlines(keepends=False)
    out: list[str] = []

    in_raw = False
    in_jinja_block = False  # True when a {{ or {% was opened but not yet closed
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not in_raw:
            if not in_jinja_block:
                out.append(f"-- DBTCOV_LINE:{i}")
            out.append(line)
            if "{% raw %}" in stripped or "{%- raw -%}" in stripped or "{%-raw-%}" in stripped:
                in_raw = True
            else:
                in_jinja_block = _ends_inside_jinja_block(line, in_jinja_block)
        else:
            out.append(line)
            if (
                "{% endraw %}" in stripped
                or "{%- endraw -%}" in stripped
                or "{%-endraw-%}" in stripped
            ):
                in_raw = False

    return "\n".join(out)


def extract_line_map(rendered_with_markers: str) -> tuple[str, dict[int, int]]:
    """Strip markers, return `(clean_sql, {rendered_line: source_line})`.

    Rendered line numbers are 1-indexed; they count lines in the cleaned output
    (after markers are removed).
    """
    clean_lines: list[str] = []
    line_map: dict[int, int] = {}
    last_source = 1

    for raw_line in rendered_with_markers.splitlines(keepends=False):
        m = _MARKER_RE.match(raw_line)
        if m:
            last_source = int(m.group(1))
            continue
        clean_lines.append(raw_line)
        line_map[len(clean_lines)] = last_source

    clean = "\n".join(clean_lines)
    if rendered_with_markers.endswith("\n"):
        clean += "\n"
    return clean, line_map
