"""The persistent inference cache: exact identity, round trip, and failure without harm."""
from ag_diogenes_spacy_txt2xml.inference_cache import Cache, text_key


def test_round_trip_and_identity_separation(tmp_path):
    first = Cache('greberta', {'model': 'a', 'adapter': '1'}, tmp_path)
    key = text_key(['ὅσα', 'δὴ'])
    first.put_many({key: [{'lemma': 'ὅσος', 'script_log_probability': -0.1}]})
    first.close()
    again = Cache('greberta', {'model': 'a', 'adapter': '1'}, tmp_path)
    assert again.get_many([key, text_key(['x'])]) == {key: [{'lemma': 'ὅσος', 'script_log_probability': -0.1}]}
    assert (again.hits, again.misses) == (1, 1)
    # Another model or adapter version never sees these values.
    assert Cache('greberta', {'model': 'b', 'adapter': '1'}, tmp_path).get_many([key]) == {}
    assert Cache('greberta', {'model': 'a', 'adapter': '2'}, tmp_path).get_many([key]) == {}


def test_disabled_and_broken_caches_only_mean_recomputation(tmp_path, monkeypatch):
    off = Cache('greta', {'model': 'a'}, False)
    off.put_many({'k': 1})
    assert not off.enabled and off.get_many(['k']) == {}
    monkeypatch.setenv('AG_INFERENCE_CACHE', 'off')
    assert not Cache('greta', {'model': 'a'}).enabled
    monkeypatch.setenv('AG_INFERENCE_CACHE', str(tmp_path / 'chosen'))
    assert Cache('greta', {'model': 'a'}).enabled and (tmp_path / 'chosen').is_dir()
    blocked = tmp_path / 'file-not-folder'
    blocked.write_text('x')
    broken = Cache('greta', {'model': 'a'}, blocked)
    assert not broken.enabled and broken.get_many(['k']) == {}
    corrupt = Cache('greta', {'model': 'c'}, tmp_path)
    path = corrupt.path
    corrupt.close()
    path.write_bytes(b'not a database')
    assert Cache('greta', {'model': 'c'}, tmp_path).get_many(['k']) == {}
