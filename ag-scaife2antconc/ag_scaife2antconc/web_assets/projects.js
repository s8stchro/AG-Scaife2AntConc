/** Navigation (3 streamlined views), project lifecycle, XML/OGA input management, inline cleansing preview, and job triggers. */
import { api } from './api.js';
import { renderJob, watchJob } from './jobs.js';
import { renderResults } from './results.js';
import { guardDraft, loadEntries, updateSave } from './review.js';
import { $, S, bind, count, decision, el, hooks, restoreReviewLocation, show, toast } from './state.js';

export function inputKind(item) {
  return item.kind === 'oga' ? 'oga' : 'xml';
}

export function selectedOga() {
  return (S.data?.inputs || []).some(item => S.selected.has(item.id) && inputKind(item) === 'oga');
}

function selectInput(item) {
  for (const other of S.data.inputs) {
    if (inputKind(other) !== inputKind(item)) S.selected.delete(other.id);
  }
  S.selected.add(item.id);
}

function updateInputs() {
  sessionStorage.setItem('ag-inputs', JSON.stringify([...S.selected]));
  renderInputs();
  renderMode();
}

export async function addInputs(items) {
  await state();
  items.forEach(selectInput);
  updateInputs();
}

export function renderInputs() {
  const list = $('input-list');
  list.replaceChildren();
  const staged = [...(S.data?.inputs || [])].filter(item => S.selected.has(item.id)).reverse();
  if (!staged.length) return;
  const header = el('div', null, 'input-list-head');
  const clearBtn = el('button', 'Clear', 'quiet');
  clearBtn.type = 'button';
  bind(clearBtn, 'click', async () => {
    await api('/api/inputs/remove', { all: true });
    S.selected.clear();
    await state();
    updateInputs();
  });
  header.append(el('span', `Selected for this run (${count(staged.length)})`, 'muted'), clearBtn);
  list.append(header);
  for (const item of staged) {
    const row = el('div', null, 'input-row');
    const label = el('span');
    label.append(el('strong', item.title), el('small', `${inputKind(item) === 'oga' ? 'OGA' : 'XML'} · ${item.name}`));
    const remove = el('button', '×', 'quiet input-remove');
    remove.type = 'button';
    remove.title = 'Remove from this run';
    remove.setAttribute('aria-label', `Remove ${item.name}`);
    bind(remove, 'click', async () => {
      await api('/api/inputs/remove', { id: item.id });
      S.selected.delete(item.id);
      await state();
      updateInputs();
    });
    row.append(label, el('span', `${count(item.units)} units`, 'units'), remove);
    list.append(row);
  }
}

export function renderMode() {
  document.querySelectorAll('input[name=mode]').forEach(node => {
    node.checked = node.value === S.mode;
  });
  const quick = S.mode === 'quick';
  const isOga = selectedOga();
  show('advanced-options', !isOga && !quick);
  if (isOga) {
    $('new-run').textContent = quick ? 'Import & Export →' : 'Import & Review →';
    $('mode-description').textContent = 'Imports token boundaries and lemmata from OGA into a new results folder.';
  } else {
    $('new-run').textContent = quick ? 'Quick Export →' : 'Analyze & Review →';
    $('mode-description').textContent = quick
      ? 'Runs with recommended cleansing defaults and opens Export when finished.'
      : 'Analyzes with Diogenes, GreBERTa joint, and GreTa, then opens Review & Edit.';
  }
}

function makePreparation() {
  const container = $('cleaning-options');
  if (container.children.length) return;
  for (const [step, title] of Object.entries(S.data.steps || {})) {
    const card = el('div', null, 'cleaning-item');
    const label = el('label', null, 'check-line');
    const box = el('input');
    box.type = 'checkbox';
    box.checked = true;
    box.dataset.step = step;
    label.append(box, el('span', title));
    const examples = el('div', 'Click “Preview cleansing examples” to inspect selected XML.', 'examples');
    examples.dataset.example = step;
    card.append(label, examples);
    container.append(card);
  }
}

export function renderRecents() {
  const side = $('sidebar-projects');
  side.replaceChildren();
  const projects = S.data?.projects || [];
  if (!projects.length) {
    side.append(el('p', 'None yet', 'muted recent-empty'));
    return;
  }
  for (const p of projects.slice(0, 6)) {
    const row = el('div', null, 'recent-item');
    const link = el('button', p.title, 'recent-link');
    link.type = 'button';
    link.title = p.folder;
    bind(link, 'click', () => openProject(p.id));
    const forget = el('button', '×', 'recent-remove');
    forget.type = 'button';
    forget.title = 'Remove from recent list (keeps files on disk)';
    forget.setAttribute('aria-label', `Remove ${p.title} from recent list`);
    bind(forget, 'click', async () => {
      if (S.project?.id === p.id && !await guardDraft()) return;
      await api(`/api/projects/${encodeURIComponent(p.id)}/forget`, {});
      if (S.project?.id === p.id) {
        S.project = null;
        S.entry = null;
        await go('home', false);
      }
      await state();
    });
    row.append(link, forget);
    side.append(row);
  }
}

