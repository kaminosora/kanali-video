/* Real UI handlers and WebGL bindings, with a deterministic DOM/GL test adapter. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const core = require('./ui-core.js');
assert.deepEqual(core.brightnessOptions({}), {brightness_enabled:false,brightness_floor:.6,brightness_power:2,brightness_strength:1});
assert.deepEqual(core.brightnessOptions({brightness_enabled:true,brightness_floor:-2,brightness_power:50,brightness_strength:NaN}),
  {brightness_enabled:true,brightness_floor:0,brightness_power:8,brightness_strength:1});
assert.match(core.brightnessSummaryLabel({enabled:true,angle_min_deg:2,angle_max_deg:49,response_min:.73,response_max:1,gain_min:.73,gain_max:1}), /2.0°–49.0°/);
assert.match(core.brightnessSummaryLabel({enabled:false,angle_min_deg:0,angle_max_deg:45,response_min:.8,response_max:1,gain_min:1,gain_max:1}), /未开启/);

const nodes = new Map();
function element(tag='div') {
  const listeners={};
  return {tag,children:[],value:'',textContent:'',hidden:false,disabled:false,checked:false,style:{},options:[],listeners,
    classList:{toggle(){},add(){},remove(){}},setAttribute(){},removeAttribute(){},before(){},pause(){},load(){},
    addEventListener(name,callback){listeners[name]=callback;},
    append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},
    querySelector(){return this.child||(this.child=element('span'));},
    set id(value){this._id=value;nodes.set(value,this);},get id(){return this._id;}};
}
const html=fs.readFileSync(require.resolve('./index.html'),'utf8');
for(const match of html.matchAll(/\bid="([^"]+)"/g)) element().id=match[1];
for(const [id,value] of Object.entries({'trim-start':'0','trim-end':'5',quality:'high',fps:'30',supersample:'1',renderer:'auto',encoder:'auto','output-name':'brightness'})) nodes.get(id).value=value;
const requests=[];
const sandbox={console,devicePixelRatio:1,ResizeObserver:class{observe(){}},
  window:{PanoramaUI:core,addEventListener(){}},
  document:{getElementById:id=>nodes.get(id)||null,createElement:element,createTextNode:text=>({textContent:text}),querySelectorAll:()=>[]},
  setTimeout(){return 1;},clearTimeout(){},
  fetch:async(path,options)=>{requests.push({path,body:options.body?JSON.parse(options.body):null});return {ok:true,json:async()=>path==='/api/preset?name=old'
    ?{name:'old',config:{fit:'cover',zoom:1,view_azimuth_deg:45,view_elevation_deg:10,view_distance_mm:650}}
    :path==='/api/brightness-calibration'?{cancelled:true}:{id:'render-test',status:'completed'}};}};
const app=fs.readFileSync(require.resolve('./app.js'),'utf8').replace(/  initialize\(\);\s*\}\)\(\);\s*$/,`
  initializing=false;sessionToken='test';source={id:'source',kind:'video',duration:5,width:1920,height:1080};mesh={};
  defaults={fit:'cover',zoom:1,view_azimuth_deg:45,view_elevation_deg:10,view_distance_mm:650};config={...defaults};
  globalThis.testUI={buildControls,wireEvents,syncControls,MeshPreview,snapshot:()=>({config,simulateAngle}),
    setViews:views=>{glViews=views;},setMesh:value=>{mesh=value;}};
})();`);
vm.runInNewContext(app,sandbox,{filename:'app.js'});
const ui=sandbox.testUI;ui.buildControls();ui.wireEvents();ui.syncControls();
assert.equal(nodes.get('brightness-enabled').checked,false);
assert.equal(nodes.get('number-brightness_floor').value,60);
nodes.get('brightness-enabled').checked=true;nodes.get('brightness-enabled').onchange();
nodes.get('range-brightness_floor').value='35';nodes.get('range-brightness_floor').listeners.input();
nodes.get('number-brightness_strength').value='40';nodes.get('number-brightness_strength').listeners.change();
assert.equal(ui.snapshot().config.brightness_enabled,true);
assert.equal(ui.snapshot().config.brightness_floor,.35);
assert.equal(ui.snapshot().config.brightness_strength,.4);
nodes.get('reset-geometry').onclick();
assert.equal(ui.snapshot().config.brightness_floor,.35,'Resetting geometry preserves explicit brightness calibration');
assert.equal(ui.snapshot().config.brightness_enabled,true);
let draws=0;ui.setViews([{unfolded:false,draw(){draws++;}},{unfolded:true,draw(){throw new Error('Simulation must not redraw/change the output preview');}}]);
nodes.get('simulate-angle').checked=true;nodes.get('simulate-angle').onchange();
assert.equal(ui.snapshot().simulateAngle,true);assert.equal(draws,1);
assert.equal(Object.hasOwn(ui.snapshot().config,'simulateAngle'),false);
assert.equal(Object.hasOwn(ui.snapshot().config,'simulate_angle'),false);
ui.setViews([]);

function mockGL() {
  const uploads=[],uniforms=[],shaders=[];let bound;
  return {uploads,uniforms,shaders,FLOAT:1,ARRAY_BUFFER:2,STATIC_DRAW:3,VERTEX_SHADER:4,FRAGMENT_SHADER:5,
    createShader(type){const s={type};shaders.push(s);return s;},shaderSource(s,text){s.text=text;},compileShader(){},getShaderParameter(){return true;},
    createProgram(){return {};},attachShader(){},linkProgram(){},getProgramParameter(){return true;},deleteShader(){},useProgram(){},
    createBuffer(){return {};},getAttribLocation(_p,name){return name;},getUniformLocation(_p,name){return name;},
    createTexture(){return {};},bindTexture(){},texParameteri(){},pixelStorei(){},uniform1i(name,value){uniforms.push([name,value]);},
    activeTexture(){},texImage2D(){},bindBuffer(_t,buffer){bound=buffer;},bufferData(_t,data){uploads.push({buffer:bound,data:Array.from(data)});},
    viewport(){},clearColor(){},clear(){},uniform2f(){},uniform4fv(){},enableVertexAttribArray(){},vertexAttribPointer(){},drawArrays(){}};
}
const gl=mockGL(),canvas={getContext(){return gl;},addEventListener(){},clientWidth:400,clientHeight:200};
const view=new ui.MeshPreview(canvas,false);view.hasTexture=true;
const data={atlas:[[0,0],[1,0],[1,1]],projected:[[0,0],[1,0],[1,1]],uv:[[0,0],[1,0],[1,1]],triangles:[[2,0,1]],bounds:[0,0,1,1],brightness_gain:[.7,.8,.9],angular_response:[1,.9,.8]};
view.setMesh(data,{fit:'cover',presentation:'plain'});
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-6,`${a} != ${b}`);
gl.uploads.find(item=>item.buffer===view.gainBuffer).data.forEach((v,i)=>near(v,[.9,.7,.8][i]));
gl.uploads.find(item=>item.buffer===view.responseBuffer).data.forEach((v,i)=>near(v,[.8,1,.9][i]));
assert.deepEqual(gl.uniforms.filter(u=>u[0]==='uSimulateAngle').at(-1),['uSimulateAngle',1]);
view.unfolded=true;view.draw();
assert.deepEqual(gl.uniforms.filter(u=>u[0]==='uSimulateAngle').at(-1),['uSimulateAngle',0]);
delete data.brightness_gain;delete data.angular_response;view.setMesh(data,{fit:'cover',presentation:'plain'});
assert.deepEqual(gl.uploads.filter(item=>item.buffer===view.gainBuffer).at(-1).data,[1,1,1]);
const fragment=gl.shaders.find(s=>s.type===gl.FRAGMENT_SHADER).text;
assert.ok(fragment.indexOf('texture2D(uSource')<fragment.indexOf('srgbToLinear(color)'),'Gain must apply to the final composite');
assert.match(fragment,/if \(factor < 1\.\)/,'Identity must bypass transfer conversion');
assert.match(fragment,/linearToSrgb\(srgbToLinear\(color\) \* factor\)/);

(async()=>{
  await nodes.get('render').onclick();
  const submitted=requests.find(r=>r.path==='/api/render').body;
  assert.equal(submitted.config.brightness_floor,.35);
  assert.equal(submitted.config.brightness_strength,.4);
  assert.ok(!JSON.stringify(submitted).includes('simulate'));
  await nodes.get('brightness-calibration').onclick();
  assert.ok(requests.some(r=>r.path==='/api/brightness-calibration'));
  nodes.get('presets').value='old';await nodes.get('presets').onchange();
  assert.equal(ui.snapshot().config.brightness_enabled,false,'Old presets must not inherit the previous brightness state');
  assert.equal(ui.snapshot().config.brightness_floor,.6);
  assert.equal(nodes.get('brightness-enabled').checked,false);
  assert.match(html,/90°/);assert.match(html,/非厂家实测/);
  console.log('Brightness controls, old presets, reset, export isolation and actual WebGL attribute/uniform bindings passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
