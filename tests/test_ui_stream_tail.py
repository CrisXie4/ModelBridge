"""AssistantStream v4 — streaming renders a bounded tail; final renders all.

Regression for stacked panel headers on CJK-heavy output: the live frame must
stay well under the terminal height so Windows Terminal's wide-char wrapping
can't push it past the screen and break Live's redraw.
"""

from __future__ import annotations

import io

from rich.console import Console

from modelbridge.agent.ui import AssistantStream, _tail_lines


def _make_stream(height: int = 20) -> AssistantStream:
    console = Console(file=io.StringIO(), width=80, height=height, force_terminal=True)
    s = AssistantStream(console, model_name="m")
    # Don't enter Live — we only exercise the renderer.
    return s


def _rendered_lines(stream, *, final: bool) -> list[str]:
    out = Console(file=io.StringIO(), width=80, height=200, force_terminal=False)
    out.print(stream._render(final=final))
    return out.file.getvalue().splitlines()


def test_tail_lines_helper():
    assert _tail_lines("", 5) == ""
    assert _tail_lines("a\nb", 5) == "a\nb"
    assert _tail_lines("\n".join(f"l{i}" for i in range(10)), 3) == "…\nl7\nl8\nl9"


def test_live_view_is_bounded_final_is_full():
    s = _make_stream(height=20)
    for i in range(60):
        s._reasoning_parts.append(f"想法{i}\n")
    for i in range(60):
        s._content_parts.append(f"行{i}\n")

    live = _rendered_lines(s, final=False)
    full = _rendered_lines(s, final=True)

    # Live view must fit comfortably under the 20-row terminal…
    assert len(live) <= 20 - 2, f"live view too tall: {len(live)} lines"
    # …while the final panel carries everything (60 reasoning lines alone
    # exceed the live budget; Markdown folds the content lines into one
    # paragraph, so compare against the reasoning height, not 120 lines).
    assert len(full) > 60
    assert any("行59" in ln for ln in full)
    assert any("想法0" in ln for ln in full)
    # Tail view shows the most recent content, not the oldest.
    assert any("行59" in ln for ln in live)
    assert not any("行0" in ln and "行09" not in ln for ln in live[:3])