export async function state(first = false) {
  const previous = S.data;
  S.data = await api('/api/state');
  show('connection-error', false);
  S.csrf = S.data.csrf;
  $('local-label').textContent = `Local · v${S.data.version}`;
  document.querySelectorAll('.local-only').forEach(node => {
    node.hidden = !S.data.local;
  });
  if (JSON.stringify(previous?.projects) !== JSON.stringify(S.data.projects)) renderRecents();
  makePreparation();
  if (first) {
    $('output-folder').value = S.data.preferences.outdir || '';
    $('existing-folder').value = S.data.preferences.outdir || '';
    S.mode = sessionStorage.getItem('ag-mode') || S.data.preferences.mode || 'review';
    if (!['quick', 'review'].includes(S.mode)) S.mode = 'review';
    let ids = [];
    try {
      ids = JSON.parse(sessionStorage.getItem('ag-inputs') || 'null') || S.data.last_inputs || [];
    } catch (_) {}
    S.selected = new Set(S.data.inputs.filter(item => ids.includes(item.id)).map(item => item.id));
    renderInputs();
    renderMode();
  } else {
    const available = new Set(S.data.inputs.map(item => item.id));
    const removed = [...S.selected].filter(id => !available.has(id));
    if (removed.length) {
      removed.forEach(id => S.selected.delete(id));
      updateInputs();
    }
  }
  show('job-dot', Boolean(S.data.active_job));
  $('quit-button').title = S.data.active_job ? 'Quit and let the current job finish in the background' : 'Close this workbench window';
  $('settings-button').disabled = Boolean(S.data.active_job);
  $('new-run').disabled = Boolean(S.data.active_job);
  if (!first && Boolean(previous?.active_job) !== Boolean(S.data.active_job)) renderProject();
  updateSave();
  if (S.text) hooks.updateTextSave();
}

export async function go(view, guard = true) {
  if (guard && !await guardDraft()) return;
  if (!['home', 'review', 'results'].includes(view)) view = 'home';
  if ((view === 'review' || view === 'results') && !S.project) {
    toast('Open a results folder or run an analysis first.');
    return;
  }
  S.view = view;
  document.querySelectorAll('.view').forEach(node => {
    node.hidden = node.id !== `view-${view}`;
  });
  document.querySelectorAll('.nav-item').forEach(node => {
    node.classList.toggle('active', node.dataset.view === view);
  });
  sessionStorage.setItem('ag-view', view);
  if (view === 'review') await loadEntries({ select: !S.entry });
  if (view === 'results') renderResults();
  if (S.job) renderJob();
  window.scrollTo({ top: 0, behavior: 'instant' });
}

export async function openProject(ident, view = 'review') {
  if (!await guardDraft()) return;
  const project = await api(`/api/projects/${encodeURIComponent(ident)}`);
  await setProject(project, view);
}

export async function setProject(project, view = 'review') {
  S.project = project;
  S.entry = null;
  S.dirty = false;
  restoreReviewLocation();
  renderProject();
  await state();
  await go(view, false);
}

export function renderProject() {
  if (!S.project) return;
  $('review-title').textContent = S.project.title;
  $('review-title').title = S.project.folder;
  $('review-stats').replaceChildren();
  for (const [key, label] of [
    ['uncertain', 'uncertain'],
    ['warnings', 'other observations'],
    ['checked', 'reviewed'],
  ]) {
    const item = el('span');
    item.append(el('strong', count(S.project.counts[key])), document.createTextNode(label));
    $('review-stats').append(item);
  }
  if (S.project.working_text) {
    const item = el('span');
    item.append(el('strong', count(S.project.text_changes_count)), document.createTextNode('text revisions'));
    $('review-stats').append(item);
  }
  show('pending-banner', S.project.pending);
  $('review-export').disabled = !S.project.can_reexport || Boolean(S.data?.active_job);
  $('review-export').title = S.project.can_reexport
    ? 'Publish exports from your saved text and choices without re-running models.'
    : 'Requires compatible saved analyses and original source files in place.';
  show('working-analyse', Boolean(S.project.working_text));
  $('working-analyse').disabled = Boolean(S.data?.active_job);
  document.querySelectorAll('.working-only').forEach(node => {
    node.hidden = !S.project.working_text;
  });
  renderResults();
  updateSave();
}

export async function openExisting() {
  const folder = $('existing-folder').value.trim();
  if (!folder) {
    toast('Choose a results folder first.');
    return;
  }
  const project = await api('/api/projects/open', { folder });
  $('folder-dialog').close();
  await setProject(project, 'review');
}

export async function uploadFiles(files) {
  if (!files.length) return;
  if (new Set(files.map(file => file.name.split('.').at(-1).toLowerCase())).size > 1) {
    throw new Error('Add only one file type (.xml or .conllu) at a time.');
  }
  const button = $('upload-button');
  const items = [];
  button.disabled = true;
  try {
    for (const file of files) {
      if (file.size > 32 * 1024 * 1024) throw new Error('Each file must be 32 MB or smaller.');
      button.textContent = `Adding ${file.name}…`;
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = '';
      for (let i = 0; i < bytes.length; i += 32768) {
        binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
      }
      items.push(await api('/api/inputs/upload', { filename: file.name, content: btoa(binary) }));
    }
    toast('Files added to local workspace.');
  } finally {
    button.disabled = false;
    button.textContent = 'Choose Files';
    $('xml-files').value = '';
    if (items.length) await addInputs(items);
  }
}

