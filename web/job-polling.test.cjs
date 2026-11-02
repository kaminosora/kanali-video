/* Exercise the actual job polling/card functions without launching a browser. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
function element(tag = 'div') {
  return {
    tag, children: [], value: '', textContent: '', hidden: false, disabled: false,
    classList: { toggle() {}, add() {}, remove() {} },
    set id(value) { this._id = value; nodes.set(value, this); }, get id() { return this._id; },
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    before() {}, setAttribute(name, value) { this[name] = value; }, removeAttribute(name) { delete this[name]; },
    querySelector() { return this.label || (this.label = element('span')); }
  };
}
for (const id of ['source-video','source-image','render','trim-start','trim-end','job-count','jobs','connection']) element().id = id;
nodes.get('trim-start').value = '0'; nodes.get('trim-end').value = '5';
let online = false;
const requests = [];
const sandbox = {
  window: { PanoramaUI: require('./ui-core.js') },
  document: { getElementById: id => nodes.get(id) || null, createElement: element },
  setTimeout: () => 1, clearTimeout() {}, AbortController, console,
  fetch: async (path, options) => {
    requests.push({path,options});
    if (!online) throw new TypeError('Failed to fetch');
    const data = path === '/api/state'
      ? {session_token:'new-session',version:'1.0.1',config:{zoom:99},source:{id:'server-source'}}
      : {jobs:[{id:'job-1',status:'completed',progress:1,log_url:'/output/job-1/render.log'}]};
    return {ok:true,json:async()=>data};
  }
};
const source = fs.readFileSync(require.resolve('./app.js'), 'utf8').replace(/  initialize\(\);\s*\}\)\(\);\s*$/, `
  initializing = false; source = {id:'editing-source',kind:'video',duration:20}; mesh = {}; config = {zoom:1.6};
  jobs = [{id:'job-1',status:'preparing',progress:0,phase_progress:.42}];
  globalThis.testUI = {pollJobs,renderJobs,snapshot:()=>({jobs,config,source,sessionToken,jobsDisconnected})};
})();`);
vm.runInNewContext(source, sandbox, {filename:'app.js'});
const ui = sandbox.testUI;
function descendants(node) { return [node,...node.children.flatMap(descendants)]; }
(async () => {
  await assert.rejects(ui.pollJobs(), /Failed to fetch/);
  assert.equal(ui.snapshot().jobsDisconnected, true);
  assert.equal(ui.snapshot().jobs[0].status, 'preparing'); // A disconnect is never a cancellation.
  assert.equal(nodes.get('render').disabled, true);
  assert.equal(nodes.get('job-connection-warning').hidden, false);
  assert.match(nodes.get('job-connection-warning').children[0].textContent, /不要重复提交/);
  assert.ok(descendants(nodes.get('jobs')).some(node => /上次状态/.test(node.textContent)));
  online = true;
  await ui.pollJobs();
  assert.equal(ui.snapshot().jobsDisconnected, false);
  assert.equal(ui.snapshot().sessionToken, 'new-session');
  assert.equal(ui.snapshot().config.zoom, 1.6);
  assert.equal(ui.snapshot().source.id, 'editing-source');
  assert.equal(nodes.get('job-connection-warning').hidden, true);
  assert.equal(nodes.get('connection').textContent, 'LOCAL · 1.0.1');
  assert.equal(nodes.get('render').disabled, false);
  assert.ok(requests.every(request => request.options.signal)); // Timeout is opt-in for these GETs.
  for (const status of ['failed','cancelled']) {
    ui.renderJobs([{id:'job-1',status,log_url:'/output/job-1/render.log'}]);
    assert.ok(descendants(nodes.get('jobs')).some(node => node.tag === 'a' && node.href === '/output/job-1/render.log'));
  }
  console.log('Job disconnect/recovery, token refresh, state preservation and terminal log checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
