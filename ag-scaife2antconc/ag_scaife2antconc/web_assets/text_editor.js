/** Working-text passage picker, modal editor, autosave drafts, and revision undo. */
import { api } from './api.js';
import { selectLatestTextDraft } from './crasis.js';
import { clearEntry, guardDraft, loadEntries, loadEntry, renderDetail, renderRows, updateSave } from './review.js';
import { $, S, bind, count, decision, el, errorMessage, hooks, show, store, stored, toast } from './state.js';

const textTabId = (() => {
  let id = sessionStorage.getItem('ag-text-tab');
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem('ag-text-tab', id);
  }
  return id;
})();

export function textDraftKey(passageId = S.text?.passage?.id, projectId = S.text?.projectId || S.project?.id) {
  return `ag-text-draft:${projectId}:${passageId}:${textTabId}`;
}

export function availableTextDraft() {
  const own = textDraftKey();
  const draft = stored(own);
  if (draft) {
    S.text.availableDraftKey = own;
    return draft;
  }
  const prefix = `ag-text-draft:${S.text.projectId}:${S.text.passage.id}`;
  const candidates = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key === prefix || key.startsWith(prefix + ':')) {
      const value = stored(key);
      if (value) candidates.push({ key, value });
    }
  }
  const latest = selectLatestTextDraft(candidates);
  S.text.availableDraftKey = latest?.key || null;
  return latest?.value || null;
}

export function textChoice() {
  return {
    text: $('text-editor').value,
    reason: $('text-reason').value.trim(),
    text_reviewed: $('text-review-checked').checked,
  };
}

export function markTextDirty() {
  if (!S.text) return;
  const choice = textChoice();
  const passage = S.text.passage;
  S.text.dirty = choice.text !== passage.text || Boolean(choice.reason) || choice.text_reviewed !== Boolean(passage.text_reviewed);
  S.text.draftStored = store(textDraftKey(), S.text.dirty ? { ...choice, revision: S.text.revision, updated_at: Date.now() } : null);
  updateTextSave();
  return S.text.draftStored;
}

export function updateTextSave() {
  const busy = Boolean(S.data?.active_job?.folder === S.project?.folder);
  $('text-save').disabled = !S.text || !S.text.dirty || S.textSaving || S.text.conflict || busy;
  $('text-save').textContent = S.textSaving ? 'Saving…' : 'Save Text';
  $('text-cancel').disabled = S.textSaving;
  $('text-editor').disabled = S.textSaving;
  $('text-reason').disabled = S.textSaving;
  $('text-review-checked').disabled = S.textSaving;
  document.querySelectorAll('[data-text-undo]').forEach(button => {
    button.disabled = S.textSaving || busy || Boolean(S.text?.conflict);
  });
  $('text-draft-status').textContent = S.text?.dirty
    ? (S.text.draftStored ? 'Draft kept in this browser. Choose Save Text to save it in the project.'
      : 'Browser storage is unavailable. Save Text before closing.')
    : 'Text and revision history are saved in the project.';
}

function formatRevisionTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value || '') : date.toLocaleString('en-US');
}

function renderTextHistory(passage) {
  const box = $('text-history');
  box.replaceChildren();
  const history = (passage.history || []).filter(
    item => ['text', 'undo'].includes(item.kind) && typeof item.old_text === 'string' && typeof item.new_text === 'string'
  );
  $('text-history-count').textContent = history.length ? `(${count(history.length)})` : '';
  if (!history.length) {
    box.append(el('p', 'No text revisions have been saved yet.', 'help'));
    return;
  }
  for (const item of [...history].reverse()) {
    const card = el('article', null, 'text-history-entry');
    card.append(el('p', `${formatRevisionTime(item.time || item.timestamp)}${item.kind === 'undo' ? ' · undo revision' : ''}`, 'text-history-date'));
    const before = el('div', null, 'history-reading');
    const after = el('div', null, 'history-reading');
    before.append(el('span', 'Before', 'history-label'), el('div', item.old_text, 'greek'));
    after.append(el('span', 'After', 'history-label'), el('div', item.new_text, 'greek'));
    card.append(before, after);
    if (item.reason) card.append(el('p', item.reason, 'help'));
    if (item.id && ['text', 'undo'].includes(item.kind)) {
      const undo = el('button', 'Undo this revision', 'secondary');
      undo.dataset.textUndo = item.id;
      undo.disabled = S.textSaving;
      bind(undo, 'click', () => undoTextRevision(item));
      card.append(undo);
    }
    box.append(card);
  }
}

