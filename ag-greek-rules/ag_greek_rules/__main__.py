"""JSON Lines interface for callers that do not use Python."""
import argparse
import json
import sys
from .api import RULE_ID, assess_pronoun, catalog
from .deictic import RULE_ID as DEICTIC_ID, assess_deictic
from .grammar import RULE_ID as POS_ID, assess_pos
from .headwords import RULE_ID as HEADWORD_ID, assess_headword


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', action='store_true', help='print the complete rule catalogue')
    args = parser.parse_args(argv)
    if args.catalog:
        print(json.dumps(catalog(), ensure_ascii=False, indent=2))
        return 0
    failed = False
    for number, line in enumerate(sys.stdin, 1):
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError('each request must be a JSON object')
            rule = request.get('rule_id', RULE_ID)
            if rule not in (RULE_ID, DEICTIC_ID, POS_ID, HEADWORD_ID):
                raise ValueError('rule is not executable in this portable release')
            fields = set()
            if rule == RULE_ID:
                fields = {'following'}
            elif rule == POS_ID:
                fields = {'original_pos', 'form_analyses', 'lemma_analyses'}
            elif rule == HEADWORD_ID:
                fields = {'entries', 'form_entry_ids', 'selected_reference_id', 'provisional_link', 'pos', 'manual'}
            allowed = {'rule_id', 'form', 'candidate_lemma'} | fields
            if set(request) - allowed:
                raise ValueError('unexpected input field')
            if rule == HEADWORD_ID:
                response = assess_headword(request['form'], request['candidate_lemma'],
                    **{k: request[k] for k in fields if k in request}).as_dict()
            elif rule == POS_ID:
                response = assess_pos(request['form'], request['candidate_lemma'], original_pos=request.get('original_pos'),
                                      form_analyses=request['form_analyses'], lemma_analyses=request['lemma_analyses']).as_dict()
            elif rule == RULE_ID:
                response = assess_pronoun(request['form'], request['candidate_lemma'], following=request.get('following')).as_dict()
            else:
                response = assess_deictic(request['form'], request['candidate_lemma']).as_dict()
        except (ValueError, KeyError, TypeError) as error:
            failed = True
            response = {'line': number, 'error': str(error)}
        print(json.dumps(response, ensure_ascii=False, allow_nan=False))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
