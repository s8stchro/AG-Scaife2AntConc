/** Browser workbench entry point: registers hooks, binds events/shortcuts, and starts polling. */
import { api, choose } from './api.js';
import { connectWindow, disconnectWindow, quitWindow } from './windows.js';
import { dismissJob, openJobResults, poll, watchJob } from './jobs.js';
import {
  addInputs, analyseWorkingText, createEditableProject, exportChoices, go,
  importOga, openExisting, preview, renderMode, renderProject,
  setProject, standardizeHeadwords, startRun, state, uploadFiles,
} from './projects.js';
import { download } from './results.js';
import {
  applyCandidateShortcut, guardDraft, loadEntries, loadEntry, markDirty,
  restoreDraft, saveEntry,
} from './review.js';
import {
  $, S, bind, decision, draftKey, errorMessage, hooks,
  restoreReviewLocation, show, store, stored, toast,
} from './state.js';
import {
  availableTextDraft, closeTextEditor, markTextDirty, openPassagePicker,
  openSelectedPassage, openTextEditor, reloadTextPassage, renderPassagePicker,
  restoreTextDraft, saveText, textDraftKey, updateTextSave,
} from './text_editor.js';

let searchTimer;

Object.assign(hooks, {
  guardDraft,
  state,
  setProject,
  renderProject,
  exportChoices,
  updateTextSave,
});

async function init() {
  const launch = new URLSearchParams(location.hash.slice(1)).get('launch');
  if (launch) {
    await api('/api/session', {}, { headers: { Authorization: 'Bearer ' + launch } });
    history.replaceState(null, '', location.pathname + location.search);
  }
  await state(true);
  await connectWindow();
  const rawView = sessionStorage.getItem('ag-view') || 'home';
  const wanted = ['home', 'review', 'results'].includes(rawView) ? rawView : 'home';
  if (S.data.last_project) {
    try {
      S.project = await api(`/api/projects/${S.data.last_project}`);
      restoreReviewLocation();
      renderProject();
    } catch (error) {
      errorMessage(error);
    }
  }
  if (S.data.active_job) {
    watchJob(S.data.active_job);
  } else {
    const job = S.data.jobs[0];
    if (job && job.id !== sessionStorage.getItem('ag-dismissed-job')) {
      watchJob(job);
      S.handled.add(job.id);
    }
  }
  await go(S.project ? wanted : 'home', false);
  document.querySelector('.shell').inert = false;
  document.querySelector('.masthead').inert = false;
  $('quit-button').disabled = false;
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) poll();
  });
  S.pollTimer = setInterval(poll, 1300);
  await poll();
}

document.querySelectorAll('[data-view]').forEach(node => {
  bind(node, 'click', async e => {
    e.preventDefault();
    await go(node.dataset.view);
  });
});

document.querySelectorAll('input[name=mode]').forEach(node => {
  bind(node, 'change', () => {
    S.mode = node.value;
    sessionStorage.setItem('ag-mode', S.mode);
    renderMode();
  });
});

document.querySelectorAll('input[name=scope]').forEach(node => bind(node, 'change', markDirty));

