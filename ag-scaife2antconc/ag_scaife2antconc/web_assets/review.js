/** Lemma review queue, detail form, manual crasis editor, provider evidence, and Morpho lookup. */
import { api } from './api.js';
import { crasisBoundaries, isCrasisChoiceComplete, reason } from './crasis.js';
import {
  $, S, bind, count, decision, draftKey, el, errorMessage, hooks,
  rememberReviewLocation, show, store, stored, toast,
} from './state.js';

export function currentChoice() {
  return {
    lemmas: S.fields.map(part => part.lemma.value.trim()),
    tags: S.fields.map(part => part.pos.value),
    scope: document.querySelector('input[name=scope]:checked').value,
    crasis: S.crasisCut
      ? { cut: Number(S.crasisCut.value), forms: S.fields.map(part => part.form.value.trim()) }
      : null,
  };
}

export function markDirty() {
  if (!S.entry) return;
  S.dirty = true;
  const storedDraft = store(draftKey(), { ...currentChoice(), beforeCrasis: S.beforeCrasis, revision: S.entry.revision });
  updateSave();
  return storedDraft;
}

export function updateSave() {
  $('entry-detail').inert = S.saving;
  const busy = Boolean(S.data?.active_job?.folder === S.project?.folder);
  $('working-headwords').disabled = !S.project || Boolean(S.data?.active_job) || S.saving || Boolean(S.text);
  $('save-entry').disabled = !S.entry || S.saving || Boolean(S.text) || Date.now() < S.cooldown || busy;
  $('save-entry').textContent = S.dirty ? 'Save & Next →' : 'Confirm & Next →';
  $('save-entry').title = 'Ctrl + Enter';
  $('draft-state').textContent = S.dirty
    ? 'Unsaved choice · press Ctrl+Enter to save'
    : 'Lemma & POS · text spelling is edited separately.';
  $('edit-text').disabled = !S.entry || S.saving || S.textOpening || busy || S.project?.can_edit_text === false;
  $('project-text').disabled = !S.project || S.saving || S.textOpening || busy || S.project?.can_edit_text === false;
}

export async function guardDraft() {
  if (S.saving || S.text || S.textOpening) return false;
  if (!S.dirty) return true;
  const answer = await decision(
    'Unsaved choice',
    'Save your current choice before continuing? Your draft is kept in browser storage.',
    [
      { label: 'Stay', value: null },
      { label: 'Discard', value: 'discard' },
      { label: 'Save', value: 'save', primary: true },
    ]
  );
  if (answer === 'save') return saveEntry(false);
  if (answer === 'discard') {
    if (!store(draftKey(), null)) return false;
    renderDetail(S.entry, false);
    return true;
  }
  return false;
}

export async function loadEntries({ select = false, around = null } = {}) {
  if (!S.project) return;
  if (!around && S.restoreKey) around = S.restoreKey;
  const query = new URLSearchParams({ view: S.filter, q: S.query, offset: String(S.offset), limit: '100' });
  if (around) query.set('around', around);
  const result = await api(`/api/projects/${S.project.id}/entries?${query}`);
  S.project = result.project;
  S.rows = result.entries;
  S.total = result.total;
  S.offset = result.offset;
  hooks.renderProject();
  renderRows();
  if (select) {
    const wanted = S.restoreKey;
    S.restoreKey = null;
    if (S.rows.length) {
      await loadEntry(S.rows.some(r => r.key === wanted) ? wanted : S.rows[0].key, false);
    } else {
      clearEntry('No occurrences match this filter.');
    }
  }
}

export function renderRows() {
  const container = $('word-list');
  container.replaceChildren();
  for (const row of S.rows) {
    const button = el('button', null, 'word-row');
    button.dataset.entry = row.key;
    button.classList.toggle('active', S.entry?.key === row.key);
    const word = el('span');
    word.append(el('span', row.form, 'form'), el('span', row.lemma || 'no lemma', 'lemma'));
    const place = el('span', null, 'place');
    if (row.checked) place.append(el('span', '✓', 'checked'));
    place.append(document.createTextNode(row.citation));
    button.append(word, place);
    bind(button, 'click', () => loadEntry(row.key));
    container.append(button);
  }
  if (!S.rows.length) {
    container.append(el('p', 'No occurrences in the current view.', 'empty-state'));
  }
  $('list-count').textContent = S.total ? `${count(S.offset + 1)}–${count(S.offset + S.rows.length)} of ${count(S.total)}` : '0 occurrences';
  $('previous-page').disabled = S.offset === 0;
  $('next-page').disabled = S.offset + S.rows.length >= S.total;
}

