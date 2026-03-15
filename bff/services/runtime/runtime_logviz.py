from __future__ import annotations

from textwrap import wrap


def render_ascii_box(title: str, lines: list[str], width: int = 108) -> str:
    """Render a readable ASCII box for terminal logs."""
    normalized_width = max(60, min(160, int(width or 108)))
    inner = normalized_width - 4

    def _line(content: str) -> str:
        return f"| {content.ljust(inner)} |"

    top = "+" + ("-" * (inner + 2)) + "+"
    content_lines: list[str] = [top, _line(f"[ {title} ]"), top]

    for raw in lines:
        text = (raw or "").replace("\r", "").strip()
        if not text:
            content_lines.append(_line(""))
            continue
        chunks = wrap(text, width=inner, replace_whitespace=False, drop_whitespace=False)
        if not chunks:
            content_lines.append(_line(""))
            continue
        for chunk in chunks:
            content_lines.append(_line(chunk))

    content_lines.append(top)
    return "\n".join(content_lines)