export function renderTextPassage(passage, restoreDraft = true) {
  S.text.passage = passage;
  S.text.revision = passage.revision || S.project.revision;
  S.text.dirty = false;
  S.text.conflict = false;
  $('text-location').textContent = `${passage.work} · ${passage.citation}`;
  $('text-original').textContent = passage.original_text;
  $('text-latest').textContent = passage.text;
  $('text-editor').value = passage.text;
  $('text-reason').value = '';
  $('text-review-checked').checked = Boolean(passage.text_reviewed);
  $('text-saved-status').textContent = passage.text_reviewed ? 'Text reviewed' : 'Needs text review';
  show('text-conflict', false);
  show('text-old-draft', false);
  show('text-latest-comparison', false);
  renderTextHistory(passage);
  const ownDraft = stored(textDraftKey());
  const draft = availableTextDraft();
  if (restoreDraft && draft) {
    if (ownDraft && draft.revision === S.text.revision) restoreTextDraft(draft);
    else show('text-old-draft');
  }
  updateTextSave();
}

export function restoreTextDraft(draft) {
  if (!S.text || !draft || typeof draft.text !== 'string') return;
  $('text-editor').value = draft.text;
  $('text-reason').value = draft.reason || '';
  $('text-review-checked').checked = Boolean(draft.text_reviewed);
  if (draft.revision !== S.text.revision) {
    show('text-latest-comparison');
    $('text-latest-comparison').open = true;
  }
  show('text-old-draft', false);
  markTextDirty();
}

export async function openTextEditor() {
  if (!S.project || !S.entry || S.textOpening || !await guardDraft()) return;
  const projectId = S.project.id;
  const key = S.entry.key;
  S.textOpening = true;
  updateSave();
  try {
    const result = await api(`/api/projects/${projectId}/passage/${encodeURIComponent(key)}`);
    if (S.project?.id !== projectId || S.entry?.key !== key) return;
    S.project = result.project || S.project;
    // Preparing an editable project can change the revision without changing the word.
    const refreshed = await api(`/api/projects/${projectId}/entry/${encodeURIComponent(key)}`);
    renderDetail(refreshed, false);
    S.text = { projectId, entryKey: key, passage: result.passage, dirty: false, conflict: false };
    renderTextPassage(result.passage);
    hooks.renderProject();
    $('text-dialog').showModal();
    $('text-editor').focus();
  } finally {
    S.textOpening = false;
    updateSave();
  }
}

export function renderPassagePicker() {
  const query = $('passage-search').value.trim().toLocaleLowerCase('el-GR');
  const select = $('passage-select');
  const matches = S.passages.filter(p => `${p.work} ${p.citation} ${p.text}`.toLocaleLowerCase('el-GR').includes(query));
  select.replaceChildren();
  const multiWork = S.passages.some(p => p.work !== S.passages[0]?.work);
  for (const passage of matches.slice(0, 300)) {
    const option = el('option', `${passage.citation} · ${passage.text || '[empty passage]'}${multiWork ? ' · ' + passage.work : ''}`);
    option.value = passage.id;
    select.append(option);
  }
  if (select.options.length) select.selectedIndex = 0;
  $('passage-count').textContent = matches.length > 300
    ? `${count(matches.length)} passages · showing first 300. Narrow your search.`
    : `${count(matches.length)} passages`;
  $('passage-open').disabled = !select.options.length;
}

export async function openPassagePicker() {
  if (!S.project || S.textOpening || !await guardDraft()) return;
  const projectId = S.project.id;
  const key = S.entry?.key;
  S.textOpening = true;
  updateSave();
  try {
    const result = await api(`/api/projects/${projectId}/passages`);
    if (S.project?.id !== projectId) return;
    S.project = result.project || S.project;
    S.passages = result.passages;
    if (key) {
      const refreshed = await api(`/api/projects/${projectId}/entry/${encodeURIComponent(key)}`);
      renderDetail(refreshed, false);
    }
    hooks.renderProject();
    $('passage-search').value = '';
    renderPassagePicker();
    $('passage-dialog').showModal();
    $('passage-search').focus();
  } finally {
    S.textOpening = false;
    updateSave();
  }
}

export async function openSelectedPassage() {
  const id = $('passage-select').value;
  if (!id || !S.project) return;
  const projectId = S.project.id;
  S.textOpening = true;
  updateSave();
  $('passage-open').disabled = true;
  try {
    const result = await api(`/api/projects/${projectId}/passages/${encodeURIComponent(id)}`);
    S.project = result.project || S.project;
    const samePassage = S.entry?.work === result.passage.work && S.entry?.citation === result.passage.citation;
    S.text = { projectId, entryKey: samePassage ? S.entry.key : null, passage: result.passage, dirty: false, conflict: false };
    renderTextPassage(result.passage);
    hooks.renderProject();
    $('passage-dialog').close();
    $('text-dialog').showModal();
    $('text-editor').focus();
  } finally {
    S.textOpening = false;
    $('passage-open').disabled = false;
    updateSave();
  }
}

