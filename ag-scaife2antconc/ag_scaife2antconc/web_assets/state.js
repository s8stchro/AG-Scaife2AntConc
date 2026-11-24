/** Shared UI state, DOM helpers, dialogs, and localStorage draft utilities. */

export const $ = id => document.getElementById(id);

export const S = {
  data: null,
  csrf: '',
  project: null,
  entry: null,
  view: 'home',
  filter: 'uncertain',
  query: '',
  offset: 0,
  rows: [],
  total: 0,
  mode: 'review',
  selected: new Set(),
  dirty: false,
  saving: false,
  job: null,
  sequence: 0,
  logs: [],
  handled: new Set(),
  dismissedJob: null,
  cooldown: 0,
  fields: [],
  crasisCut: null,
  beforeCrasis: null,
  polling: false,
  closed: false,
  quitting: false,
  pollTimer: null,
  restoreKey: null,
  text: null,
  textOpening: false,
  textSaving: false,
  passages: [],
};

/** Late-bound cross-module callbacks to keep the ES-module dependency graph strictly acyclic. */
export const hooks = {
  guardDraft: async () => true,
  state: async () => {},
  setProject: async () => {},
  renderProject: () => {},
  exportChoices: async () => {},
  updateTextSave: () => {},
};

let toastTimer;

export function el(tag, text = '', cls = '') {
  const node = document.createElement(tag);
  if (text !== null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

export function show(id, visible = true) {
  $(id).hidden = !visible;
}

export function count(n) {
  return Number(n || 0).toLocaleString('en-US');
}

export function bind(target, event, fn) {
  const node = typeof target === 'string' ? $(target) : target;
  if (!node) return;
  node.addEventListener(event, e => {
    try {
      Promise.resolve(fn(e)).catch(errorMessage);
    } catch (error) {
      errorMessage(error);
    }
  });
}

export function toast(message, error = false) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').classList.toggle('error', error);
  show('toast');
  toastTimer = setTimeout(() => show('toast', false), error ? 9000 : 4500);
}

export function errorMessage(error) {
  toast(error.message || String(error), true);
}

export function stored(key, fallback = null) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : fallback;
  } catch (_) {
    return fallback;
  }
}

export function store(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch (_) {
    toast('Could not save draft in browser storage. Click Save before closing this window.', true);
    return false;
  }
}

export function draftKey(key = S.entry?.key) {
  return `ag-draft:${S.project?.id}:${key}`;
}

export function rememberReviewLocation() {
  if (S.project) {
    store(`ag-review:${S.project.id}`, {
      filter: S.filter,
      query: S.query,
      offset: S.offset,
      key: S.entry?.key || null,
    });
  }
}

export function restoreReviewLocation() {
  const loc = S.project ? stored(`ag-review:${S.project.id}`) : null;
  const initial = S.project?.provider === 'oga' ? 'all' : 'uncertain';
  S.filter = loc && ['uncertain', 'warnings', 'all', 'checked'].includes(loc.filter) ? loc.filter : initial;
  S.query = loc?.query || '';
  S.offset = loc?.offset || 0;
  S.restoreKey = loc?.key || null;
  $('entry-filter').value = S.filter;
  $('entry-search').value = S.query;
}

export function decision(title, text, buttons) {
  if ($('decision-dialog').open) return Promise.resolve(null);
  return new Promise(resolve => {
    $('decision-title').textContent = title;
    $('decision-text').textContent = text;
    $('decision-actions').replaceChildren();
    const dialog = $('decision-dialog');
    let resolved = false;
    const finish = value => {
      if (resolved) return;
      resolved = true;
      dialog.close();
      resolve(value);
    };
    for (const button of buttons) {
      const node = el('button', button.label, button.primary ? 'primary' : 'secondary');
      node.type = 'button';
      bind(node, 'click', () => finish(button.value));
      $('decision-actions').append(node);
    }
    dialog.oncancel = e => {
      e.preventDefault();
      finish(null);
    };
    dialog.showModal();
    $('decision-actions').firstElementChild?.focus();
  });
}