export async function loadEntry(key, guard = true) {
  if (guard && !await guardDraft()) return;
  const result = await api(`/api/projects/${S.project.id}/entry/${encodeURIComponent(key)}`);
  renderDetail(result);
  renderRows();
}

export function clearEntry(message) {
  S.entry = null;
  S.fields = [];
  S.dirty = false;
  show('entry-detail', false);
  show('entry-empty');
  $('entry-empty').replaceChildren(
    el('span', 'α', 'card-symbol'),
    el('h2', message),
    el('p', 'Select another occurrence from the list or update your exports.')
  );
  updateSave();
  renderRows();
}

export function applyCandidateShortcut(slotIndex) {
  if (!S.entry || S.crasisCut || !S.fields.length) return false;
  const component = S.entry.readings?.[0]?.components?.[0];
  if (!component) return false;
  const candidates = [...(component.candidates || [])].sort(
    (a, b) => Number(b.lemma_id === component.selected_id) - Number(a.lemma_id === component.selected_id)
  );
  const picked = candidates[slotIndex];
  if (!picked?.lemma) return false;
  S.fields[0].lemma.value = picked.lemma;
  if (picked.pos && S.data.pos_names[picked.pos]) S.fields[0].pos.value = picked.pos;
  markDirty();
  toast(`Selected candidate ${slotIndex + 1}: ${picked.lemma}`);
  return true;
}

function contextText(text) {
  const box = $('entry-context');
  box.replaceChildren();
  const start = text.indexOf('«');
  const end = text.indexOf('»', start + 1);
  if (start < 0 || end < 0) {
    box.textContent = text;
    return;
  }
  box.append(
    document.createTextNode(text.slice(0, start)),
    el('mark', text.slice(start + 1, end)),
    document.createTextNode(text.slice(end + 1))
  );
}

function greekField(container, title, id, value) {
  const label = el('label', title);
  const input = el('input');
  label.htmlFor = input.id = id;
  input.value = value || '';
  input.lang = 'grc';
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.setAttribute('autocorrect', 'off');
  input.setAttribute('autocapitalize', 'none');
  container.append(label, input);
  bind(input, 'input', markDirty);
  return input;
}

function cancelCrasis() {
  let previous = S.beforeCrasis;
  if (!previous && !S.entry.manual_crasis) {
    previous = {
      expansion: S.entry.expansion,
      lemmas: S.entry.lemma.split('+'),
      tags: S.entry.pos.split('+'),
      dirty: false,
      scope: 'one',
    };
  }
  S.beforeCrasis = null;
  if (previous) {
    renderFields(previous.expansion, previous.lemmas, previous.tags);
    document.querySelector(`input[name=scope][value=${previous.scope === 'all' ? 'all' : 'one'}]`).checked = true;
    S.dirty = previous.dirty;
    if (S.dirty) markDirty();
    else store(draftKey(), null);
  } else {
    const { machine } = S.entry;
    const [lemma, pos] = machine.before_correction || [machine.lemma, machine.pos];
    renderFields(machine.expansion, lemma.split('+'), pos.split('+'));
    markDirty();
    toast('Manual crasis cleared. Save your choice to apply.');
  }
  renderEvidence(S.entry);
  updateSave();
  S.fields[0]?.lemma.focus();
}