export function closeTextEditor() {
  if (S.textSaving) return false;
  if (S.text?.dirty && !markTextDirty()) return false;
  const savedDraft = Boolean(S.text?.dirty);
  $('text-dialog').close();
  S.text = null;
  updateSave();
  if (savedDraft) toast('Text draft saved in browser storage.');
  return true;
}

export async function reloadTextPassage() {
  if (!S.text || S.textSaving) return;
  const result = await api(`/api/projects/${S.text.projectId}/passages/${encodeURIComponent(S.text.passage.id)}`);
  S.project = result.project || S.project;
  renderTextPassage(result.passage, false);
  show('text-old-draft', Boolean(availableTextDraft()));
  hooks.renderProject();
}

async function refreshAfterText(result) {
  const projectId = S.text.projectId;
  const oldKey = S.text.entryKey;
  const passage = result.passage;
  S.project = result.project || await api(`/api/projects/${projectId}`);
  S.entry = null;
  S.fields = [];
  S.dirty = false;
  S.restoreKey = null;
  S.filter = 'all';
  S.query = '';
  S.offset = 0;
  $('entry-filter').value = 'all';
  $('entry-search').value = '';
  await loadEntries({ around: result.entry?.key || oldKey });
  if (result.entry) {
    renderDetail(result.entry, false);
    S.text.entryKey = result.entry.key;
  } else {
    let current = null;
    if (oldKey) {
      try {
        current = await api(`/api/projects/${projectId}/entry/${encodeURIComponent(oldKey)}`);
      } catch (error) {
        if (error.status !== 404) throw error;
      }
    }
    const nearby = S.rows.find(r => r.work === passage.work && r.citation === passage.citation) || S.rows[0];
    if (current) {
      renderDetail(current, false);
    } else if (nearby) {
      await loadEntry(nearby.key, false);
      S.text.entryKey = nearby.key;
    } else {
      clearEntry('Text saved. No words match this filter.');
    }
  }
  renderRows();
  await hooks.state();
  hooks.renderProject();
}

export async function saveText() {
  if (!S.text || !S.text.dirty || S.textSaving || S.text.conflict) return false;
  const projectId = S.text.projectId;
  const passageId = S.text.passage.id;
  const draftKeyValue = textDraftKey();
  const body = { passage_id: passageId, revision: S.text.revision, ...textChoice() };
  S.textSaving = true;
  updateTextSave();
  try {
    const result = await api(`/api/projects/${projectId}/text`, body);
    store(draftKeyValue, null);
    renderTextPassage(result.passage, false);
    await refreshAfterText(result);
    toast('Text saved. Lemmata are reviewed separately.');
    return true;
  } catch (error) {
    if (error.code === 'revision_conflict') {
      S.text.conflict = true;
      show('text-conflict');
    }
    errorMessage(error);
    return false;
  } finally {
    S.textSaving = false;
    updateTextSave();
  }
}

export async function undoTextRevision(item) {
  if (!S.text || S.textSaving) return;
  const answer = await decision(
    'Undo text revision',
    `Restore the text prior to this change:\n\n${item.old_text}\n\nThis undo will be recorded as a new revision.${S.text.dirty ? ' Your current draft will remain available in browser storage.' : ''}`,
    [
      { label: 'Cancel', value: null },
      { label: 'Undo Revision', value: 'undo', primary: true },
    ]
  );
  if (answer !== 'undo') return;
  const draft = S.text.dirty ? { ...textChoice(), revision: S.text.revision, updated_at: Date.now() } : null;
  const projectId = S.text.projectId;
  const draftKeyValue = textDraftKey();
  S.textSaving = true;
  updateTextSave();
  try {
    const result = await api(`/api/projects/${projectId}/text/undo`, {
      passage_id: S.text.passage.id,
      revision: S.text.revision,
      revision_id: item.id,
    });
    if (draft) store(draftKeyValue, draft);
    renderTextPassage(result.passage, false);
    show('text-old-draft', Boolean(stored(draftKeyValue)));
    await refreshAfterText(result);
    toast('Revision undone. History was preserved.');
  } catch (error) {
    if (error.code === 'revision_conflict') {
      S.text.conflict = true;
      show('text-conflict');
    }
    errorMessage(error);
  } finally {
    S.textSaving = false;
    updateTextSave();
  }
}