export async function importOga() {
  const input = $('oga-url');
  const button = $('oga-import');
  if (button.disabled) return;
  const url = input.value.trim();
  if (!url) throw new Error('Paste an OGA .conllu URL first.');
  button.disabled = true;
  button.textContent = 'Fetching…';
  try {
    const item = await api('/api/inputs/oga', { url });
    await addInputs([item]);
    input.value = '';
    toast('OGA work added.');
  } finally {
    button.disabled = false;
    button.textContent = 'Add OGA URL';
  }
}

export async function preview() {
  if (!S.selected.size) {
    toast('Add and select at least one XML file first.');
    return;
  }
  const values = await api('/api/preview', { inputs: [...S.selected] });
  document.querySelectorAll('[data-example]').forEach(node => {
    const value = values[node.dataset.example];
    node.replaceChildren();
    if (!value) {
      node.append(el('span', 'Not found in the selected texts.'));
      return;
    }
    node.append(el('div', `${count(value.count)} occurrences · ${count(value.words)} words`, 'example-count'));
    for (const text of value.examples.slice(0, 2)) {
      node.append(el('div', text));
    }
  });
}

function runRequest() {
  const request = {
    inputs: [...S.selected],
    folder: $('output-folder').value.trim(),
    mode: S.mode,
    replace: false,
  };
  if (selectedOga()) return request;
  return {
    ...request,
    keep: [...document.querySelectorAll('[data-step]')].filter(n => !n.checked).map(n => n.dataset.step),
    accents: $('accents-option').checked,
    xml: $('xml-option').checked,
    corrections: $('corrections-path').value.trim() || null,
  };
}

export async function startRun() {
  if (!await guardDraft()) return;
  if (!S.selected.size) throw new Error('Add and select at least one Greek text.');
  const request = runRequest();
  if (!request.folder) throw new Error('Choose a results folder.');
  const preflight = await api('/api/preflight', request);
  if (preflight.obsolete.length) {
    const names = preflight.obsolete.slice(0, 15).join('\n');
    const answer = await decision(
      'Existing outputs in target folder',
      'These previous outputs do not belong to the new run:\n\n' +
        names + (preflight.obsolete.length > 15 ? '\n…' : '') +
        '\n\nRemove them when publishing? Existing manual choices will be preserved.',
      [
        { label: 'Choose Another Folder', value: null },
        { label: 'Continue', value: 'replace', primary: true },
      ]
    );
    if (answer !== 'replace') return;
    request.replace = true;
  }
  const job = await api('/api/jobs', request);
  S.selected.clear();
  sessionStorage.removeItem('ag-inputs');
  watchJob(job, true);
  await state();
  renderInputs();
  renderMode();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

export async function createEditableProject() {
  if (!await guardDraft()) return;
  if (!S.selected.size) throw new Error('Add and select at least one Greek text.');
  const folder = $('output-folder').value.trim();
  if (!folder) throw new Error('Choose a new folder for the project.');
  const button = $('new-edit-text');
  button.disabled = true;
  try {
    const project = await api('/api/projects/create', { inputs: [...S.selected], folder });
    S.selected.clear();
    sessionStorage.removeItem('ag-inputs');
    renderInputs();
    renderMode();
    await setProject(project, 'review');
    toast('Text is ready for editing. You can run automatic lemmatization later.');
  } finally {
    button.disabled = false;
  }
}

export async function analyseWorkingText() {
  if (!S.project || !await guardDraft()) return;
  const scope = await decision(
    'Analyze with GreBERTa joint',
    'Analyze only new/edited passages or the whole text? Manual choices and revision history are preserved.',
    [
      { label: 'Cancel', value: null },
      { label: 'New / Edited Only', value: 'changed', primary: true },
      { label: 'Whole Text', value: 'all' },
    ]
  );
  if (!scope) return;
  const job = await api(`/api/projects/${S.project.id}/analyse`, { scope });
  watchJob(job, true);
  await state();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

export async function standardizeHeadwords() {
  if (!S.project || !await guardDraft()) return;
  const answer = await decision(
    'Standardize headwords',
    'Map automatic lemmata to verified Diogenes / LSJ headwords without re-running neural models. Manual choices are preserved; uncertain mappings are flagged for review.',
    [
      { label: 'Cancel', value: null },
      { label: 'Standardize', value: true, primary: true },
    ]
  );
  if (!answer) return;
  const job = await api(`/api/projects/${S.project.id}/headwords`, {});
  watchJob(job, true);
  await state();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

export async function exportChoices() {
  if (!S.project || !await guardDraft()) return;
  const job = await api(`/api/projects/${S.project.id}/export`, {});
  watchJob(job, true);
  await state();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
