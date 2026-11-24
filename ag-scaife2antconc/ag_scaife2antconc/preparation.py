"""Cleansing previews shared by browser and native interfaces; no GUI imports."""
from collections import Counter
import xml.etree.ElementTree as ET
from . import accents
from .reader import STEPS, read


def scan(paths):
    found, speakers, changed = {}, None, Counter()
    for path in paths:
        try:
            work = read(path)
        except (ET.ParseError, OSError):
            continue
        changed += accents.normalize_lines(work.lines)
        speakers = work.speakers if speakers is None else speakers + work.speakers
        for step in STEPS:
            count, words, examples = found.get(step, (0, 0, []))
            examples = examples + [e for e in work.examples[step] if e not in examples]
            found[step] = count + work.elements[step], words + work.dropped[step], examples[:5]
    if speakers:
        count, words, _ = found['speaker names']
        found['speaker names'] = count, words, [', '.join(f'{s} ({n})' for s, n in speakers.most_common(12))
                                                  + (' …' if len(speakers) > 12 else '')]
    grave, enclitic, examples = accents.summary(changed, limit=6)
    found['accents'] = grave + enclitic, enclitic, examples
    return {step: values for step, values in found.items() if values[0]}
