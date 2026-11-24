/** Pure, DOM-free frontend helpers for crasis boundaries, review reasons, downloads, and drafts. */

export const reasonTranslations = {
  'unresolved lemma': 'No lemma was found.',
  'GreTa and selected lexical analysis disagree': 'GreTa proposed a different lemma.',
  'lemma lacks a full local morphological analysis': 'Lemma lacks a full local morphological analysis.',
  'ambiguous grammatical/lexical analysis': 'Close competing grammatical or lexical analyses.',
  'competing lemmas; ranking margin below 0.75 (not a probability)': 'Close competing alternatives with different lemmata (margin < 0.75).',
  'competing lexical expansions; ranking margin below 0.75 (not a probability)': 'Close competing expansions with different lemmata or word counts.',
  'interrogative grammar conflicts with indefinite lemma': 'Interrogative grammar conflicts with indefinite lemma τις.',
  'interrogative/indefinite accent conflicts with selected lemma': 'Interrogative/indefinite accent (τίς/τις) conflicts with the selected lemma.',
  'crasis detected but no supported inflected expansion': 'Possible crasis without a supported inflected expansion.',
  'internal coronis detected; no supported independent-word expansion': 'Internal coronis without a supported independent-word expansion.',
  'unexpanded elision; restoration uncertain': 'Elision could not be restored with confidence.',
  'unresolved initial elision': 'Unresolved prodelision (initial elision).',
  'expansion candidate limit reached (8)': 'Expansion search reached the candidate limit (8).',
  'GreTa length limit or incomplete prediction': 'GreTa length limit or incomplete prediction.',
  'text changed; annotation needs review': 'Text changed; lemma and POS need review.',
  'context changed; annotation needs review': 'Surrounding context changed; review the previous choice.',
  'contextual model unresolved; fallback needs review': 'Contextual model did not resolve a lemma; check the fallback.',
  'contextual model used a shortened context': 'Contextual model used a shortened/windowed context due to input length.',
  'contextual model used a lookup fallback for an ambiguous form': 'Ambiguous form: model fell back to its lookup table.',
  'contextual top-1 outside local candidates; top-k contextual candidate used': 'Model top-1 was outside local candidates; used ranked top-k candidate.',
  'GreBERTa and unambiguous local lemma disagree': 'GreBERTa disagreed with the unique local lemma.',
  'headword standard unresolved; proposal retained for review': 'Headword could not be anchored confidently in Diogenes / LSJ.',
  'selected Diogenes dictionary association is provisional': 'Diogenes dictionary association is provisional.',
};

export function reason(text) {
  if (text.startsWith('lexical and contextual grammar disagree:')) {
    return 'Grammar disagreement: ' + text.split(':')[1].trim();
  }
  if (text.startsWith('ambiguous expansion')) {
    return 'Close ranking scores among alternative expansions.';
  }
  if (text.startsWith('contextual lemma outside the local candidates;')) {
    return 'Contextual top-1 was outside local candidates; used fallback (' + text.split(';')[1].trim() + ').';
  }
  return reasonTranslations[text] || text;
}

export function crasisBoundaries(form) {
  const characters = Array.from(form);
  let letters = 0;
  const choices = [];
  for (let index = 1; index < characters.length; index++) {
    if (!/\p{M}/u.test(characters[index - 1])) letters++;
    if (/\p{M}/u.test(characters[index])) continue;
    const first = characters.slice(0, index).join('');
    const second = characters.slice(index).join('');
    if (/\p{L}/u.test(first) && /\p{L}/u.test(second)) {
      choices.push([letters, `${first} | ${second}`]);
    }
  }
  return choices;
}

export function isCrasisChoiceComplete(choice) {
  if (!choice?.crasis) return true;
  return Boolean(
    choice.crasis.cut &&
    choice.crasis.forms.every(Boolean) &&
    choice.lemmas.every(Boolean)
  );
}

export const STALE_EXPORT_DOWNLOADS = new Set(['tagged', 'xml', 'all', 'review', 'report', 'evidence']);

export function isDownloadDisabled(kind, staleExports) {
  return Boolean(staleExports && STALE_EXPORT_DOWNLOADS.has(kind));
}

export function parseDownloadFilename(disposition, kind, projectTitle) {
  const match = (disposition || '').match(/filename\*=UTF-8''([^;]+)|filename="([^"]+)"|filename=([^;\s]+)/i);
  const serverName = match?.[1] ? decodeURIComponent(match[1]) : match?.[2] || match?.[3];
  if (serverName) return serverName;
  if (kind === 'review') return 'lemma-review.tsv';
  if (kind === 'report') return 'report.txt';
  if (kind === 'evidence') return 'analysis-review.jsonl';
  const prefix = { tagged: 'AntConc', xml: 'XML', working: 'Working-text' }[kind] || 'Scaife2AntConc';
  return `${prefix}-${projectTitle}.zip`;
}

export function selectLatestTextDraft(entries) {
  const sorted = [...entries].sort((a, b) => (b.value?.updated_at || 0) - (a.value?.updated_at || 0));
  return sorted[0] || null;
}