function renderFields(expansion, lemmas, tags, manual = null) {
  const parts = $('entry-parts');
  parts.replaceChildren();
  S.fields = [];
  S.crasisCut = null;
  const boundaries = crasisBoundaries(S.entry.form);
  const controls = el('div', null, 'crasis-controls');
  parts.append(controls);
  if (manual) {
    const label = el('label', 'Split point in written form');
    const select = el('select', null, 'greek');
    label.htmlFor = select.id = 'crasis-boundary';
    const placeholder = el('option', 'Choose split point');
    placeholder.value = '';
    select.append(placeholder);
    for (const [cut, text] of boundaries) {
      const option = el('option', text);
      option.value = String(cut);
      select.append(option);
    }
    select.value = manual.cut ? String(manual.cut) : '';
    S.crasisCut = select;
    bind(select, 'change', markDirty);
    const cancel = el('button', S.entry.manual_crasis && !S.beforeCrasis ? 'Remove Manual Crasis' : 'Cancel Crasis Split', 'secondary');
    cancel.id = 'cancel-crasis';
    cancel.type = 'button';
    bind(cancel, 'click', cancelCrasis);
    controls.append(cancel, label, select, el('p', 'Enter the full inflected form, lemma, and POS for each component.', 'help'));
  } else if (boundaries.length) {
    const define = el('button', 'Define Manual Crasis', 'secondary');
    define.id = 'define-crasis';
    bind(define, 'click', () => {
      const choice = currentChoice();
      S.beforeCrasis = {
        expansion: [...expansion],
        lemmas: choice.lemmas,
        tags: choice.tags,
        scope: choice.scope,
        dirty: S.dirty,
      };
      const forms = expansion.length === 2 ? expansion : ['', ''];
      renderFields(
        forms,
        expansion.length === 2 ? choice.lemmas : [],
        expansion.length === 2 ? choice.tags : [],
        { cut: 0, forms }
      );
      renderEvidence(S.entry);
      markDirty();
      S.crasisCut.focus();
    });
    controls.append(define);
  }
  expansion.forEach((surface, index) => {
    const part = el('div');
    const fields = el('div', null, 'part-fields');
    const left = el('div');
    const right = el('div');
    let form = null;
    if (manual) {
      const restored = el('div', null, 'crasis-form');
      form = greekField(restored, `Inflected form ${index + 1}`, `restored-${index}`, surface);
      part.append(restored);
    } else if (expansion.length > 1) {
      part.append(el('div', surface, 'part-form'));
    }
    const lemma = greekField(left, 'Lemma', `lemma-${index}`, lemmas[index]);
    const label = el('label', 'Part of Speech');
    const pos = el('select');
    label.htmlFor = pos.id = `pos-${index}`;
    for (const [code, name] of Object.entries(S.data.pos_names)) {
      const option = el('option', `${name} (${code})`);
      option.value = code;
      pos.append(option);
    }
    pos.value = S.data.pos_names[tags[index]] ? tags[index] : 'X';
    bind(pos, 'change', markDirty);
    right.append(label, pos);
    fields.append(left, right);
    part.append(fields);
    parts.append(part);
    S.fields.push({ form, lemma, pos });
  });
}

export function renderDetail(record, restore = true) {
  S.entry = record;
  S.dirty = false;
  S.fields = [];
  S.beforeCrasis = null;
  show('entry-empty', false);
  show('entry-detail');
  $('entry-location').textContent = `${record.work} · ${record.citation}`;
  $('entry-form').textContent = record.form;
  show('entry-checked', record.checked);
  contextText(record.context);
  const status = $('entry-text-status');
  status.replaceChildren();
  const stale = record.text_stale || record.stale || record.annotation_stale;
  if (stale) {
    status.append(el('p', 'The text changed. Please enter or re-analyze the lemma and POS.'));
  } else if (record.context_stale) {
    status.append(el('p', 'Surrounding context changed. The previous choice is retained for review.'));
  }
  const previous = record.prior_annotations || record.previous_annotations || [];
  if (previous.length) {
    const history = el('details');
    history.append(el('summary', 'Previous choices (for comparison)'));
    for (const item of previous) {
      const annotation = item.annotation || item;
      history.append(el('p', `${item.form || annotation.form || ''} → ${annotation.lemma || '—'} · ${annotation.pos || '—'}`, 'greek'));
    }
    status.append(history);
  }
  show('entry-text-status', Boolean(status.children.length));
  const reasons = $('entry-reasons');
  reasons.replaceChildren();
  for (const text of record.priorities) {
    reasons.append(el('p', reason(text)));
  }
  const secondary = record.observations.filter(r => !record.priorities.includes(r));
  if (secondary.length) {
    const details = el('details');
    details.append(el('summary', `${secondary.length} other observation${secondary.length === 1 ? '' : 's'}`));
    for (const text of [...new Set(secondary)]) {
      details.append(el('p', reason(text), 'secondary-reason'));
    }
    reasons.append(details);
  }
  renderFields(record.expansion, record.lemma.split('+'), record.pos.split('+'), record.manual_crasis);
  for (const trace of record.rule_contributions || []) {
    if (trace.rule_id === 'grc.grammar.corroborated-pos') {
      if (trace.status === 'applied') {
        reasons.append(el('p', `Corroborated form/headword POS · ${trace.original_pos || '—'} → ${trace.proposed_pos} (rule ${trace.rule_version}).`, 'help'));
      }
    } else if (trace.rule_id === 'grc.lemma.deictic-iota-headword') {
      if (trace.status === 'applied') {
        reasons.append(el('p', `Deictic -ι · separate headword ${trace.headword} (kept as one token).`, 'help'));
      } else if (trace.status === 'abstained') {
        reasons.append(el('p', 'Deictic -ι · selected lemma is outside the documented family; please verify.', 'help'));
      }
    } else if (trace.rule_id === 'grc.lemma.diogenes-headword') {
      if (trace.status === 'applied') {
        reasons.append(el('p', `Diogenes / LSJ headword: ${trace.headword}${trace.candidate_lemma !== trace.headword ? ' · original proposal: ' + trace.candidate_lemma : ''}. Headword mapping does not verify morphological correctness.`, 'help'));
      } else if (trace.status === 'abstained') {
        reasons.append(el('p', 'Headword could not be anchored confidently in the reference dictionary; original proposal retained for review.', 'help'));
      }
    }
  }
  document.querySelector('input[name=scope][value=one]').checked = true;
  $('evidence-panel').open = false;
  renderEvidence(record);
  show('old-draft', false);
  const draft = stored(draftKey());
  if (restore && draft) {
    if (draft.revision === record.revision) restoreDraft(draft);
    else show('old-draft');
  }
  rememberReviewLocation();
  updateSave();
}

