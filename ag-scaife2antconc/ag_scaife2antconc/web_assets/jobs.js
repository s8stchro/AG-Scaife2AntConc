/** Inline background job progress banner and visibility-aware polling. */
import { api } from './api.js';
import { $, S, count, hooks, show, toast } from './state.js';

export function watchJob(job, startedHere = false) {
  if (startedHere) sessionStorage.setItem('ag-started-job', job.id);
  if (S.job?.id !== job.id) {
    S.sequence = 0;
    S.logs = [];
    $('job-log').textContent = '';
    S.dismissedJob = null;
  }
  S.job = job;
  renderJob();
}

export function dismissJob() {
  if (S.job) S.dismissedJob = S.job.id;
  sessionStorage.setItem('ag-dismissed-job', S.dismissedJob);
  show('job-panel', false);
}

export function renderJob() {
  const job = S.job;
  if (!job || S.dismissedJob === job.id) {
    show('job-panel', false);
    return;
  }
  const active = ['queued', 'running', 'publishing'].includes(job.state);
  show('job-panel', true);
  const imported = job.provider === 'oga';
  show('stage-list', !imported && job.operation !== 'working-export');
  const states = {
    queued: 'Preparing',
    running: 'Running',
    publishing: 'Publishing',
    done: 'Completed',
    cancelled: 'Cancelled',
    failed: 'Failed',
    interrupted: 'Interrupted',
  };
  $('job-state').textContent = states[job.state] || job.state;
  let stage = 0;
  let title = job.operation === 'working-export'
    ? 'Exporting working text'
    : job.replay
      ? 'Re-exporting saved analyses'
      : imported
        ? 'Importing OGA lemmata…'
        : 'Loading models & resources…';
  let numbers = null;
  for (const event of S.logs) {
    const text = event.message;
    if (text.includes('GreBERTa joint:')) { stage = 1; title = 'GreBERTa: contextual lemmata & grammar'; numbers = null; }
    if (text.includes('GreTa: comparator') || text.includes('GreTa: συγκριτικά')) { stage = 2; title = 'GreTa: comparator lemmata'; numbers = null; }
    if (text.includes('Morpho:')) { stage = 3; title = 'Additional Morpho evidence'; numbers = null; }
    const match = text.match(/GreTa (?:lemmata|candidates|λήμματα|υποψήφιοι): ([\d,]+)\/([\d,]+)/);
    if (match) {
      stage = text.includes('candidates') || text.includes('υποψήφιοι') ? 3 : 2;
      title = stage === 2 ? 'GreTa comparator lemmata' : 'Scoring alternatives';
      numbers = [Number(match[1].replaceAll(',', '')), Number(match[2].replaceAll(',', ''))];
    }
  }
  if (job.state === 'publishing') {
    stage = 4;
    title = 'Publishing outputs';
    numbers = null;
  }
  if (!active) {
    stage = job.state === 'done' ? 5 : stage;
    title = job.phase || states[job.state];
    numbers = job.state === 'done' ? [1, 1] : null;
  }
  $('phase-title').textContent = title;
  const progress = $('progress-meter');
  if (numbers) {
    progress.max = Math.max(1, numbers[1]);
    progress.value = numbers[0];
    $('progress-count').textContent = numbers[1] > 1 ? `${count(numbers[0])} / ${count(numbers[1])} in this stage` : '';
  } else {
    progress.removeAttribute('value');
    $('progress-count').textContent = '';
  }
  [...$('stage-list').children].forEach((node, index) => {
    node.classList.toggle('complete', index < stage);
    node.classList.toggle('current', index === stage);
  });
  const seconds = Math.max(0, Math.floor((job.finished || Date.now() / 1000) - (job.started || job.created)));
  $('elapsed').textContent = `Elapsed: ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  $('cancel-job').disabled = !['queued', 'running'].includes(job.state);
  show('cancel-job', active);
  show('dismiss-job', !active);
  show('job-error', Boolean(job.error));
  $('job-error').textContent = job.error || '';
  show('open-job-results', job.state === 'done');
  $('job-log').textContent = S.logs.map(event => event.message).join('\n');
}

export async function openJobResults() {
  if (!S.job || !await hooks.guardDraft()) return;
  const project = await api('/api/projects/open', { folder: S.job.folder });
  dismissJob();
  const exported = S.job.mode === 'quick' || S.job.replay || S.job.operation === 'working-export';
  await hooks.setProject(project, exported ? 'results' : 'review');
}

export async function poll() {
  if (S.closed || S.quitting || S.polling || document.hidden) return;
  S.polling = true;
  try {
    await hooks.state();
    if (S.closed || S.quitting) return;
    const target = S.data.active_job || (S.job && S.data.jobs.find(j => j.id === S.job.id));
    if (target) {
      if (S.job?.id !== target.id) watchJob(target);
      const job = await api(`/api/jobs/${target.id}?after=${S.sequence}`);
      if (S.closed || S.quitting) return;
      for (const event of job.events) {
        if (event.sequence > S.sequence) {
          S.logs.push(event);
          S.sequence = event.sequence;
        }
      }
      if (S.logs.length > 500) S.logs = S.logs.slice(-500);
      S.job = job;
      renderJob();
      if (!['queued', 'running', 'publishing'].includes(job.state) && !S.handled.has(job.id)) {
        S.handled.add(job.id);
        if (job.state === 'done') {
          if (sessionStorage.getItem('ag-started-job') === job.id &&
              !S.dirty && !S.saving && !S.textOpening && !document.querySelector('dialog[open]')) await openJobResults();
          else toast('Job finished. Click “Open Results” in the banner above when ready.');
        } else if (job.error) {
          toast(job.error, true);
        }
      }
    }
  } catch (error) {
    if (!S.closed && !S.quitting) {
      $('connection-error').textContent = error.message;
      show('connection-error');
    }
  } finally {
    S.polling = false;
  }
}
