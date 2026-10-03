const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const test = require('node:test');
const root = path.resolve(__dirname, '..');
const clientRequire = createRequire(path.join(root, 'client/package.json'));
const ts = clientRequire('typescript');
const skills = path.join(root, 'client/src/components/views/skills');

class Clock {
  pending = new Map();
  id = 0;
  setTimeout = (callback) => { const id = ++this.id; this.pending.set(id, callback); return id; };
  clearTimeout = (id) => this.pending.delete(id);
  run() {
    const first = this.pending.entries().next().value;
    assert.ok(first, 'expected a scheduled task');
    this.pending.delete(first[0]);
    first[1]();
  }
}

function loadTs(filename, clock = new Clock()) {
  const fullPath = path.join(skills, filename);
  const output = ts.transpileModule(fs.readFileSync(fullPath, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText;
  const context = { exports: {}, require: createRequire(fullPath), AbortController,
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout };
  vm.runInNewContext(output, context, { filename: fullPath });
  return context.exports;
}

const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

function loadApi(fetch) {
  const fullPath = path.join(root, 'client/src/services/api.ts');
  const source = fs.readFileSync(fullPath, 'utf8').replace('import.meta.env.VITE_API_BASE_URL', "'http://test/api'");
  const output = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const storage = new Map();
  const context = { exports: {}, fetch, AbortController,
    localStorage: { getItem: (key) => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) } };
  vm.runInNewContext(output, context, { filename: fullPath });
  return context.exports;
}

test('scene snapshots are session scoped and explicit refresh forces server recomputation', async () => {
  const calls = [];
  const snapshot = { cards: [{ scene_id: 'scene-a', available_atom_count: 3 }] };
  const { api, setAuthToken } = loadApi(async (url) => {
    calls.push(url); return { ok: true, status: 200, json: async () => snapshot };
  });
  setAuthToken('first-session');
  assert.equal(api.getCachedSceneCards(), null);
  await api.getSceneCards();
  assert.equal(api.getCachedSceneCards(), snapshot);
  assert.equal(calls[0], 'http://test/api/skill-factory/scenes/cards?background_refresh=true');
  await api.getSceneCards(true);
  assert.equal(calls[1], 'http://test/api/skill-factory/scenes/cards?background_refresh=true&force_refresh=true');
  setAuthToken('other-session');
  assert.equal(api.getCachedSceneCards(), null, 'another account must never see the previous snapshot');
});

test('late scene responses cannot restore a previous session or overwrite the newest snapshot', async () => {
  const pending = [];
  const { api, setAuthToken } = loadApi(() => {
    const response = deferred(); pending.push(response); return response.promise;
  });
  const response = (value) => ({ ok: true, status: 200, json: async () => value });
  setAuthToken('first-session');
  const first = api.getSceneCards();
  setAuthToken('second-session');
  const second = api.getSceneCards();
  const latest = { cards: ['new'] };
  pending[1].resolve(response(latest)); await second;
  pending[0].resolve(response({ cards: ['old-account'] })); await first;
  assert.equal(api.getCachedSceneCards(), latest);
  const old = api.getSceneCards();
  const fresh = api.getSceneCards(true);
  const refreshed = { cards: ['refreshed'] };
  pending[3].resolve(response(refreshed)); await fresh;
  pending[2].resolve(response({ cards: ['late'] })); await old;
  assert.equal(api.getCachedSceneCards(), refreshed);
  const controller = new AbortController();
  const aborted = api.getSceneCards(false, controller.signal);
  controller.abort();
  pending[4].resolve(response({ cards: ['aborted'] })); await aborted;
  assert.equal(api.getCachedSceneCards(), refreshed);
});
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function fixture() {
  const clock = new Clock();
  const { createLatestCheck } = loadTs('asyncTasks.ts', clock);
  const requests = [], results = [], errors = [], busy = [];
  const checks = createLatestCheck((value, signal) => {
    const pending = deferred(); requests.push({ ...pending, value, signal }); return pending.promise;
  }, { result: (value) => results.push(value), error: (error) => errors.push(error), busy: (value) => busy.push(value) });
  return { clock, requests, results, errors, busy, checks };
}

test('rapid edits debounce into one request carrying the latest draft', async () => {
  const f = fixture();
  f.checks.schedule('first', 500); f.checks.schedule('second', 500);
  assert.equal(f.clock.pending.size, 1);
  assert.equal(f.busy.at(-1), true, 'approval must be blocked during debounce');
  f.clock.run(); assert.equal(f.requests.length, 1); assert.equal(f.requests[0].value, 'second');
  f.requests[0].resolve('latest'); await flush();
  assert.deepEqual(f.results, ['latest']); assert.equal(f.busy.at(-1), false);
});

test('a late response after abort cannot overwrite a newer edit or clear its busy state', async () => {
  const f = fixture();
  f.checks.schedule('old', 0); f.checks.schedule('new', 500);
  assert.equal(f.requests[0].signal.aborted, true);
  f.requests[0].resolve('old response'); await flush();
  assert.deepEqual(f.results, []); assert.equal(f.busy.at(-1), true);
  f.clock.run(); f.requests[1].resolve('new response'); await flush();
  assert.deepEqual(f.results, ['new response']); assert.equal(f.clock.pending.size, 0, 'applying a response must not schedule a second check');
});

test('an obsolete transport error does not surface over a newer request', async () => {
  const f = fixture();
  f.checks.schedule('old', 0); f.checks.schedule('new', 0);
  f.requests[0].reject(new Error('late failure')); await flush();
  assert.equal(f.errors.length, 0); assert.equal(f.busy.at(-1), true);
  f.requests[1].reject(new Error('current failure')); await flush();
  assert.equal(f.errors[0].message, 'current failure'); assert.equal(f.busy.at(-1), false);
});

test('cancel invalidates in-flight callbacks and removes pending debounce timers', async () => {
  const f = fixture();
  f.checks.schedule('active', 0); f.checks.cancel();
  f.requests[0].resolve('ignored'); await flush();
  assert.deepEqual(f.results, []); assert.equal(f.busy.at(-1), false);
  f.checks.schedule('waiting', 500); f.checks.cancel(); assert.equal(f.clock.pending.size, 0);
});

test('polling retries after a rejected request and waits for completion before its next timer', async () => {
  const clock = new Clock();
  const { startPolling } = loadTs('asyncTasks.ts', clock);
  const attempts = [];
  const stop = startPolling(() => { const pending = deferred(); attempts.push(pending); return pending.promise; }, 2000);
  clock.run(); assert.equal(clock.pending.size, 0, 'polls must not overlap');
  attempts[0].reject(new Error('temporary network failure')); await flush();
  assert.equal(clock.pending.size, 1); clock.run(); assert.equal(attempts.length, 2);
  stop(); attempts[1].resolve(); await flush(); assert.equal(clock.pending.size, 0);
});

test('stopping polling also clears a scheduled retry', () => {
  const clock = new Clock();
  const { startPolling } = loadTs('asyncTasks.ts', clock);
  const stop = startPolling(async () => {}, 2000); stop(); assert.equal(clock.pending.size, 0);
});

test('shared modal prevents backdrop dismissal and disables close while submitting', () => {
  const { ModalShell } = loadTs('skillReviewShared.tsx');
  let closed = 0;
  const props = { title: 'Review', onClose: () => closed++, children: 'body' };
  const target = {};
  const blocked = ModalShell({ ...props, busy: true });
  blocked.props.onMouseDown({ target, currentTarget: target }); assert.equal(closed, 0);
  const dialog = blocked.props.children;
  const close = dialog.props.children[0].props.children[1]; assert.equal(close.props.disabled, true);
  const idle = ModalShell({ ...props, busy: false });
  idle.props.onMouseDown({ target: {}, currentTarget: target }); assert.equal(closed, 0);
  idle.props.onMouseDown({ target, currentTarget: target }); assert.equal(closed, 1);
});

test('review contracts reject missing resolutions and absent save-response atoms at compile time', () => {
  const filename = path.join(root, 'tests/__skill_review_contract__.ts');
  const source = `import { SkillReviewDraft, SkillReviewPayloads } from '../client/src/types';
    import { api } from '../client/src/services/api';
    declare const draft: SkillReviewDraft;
    const { stale_resolutions, ...incomplete } = draft;
    const invalid: SkillReviewPayloads['approve'] = { ...incomplete, comment: '' };
    const valid: SkillReviewPayloads['approve'] = { ...draft, comment: '' };
    declare const saved: Awaited<ReturnType<typeof api.saveSkillDraft>>;
    saved.atoms;
    api.submitSkillReview('skill', 'abandon', { revision_token: 'token', queue: [] });`;
  const options = { strict: true, noEmit: true, skipLibCheck: true, target: ts.ScriptTarget.ESNext,
    module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Node10 };
  const host = ts.createCompilerHost(options);
  const readFile = host.readFile.bind(host), exists = host.fileExists.bind(host), getSourceFile = host.getSourceFile.bind(host);
  host.readFile = (file) => path.resolve(file) === filename ? source : readFile(file);
  host.fileExists = (file) => path.resolve(file) === filename || exists(file);
  host.getSourceFile = (file, language, onError) => path.resolve(file) === filename
    ? ts.createSourceFile(file, source, language) : getSourceFile(file, language, onError);
  const program = ts.createProgram([filename, path.join(root, 'client/src/vite-env.d.ts')], options, host);
  const diagnostics = ts.getPreEmitDiagnostics(program);
  assert.equal(diagnostics.length, 2, diagnostics.map((d) => ts.flattenDiagnosticMessageText(d.messageText, '\n')).join('\n'));
  assert.match(ts.flattenDiagnosticMessageText(diagnostics[0].messageText, '\n'), /stale_resolutions/);
  assert.match(ts.flattenDiagnosticMessageText(diagnostics[1].messageText, '\n'), /atoms/);
});
