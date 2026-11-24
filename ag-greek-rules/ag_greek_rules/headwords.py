"""An explicit headword convention, separate from contextual lemma selection.

Reference entries are supplied by the caller. This module contains no dictionary
data, performs no lookup/networking, and never mutates provider evidence.
"""
from dataclasses import asdict, dataclass
from importlib.resources import files
import json
import unicodedata

from .deictic import assess_deictic

RULE_ID = 'grc.lemma.diogenes-headword'
RULE_VERSION = '1.1.0'


def headword_mappings():
    """Return a fresh copy of the original, sourced correspondence catalogue."""
    return json.loads(files(__package__).joinpath('headword-mappings.json').read_text(encoding='utf-8'))


def headword_key(value, *, case_sensitive=False):
    """Preserve linguistic diacritics/numbers; vowel-length marks are typography."""
    value = value if case_sensitive else value.lower()
    nfd = unicodedata.normalize('NFD', value).replace('\u0300', '\u0301')
    return unicodedata.normalize('NFC', ''.join(c for c in nfd if c not in '\u0304\u0306'))


@dataclass(frozen=True)
class HeadwordAssessment:
    rule_id: str
    rule_version: str
    category: str
    status: str
    reason: str
    form: str
    candidate_lemma: str
    headword: str
    reference_id: str | None
    reference_entries: tuple
    mapping_ids: tuple

    def as_dict(self):
        return asdict(self)


def assess_headword(form, candidate_lemma, *, entries=(), form_entry_ids=(),
                    selected_reference_id=None, provisional_link=False,
                    pos=None, manual=False, mappings=None):
    """Name an already selected lexical item using a supplied reference inventory.

    `entries` contain id/headword/verified and snapshot provenance. A matching
    spelling does not collapse homographs. A caller may supply a specific entry
    identity only when the selected provider's dictionary association is verified.
    Missing evidence retains the proposal, explicitly provisional. It does not
    substitute a different, merely familiar dictionary word.
    """
    if not isinstance(form, str) or not isinstance(candidate_lemma, str):
        raise TypeError('form and candidate_lemma must be strings')
    entries = tuple(dict(e) for e in entries)
    if any(not isinstance(e.get('id'), str) or not isinstance(e.get('headword'), str) for e in entries):
        raise ValueError('reference entries require string id and headword')

    def result(status, reason, headword=None, ident=None, matches=(), mapping_ids=()):
        return HeadwordAssessment(RULE_ID, RULE_VERSION, 'lexicographic_normalisation_policy',
            status, reason, form, candidate_lemma, candidate_lemma if headword is None else headword,
            ident, tuple(dict(e) for e in matches), tuple(mapping_ids))

    if manual:
        return result('not_applicable', 'manual_choice_preserved')
    if not candidate_lemma:
        return result('abstained', 'missing_proposal')
    verified = {e['id']: e for e in entries if e.get('verified') is True}
    deictic = assess_deictic(form, candidate_lemma)
    if deictic.status == 'applied':
        exact_deictic = [e for e in verified.values()
                         if headword_key(e['headword'], case_sensitive=True) ==
                         headword_key(deictic.headword, case_sensitive=True)]
        if len(exact_deictic) == 1:
            return result('not_applicable', 'separate_deictic_headword_preserved',
                          ident=exact_deictic[0]['id'], matches=(exact_deictic[0],))
        return result('not_applicable', 'separate_deictic_headword_preserved')
    if provisional_link:
        return result('abstained', 'provisional_provider_link', matches=verified.values())
    # Identity is stronger than spelling, but must actually name this proposal.
    selected = verified.get(selected_reference_id)
    if selected is not None and headword_key(selected['headword']) == headword_key(candidate_lemma):
        return result('applied', 'verified_provider_entry', selected['headword'], selected['id'], (selected,))

    mappings = headword_mappings()['mappings'] if mappings is None else mappings
    applicable, unavailable = [], False
    for mapping in mappings:
        if headword_key(mapping['from']) != headword_key(candidate_lemma):
            continue
        if mapping.get('pos') and pos not in mapping['pos']:
            unavailable = True
            continue
        targets = [e for e in verified.values() if headword_key(e['headword']) == headword_key(mapping['to'])]
        if mapping.get('requires_form_entry'):
            targets = [e for e in targets if e['id'] in form_entry_ids]
            if not targets:
                continue  # This scoped correspondence does not apply to this form.
        if not targets:
            unavailable = True
            continue
        applicable.extend((e, mapping) for e in targets)
    if applicable:
        targets = {e['id']: e for e, _ in applicable}
        ids = tuple(dict.fromkeys(m['id'] for _, m in applicable))
        if len(targets) != 1:
            return result('abstained', 'ambiguous_mapping', matches=targets.values(), mapping_ids=ids)
        target = next(iter(targets.values()))
        return result('applied', 'documented_equivalent', target['headword'], target['id'], (target,), ids)
    if unavailable:
        # Do not quietly report a variant as standard when the declared mapping
        # lacks its target or the required contextual/form evidence.
        return result('abstained', 'mapping_evidence_unavailable', matches=verified.values())

    matches = [e for e in verified.values() if headword_key(e['headword']) == headword_key(candidate_lemma)]
    # Preserve proper/common distinctions if the proposed casing names an entry.
    exact = [e for e in matches if headword_key(e['headword'], case_sensitive=True) ==
             headword_key(candidate_lemma, case_sensitive=True)]
    if matches and not exact:
        return result('abstained', 'case_mismatch_requires_identity', matches=matches)
    matches = exact
    if len(matches) == 1:
        target = matches[0]
        return result('applied', 'exact_reference_headword', target['headword'], target['id'], matches)
    if matches:
        return result('abstained', 'ambiguous_reference_identity', matches=matches)
    return result('abstained', 'headword_outside_reference')
