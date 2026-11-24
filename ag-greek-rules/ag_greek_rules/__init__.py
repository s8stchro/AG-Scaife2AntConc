"""Independent rule assessments; no model loading, networking or automatic rewriting."""
from .api import Assessment, assess_pronoun, catalog
from .deictic import DeicticAssessment, assess_deictic
from .grammar import PosAssessment, assess_pos
from .selection import select_lemma
from .headwords import HeadwordAssessment, assess_headword, headword_mappings
from .proper_noun import ProperNounAssessment, assess_proper_noun
from .generated import GeneratedLemmaAssessment, assess_generated_lemma, headword_problems

__version__ = '0.8.1'
__all__ = ['Assessment', 'assess_pronoun', 'catalog', 'DeicticAssessment', 'assess_deictic', 'PosAssessment', 'assess_pos', 'select_lemma', 'HeadwordAssessment', 'assess_headword', 'headword_mappings', 'ProperNounAssessment', 'assess_proper_noun', 'GeneratedLemmaAssessment', 'assess_generated_lemma', 'headword_problems']
