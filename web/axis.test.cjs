/* Exercise actual UI handlers: assumed/actual geometry must stay separate. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const core=require('./ui-core.js');
assert.deepEqual(core.axisLimits({screen_width_mm:100,curve_length_mm:20,bend_deg:90}),[.1,.9]);
assert.equal(core.axisCenter({},undefined),.5);
assert.equal(core.axisCenter({screen_width_mm:100,curve_length_mm:20,bend_deg:90},.99),.9);
assert.deepEqual(core.axisLimits({screen_width_mm:100,curve_length_mm:100,bend_deg:0}),[.001,.999]);
assert.equal(core.brightnessOptions({}).brightness_shoulder,1);
const nodes=new Map();
function element(){const listeners={};return {children:[],value:'',textContent:'',hidden:false,disabled:false,checked:false,style:{},options:[],listeners,
  classList:{toggle(){},add(){},remove(){}},setAttribute(){},removeAttribute(){},before(){},pause(){},load(){},
  addEventListener(name,callback){listeners[name]=callback;},append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},
  querySelector(){return this.child||(this.child=element());},
  set id(value){this._id=value;nodes.set(value,this);},get id(){return this._id;}};}
const html=fs.readFileSync(require.resolve('./index.html'),'utf8');
for(const match of html.matchAll(/\bid="([^"]+)"/g))element().id=match[1];
for(const [id,value] of Object.entries({'trim-start':'0','trim-end':'5',quality:'high',fps:'30',supersample:'1',renderer:'auto',encoder:'auto','output-name':'axis'}))nodes.get(id).value=value;
const renderMesh={projected:[[0,0],[1,0],[0,1]],bounds:[0,0,1,1],uv:[[.1,.2],[.9,.2],[.1,.8]],atlas:[[0,0],[1,0],[0,1]],brightness_gain:[.8,.9,1],angular_response:[1,1,1],triangles:[[0,1,2]],boundary_uv:[]};
const actual={projected:[[2,0],[4,0],[2,2]],bounds:[2,0,4,2],angular_response:[.7,.8,.9],render_bend_center_u:.5,actual_bend_center_u:.52,delta_u:.02,delta_pixels:43.2,render_center_px:1080,actual_center_px:1123.2};
const requests=[],modes=['corrected','uncorrected'].map(mode=>Object.assign(element(),{dataset:{mode}}));
const sandbox={console,devicePixelRatio:1,ResizeObserver:class{observe(){}},window:{PanoramaUI:core,addEventListener(){}},
  document:{getElementById:id=>nodes.get(id)||null,createElement:element,createTextNode:text=>({textContent:text}),querySelectorAll:selector=>selector==='[data-mode]'?modes:[]},
  setTimeout(){return 1;},clearTimeout(){},fetch:async(path,options={})=>{const body=options.body?JSON.parse(options.body):null;requests.push({path,body});return {ok:true,json:async()=>
    path==='/api/mesh'?{config:body.config,mesh:renderMesh,axis_preview:body.axis_preview?actual:null}:path==='/api/preset?name=old'?{name:'old',config:{fit:'cover',zoom:1,view_azimuth_deg:45,view_elevation_deg:10,view_distance_mm:650}}:path==='/api/axis-calibration'?{cancelled:true}:{id:'render-test',status:'completed'}};}};
const app=fs.readFileSync(require.resolve('./app.js'),'utf8').replace(/  initialize\(\);\s*\}\)\(\);\s*$/,`
  initializing=false;sessionToken='test';source={id:'source',kind:'image',duration:5,width:2160,height:1080};mesh={};
  defaults={fit:'cover',zoom:1,view_azimuth_deg:45,view_elevation_deg:10,view_distance_mm:650};config={...defaults};
  drawOverlay=()=>{};
  globalThis.testUI={buildControls,wireEvents,syncControls,updateMesh,refreshPreviewMeshes,
    snapshot:()=>({config,axisPreviewEnabled,actualBendCenterU,axisPreviewData}),
    runMesh:()=>updateMesh(meshSerial),setViews:views=>{glViews=views;}};
})();`);
vm.runInNewContext(app,sandbox,{filename:'app.js'});
const ui=sandbox.testUI;ui.buildControls();ui.wireEvents();ui.syncControls();
assert.equal(nodes.get('axis-preview-enabled').checked,false);
assert.equal(nodes.get('number-bend_center_u').value,50);
for(const stem of ['bend_center_u','actual-bend-center']) {
  const number=nodes.get('number-'+stem),range=nodes.get('range-'+stem);
  assert.equal(number.step,'any','Number entries must accept the exact continuous centre');
  assert.equal(Number(range.min),4.59);assert.equal(Number(range.max),95.41);
  assert.equal(Number(range.step),.01);
  assert.ok(Number(range.min)>=Number(number.min));assert.ok(Number(range.max)<=Number(number.max));
  assert.ok(Math.abs((50-Number(range.min))/.01-Math.round((50-Number(range.min))/.01))<1e-8,'The slider step grid must include 50%');
}
Object.assign(ui.snapshot().config,{screen_width_mm:100,curve_length_mm:99.999999,bend_deg:90});
ui.syncControls();
for(const stem of ['bend_center_u','actual-bend-center']) {
  const range=nodes.get('range-'+stem);assert.equal(Number(range.min),50);assert.equal(Number(range.max),50);
  assert.equal(Number(range.value),50,'Almost full-width curves retain the centre on the slider');
}
Object.assign(ui.snapshot().config,{screen_width_mm:152.65185185185183,curve_length_mm:14,bend_deg:90});
ui.syncControls();
nodes.get('number-bend_center_u').value='50.003';nodes.get('number-bend_center_u').listeners.change();
assert.ok(Math.abs(ui.snapshot().config.bend_center_u-.50003)<1e-12,'Exact numeric centres must not be quantized to the slider grid');
assert.equal(Number(nodes.get('range-bend_center_u').value),50);
nodes.get('number-bend_center_u').value='50';nodes.get('number-bend_center_u').listeners.change();
assert.equal(nodes.get('number-brightness_shoulder').value,1);
nodes.get('range-brightness_shoulder').value='2';nodes.get('range-brightness_shoulder').listeners.input();
assert.equal(ui.snapshot().config.brightness_shoulder,2);
nodes.get('axis-start').onclick();
assert.equal(ui.snapshot().axisPreviewEnabled,true);
nodes.get('range-actual-bend-center').value='52';nodes.get('range-actual-bend-center').listeners.input();
assert.equal(ui.snapshot().actualBendCenterU,.52);assert.equal(ui.snapshot().config.bend_center_u,.5);
const received=[];ui.setViews([{unfolded:false,decorationURL:null,setMesh:data=>received.push(['observer',data])},{unfolded:true,decorationURL:null,setMesh:data=>received.push(['atlas',data])}]);
(async()=>{
  await ui.runMesh();
  let request=requests.find(r=>r.path==='/api/mesh');
  assert.equal(request.body.config.bend_center_u,.5);assert.equal(request.body.axis_preview.actual_bend_center_u,.52);
  assert.ok(!Object.hasOwn(request.body.config,'actual_bend_center_u'));
  let observer=received.find(r=>r[0]==='observer')[1],atlas=received.find(r=>r[0]==='atlas')[1];
  assert.strictEqual(observer.projected,actual.projected);assert.strictEqual(observer.uv,renderMesh.uv);
  assert.strictEqual(observer.brightness_gain,renderMesh.brightness_gain);assert.strictEqual(observer.angular_response,actual.angular_response);
  assert.strictEqual(atlas,renderMesh,'Unfolded output must not inherit simulated positions/response');
  received.length=0;modes[1].onclick();
  assert.strictEqual(received.find(r=>r[0]==='observer')[1].projected,actual.projected,'Comparison toggles must preserve actual test geometry');
  await nodes.get('render').onclick();
  const submitted=requests.find(r=>r.path==='/api/render').body;
  assert.equal(submitted.config.bend_center_u,.5);assert.ok(!JSON.stringify(submitted).includes('axis_preview'));
  assert.ok(!JSON.stringify(submitted).includes('actual_bend'));
  nodes.get('zoom').value='2';nodes.get('zoom').onchange();
  nodes.get('axis-apply').onclick();
  assert.equal(ui.snapshot().config.bend_center_u,.52);assert.equal(ui.snapshot().config.zoom,1);assert.equal(ui.snapshot().config.pan_x,0);
  nodes.get('axis-preview-enabled').checked=false;nodes.get('axis-preview-enabled').onchange();
  await ui.runMesh();assert.ok(!Object.hasOwn(requests.filter(r=>r.path==='/api/mesh').at(-1).body,'axis_preview'));
  await nodes.get('axis-calibration').onclick();assert.ok(requests.some(r=>r.path==='/api/axis-calibration'));
  nodes.get('presets').value='old';await nodes.get('presets').onchange();
  assert.equal(ui.snapshot().config.bend_center_u,.5);assert.equal(ui.snapshot().config.brightness_shoulder,1);
  assert.match(html,/人工匹配/);assert.match(html,/并非自动测量/);
  console.log('Axis UI geometry separation, manual calibration, output isolation and old presets passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
