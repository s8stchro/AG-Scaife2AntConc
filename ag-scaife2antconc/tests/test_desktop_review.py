"""Exercise saving and selection through the real Tk review controls."""
import tkinter as tk

import pytest

from ag_scaife2antconc import desktop, gui
from ag_scaife2antconc.workspace import identity
from .test_workspace import corpus


@pytest.fixture
def app(corpus, tmp_path, monkeypatch):
    monkeypatch.setattr(gui, 'SETTINGS', str(tmp_path / 'settings.json'))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('Tk review workflow requires a desktop')
    root.withdraw()
    instance = gui.App(root, [])
    instance.load_output(corpus[1])
    instance.filter.set('Όλες')
    root.update()
    yield instance
    root.destroy()


@pytest.mark.parametrize('filter_name', ['Όλες', 'Αμφίβολες'])
def test_confirmation_advances_and_stops_at_end(app, filter_name):
    app.filter.set(filter_name)
    app.root.update()
    records = app.workspace.records
    assert app.selected is records[0]
    for i in range(3):
        assert app.save_choice()
        app.root.update()
        assert app.workspace.checked(records[i])
        if i < 2:
            assert app.selected is records[i + 1]
            assert not app.workspace.checked(records[i + 1])
    assert app.selected is (records[-1] if filter_name == 'Όλες' else None)
    assert 'Δεν υπάρχει επόμενη' in app.status.get()


def test_reaching_end_does_not_wrap_to_earlier_unreviewed_entries(app):
    app.filter.set('Αμφίβολες')
    app.root.update()
    app.words.selection_set('2')
    app.root.update()
    assert app.save_choice()
    app.root.update()
    assert app.selected is None
    assert len(app.visible_records) == 2
    assert not app.workspace.checked(app.workspace.records[0])


def test_failed_save_keeps_entry_and_draft(app, monkeypatch):
    first = app.selected
    before = app.workspace.sheet.read_bytes()
    app.part_vars[0][0].set('ἕ')
    def locked(_choices):
        raise PermissionError('sheet is open')
    monkeypatch.setattr(app.workspace, '_write', locked)
    errors = []
    monkeypatch.setattr(desktop.messagebox, 'showerror', lambda *args: errors.append(args))
    assert not app.save_choice()
    app.root.update()
    assert errors and app.selected is first and app.dirty
    assert app.workspace.sheet.read_bytes() == before
    assert not app.workspace.checked(first)


def test_quit_during_work_waits_for_completion_after_one_prompt(app, monkeypatch):
    prompts = []
    monkeypatch.setattr(desktop.messagebox, 'askokcancel', lambda *args, **kw: prompts.append(True) or True)
    closed = []
    with monkeypatch.context() as patch:
        patch.setattr(app.root, 'destroy', lambda: closed.append(True))
        app.busy = True
        app.close()
        app.close()
        assert app.closing and prompts == [True] and not closed
        assert not app.cancel_event.is_set()
        app.messages.put(('done', None))
        app.poll()
        assert closed == [True]


def test_discard_then_return_to_review_shows_saved_lemma(app, monkeypatch):
    original = app.part_vars[0][0].get()
    app.part_vars[0][0].set('ἕ')
    monkeypatch.setattr(desktop.messagebox, 'askyesnocancel', lambda *args: False)
    app.show('results')
    app.show('review')
    assert app.part_vars[0][0].get() == original and not app.dirty


def test_saving_during_manual_navigation_keeps_requested_destination(app, monkeypatch):
    records = app.workspace.records
    app.part_vars[0][0].set('ἕ')
    monkeypatch.setattr(desktop.messagebox, 'askyesnocancel', lambda *args: True)
    app.words.selection_set('2')
    app.root.update()
    assert app.selected is records[2]
    assert app.workspace.values(records[0])[0] == 'ἕ'
    assert not app.workspace.checked(records[1])


def test_advancing_stays_inside_search_results(app):
    app.query.set('2')
    app.root.update()
    assert app.selected is app.workspace.records[2]
    assert app.save_choice()
    app.root.update()
    assert app.selected is app.workspace.records[2]
    assert len(app.visible_records) == 1
    assert not app.workspace.checked(app.workspace.records[0])


def test_secondary_warnings_have_their_own_view_and_keep_saved_choices(app):
    first, second, third = app.workspace.records
    app.workspace.priorities[identity(first)] = []
    app.filter.set('Αμφίβολες')
    app.root.update()
    assert app.selected is second
    assert app.save_choice()
    app.root.update()
    assert app.selected is third
    app.filter.set('Λοιπές επισημάνσεις')
    app.root.update()
    assert app.selected is first and len(app.visible_records) == 1
    assert app.save_choice()
    app.root.update()
    app.filter.set('Αποθηκευμένες')
    app.root.update()
    assert {identity(r) for r in app.visible_records.values()} == {identity(first), identity(second)}
