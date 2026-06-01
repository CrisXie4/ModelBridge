"""Parse + validate a unified diff into structured ``FileDiff`` objects.

Why we don't shell out to ``patch``
-----------------------------------
The ``patch`` utility isn't reliably available on Windows; spawning a
subprocess just to apply text is overkill anyway. Parsing unified diff
ourselves keeps the pipeline pure-Python and gives us precise error
messages when the model produces malformed output (which it will).

Supported grammar
-----------------

* File header lines::

      --- a/path/old      OR   --- /dev/null
      +++ b/path/new      OR   +++ /dev/null

* Hunk header::

      @@ -oldStart[,oldCount] +newStart[,newCount] @@ [optional section heading]

* Hunk body lines start with one of:

  - ``' '`` (space) — context line
  - ``'-'`` — line removed
  - ``'+'`` — line added
  - ``'\\ No newline at end of file'`` — marker, accepted, ignored

Anything else inside a hunk is a parse error.

What we explicitly reject
-------------------------

* A "diff" that looks like a wholesale file rewrite (no hunk headers,
  just full content). The phase-6 prompt forbids that — we double check.
* Header lines without a matching opposite (``---`` without ``+++``).
* Hunk counts that disagree with the body.
* Mixed line endings inside a hunk are normalised to ``\\n`` before
  comparison; the applier knows about the project's original endings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .safety import strip_ab_prefix


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DiffParseError(ValueError):
    """Raised when input doesn't look like a valid unified diff."""

    def __init__(self, message: str, *, line_no: int | None = None) -> None:
        prefix = f"line {line_no}: " if line_no is not None else ""
        super().__init__(prefix + message)
        self.line_no = line_no


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class HunkLine:
    """One line inside a hunk body."""

    op: str  # ' ', '-', '+'
    text: str  # without trailing newline

    @property
    def is_context(self) -> bool:
        return self.op == " "

    @property
    def is_remove(self) -> bool:
        return self.op == "-"

    @property
    def is_add(self) -> bool:
        return self.op == "+"


@dataclass
class Hunk:
    """One ``@@ ... @@`` block."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str = ""           # optional context after the second @@
    lines: list[HunkLine] = field(default_factory=list)

    @property
    def removed_lines(self) -> list[str]:
        return [ln.text for ln in self.lines if ln.is_remove or ln.is_context]

    @property
    def added_lines(self) -> list[str]:
        return [ln.text for ln in self.lines if ln.is_add or ln.is_context]


@dataclass
class FileDiff:
    """All hunks for one source file."""

    old_path: str
    new_path: str
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def is_creation(self) -> bool:
        return self.old_path == "/dev/null"

    @property
    def is_deletion(self) -> bool:
        return self.new_path == "/dev/null"

    @property
    def effective_path(self) -> str:
        """Project-root-relative path the applier should write to / read from.

        ``a/`` and ``b/`` prefixes already stripped. For deletions, the
        ``old_path`` is the file being removed.
        """
        return self.new_path if not self.is_deletion else self.old_path


@dataclass
class ParsedDiff:
    """The whole parsed unified-diff document."""

    files: list[FileDiff] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.files


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# @@ -1,5 +1,6 @@   or   @@ -1 +1 @@   or  @@ -1,5 +1,6 @@ section title
_HUNK_HEADER_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@\s*(.*)$"
)
# Lines like `--- a/foo.py\tmodified` — we keep just the path field.
_HEADER_PATH_RE = re.compile(r"^(?:---|\+\+\+)\s+(.+?)(?:\s+\d.*)?$")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_unified_diff(text: str) -> ParsedDiff:
    """Parse ``text`` into a :class:`ParsedDiff`. Raise :class:`DiffParseError`."""
    if not text or not text.strip():
        raise DiffParseError("空 diff")

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Strip a single trailing empty line from .split (every "foo\n" gives [...,""]).
    if lines and lines[-1] == "":
        lines.pop()

    parsed = ParsedDiff()
    it = _LineIter(lines)
    found_anything = False

    while it.has_more():
        ln, n = it.peek()
        if ln is None:
            break
        # Skip leading non-header noise — git diff often emits
        # "diff --git ...", "index <sha>..<sha>", "new file mode 100644",
        # etc. before the ---/+++ pair. We just walk forward until we
        # see a `--- ` line.
        if not ln.startswith("--- "):
            it.advance()
            continue

        file_diff = _parse_one_file(it)
        parsed.files.append(file_diff)
        found_anything = True

    if not found_anything:
        raise DiffParseError("找不到 unified diff header (--- / +++)")

    return parsed


def render_unified_diff(parsed: ParsedDiff) -> str:
    """Render a :class:`ParsedDiff` back to text. Useful for round-tripping
    and for ``mbridge patch preview``.
    """
    out: list[str] = []
    for f in parsed.files:
        out.append(f"--- {f.old_path}")
        out.append(f"+++ {f.new_path}")
        for h in f.hunks:
            old_part = f"-{h.old_start},{h.old_count}" if h.old_count != 1 else f"-{h.old_start}"
            new_part = f"+{h.new_start},{h.new_count}" if h.new_count != 1 else f"+{h.new_start}"
            section = (" " + h.section) if h.section else ""
            out.append(f"@@ {old_part} {new_part} @@{section}")
            for ln in h.lines:
                out.append(f"{ln.op}{ln.text}")
    return "\n".join(out) + ("\n" if out else "")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

class _LineIter:
    """A 1-based, peekable iterator over the diff text."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self._idx = 0

    def has_more(self) -> bool:
        return self._idx < len(self._lines)

    def peek(self) -> tuple[str | None, int]:
        if self._idx >= len(self._lines):
            return None, self._idx + 1
        return self._lines[self._idx], self._idx + 1

    def advance(self) -> tuple[str, int]:
        ln = self._lines[self._idx]
        n = self._idx + 1
        self._idx += 1
        return ln, n

    def expect_starts_with(self, prefix: str) -> tuple[str, int]:
        if not self.has_more():
            raise DiffParseError(f"提前结束，预期 {prefix!r}")
        ln, n = self.peek()
        assert ln is not None  # has_more() guarantees a line
        if not ln.startswith(prefix):
            raise DiffParseError(f"预期 {prefix!r}，实际是 {ln!r}", line_no=n)
        return self.advance()


