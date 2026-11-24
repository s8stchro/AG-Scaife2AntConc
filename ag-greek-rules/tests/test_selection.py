"""Responsibility decisions preserve evidence and have no hidden provider weights."""
from copy import deepcopy
from ag_greek_rules import select_lemma


def test_unique_morphology_context_ambiguity_and_helper_have_distinct_roles():
    context={'lemma':'ἔσπεισομαι','resolved':True,'lemma_source':'edit_script'}
    original=deepcopy(context)
    assert select_lemma(context,['σπένδω'],'σπένδω')['selected_lemma']=='σπένδω'
    context={'lemma':'ἐγώ','resolved':True,'lemma_source':'edit_script'}
    result=select_lemma(context,['ἐγώ','ἡμός'],'ἡμός')
    assert result['selected_lemma']=='ἐγώ' and result['selected_by']=='context'
    assert select_lemma({},['ἐγώ','ἡμός'],'ἐγώ')['selected_by']=='greta'
    assert select_lemma({},[],None)['status']=='abstained'
    assert original=={'lemma':'ἔσπεισομαι','resolved':True,'lemma_source':'edit_script'}


def test_contextual_proposal_must_be_a_local_candidate_when_there_are_several():
    lexical=['Κλεώνυμος','Κλεωνύμης']
    context={'lemma':'Κλείνομος','resolved':True,'lemma_source':'edit_script','canonical_lemma':'Κλεώνυμος'}
    original=deepcopy(context)
    result=select_lemma(context,lexical,'κλεώνυμος')
    assert (result['selected_lemma'],result['selected_by'],result['reason'])==(
        'Κλεώνυμος','context_lookup','context_outside_inventory_lookup')
    assert result['context_lemma']=='Κλείνομος' and context==original     # raw proposal retained, not modified
    # Lookup not in the inventory: GreTa may supply a candidate (case is presentation only).
    context={'lemma':'Πολυάρχας','resolved':True,'canonical_lemma':'Πολυάρχας'}
    assert select_lemma(context,['Πολύαρχος','πολύαρχος'],'πολύαρχος')['reason']=='context_outside_inventory_comparator'
    # Neither: abstain rather than export an unattested string.
    context={'lemma':'ἀπεχθίζομαι','resolved':True,'canonical_lemma':'ἀπεχθίζομαι'}
    result=select_lemma(context,['ἀπέσσομαι','ἀπεχθέω'],'ἀπεχθάνομαι')
    assert result['status']=='abstained' and result['reason']=='context_outside_inventory_abstained'
    # A real headword missing from this form's inventory is kept when the morphology attests it.
    known={'Φαλῆς','φαλός','Φαλής'}.__contains__
    context={'lemma':'Φαλῆς','resolved':True,'canonical_lemma':'Φαλῆς'}
    result=select_lemma(context,['Φαλής','φαλός'],'φαλή',known)
    assert (result['selected_lemma'],result['selected_by'],result['reason'])==(
        'Φαλῆς','context','context_outside_inventory_known_headword')
    # An unattested string still abstains; the lookup still takes precedence over the rescue.
    assert select_lemma({'lemma':'ιωρίζω','resolved':True},['διορίζω','διωρίζω'],None,known)['status']=='abstained'
    assert select_lemma({'lemma':'Φαλῆς','resolved':True,'canonical_lemma':'φαλός'},
                        ['Φαλής','φαλός'],None,known)['selected_lemma']=='φαλός'


def test_constraint_boundaries_keep_earlier_behaviour():
    # Grave/acute and case differences count as the same candidate; other accents do not.
    assert select_lemma({'lemma':'ποθὲν','resolved':True},['πίνω','ποθέν'])['reason']=='contextual_disambiguation'
    assert select_lemma({'lemma':'κλήρος','resolved':True,'canonical_lemma':'κλῆρος'},
                        ['κλῆρος','κληρόω'])['selected_lemma']=='κλῆρος'
    # Unseen form (no inventory): the contextual proposal is still accepted.
    assert select_lemma({'lemma':'ταὧς','resolved':True},[],'τώι')['selected_by']=='context'
    # Unresolved context keeps the 1.0.0 GreTa helper.
    assert select_lemma({'resolved':False},['ἐγώ','ἡμός'],'ἐγώ')['reason']=='unresolved_context_helper'