bind('upload-button', 'click', () => $('xml-files').click());
bind('xml-files', 'change', e => uploadFiles([...e.target.files]));
bind('native-files-button', 'click', async () => {
  const items = await choose('files');
  if (items?.length) await addInputs(items);
});
bind('oga-import', 'click', importOga);
bind('oga-url', 'keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    return importOga();
  }
});
bind('drop-zone', 'dragover', e => {
  e.preventDefault();
  $('drop-zone').classList.add('drag-over');
});
bind('drop-zone', 'dragleave', () => $('drop-zone').classList.remove('drag-over'));
bind('drop-zone', 'drop', e => {
  e.preventDefault();
  $('drop-zone').classList.remove('drag-over');
  return uploadFiles([...e.dataTransfer.files]);
});
bind('output-browse', 'click', async () => {
  const folder = await choose('folder');
  if (folder) $('output-folder').value = folder;
});
bind('corrections-browse', 'click', async () => {
  const file = await choose('corrections');
  if (file) $('corrections-path').value = file;
});
bind('new-run', 'click', startRun);
bind('refresh-preview', 'click', preview);
bind('new-edit-text', 'click', createEditableProject);
bind('working-analyse', 'click', analyseWorkingText);
bind('working-headwords', 'click', standardizeHeadwords);
bind('edit-text', 'click', openTextEditor);
bind('project-text', 'click', openPassagePicker);
bind('passage-search', 'input', renderPassagePicker);
bind('passage-select', 'dblclick', openSelectedPassage);
bind('passage-open', 'click', openSelectedPassage);
bind('passage-cancel', 'click', () => $('passage-dialog').close());
bind('text-editor', 'input', () => {
  $('text-review-checked').checked = false;
  markTextDirty();
});
bind('text-reason', 'input', markTextDirty);
bind('text-review-checked', 'change', markTextDirty);
bind('text-save', 'click', saveText);
bind('text-cancel', 'click', closeTextEditor);
bind('text-reload', 'click', reloadTextPassage);
bind('text-restore-draft', 'click', () => restoreTextDraft(availableTextDraft()));
bind('text-discard-draft', 'click', () => {
  const key = S.text?.availableDraftKey || textDraftKey();
  store(key, null);
  show('text-old-draft', false);
});
$('text-dialog').addEventListener('cancel', event => {
  event.preventDefault();
  closeTextEditor();
});
bind('open-project-button', 'click', async () => {
  if (await guardDraft()) $('folder-dialog').showModal();
});
bind('existing-browse', 'click', async () => {
  const folder = await choose('folder');
  if (folder) $('existing-folder').value = folder;
});
bind('existing-open', 'click', openExisting);
bind('existing-folder', 'keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    return openExisting();
  }
});
bind('entry-filter', 'change', async () => {
  if (!await guardDraft()) {
    $('entry-filter').value = S.filter;
    return;
  }
  S.filter = $('entry-filter').value;
  S.offset = 0;
  S.entry = null;
  await loadEntries({ select: true });
});
bind('entry-search', 'input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    try {
      if (!await guardDraft()) {
        $('entry-search').value = S.query;
        return;
      }
      S.query = $('entry-search').value;
      S.offset = 0;
      S.entry = null;
      await loadEntries({ select: true });
    } catch (error) {
      errorMessage(error);
    }
  }, 300);
});
bind('previous-page', 'click', async () => {
  if (!await guardDraft()) return;
  S.offset = Math.max(0, S.offset - 100);
  await loadEntries({ select: true });
});
bind('next-page', 'click', async () => {
  if (!await guardDraft()) return;
  S.offset += 100;
  await loadEntries({ select: true });
});
bind('reload-entry', 'click', async () => {
  if (!await guardDraft()) return;
  const key = S.entry?.key;
  await loadEntries({ select: !key, around: key });
  if (key) await loadEntry(key, false);
});
bind('restore-old-draft', 'click', async () => {
  const answer = await decision(
    'Earlier draft found',
    'Saved choices changed since this draft was written. Load it for comparison? Loading does not save it.',
    [
      { label: 'Cancel', value: null },
      { label: 'Load Draft', value: 'restore', primary: true },
    ]
  );
  if (answer === 'restore') restoreDraft(stored(draftKey()));
});
bind('save-entry', 'click', () => saveEntry());
bind('review-export', 'click', exportChoices);
bind('cancel-job', 'click', async () => {
  if (S.job) {
    await api(`/api/jobs/${S.job.id}/cancel`, {});
    toast('Cancellation requested at the next safe checkpoint.');
  }
});
bind('open-job-results', 'click', openJobResults);
bind('dismiss-job', 'click', dismissJob);
document.querySelectorAll('[data-download]').forEach(node => {
  bind(node, 'click', () => download(node.dataset.download));
});
bind('open-result-folder', 'click', () => S.project && api(`/api/projects/${S.project.id}/open-folder`, {}));
bind('settings-button', 'click', () => {
  $('setting-diogenes').value = S.data.preferences.diogenes || '';
  $('setting-greta').value = S.data.preferences.greta_model || '';
  $('setting-context').value = S.data.preferences.context_model || '';
  $('settings-dialog').showModal();
});
document.querySelectorAll('[data-pick-setting]').forEach(node => {
  bind(node, 'click', async () => {
    const folder = await choose('folder');
    if (folder) $(node.dataset.pickSetting).value = folder;
  });
});
bind('settings-save', 'click', async () => {
  await api('/api/preferences', {
    diogenes: $('setting-diogenes').value.trim() || null,
    greta_model: $('setting-greta').value.trim() || null,
    context_model: $('setting-context').value.trim() || null,
  });
  $('settings-dialog').close();
  await state();
  toast('Settings saved.');
});
async function quitWorkbench() {
  if (S.quitting || S.closed) return;
  if (S.saving || S.textSaving || S.textOpening) {
    toast('Please wait for the current edit to finish.');
    return;
  }
  S.quitting = true;
  clearTimeout(searchTimer);
  const controls = document.querySelectorAll('.masthead, .shell, #text-dialog');
  try {
    controls.forEach(node => { node.inert = true; });
    if (S.dirty || S.text?.dirty) {
      const answer = await decision(
        'Quit with an unsaved edit?',
        'Save the edit in the project, or keep a draft to resume in this browser.',
        [
          { label: 'Stay', value: null },
          { label: 'Keep draft & quit', value: 'draft' },
          { label: 'Save & quit', value: 'save', primary: true },
        ]
      );
      if (!answer) return;
      if (answer === 'save' && !(S.text ? await saveText() : await saveEntry(false))) return;
      if (answer === 'draft' && !(S.text ? markTextDirty() : markDirty())) return;
    }
    const result = await quitWindow();
    S.closed = true;
    clearInterval(S.pollTimer);
    document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
    document.querySelector('.masthead').hidden = true;
    document.querySelector('.shell').hidden = true;
    $('closed-message').textContent = {
      other_windows: 'Your other workbench windows are still open.',
      finishing_job: 'Your job is finishing in the background. Reopen the workbench to check its progress.',
      closed: 'To return, open Start-Workbench.bat or launch AG-Scaife2AntConc from the Start menu.',
    }[result.status];
    show('closed-view');
    $('closed-title').focus();
    window.close();
  } finally {
    S.quitting = false;
    controls.forEach(node => { node.inert = false; });
  }
}