export function restoreDraft(draft) {
  if (!draft) return;
  const expansion = draft.crasis?.forms || S.entry.machine.expansion;
  if (draft.lemmas?.length !== expansion.length) return;
  S.beforeCrasis = draft.crasis ? draft.beforeCrasis || null : null;
  renderFields(expansion, draft.lemmas, draft.tags, draft.crasis);
  renderEvidence(S.entry);
  document.querySelector(`input[name=scope][value=${draft.scope === 'all' ? 'all' : 'one'}]`).checked = true;
  S.dirty = true;
  show('old-draft', false);
  updateSave();
  toast('Draft choice restored.');
}

function renderMorphoLookup(box, record) {
  const section = el('div', null, 'component');
  section.append(el('p', 'Morpho / Logeion · optional online lookup. Responses never alter your choice automatically.', 'help'));
  record.expansion.forEach((form, index) => {
    const button = el('button', 'Morpho: ' + form, 'quiet');
    const result = el('div');
    bind(button, 'click', async () => {
      button.disabled = true;
      button.textContent = 'Looking up…';
      try {
        const reply = await api(`/api/projects/${S.project.id}/lookup/morpho`, { key: record.key, component: index });
        result.replaceChildren(el('p', 'Proposals from dictionary definitions; exact-form verification is not established.', 'help'));
        if (reply.error) result.append(el('p', 'Lookup failed: ' + reply.error, 'help'));
        if (!reply.lemmas.length) result.append(el('p', 'No lemma proposal returned.'));
        for (const lemma of reply.lemmas) {
          const row = el('div', null, 'candidate-heading');
          row.append(el('span', lemma, 'greek'));
          const use = el('button', 'Use lemma', 'quiet');
          bind(use, 'click', () => {
            if (S.entry?.key === record.key && S.fields[index]) {
              S.fields[index].lemma.value = lemma;
              markDirty();
            }
          });
          row.append(use);
          result.append(row);
        }
        const link = el('a', 'Open in Logeion');
        link.href = reply.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        result.append(link);
        if (reply.evidence.length) {
          const raw = el('details');
          const pre = el('pre', JSON.stringify(reply.evidence, null, 2));
          raw.append(el('summary', 'Raw Morpho response'), pre);
          result.append(raw);
        }
      } finally {
        button.disabled = false;
        button.textContent = 'Morpho: ' + form;
      }
    });
    section.append(button, result);
  });
  box.append(section);
}

