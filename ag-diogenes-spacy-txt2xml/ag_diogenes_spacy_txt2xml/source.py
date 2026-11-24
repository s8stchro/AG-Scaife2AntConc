"""Read the input file: one text line per row, optionally `running-number  label : text`.

The label is a citation like `Or1.12.3` (work.section.line). Every level above the
last becomes a `segment` milestone when it changes; the last level is the `line`.
"""
import re
from dataclasses import dataclass, field

LINE_RE = re.compile(r'^\s*(?:(\d+)\s+)?([^\s:]*\d[^\s:]*)\s+:\s?(.*)$')
HYPHENS = '–-‐‑\u00ad'


@dataclass
class Milestone:
    unit: str                 # 'segment' or 'line'
    n: str
    xml_id: str | None = None


@dataclass
class Line:
    number: str | None        # running number of the source file, if any
    label: str | None
    text: str
    milestones: list = field(default_factory=list)


def parse_line(raw):
    m = LINE_RE.match(raw)
    if m:
        return Line(m.group(1), m.group(2), m.group(3))
    return Line(None, None, raw.strip())


def read_lines(path):
    with open(path, encoding='utf-8-sig') as fh:
        raw = fh.read().splitlines()
    lines = [parse_line(r) for r in raw if r.strip()]
    add_milestones(lines)
    return lines


def add_milestones(lines):
    prev = []
    for i, line in enumerate(lines, 1):
        if line.label is None:
            line.milestones = [Milestone('line', str(i))]
            continue
        parts = line.label.split('.')
        higher = parts[:-1]
        changed = next((d for d in range(len(higher)) if prev[:d + 1] != higher[:d + 1]), len(higher))
        line.milestones = [Milestone('segment', '.'.join(higher[:d + 1])) for d in range(changed, len(higher))]
        line.milestones.append(Milestone('line', line.label, f'l{line.number}' if line.number else None))
        prev = higher


def format_line(line, text):
    """Rebuild a source row with new text, keeping number and label."""
    if line.label is None:
        return text
    head = f'{line.number}   {line.label}' if line.number else line.label
    return f'{head} : {text}'


def _broken(text):
    return len(text) > 1 and text[-1] in HYPHENS and not text[-2].isspace()


def dehyphenate(lines):
    """Complete a word broken at the end of a line on that line: the rest of the word,
    with any punctuation attached to it, moves up from the next line and the hyphen goes.

        γενέ–  /  σθαι, οἷοίπερ   ->   γενέσθαι,  /  οἷοίπερ
    """
    for i, line in enumerate(lines):
        j = i + 1
        while j < len(lines) and _broken(line.text):
            if not lines[j].text:          # emptied by an earlier move: take from the line after
                j += 1
                continue
            head, _, rest = lines[j].text.partition(' ')
            line.text = line.text[:-1] + head
            lines[j].text = rest
    return lines
