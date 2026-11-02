/* Actual export handlers in a VM: no server, GPU, or browser is required. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
function element(tag='div') {
  const node = {tag,children:[],value:'',textContent:'',hidden:false,disabled:false,style:{},options:[],
    classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},removeAttribute(){},before(){},
    append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},
    querySelector(){return this.label||(this.label=element('span'));},
    set id(value){this._id=value;nodes.set(value,this);},get id(){return this._id;},
    get selectedOptions(){return this.options.filter(option=>option.value===this.value);}};
  return node;
}
const html=fs.readFileSync(require.resolve('./index.html'),'utf8');
for(const match of html.matchAll(/\bid="([^"]+)"/g)) element().id=match[1];
for(const [id,value] of Object.entries({'trim-start':'0','trim-end':'207.092',fps:'0',supersample:'2',quality:'high',renderer:'gpu',encoder:'h264_nvenc','output-name':'test'})) nodes.get(id).value=value;
nodes.get('renderer').options=['auto','gpu','blender'].map(value=>({value,disabled:false}));
nodes.get('encoder').options=['auto','h264_nvenc','h264_qsv','h264_amf','libx264'].map(value=>({value,disabled:false}));
const requests=[],timers=new Map(),delays=[];let timerId=0,estimateFails=false;
const sandbox={console,AbortController,Option:element,ResizeObserver:class{observe(){}},
  window:{PanoramaUI:require('./ui-core.js'),addEventListener(){}},
  document:{getElementById:id=>nodes.get(id)||null,createElement:element,querySelectorAll:()=>[]},
  setTimeout(callback,delay){const id=++timerId;timers.set(id,{callback,delay});delays.push(delay);return id;},
  clearTimeout(id){timers.delete(id);},
  fetch:async(path,options)=>{
    requests.push({path,options,body:options.body?JSON.parse(options.body):null});
    if(path==='/api/estimate'&&estimateFails) throw new TypeError('estimate offline');
    const result=path==='/api/render'?{id:'new-job',status:'completed'}
      :path==='/api/estimate'?{limit_bytes:500000000,target_bytes:480000000,budget_bitrate:18000000,frames:12426,duration:207.1,enforced:true}
      :{gpu:{available:false},encoders:{h264_nvenc:{available:false},h264_qsv:{available:false},h264_amf:{available:false},libx264:{available:true}}};
    return {ok:true,json:async()=>result};
  }};
const app=fs.readFileSync(require.resolve('./app.js'),'utf8').replace(/  initialize\(\);\s*\}\)\(\);\s*$/,`
  initializing=false;sessionToken='token';source={id:'source-1',kind:'video',duration:207.09166666666667};mesh={};
  environmentState={ready:true,tools:{ffmpeg:{path:'ffmpeg.exe'}}};capabilitiesPath='ffmpeg.exe';
  globalThis.testUI={wireEvents,scheduleEstimate,detectCapabilities};
})();`);
vm.runInNewContext(app,sandbox,{filename:'app.js'});
const ui=sandbox.testUI;ui.wireEvents();
async function flushEstimate(){for(const [id,timer] of [...timers])if(timer.delay===300){timers.delete(id);await timer.callback();}}
(async()=>{
  await nodes.get('render').onclick();
  let submitted=requests.filter(r=>r.path==='/api/render').at(-1).body;
  assert.equal(submitted.renderer,'gpu');assert.equal(submitted.encoder,'h264_nvenc');
  assert.equal(submitted.end,207.09166666666667);
  nodes.get('quality').value='master';nodes.get('quality').onchange();
  assert.equal(nodes.get('renderer').value,'blender');assert.equal(nodes.get('renderer').disabled,true);
  await nodes.get('render').onclick();submitted=requests.filter(r=>r.path==='/api/render').at(-1).body;
  assert.equal(submitted.renderer,'blender');assert.equal(submitted.encoder,'auto');
  assert.match(nodes.get('budget-info').textContent,/不执行 500 MB/);
  nodes.get('quality').value='high';nodes.get('quality').onchange();
  assert.equal(nodes.get('renderer').value,'gpu');assert.equal(nodes.get('encoder').value,'h264_nvenc');
  nodes.get('fps').value='60';nodes.get('fps').onchange();await flushEstimate();
  const estimate=requests.filter(r=>r.path==='/api/estimate').at(-1).body;
  assert.equal(requests.filter(r=>r.path==='/api/estimate').length,1);
  assert.equal(estimate.fps,60);assert.equal(estimate.end,207.09166666666667);
  assert.match(nodes.get('budget-info').textContent,/18\.00 Mbps/);
  const previousToast=nodes.get('toast').textContent;
  estimateFails=true;ui.scheduleEstimate();await flushEstimate();
  assert.equal(nodes.get('toast').textContent,previousToast);
  assert.match(nodes.get('budget-info').textContent,/预算暂未取得/);
  await ui.detectCapabilities();
  assert.equal(nodes.get('renderer').value,'auto');assert.equal(nodes.get('encoder').value,'auto');
  assert.match(nodes.get('capability-status').textContent,/回退.*Blender.*CPU H.264/);
  assert.ok(delays.includes(80000));
  assert.ok(requests.find(r=>r.path==='/api/capabilities').options.signal);
  assert.ok(requests.filter(r=>r.path==='/api/render'||r.path==='/api/estimate').every(r=>!r.options.signal));
  console.log('Export selections, master override, budget debounce, quiet errors and capability fallback checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
