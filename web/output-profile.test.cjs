/* Exercise the actual atlas preview and labels against the server output profile. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(require.resolve('./index.html'), 'utf8');
const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], {textContent:''}]));
const sandbox = {console,window:{PanoramaUI:require('./ui-core.js')},document:{getElementById:id=>nodes.get(id)||null}};
const app = fs.readFileSync(require.resolve('./app.js'), 'utf8').replace(/  initialize\(\);\s*\}\)\(\);\s*$/, `
  globalThis.testUI={MeshPreview,setProfile:value=>applyOutputProfile(value)};
})();`);
vm.runInNewContext(app,sandbox,{filename:'app.js'});
const data={atlas:[[0,0],[1,0],[1,1]],projected:[[10,20],[16,20],[16,22]],uv:[[0,0],[1,0],[1,1]],
  triangles:[[0,1,2]],bounds:[10,20,16,22]};
function preview(unfolded) {
  const view=Object.create(sandbox.testUI.MeshPreview.prototype);
  Object.assign(view,{unfolded,gl:{bindBuffer(){},bufferData(){}},draw(){}});
  view.setMesh(data,{fit:'cover',presentation:'plain'});
  return view;
}
assert.equal(preview(true).aspect,2,'Fallback atlas must be 2:1, including older state fixtures without output_profile');
sandbox.testUI.setProfile({width:2160,height:1080,aspect:2,label:'KANALI 2:1'});
assert.equal(preview(true).aspect,2);
assert.equal(preview(false).aspect,3,'Observer aspect follows projected geometry, not the upload raster');
assert.equal(nodes.get('output-header').textContent,'2160 × 1080 / ≤ 60 FPS');
assert.equal(nodes.get('output-resolution').textContent,'2160 × 1080');
assert.equal(nodes.get('output-dimensions').textContent,'2160 × 1080');
assert.equal(nodes.get('output-profile-label').textContent,'KANALI 2:1 上传画布');
// Dimensions, not an independently stale aspect field, determine the atlas shape.
sandbox.testUI.setProfile({width:1920,height:1080,aspect:99,label:'Test profile'});
assert.equal(preview(true).aspect,16/9);
assert.equal(nodes.get('output-resolution').textContent,'1920 × 1080');
sandbox.testUI.setProfile(undefined);
assert.equal(preview(true).aspect,2);
assert.equal(nodes.get('output-resolution').textContent,'2160 × 1080');
assert.doesNotMatch(html,/2240|原生展开画幅/);
assert.match(app,/applyOutputProfile\(state\.output_profile\)/);
console.log('Atlas geometry, upload labels and server output profile checks passed');
