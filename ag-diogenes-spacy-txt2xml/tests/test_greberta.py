"""Asset integrity and complete-token coverage of the pinned contextual adapter."""
import hashlib
from io import BytesIO
import os
import tarfile

import pytest

from ag_diogenes_spacy_txt2xml import greberta


def test_preparation_preserves_foreign_folders_and_rejects_unverified_archives(tmp_path):
    target=tmp_path/'foreign'; target.mkdir(); protected=target/'notes.txt'; protected.write_text('keep')
    with pytest.raises(greberta.JointError): greberta.prepare(target)
    assert protected.read_text()=='keep'
    archive=tmp_path/'bad.tar.gz'; archive.write_bytes(b'incomplete or wrong download')
    with pytest.raises(greberta.JointError,match='SHA-256'):
        greberta.prepare(tmp_path/'model',archive)
    assert not (tmp_path/'model/model-info.json').exists()


def test_preparation_receipt_detects_corruption_and_rejects_duplicate_members(tmp_path,monkeypatch):
    archive=tmp_path/'fixture.tar.gz'
    members={name:(b'{}' if name.endswith('.json') else b'test model') for name in greberta.FILES}
    with tarfile.open(archive,'w:gz') as tar:
        for name,content in members.items():
            info=tarfile.TarInfo(name); info.size=len(content); tar.addfile(info,BytesIO(content))
    monkeypatch.setattr(greberta,'SHA256',hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(greberta,'MANIFEST_SHA256',hashlib.sha256(members['manifest.json']).hexdigest())
    target=tmp_path/'model'
    greberta.prepare(target,archive)
    assert greberta.verified(target)
    (target/'model.onnx').write_bytes(b'corrupted')
    assert not greberta.verified(target)
    with tarfile.open(archive,'w:gz') as tar:
        for prefix in ('one/','two/'):
            info=tarfile.TarInfo(prefix+'model.onnx'); info.size=1; tar.addfile(info,BytesIO(b'x'))
    monkeypatch.setattr(greberta,'SHA256',hashlib.sha256(archive.read_bytes()).hexdigest())
    with pytest.raises(greberta.JointError): greberta.prepare(tmp_path/'duplicate',archive)


@pytest.mark.skipif(os.environ.get('AG_RUN_MODEL_TESTS')!='1',reason='opt-in GreBERTa joint model check')
def test_real_context_predictions_keep_every_token_above_subword_limit():
    model=greberta.Joint()
    sentence=['ἡμῶν','δὲ','προσεῖπον','τί','ἐστιν',';']
    result=next(model.pipe([sentence]))
    assert len(result)==len(sentence)
    assert result[0].lemma=='ἐγώ' and result[2].pos=='VERB' and result[3].lemma=='τίς'
    assert result[2].canonical_lemma=='προσλέγω'
    words=['ἀνθρωποσχηματίζομαι']*180
    long=next(model.pipe([words]))
    assert len(long)==len(words)
    assert all(p.context_scope=='overlapping subword-limit window' for p in long)
