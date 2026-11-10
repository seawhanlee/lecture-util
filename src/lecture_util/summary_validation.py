"""Conservative OFM checks, without enforcing the summarizer's writing style."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    line: int
    message: str


def _escaped(text: str, index: int) -> bool:
    start = index
    while start and text[start - 1] == "\\":
        start -= 1
    return (index - start) % 2 == 1


def _visible_lines(content: str) -> tuple[list[str], list[ValidationIssue]]:
    """Mask literal regions, preserving newlines and quote/list code examples."""
    lines = [re.sub(r"^(?: {0,3}>[ \t]?)+", "", line)
             for line in content.splitlines()]
    text = "\n".join(lines)
    visible = list(text)
    issues: list[ValidationIssue] = []
    fence: tuple[str, int, int] | None = None
    math_line: int | None = None
    comment_end: str | None = None
    inline_ticks: str | None = None
    frontmatter = bool(lines and lines[0].strip() == "---"
                       and any(line.strip() in {"---", "..."} for line in lines[1:]))
    index, number = 0, 1

    def hide(first: int, last: int) -> None:
        visible[first:last] = ["\n" if c == "\n" else " " for c in text[first:last]]

    while index < len(text):
        at_start = index == 0 or text[index - 1] == "\n"
        if at_start and not (math_line or comment_end or inline_ticks):
            end = text.find("\n", index)
            end = len(text) if end == -1 else end
            line = text[index:end]
            marker = re.match(r" {0,3}(`{3,}|~{3,})(.*)$", line)
            if fence:
                if (marker and marker[1][0] == fence[0]
                        and len(marker[1]) >= fence[1] and not marker[2].strip()):
                    fence = None
                hide(index, end)
                index = end + int(end < len(text))
                number += int(end < len(text))
                continue
            if frontmatter:
                if number > 1 and line.strip() in {"---", "..."}:
                    frontmatter = False
                hide(index, end)
                index = end + int(end < len(text))
                number += int(end < len(text))
                continue
            if marker and not (marker[1][0] == "`" and "`" in marker[2]):
                fence = (marker[1][0], len(marker[1]), number)
                hide(index, end)
                index = end + int(end < len(text))
                number += int(end < len(text))
                continue
            if line.startswith(("    ", "\t")):
                hide(index, end)
                index = end + int(end < len(text))
                number += int(end < len(text))
                continue
        if text[index] == "\n":
            number += 1
            index += 1
            continue
        if comment_end:
            if text.startswith(comment_end, index):
                hide(index, index + len(comment_end))
                index += len(comment_end)
                comment_end = None
            else:
                hide(index, index + 1)
                index += 1
            continue
        if inline_ticks:
            ticks = re.match(r"`+", text[index:]) if text[index] == "`" else None
            size = len(ticks[0]) if ticks else 1
            hide(index, index + size)
            index += size
            if ticks and ticks[0] == inline_ticks:
                inline_ticks = None
            continue
        if math_line:
            size = 2 if text.startswith("$$", index) and not _escaped(text, index) else 1
            hide(index, index + size)
            index += size
            if size == 2:
                math_line = None
            continue
        if not _escaped(text, index):
            if text.startswith(("%%", "<!--"), index):
                size = 2 if text.startswith("%%", index) else 4
                comment_end = "%%" if size == 2 else "-->"
                hide(index, index + size)
                index += size
                continue
            if text.startswith("[[", index):
                # Link titles/aliases may themselves contain math or code markup.
                end = text.find("\n", index)
                end = len(text) if end == -1 else end
                close = text.find("]]", index + 2, end)
                index = end if close == -1 else close + 2
                continue
            if text[index] == "`":
                ticks = re.match(r"`+", text[index:])[0]
                closing = re.search(r"(?<!`)" + re.escape(ticks) + r"(?!`)",
                                    text[index + len(ticks):])
                if closing:
                    inline_ticks = ticks
                    hide(index, index + len(ticks))
                    index += len(ticks)
                    continue
            if text.startswith("$$", index):
                math_line = number
                hide(index, index + 2)
                index += 2
                continue
            if text[index] == "$":
                # Single dollars may be currency; only mask a complete inline pair.
                end = index + 1
                while end < len(text) and text[end] != "\n":
                    if text[end] == "$" and not _escaped(text, end):
                        hide(index, end + 1)
                        index = end + 1
                        break
                    end += 1
                else:
                    index += 1
                continue
        index += 1
    if fence:
        issues.append(ValidationIssue("unclosed_fence", fence[2], "Close the code fence."))
    if math_line:
        issues.append(ValidationIssue("unclosed_math", math_line, "Close the $$ math block."))
    return "".join(visible).split("\n"), issues


def _table_rows(lines: list[str]) -> set[int]:
    rows: set[int] = set()
    active = False
    for index, line in enumerate(lines):
        cells = line.strip().strip("|").split("|")
        separator = ("|" in line and cells
                     and all(re.fullmatch(r"\s*:?-+:?\s*", cell) for cell in cells))
        if separator and index and lines[index - 1].strip():
            rows.update((index - 1, index))
            active = True
        elif active and line.strip() and "|" in line:
            rows.add(index)
        else:
            active = False
    return rows


def validate_summary(content: str) -> list[ValidationIssue]:
    """Report unambiguous delimiter errors; never rewrite OFM or resolve links."""
    if not content.strip():
        return [ValidationIssue("empty_summary", 1, "Return a non-empty summary.")]
    lines, issues = _visible_lines(content)
    tables = _table_rows(lines)
    for number, line in enumerate(lines, 1):
        cursor = 0
        while (start := line.find("[[", cursor)) != -1:
            cursor = start + 2
            if _escaped(line, start):
                continue
            end = line.find("]]", cursor)
            if end == -1:
                issues.append(ValidationIssue("unclosed_wikilink", number, "Close the [[wikilink]]."))
                break
            body = line[cursor:end]
            nested = body.find("[[")
            if nested != -1 and not _escaped(body, nested):
                issues.append(ValidationIssue(
                    "unclosed_wikilink", number, "Close the wikilink before starting another one.",
                ))
                continue
            target = body.split("|", 1)[0].removesuffix("\\").strip()
            if not target or not target.lstrip("#^").strip():
                issues.append(ValidationIssue("empty_link_target", number, "Give the wikilink a target."))
            if number - 1 in tables and any(
                char == "|" and not _escaped(body, pos) for pos, char in enumerate(body)
            ):
                issues.append(ValidationIssue(
                    "unescaped_table_alias", number,
                    "Escape the wikilink alias pipe as \\| inside the table.",
                ))
            cursor = end + 2
    return sorted(issues, key=lambda issue: (issue.line, issue.code))
