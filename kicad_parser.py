"""KiCad S-expression parser and serializer.

Handles .kicad_mod, .kicad_sym, .kicad_pcb, .kicad_sch file formats.
Converts between KiCad S-expression text and nested Python lists.
"""

from __future__ import annotations

from typing import Any, Optional


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list[tuple[str, Any]]:
    """Tokenize S-expression text into ``(type, value)`` pairs."""
    tokens: list[tuple[str, Any]] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\n\r":
            i += 1
            continue
        if c == "(":
            tokens.append(("OPEN", "("))
            i += 1
        elif c == ")":
            tokens.append(("CLOSE", ")"))
            i += 1
        elif c == '"':
            # Quoted string
            j = i + 1
            parts: list[str] = []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    parts.append(text[j + 1])
                    j += 2
                elif text[j] == '"':
                    break
                else:
                    parts.append(text[j])
                    j += 1
            tokens.append(("STR", "".join(parts)))
            i = j + 1
        else:
            # Atom (keyword or number)
            j = i
            while j < n and text[j] not in ' \t\n\r()"':
                j += 1
            atom = text[i:j]
            try:
                tokens.append(("NUM", int(atom)))
            except ValueError:
                try:
                    tokens.append(("NUM", float(atom)))
                except ValueError:
                    tokens.append(("ATOM", atom))
            i = j
    return tokens


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_sexpr(text: str) -> list:
    """Parse KiCad S-expression text into nested Python lists.

    Each ``(keyword arg1 arg2 (child ...))`` becomes
    ``["keyword", "arg1", "arg2", ["child", ...]]``.

    Atoms are kept as ``str``, numbers become ``int`` or ``float``,
    and quoted strings become ``str``.
    """
    tokens = tokenize(text)
    pos = [0]

    def _parse_one() -> Any:
        if pos[0] >= len(tokens):
            return None
        typ, val = tokens[pos[0]]
        if typ == "OPEN":
            pos[0] += 1
            lst: list = []
            while pos[0] < len(tokens) and tokens[pos[0]][0] != "CLOSE":
                item = _parse_one()
                if item is not None:
                    lst.append(item)
            if pos[0] < len(tokens):
                pos[0] += 1  # skip CLOSE
            return lst
        else:
            pos[0] += 1
            return val

    return _parse_one()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tree query helpers
# ---------------------------------------------------------------------------

def find_node(expr: list, tag: str) -> Optional[list]:
    """Return the first direct child list whose first element equals *tag*."""
    if not isinstance(expr, list):
        return None
    for child in expr:
        if isinstance(child, list) and child and child[0] == tag:
            return child
    return None


def find_all(expr: list, tag: str) -> list[list]:
    """Return every direct child list whose first element equals *tag*."""
    if not isinstance(expr, list):
        return []
    return [c for c in expr if isinstance(c, list) and c and c[0] == tag]


def find_value(expr: list, tag: str, default: Any = None) -> Any:
    """Get the second element from a simple ``(tag value)`` child node."""
    node = find_node(expr, tag)
    if node and len(node) > 1:
        return node[1]
    return default


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------

def _needs_quoting(s: str) -> bool:
    if not s:
        return True
    for c in s:
        if c in ' \t\n\r()"\\':
            return True
    # Also quote if it looks like a number (to avoid reparse issues)
    try:
        float(s)
        return True
    except ValueError:
        pass
    return False


def _fmt_num(n: int | float) -> str:
    if isinstance(n, float):
        if n == int(n) and abs(n) < 1e15:
            return str(int(n))
        return f"{n:.6g}"
    return str(n)


def serialize_sexpr(expr: Any, indent: int = 0) -> str:
    """Convert a parsed S-expression tree back to KiCad-compatible text.

    The output is indented with tabs and produces valid files that KiCad 9
    can open.
    """
    if not isinstance(expr, list):
        if isinstance(expr, str):
            if _needs_quoting(expr):
                escaped = expr.replace("\\", "\\\\").replace('"', '\\"')
                return f'"{escaped}"'
            return expr
        elif isinstance(expr, (int, float)):
            return _fmt_num(expr)
        return str(expr)

    if not expr:
        return "()"

    prefix = "\t" * indent
    has_nested = any(isinstance(x, list) for x in expr[1:])

    if not has_nested:
        # Simple node – everything on one line
        parts = [serialize_sexpr(x) for x in expr]
        line = f"({' '.join(parts)})"
        return f"{prefix}{line}" if indent > 0 else line

    # Complex node with child lists
    lines: list[str] = []

    # Opening: keyword + any leading scalar arguments
    first_parts = [serialize_sexpr(expr[0])]
    i = 1
    while i < len(expr) and not isinstance(expr[i], list):
        first_parts.append(serialize_sexpr(expr[i]))
        i += 1
    lines.append(f"{prefix}({' '.join(first_parts)}")

    while i < len(expr):
        if isinstance(expr[i], list):
            lines.append(serialize_sexpr(expr[i], indent + 1))
        else:
            lines.append(f"{prefix}\t{serialize_sexpr(expr[i])}")
        i += 1

    lines.append(f"{prefix})")
    return "\n".join(lines)
