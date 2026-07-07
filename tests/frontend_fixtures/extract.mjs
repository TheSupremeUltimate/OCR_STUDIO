/**
 * Brace-matching extractor for named top-level functions in frontend/js/app.js.
 * Lets Node tests exercise the SHIPPED logic (not a re-typed copy), matching the
 * methodology used by the v3 audit's Node fixtures. Read-only: never mutates app.js.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const APP_JS = join(__dirname, '..', '..', 'frontend', 'js', 'app.js');

export function extractFunction(name, source = readFileSync(APP_JS, 'utf8')) {
  const sig = `function ${name}(`;
  const start = source.indexOf(sig);
  if (start === -1) throw new Error(`function ${name} not found in app.js`);
  // Walk from the opening brace, balancing braces while skipping strings/comments.
  let i = source.indexOf('{', start);
  const bodyStart = i;
  let depth = 0, inS = null, inLineC = false, inBlockC = false;
  for (; i < source.length; i++) {
    const c = source[i], n = source[i + 1];
    if (inLineC) { if (c === '\n') inLineC = false; continue; }
    if (inBlockC) { if (c === '*' && n === '/') { inBlockC = false; i++; } continue; }
    if (inS) { if (c === '\\') { i++; continue; } if (c === inS) inS = null; continue; }
    if (c === '/' && n === '/') { inLineC = true; i++; continue; }
    if (c === '/' && n === '*') { inBlockC = true; i++; continue; }
    if (c === '"' || c === "'" || c === '`') { inS = c; continue; }
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) { i++; break; } }
  }
  const body = source.slice(bodyStart, i);
  // eslint-disable-next-line no-new-func
  return new Function(`${sig.slice(9)}${source.slice(source.indexOf('(', start), bodyStart)} ${body}`.replace(/^\w+/, 'return function ' + name) )();
}

// Simpler, robust variant: eval the exact source slice as a function expression.
export function loadFunction(name) {
  const source = readFileSync(APP_JS, 'utf8');
  const sig = `function ${name}(`;
  const start = source.indexOf(sig);
  if (start === -1) throw new Error(`function ${name} not found`);
  let i = source.indexOf('{', start), depth = 0, inS = null, inLineC = false, inBlockC = false;
  const bodyOpen = i;
  for (; i < source.length; i++) {
    const c = source[i], n = source[i + 1];
    if (inLineC) { if (c === '\n') inLineC = false; continue; }
    if (inBlockC) { if (c === '*' && n === '/') { inBlockC = false; i++; } continue; }
    if (inS) { if (c === '\\') { i++; continue; } if (c === inS) inS = null; continue; }
    if (c === '/' && n === '/') { inLineC = true; i++; continue; }
    if (c === '/' && n === '*') { inBlockC = true; i++; continue; }
    if (c === '"' || c === "'" || c === '`') { inS = c; continue; }
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) { i++; break; } }
  }
  const params = source.slice(source.indexOf('(', start), bodyOpen);
  const body = source.slice(bodyOpen, i);
  return new Function(`return (function ${name}${params}${body});`)();
}
