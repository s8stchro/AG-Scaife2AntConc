"""Exercise startup without cached dependency bytecode, as on a fresh installation."""
import os
from pathlib import Path
import subprocess
import sys


def test_beta_code_warning_is_scoped_and_conversion_still_works(tmp_path):
    source = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(source))
    check = '''
import warnings
original_filters = warnings.filters[:]
from ag_diogenes_spacy_txt2xml.diogenes import greek
assert greek('lo/gos') == 'λόγος'
assert warnings.filters == original_filters
try:
    warnings.warn('unrelated warning', SyntaxWarning)
except SyntaxWarning:
    pass
else:
    raise AssertionError('Unrelated warnings must remain enabled')
'''
    result = subprocess.run([sys.executable, '-X', 'utf8', '-X', f'pycache_prefix={tmp_path / "bytecode"}',
                             '-B', '-W', 'error::SyntaxWarning', '-c', check],
                            env=env, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    assert not result.stderr