bind('quit-button', 'click', quitWorkbench);
bind('text-quit', 'click', quitWorkbench);

window.addEventListener('beforeunload', e => {
  if (!S.closed && (S.dirty || S.text?.dirty || S.saving || S.textSaving)) {
    e.preventDefault();
    e.returnValue = '';
  }
});

window.addEventListener('keydown', e => {
  if (S.closed || S.quitting || (document.querySelector('dialog[open]') && !$('text-dialog').open) ||
      $('decision-dialog').open) return;
  if (e.ctrlKey && e.key === 'Enter' && !e.repeat && !e.isComposing) {
    if (S.text && $('text-dialog').open) {
      e.preventDefault();
      saveText().catch(errorMessage);
    } else if (S.view === 'review') {
      e.preventDefault();
      saveEntry().catch(errorMessage);
    }
  } else if (e.altKey && !e.ctrlKey && !e.metaKey && S.view === 'review' && !S.text) {
    if (e.key >= '1' && e.key <= '8') {
      e.preventDefault();
      applyCandidateShortcut(Number(e.key) - 1);
    } else if (e.key.toLowerCase() === 'e') {
      e.preventDefault();
      openTextEditor().catch(errorMessage);
    }
  }
});

init().catch(error => {
  disconnectWindow().catch(errorMessage);
  document.querySelector('.masthead').hidden = true;
  document.querySelector('.shell').hidden = true;
  $('closed-title').textContent = 'The workbench could not open.';
  $('closed-message').textContent = error.message;
  show('closed-view');
  $('closed-title').focus();
});