export function renderEvidence(record) {
  const box = $('entry-evidence');
  box.replaceChildren();
  renderMorphoLookup(box, record);
  const oga = record.readings[0]?.oga;
  const reference = record.readings[0]?.headword_reference;
  if (reference) {
    const info = el('details');
    info.append(
      el('summary', 'Reference headword dictionary'),
      el('p', 'Diogenes / Morpheus and LSJ · headword matching does not verify contextual correctness.'),
      el('small', reference.dictionary_file || 'Reference dictionary unavailable.'),
      el('small', reference.dictionary_sha256 ? 'Dictionary SHA-256: ' + reference.dictionary_sha256 : '')
    );
    box.append(info);
  }
  if (oga) {
    const details = el('details', null, 'reading');
    details.open = true;
    details.append(
      el('summary', 'Original OGA record'),
      el('p', `${oga.form} → ${oga.lemma === '_' ? '—' : oga.lemma}`, 'greek'),
      el('small', `${oga.reference || record.citation} · ${oga.token} · ${oga.tag}`)
    );
    box.append(details);
    return;
  }
  record.readings.forEach((reading, ri) => {
    const details = el('details', null, 'reading');
    details.open = ri === 0;
    details.append(el('summary', (ri === 0 ? 'Selected: ' : 'Alternative: ') + reading.expansion.join(' + ')));
    reading.components.forEach((component, i) => {
      const group = el('div', null, 'component');
      group.append(el('p', `${component.form} · GreTa (comparator): ${component.greta || '—'}`));
      if (component.context) {
        const context = component.context;
        const kind = {
          edit_script: 'neural edit script',
          lookup_form_upos: 'model form+UPOS lookup table',
          lookup_form: 'model form lookup table',
          lookup_lower_fallback: 'model lowercase lookup table',
          identity_fallback: 'unresolved identity fallback',
          unresolved: 'unresolved',
        }[context.lemma_source] || context.lemma_source;
        group.append(el('p', `GreBERTa joint: ${context.lemma || '—'} · ${context.pos || '—'} · ${context.msd || '—'}`));
        group.append(el('small', `Source: ${kind}. Selected by: ${component.selection?.selected_by || '—'}.`));
        if (context.neural_lemma && context.canonical_lemma !== context.neural_lemma) {
          group.append(el('small', `Neural lemma: ${context.neural_lemma} · model canonical lookup: ${context.canonical_lemma || '—'}`));
        }
      } else if (component.tagger) {
        group.append(el('p', `OdyCy (legacy saved analysis): ${component.tagger.pos || '—'} · ${component.tagger.msd || '—'}`));
      }
      const candidates = [...component.candidates].sort(
        (a, b) => Number(b.lemma_id === component.selected_id) - Number(a.lemma_id === component.selected_id)
      );
      candidates.slice(0, 8).forEach((candidate, ci) => {
        const row = el('div', null, 'candidate');
        const head = el('div', null, 'candidate-heading');
        head.append(el('span', candidate.lemma || '—', 'lemma'), el('span', candidate.pos || '', 'badge'));
        if (!S.crasisCut && ri === 0 && i < S.fields.length && candidate.lemma) {
          const hint = i === 0 && ci < 8 ? ` (Alt+${ci + 1})` : '';
          const use = el('button', `Use lemma${hint}`, 'quiet');
          bind(use, 'click', () => {
            S.fields[i].lemma.value = candidate.lemma;
            if (candidate.pos && S.data.pos_names[candidate.pos]) S.fields[i].pos.value = candidate.pos;
            markDirty();
          });
          head.append(use);
        }
        row.append(
          head,
          el('small', `Source: ${candidate.source || '—'}${Number.isFinite(candidate.score) ? ' · score: ' + candidate.score.toFixed(2) : ''}`)
        );
        if (candidate.msd) row.append(el('small', candidate.msd));
        if (candidate.raw) row.append(el('small', candidate.raw));
        if (candidate.dictionary_link) {
          const link = candidate.dictionary_link;
          const status = { missing: 'missing', guessed: 'guessed', linked: 'linked' }[link.provider_status] || link.provider_status;
          const detail = el('details');
          detail.append(
            el('summary', 'Dictionary link: ' + status),
            el('small', `Provider headword: ${link.provider_headword} · marker: ${link.marker} · offset: ${link.offset}`),
            el('small', `Dictionary headword: ${link.dictionary_headword || 'not checked'} · match: ${link.headword_match}`)
          );
          row.append(detail);
        }
        group.append(row);
      });
      details.append(group);
    });
    box.append(details);
  });
}

export async function saveEntry(advance = true) {
  if (!S.entry || S.saving || S.text || Date.now() < S.cooldown) return false;
  const choice = currentChoice();
  if (!isCrasisChoiceComplete(choice)) {
    toast('Choose a split point and fill in both inflected forms and lemmata.', true);
    return false;
  }
  S.saving = true;
  updateSave();
  const key = S.entry.key;
  const form = S.entry.form;
  try {
    const result = await api(`/api/projects/${S.project.id}/choice`, {
      key,
      revision: S.entry.revision,
      ...choice,
      view: S.filter,
      query: S.query,
    });
    store(draftKey(key), null);
    S.dirty = false;
    S.project = result.project;
    hooks.renderProject();
    if (advance) {
      if (result.next) {
        renderDetail(result.next);
        await loadEntries({ around: result.next.key });
        $('word-list').querySelector('.active')?.scrollIntoView({ block: 'nearest' });
      } else {
        await loadEntries();
        clearEntry('Choice saved. You have reached the end of this view.');
      }
    } else {
      const current = await api(`/api/projects/${S.project.id}/entry/${encodeURIComponent(key)}`);
      renderDetail(current, false);
    }
    toast(`Saved: ${form}${result.at_end && advance ? ' · End of current view.' : ''}`);
    S.cooldown = Date.now() + 350;
    setTimeout(updateSave, 360);
    return true;
  } catch (error) {
    errorMessage(error);
    return false;
  } finally {
    S.saving = false;
    updateSave();
  }
}
