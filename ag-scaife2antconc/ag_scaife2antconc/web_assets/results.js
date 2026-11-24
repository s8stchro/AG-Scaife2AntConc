/** Export status summary and file downloads. */
import { api } from './api.js';
import { isDownloadDisabled, parseDownloadFilename } from './crasis.js';
import { $, S, bind, count, el, hooks } from './state.js';

export function renderResults() {
  const box = $('result-summary');
  box.replaceChildren();
  if (!S.project) {
    box.append(el('p', 'Open a results folder first.'));
    return;
  }
  box.append(el('p', `${count(S.project.counts.all)} words · ${count(S.project.expanded)} lexical units for AntConc`, 'result-title'));
  box.append(el(
    'p',
    S.project.published_checked
      ? `Manual review in published export: ${count(S.project.published_checked)} / ${count(S.project.counts.all)} occurrences.`
      : 'This export does not include per-occurrence manual review yet.',
    'muted'
  ));
  const staleExports = Boolean(S.project.working_text && S.project.exports_current === false);
  for (const button of document.querySelectorAll('[data-download]')) {
    const disabled = isDownloadDisabled(button.dataset.download, staleExports);
    button.disabled = disabled;
    button.title = disabled ? 'Click “Update Exports” to publish the current revision.' : '';
  }
  if (S.project.working_text) {
    box.append(el('p', `${count(S.project.text_changes_count)} text revisions · ${count(S.project.stale_annotations)} annotations need review.`, 'muted'));
    if (staleExports) {
      box.append(el('p', 'Generate a new export to publish your latest text and choices.', 'help'));
    }
    for (const [work, fidelity] of Object.entries(S.project.export_fidelity || {})) {
      const label = fidelity === 'source-preserving'
        ? 'XML preserves the original source structure.'
        : 'XML is a derived TEI edition with the corrected text and annotations.';
      box.append(el('p', `${work}: ${label}`, 'help'));
    }
    for (const warning of S.project.export_warnings || []) {
      box.append(el('p', warning, 'notice'));
    }
  }
  if (S.project.pending) {
    const note = el('div', null, 'notice');
    note.append(el('p', 'You have saved changes that are not yet reflected in the export files.'));
    const button = el('button', 'Update Exports', 'primary');
    button.disabled = !S.project.can_reexport || Boolean(S.data?.active_job);
    bind(button, 'click', () => hooks.exportChoices());
    note.append(button);
    box.append(note);
  }
  box.append(el('div', S.project.folder, 'folder-path'));
}

export async function download(kind) {
  if (!S.project || !await hooks.guardDraft()) return;
  const response = await api(`/api/projects/${S.project.id}/download/${kind}`, undefined, { raw: true });
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = el('a');
  link.href = url;
  link.download = parseDownloadFilename(response.headers.get('Content-Disposition'), kind, S.project.title);
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
