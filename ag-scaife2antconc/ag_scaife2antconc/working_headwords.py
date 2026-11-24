"""Explicit model-free headword standardization; manual annotations remain intact."""
from collections import Counter
from copy import deepcopy

from ag_diogenes_spacy_txt2xml import analyses, diogenes
from ag_diogenes_spacy_txt2xml.headwords import Reference, standardize_record
from ag_diogenes_spacy_txt2xml.proper_nouns import apply_proper_noun_record
from ag_greek_rules.headwords import RULE_ID, RULE_VERSION


def standardize(model, options, *, log=print, before_publish=None, reference=None):
    own = reference is None
    store = None
    if own:
        source = diogenes.find(options.diogenes)
        if not source:
            raise ValueError('Diogenes was not found for headword standardization. Set its folder in Settings.')
        # Only morphology and the heading index; no contextual/GreTa model loading.
        log('Loading Diogenes / LSJ headwords and correspondences…')
        store = analyses.load(source, log=log)
        try:
            reference = Reference.from_store(store, log=log)
        except BaseException:
            store.close()
            raise
        if reference is None:
            store.close()
            raise ValueError('Diogenes LSJ dictionary was not found. Files were preserved.')
    expected, updates, stats = model.revision, [], Counter()
    try:
        for passage in model.data['passages']:
            records, changed = [], False
            for token in passage['tokens']:
                record = deepcopy(token['record'])
                annotation = token['annotation']
                if annotation.get('manual') or annotation.get('reviewed'):
                    stats['manual choices preserved'] += 1
                elif annotation.get('stale') or annotation.get('context_stale'):
                    stats['stale analyses preserved'] += 1
                else:
                    stats.update(standardize_record(record, reference))
                    stats.update(apply_proper_noun_record(record))
                    changed |= record != token['record']
                records.append(record)
            if changed: updates.append({'passage_id': passage['id'], 'records': records})
        if before_publish: before_publish()
        if updates:
            model.apply_analyses(updates, expected_revision=expected, headwords_only=True, metadata={
                'operation': 'headword-standardization', 'rule_id': RULE_ID, 'rule_version': RULE_VERSION,
                'headword_reference': reference.receipt, 'neural_models_called': False})
        log(f"Standardization: {stats['headwords renamed']} headwords renamed · "
            f"{stats['headwords provisional']} provisional correspondences · "
            f"{stats['proper nouns']} proper nouns (PROPN) · "
            f"{stats['manual choices preserved']} manual choices preserved.")
        return dict(stats)
    finally:
        if own and reference is not None: reference.close()
        if store is not None: store.close()