def test_lookup_provenance_remains_explicit():
    context={'lemma':'ὁ','resolved':True,'lemma_source':'lookup_form_upos'}
    result=select_lemma(context,['ὁ','ἕ'],'ἕ')
    assert result['context_source']=='lookup_form_upos'
    assert result['rule_id']=='grc.selection.contextual-primary'
    assert result['rule_version']=='1.3.0'


def test_topk_and_canonical_precedence_and_known_headword_tail_guard():
    lexical=['Κλεώνυμος','Κλεωνύμης']
    # 1. In-inventory canonical_lemma (UPOS-conditioned lookup) takes precedence over top_k.
    with_canon={
        'lemma':'Κλείνομος',
        'resolved':True,
        'lemma_source':'edit_script',
        'canonical_lemma':'Κλεωνύμης',
        'script_log_probability':-0.4,
        'top_k':[
            {'lemma':'Κλείνομος','script_id':10,'log_probability':-0.4},
            {'lemma':'Κλεώνυμος','script_id':11,'log_probability':-1.1},
        ],
    }
    res_canon=select_lemma(with_canon,lexical,'κλεώνυμος')
    assert (res_canon['selected_lemma'],res_canon['selected_by'],res_canon['reason'])==(
        'Κλεωνύμης','context_lookup','context_outside_inventory_lookup')
    assert 'context_topk_candidate' not in res_canon

    # 2. When canonical_lemma is absent or outside lexical, top_k rescues ahead of GreTa.
    no_canon={
        'lemma':'Κλείνομος',
        'resolved':True,
        'lemma_source':'edit_script',
        'canonical_lemma':'Κλείνομος',
        'script_log_probability':-0.4,
        'top_k':[
            {'lemma':'Κλείνομος','script_id':10,'log_probability':-0.4},
            {'lemma':'Κλεώνυμος','script_id':11,'log_probability':-1.1},
            {'lemma':'Κλεωνύμης','script_id':12,'log_probability':-2.5},
        ],
    }
    original=deepcopy(no_canon)
    result=select_lemma(no_canon,lexical,'κλεωνύμης')
    assert (result['selected_lemma'],result['selected_by'],result['reason'])==(
        'Κλεώνυμος','context_topk','context_topk_inventory_disambiguation')
    assert result['context_lemma']=='Κλείνομος'
    assert result['context_topk_candidate']['script_id']==11
    assert no_canon==original
    # Unseen form (no local inventory) and unique local lemma are unaffected by top_k.
    assert select_lemma(no_canon,[],'κλεωνύμης')['selected_by']=='context'
    assert select_lemma(no_canon,['Κλεωνύμης'],'κλεωνύμης')['selected_by']=='diogenes'

    # 3. High-confidence attested headword (Φαλῆς) is NOT overridden by a deep-tail top_k script (gap > 2.5).
    known={'Φαλῆς','φαλός','Φαλής','ἀπέρχομαι','ἄπειμι'}.__contains__
    phales={
        'lemma':'Φαλῆς',
        'resolved':True,
        'lemma_source':'edit_script',
        'canonical_lemma':'Φαλῆς',
        'script_log_probability':-0.32,
        'top_k':[
            {'lemma':'Φαλῆς','script_id':0,'log_probability':-0.32},
            {'lemma':'Φαλής','script_id':701,'log_probability':-5.34},
        ],
    }
    res_phales=select_lemma(phales,['Φαλής','φαλός'],'φαλή',known)
    assert (res_phales['selected_lemma'],res_phales['selected_by'],res_phales['reason'])==(
        'Φαλῆς','context','context_outside_inventory_known_headword')
    assert 'context_topk_candidate' not in res_phales

    # 4. When top-1 is attested but top_k candidate is close (gap <= 2.5, e.g. ἀπιέναι -> ἄπειμι), top_k rescues.
    apienai={
        'lemma':'ἀπέρχομαι',
        'resolved':True,
        'lemma_source':'edit_script',
        'canonical_lemma':'ἀπέρχομαι',
        'script_log_probability':-0.90,
        'top_k':[
            {'lemma':'ἀπέρχομαι','script_id':5,'log_probability':-0.90},
            {'lemma':'ἄπειμι','script_id':1624,'log_probability':-1.98},
        ],
    }
    res_apienai=select_lemma(apienai,['ἄπειμι','ἀφίημι'],'ἀφίημι',known)
    assert (res_apienai['selected_lemma'],res_apienai['selected_by'],res_apienai['reason'])==(
        'ἄπειμι','context_topk','context_topk_inventory_disambiguation')