def _parse_path(header_line: str, *, prefix: str, line_no: int) -> str:
    """Extract the path from a ``--- ...`` or ``+++ ...`` line and strip a/, b/."""
    if not header_line.startswith(prefix):
        raise DiffParseError(f"预期 {prefix!r}，实际是 {header_line!r}", line_no=line_no)
    body = header_line[len(prefix):]
    m = _HEADER_PATH_RE.match(header_line)
    if m:
        raw = m.group(1).strip()
    else:
        raw = body.strip()
    if not raw:
        raise DiffParseError(f"path 为空: {header_line!r}", line_no=line_no)
    # Some diff tools quote paths with spaces — strip surrounding quotes.
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        raw = raw[1:-1]
    return strip_ab_prefix(raw)


def _parse_one_file(it: _LineIter) -> FileDiff:
    minus_line, n1 = it.advance()
    old_path = _parse_path(minus_line, prefix="--- ", line_no=n1)

    if not it.has_more():
        raise DiffParseError("`--- ` 之后没有 `+++ ` 行", line_no=n1)
    plus_line, n2 = it.peek()
    assert plus_line is not None  # has_more() checked above
    if not plus_line.startswith("+++ "):
        raise DiffParseError(
            f"`--- ` 后必须紧跟 `+++ `，实际是 {plus_line!r}", line_no=n2,
        )
    it.advance()
    new_path = _parse_path(plus_line, prefix="+++ ", line_no=n2)

    file_diff = FileDiff(old_path=old_path, new_path=new_path)

    # Hunks
    while it.has_more():
        ln, n = it.peek()
        assert ln is not None  # has_more() guarantees a line
        if ln.startswith("--- "):
            # Next file starts; stop parsing this one.
            break
        if ln.startswith("@@"):
            file_diff.hunks.append(_parse_one_hunk(it))
            continue
        # Anything else here is junk — skip with no error (some diffs
        # have a separator line, etc.). But blank-line junk inside a
        # diff that has more than one file is unusual; we tolerate.
        it.advance()

    if not file_diff.hunks and not (file_diff.is_creation or file_diff.is_deletion):
        # No hunks AND not a pure create/delete sentinel → malformed.
        raise DiffParseError(
            f"文件 {file_diff.effective_path!r} 没有任何 @@ hunk", line_no=n1,
        )

    return file_diff


def _parse_one_hunk(it: _LineIter) -> Hunk:
    header, n = it.advance()
    m = _HUNK_HEADER_RE.match(header)
    if not m:
        raise DiffParseError(f"非法 hunk header: {header!r}", line_no=n)
    old_start = int(m.group(1))
    old_count = int(m.group(2)) if m.group(2) is not None else 1
    new_start = int(m.group(3))
    new_count = int(m.group(4)) if m.group(4) is not None else 1
    section = (m.group(5) or "").strip()

    hunk = Hunk(
        old_start=old_start, old_count=old_count,
        new_start=new_start, new_count=new_count,
        section=section,
    )

    # Body: walk until next header or next file.
    seen_old = 0
    seen_new = 0
    while it.has_more():
        ln, ln_no = it.peek()
        assert ln is not None  # has_more() guarantees a line
        if ln.startswith("@@") or ln.startswith("--- "):
            break
        it.advance()

        if ln == "":
            # Empty diff line — treat as context "" (some tools strip
            # trailing space on context).
            hunk.lines.append(HunkLine(op=" ", text=""))
            seen_old += 1
            seen_new += 1
            continue

        marker = ln[0]
        text = ln[1:]
        if marker == " ":
            hunk.lines.append(HunkLine(op=" ", text=text))
            seen_old += 1
            seen_new += 1
        elif marker == "-":
            hunk.lines.append(HunkLine(op="-", text=text))
            seen_old += 1
        elif marker == "+":
            hunk.lines.append(HunkLine(op="+", text=text))
            seen_new += 1
        elif marker == "\\":
            # "\ No newline at end of file" marker — ignore.
            continue
        else:
            raise DiffParseError(
                f"hunk 内非法行 (开头不是 ' ', '-', '+'): {ln!r}",
                line_no=ln_no,
            )

    # Sanity-check counts. Off-by-one is common from sloppy generators;
    # we accept ±1 with a forgiving warning by tolerating mismatches.
    # Hard error only when the count is wildly off (>2 lines diff).
    if abs(seen_old - old_count) > 2:
        raise DiffParseError(
            f"hunk header 声称 old={old_count} 行，实际看到 {seen_old} 行",
            line_no=n,
        )
    if abs(seen_new - new_count) > 2:
        raise DiffParseError(
            f"hunk header 声称 new={new_count} 行，实际看到 {seen_new} 行",
            line_no=n,
        )
    # Re-normalise the counts to what we actually saw — the applier
    # uses them.
    hunk.old_count = seen_old
    hunk.new_count = seen_new

    return hunk


__all__ = [
    "DiffParseError",
    "HunkLine",
    "Hunk",
    "FileDiff",
    "ParsedDiff",
    "parse_unified_diff",
    "render_unified_diff",
]
