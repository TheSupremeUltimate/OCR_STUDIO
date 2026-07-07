/**
 * Committed Node fixtures for OCR Studio frontend logic (Bugs Hunt 2026-07).
 * Runs the SHIPPED functions from frontend/js/app.js by brace-extraction
 * (same methodology as the v3 audit). Assertions describe the CORRECT
 * (post-fix) behaviour: they FAIL on buggy code, PASS once fixed.
 * Exit 0 = all pass, 1 = any fail.
 */
import { loadFunction } from './extract.mjs';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const APP_SRC = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'frontend', 'js', 'app.js'),
  'utf8',
);

let pass = 0, fail = 0;
const ok = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`PASS ${name}`); }
  else { fail++; console.log(`FAIL ${name} ${detail}`); }
};

const parseMarkdownIntoPages = loadFunction('parseMarkdownIntoPages');
const applyCorrectionsToTokens = loadFunction('applyCorrectionsToTokens');
const translationFilename = loadFunction('translationFilename');

// Faithful reconstruction of the FIXED getMarkdownText() serialisation of the
// page-blocks showResults() builds: preamble block (page_num 0) round-trips
// verbatim with no PAGE header; every other page serialises as
// `<!-- PAGE NNN -->\n{content}\n\n`.
function serialiseLikeGetMarkdownText(pages) {
  return pages
    .map((p) => (p.isPreamble || p.page_num === 0)
      ? p.content
      : `<!-- PAGE ${String(p.page_num).padStart(3, '0')} -->\n${p.content.replace(/^\n+|\n+$/g, '')}\n\n`)
    .join('');
}

// ===========================================================================
// H-1 — generated Table of Contents must survive an edit->autosave round-trip
// ===========================================================================
{
  const toc = '# Table of Contents\n\n- [Chapter One](#chapter-one)\n\n---\n\n';
  const disk =
    toc +
    '<!-- PAGE 001 -->\n## Chapter One\nBody of page one.\n\n' +
    '<!-- PAGE 002 -->\nBody of page two.\n\n';

  const pages = parseMarkdownIntoPages(disk);
  const roundTripped = serialiseLikeGetMarkdownText(pages);

  console.log(`     [H-1 evidence] disk TOC=${disk.includes('# Table of Contents')} -> roundtrip TOC=${roundTripped.includes('# Table of Contents')}`);
  ok('H1.TOC_survives_roundtrip',
    roundTripped.includes('# Table of Contents'),
    '- TOC destroyed by parse+serialise round-trip (autosave will overwrite _FULL.md without it)');
  ok('H1.page_bodies_intact',
    roundTripped.includes('Body of page one.') && roundTripped.includes('Body of page two.'),
    '- page bodies must always survive');
}

// ===========================================================================
// H-6 — frontend/backend correction parity on the SHIPPED unequal-length pair
//         (predicted KILLED-LEAD: single pairs already parse identically)
// ===========================================================================
{
  const backendApply = (text, pairs) => {
    for (const [bad, good] of pairs) if (bad) text = text.split(bad).join(good);
    return text;
  };
  const mkTokens = (s) => [...s].map((ch) => ({ token: ch, confidence: 99, top_logprobs: [] }));

  const frontOut = applyCorrectionsToTokens(mkTokens('蓍目之變也'), [{ bad: '蓍目', good: '蓍' }])
    .map((t) => t.token).join('');
  const backOut = backendApply('蓍目之變也', [['蓍目', '蓍']]);

  console.log(`     [H-6 evidence] unequal-length pair: front="${frontOut}" back="${backOut}"`);
  ok('H6.single_pair_parity', frontOut === backOut, `- front="${frontOut}" back="${backOut}"`);
}

// ===========================================================================
// H-9 — translation must be saved to a SEPARATE file, never over the source
// ===========================================================================
{
  const src = 'page_008_FULL.md';
  const dst = translationFilename(src);
  console.log(`     [H-9 evidence] ${src} -> ${dst}`);
  ok('H9.translation_target_differs', dst !== src, `- translation would overwrite source ${src}`);
  ok('H9.translation_target_shape', dst === 'page_008_FULL_EN.md', `- got ${dst}`);
  ok('H9.translation_idempotent', translationFilename(dst) === dst, `- got ${translationFilename(dst)}`);
}

// ===========================================================================
// H-8 — zone re-run must target the document being viewed, not a queued batch
//        job. showResults() pins currentResultsJobId; the re-run handler must
//        post THAT id, never the mutating global currentJobId. (Source-scan
//        guard: the handler is DOM/async-heavy and not brace-extractable.)
// ===========================================================================
{
  const declares = /let\s+currentResultsJobId\s*=/.test(APP_SRC);
  const showResultsPins = /currentResultsJobId\s*=\s*matchingJob\s*\?/.test(APP_SRC);
  // The reprocess-zone POST body must send currentResultsJobId, not currentJobId.
  const zoneBody = APP_SRC.slice(APP_SRC.indexOf('/api/jobs/reprocess-zone'));
  const postsResultsId = /job_id:\s*currentResultsJobId/.test(zoneBody);
  const postsGlobalId = /job_id:\s*currentJobId\b/.test(zoneBody);

  console.log(`     [H-8 evidence] declares=${declares} pins=${showResultsPins} postsResultsId=${postsResultsId} postsGlobalId=${postsGlobalId}`);
  ok('H8.results_job_id_declared', declares, '- currentResultsJobId state var missing');
  ok('H8.showResults_pins_results_id', showResultsPins, '- showResults must capture matchingJob.job_id');
  ok('H8.zone_rerun_uses_results_id', postsResultsId && !postsGlobalId,
    '- zone re-run must POST currentResultsJobId, never the mutating currentJobId');
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
