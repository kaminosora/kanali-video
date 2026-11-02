/* PANORAMA Mapper — local UI; geometry is supplied exclusively by /api/mesh. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const { fitRect, uvToCanvas, meshUVSpan, trimRange, trimDraft, zoomForFit, decorationDomain, frameForMode, activeStandee, brightnessOptions, brightnessSummaryLabel, axisLimits, axisCenter, axisSummaryLabel, newestJobs, jobFeedback, elapsedLabel, exportTrim, renderPreferences, estimateLabel, jobResultLabel, accelerationLabel } = window.PanoramaUI;
  const video = $('source-video'), image = $('source-image');
  const activeStates = new Set(['queued', 'preparing', 'rendering', 'encoding']);
  const parameters = [
    ['view_azimuth_deg', '水平角度', '°', 15, 75, 0.5, 'view-controls'],
    ['view_elevation_deg', '俯视角度', '°', -20, 35, 0.5, 'view-controls'],
    ['view_distance_mm', '观看距离', 'mm', 250, 2000, 10, 'view-controls'],
    ['screen_height_mm', '有效高度', 'mm', 30, 200, 0.1, 'screen-controls'],
    ['screen_width_mm', '展开宽度', 'mm', 60, 400, 0.1, 'screen-controls'],
    ['bend_deg', '两翼弯折', '°', 0, 110, 1, 'screen-controls'],
    ['curve_length_mm', 'G2 过渡弧长', 'mm', 2, 40, 0.5, 'screen-controls'],
    ['bend_center_u', '渲染假设轴 · 写入导出', '%', 0.1, 99.9, 0.01, 'axis-render-controls', 100],
    ['brightness_floor', '90° 掠射相对亮度', '%', 0, 100, 1, 'brightness-controls', 100],
    ['brightness_power', '衰减曲线指数', '', 0.25, 8, 0.05, 'brightness-controls'],
    ['brightness_shoulder', '肩部过渡陡峭度', '', 0.25, 8, 0.05, 'brightness-controls'],
    ['brightness_strength', '补偿强度', '%', 0, 100, 1, 'brightness-controls', 100]
  ];
  let config = {}, defaults = {}, source = null, mesh = null, sessionToken = '', presetNames = [];
  let comparison = 'corrected', overlayVisible = true, importing = false, submitting = false;
  let meshTimer, meshSerial = 0, meshPending = false, toastTimer, drag = null, jobs = [];
  let dirtyTexture = true, lastFrameTime = -1, glViews = [], initializing = true;
  const decorationCache = new Map();
  let jobsDisconnected = false, jobsPolling = null, connectionReadyLabel = 'LOCAL · READY';
  let environmentState = null, environmentPending = false, environmentDirty = false;
  let capabilitiesState = null, capabilitiesPending = false, capabilitiesPath = null, capabilitiesRequested = false;
  let estimateTimer, estimateSerial = 0, previousQuality = 'high', highPreferences = {renderer:'auto',encoder:'auto'};
  let outputProfile = {width:2160,height:1080,aspect:2,label:'KANALI 2:1'};
  // This is an observer-only display option; never persist or send it as render config.
  let simulateAngle = false;
  // Actual geometry is a local hypothesis for testing; only bend_center_u exports.
  let axisPreviewEnabled = false, actualBendCenterU = .5, axisPreviewData = null;

  function applyOutputProfile(value) {
    const valid = value && Number.isSafeInteger(value.width) && value.width > 0 && Number.isSafeInteger(value.height) && value.height > 0;
    outputProfile = valid
      ? {width:value.width,height:value.height,aspect:value.width/value.height,label:value.label || 'KANALI 2:1'}
      : {width:2160,height:1080,aspect:2,label:'KANALI 2:1'};
    const dimensions = `${outputProfile.width} × ${outputProfile.height}`;
    const labels = {'output-header':`${dimensions} / ≤ 60 FPS`,'output-resolution':dimensions,
                    'output-dimensions':dimensions,'output-profile-label':`${outputProfile.label} 上传画布`};
    for (const [id,text] of Object.entries(labels)) if ($(id)) $(id).textContent = text;
    for (const view of glViews) if (view.unfolded) { view.aspect = outputProfile.aspect; view.draw(); }
  }

  function notify(message, error = false) {
    $('toast').textContent = message;
    $('toast').classList.toggle('error', error); $('toast').hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('toast').hidden = true; }, error ? 9000 : 4500);
  }
  async function api(path, body, requestOptions = {}) {
    const options = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Session-Token': sessionToken }, body: JSON.stringify(body) };
    const response = await fetch(path, { ...options, ...requestOptions });
    const result = await response.json();
    if (!response.ok) { const error = new Error(result.error || `请求失败 (${response.status})`); error.status = response.status; throw error; }
    return result;
  }
  function action(fn) { return async event => { try { await fn(event); } catch (error) { notify(error.message, true); } }; }
  function timeLabel(seconds) {
    seconds = Math.max(0, Number(seconds) || 0);
    const minutes = Math.floor(seconds / 60), remain = (seconds % 60).toFixed(3).padStart(6, '0');
    return `${String(minutes).padStart(2, '0')}:${remain}`;
  }
  function buildControls() {
    for (const [key, label, unit, min, max, step, parent, displayScale = 1] of parameters) {
      const field = document.createElement('div'); field.className = 'parameter';
      const head = document.createElement('div'); head.className = 'parameter-head';
      const title = document.createElement('label'); title.textContent = label; title.htmlFor = `number-${key}`;
      const value = document.createElement('span'); value.className = 'parameter-value';
      const number = document.createElement('input'); number.type = 'number'; number.id = `number-${key}`;
      const range = document.createElement('input'); range.type = 'range'; range.id = `range-${key}`; range.setAttribute('aria-label', label);
      for (const input of [number, range]) { input.min = min; input.max = max; input.step = step; }
      value.append(number, document.createTextNode(unit)); head.append(title, value);
      const limits = document.createElement('div'); limits.className = 'range-labels';
      const lo = document.createElement('span'), hi = document.createElement('span'); lo.id = `low-${key}`; hi.id = `high-${key}`; lo.textContent = `${min} ${unit}`; hi.textContent = `${max} ${unit}`; limits.append(lo, hi);
      field.append(head, range, limits); $(parent).append(field);
      range.addEventListener('input', () => { config[key] = Number(range.value) / displayScale; number.value = range.value; parameterChanged(key); scheduleMesh(); });
      number.addEventListener('change', () => {
        if (!Number.isFinite(Number(number.value)) || number.value === '') return syncControls();
        config[key] = Math.max(min, Math.min(max, Number(number.value))) / displayScale; parameterChanged(key); syncControls(); scheduleMesh();
      });
    }
  }
  function parameterChanged(key) {
    const before = config.bend_center_u;
    config.bend_center_u = axisCenter(config, before);
    actualBendCenterU = axisCenter(config, actualBendCenterU);
    if (key === 'bend_center_u' || before !== config.bend_center_u) {
      config = frameForMode(config, config.fit || 'cover');
      $('zoom').value = 1;
    }
    syncAxisControls();
  }
  function syncAxisControls() {
    const [low, high] = axisLimits(config).map(value => value * 100);
    // Native range inputs anchor their step grid at min. Round inward so 50%
    // remains on the .01% grid without ever extending beyond the physical arc.
    let rangeLow = Math.ceil(low * 100) / 100, rangeHigh = Math.floor(high * 100) / 100;
    if (rangeLow > rangeHigh) rangeLow = rangeHigh = 50;
    for (const [stem, center] of [['bend_center_u',config.bend_center_u],['actual-bend-center',actualBendCenterU]]) {
      const number = $(`number-${stem}`), range = $(`range-${stem}`);
      number.min = low; number.max = high; number.step = 'any'; number.value = center * 100;
      range.min = rangeLow; range.max = rangeHigh; range.step = .01;
      range.value = Math.max(rangeLow, Math.min(rangeHigh, Math.round(center * 10000) / 100));
      if (stem === 'actual-bend-center') { number.disabled = !axisPreviewEnabled; range.disabled = !axisPreviewEnabled; }
    }
    $('low-bend_center_u').textContent = `${low.toFixed(2)} %`; $('high-bend_center_u').textContent = `${high.toFixed(2)} %`;
    $('axis-preview-enabled').checked = axisPreviewEnabled; $('axis-apply').disabled = !axisPreviewEnabled;
    $('axis-apply').textContent = `应用实际轴 ${(actualBendCenterU * 100).toFixed(2)}% 到渲染`;
    $('axis-summary').textContent = axisSummaryLabel(config, actualBendCenterU, outputProfile.width, axisPreviewEnabled);
  }
  function refreshPreviewMeshes() {
    if (!mesh) return;
    const observed = axisPreviewEnabled && axisPreviewData
      ? {...mesh, projected:axisPreviewData.projected, bounds:axisPreviewData.bounds, angular_response:axisPreviewData.angular_response}
      : mesh;
    for (const view of glViews) view.setMesh(view.unfolded ? mesh : observed, config);
    $('comparison-caption').textContent = axisPreviewEnabled
      ? (comparison === 'corrected' ? '实验：将渲染假设轴生成的画面贴到模拟实际屏幕上' : '实验：普通视频直接贴到模拟实际屏幕上')
      : comparison === 'corrected' ? '● 几何预变形后，从设定视点观看的效果' : '未做几何预变形，亮度补偿仍按实验开关应用';
  }
  function syncControls() {
    config = { ...config, ...brightnessOptions(config) };
    config.bend_center_u = axisCenter(config, config.bend_center_u);
    actualBendCenterU = axisCenter(config, actualBendCenterU);
    for (const [key, , , , , , , displayScale = 1] of parameters) {
      if (config[key] === undefined) continue;
      $(`number-${key}`).value = Number((config[key] * displayScale).toFixed(4)); $(`range-${key}`).value = config[key] * displayScale;
    }
    $('brightness-enabled').checked = config.brightness_enabled;
    $('brightness-summary').textContent = brightnessSummaryLabel(mesh && mesh.brightness_summary);
    syncAxisControls();
    $('zoom').value = Number(Number(config.zoom || 1).toFixed(3));
    $('zoom').min = config.fit === 'cover' ? '1' : '0.25';
    $('framing-note').textContent = config.fit === 'cover'
      ? '拖动选择保留区域，暗区为裁切内容。铺满模式自动限制边界，不增加画面外黑边；素材自带黑边不会自动去除。'
      : '立牌小屏保留完整视频，配合边框、厚度感与底座装饰；视频和装饰仍按目标视点校正。切回「铺满取景」可裁切原片并铺满屏幕。';
    $('framing-limit').textContent = config.fit === 'cover'
      ? '可移动范围取决于原片比例与曲面几何；部分影片需要先放大，才能上下移动取景。'
      : '切入立牌时显示完整视频；继续放大或平移可能裁出边框。未校正对照仅显示普通视频，不含立牌装饰。';
    document.querySelectorAll('[data-fit]').forEach(button => button.classList.toggle('active', button.dataset.fit === config.fit));
    $('view-caption').textContent = `${Number(config.view_azimuth_deg).toFixed(1)}° / ${Number(config.view_elevation_deg).toFixed(1)}° / ${config.view_distance_mm} mm`;
  }
  function scheduleMesh(immediate = false) {
    clearTimeout(meshTimer); meshSerial++; meshPending = true; updateRenderButton();
    $('mesh-status').textContent = '更新映射…'; $('mesh-status').classList.add('updating');
    meshTimer = setTimeout(() => updateMesh(meshSerial), immediate ? 0 : 120);
  }
  async function updateMesh(serial) {
    const requestConfig = { ...config }; meshPending = true; updateRenderButton();
    try {
      const result = await api('/api/mesh', { config: requestConfig, source_aspect: source ? source.aspect || source.width / source.height : 16 / 9,
        ...(axisPreviewEnabled ? {axis_preview:{actual_bend_center_u:actualBendCenterU}} : {}) });
      if (serial !== meshSerial) return;
      config = result.config; mesh = result.mesh; axisPreviewData = result.axis_preview || null; syncControls();
      const decorationURL = config.presentation === 'standee' ? result.decoration_url : null;
      for (const view of glViews) if (view.decorationURL !== decorationURL) view.clearDecoration();
      refreshPreviewMeshes();
      if (decorationURL) {
        $('mesh-status').textContent = '载入立牌装饰…';
        await loadDecoration(decorationURL, serial);
        if (serial !== meshSerial) return;
      }
      drawOverlay(); $('mesh-status').textContent = source ? '实时映射' : '等待素材'; $('mesh-status').classList.remove('updating');
    } catch (error) {
      if (serial === meshSerial) { $('mesh-status').textContent = '映射失败'; notify(error.message, true); }
    } finally { if (serial === meshSerial) { meshPending = false; updateRenderButton(); } }
  }

  async function loadDecoration(url, serial) {
    if (!decorationCache.has(url)) {
      const loading = new Promise((resolve, reject) => {
        const bitmap = new Image();
        bitmap.onload = () => resolve(bitmap);
        bitmap.onerror = () => reject(new Error('立牌装饰图片读取失败，请重新切换模式或检查本地服务。'));
        bitmap.src = url;
      });
      decorationCache.set(url, loading);
      loading.catch(() => { if (decorationCache.get(url) === loading) decorationCache.delete(url); });
    }
    const bitmap = await decorationCache.get(url);
    if (serial !== meshSerial || config.presentation !== 'standee') return;
    for (const view of glViews) view.setDecoration(bitmap, url);
    dirtyTexture = true;
  }

  class MeshPreview {
    constructor(canvas, unfolded) {
      this.canvas = canvas; this.unfolded = unfolded; this.count = 0; this.hasTexture = false;
      this.hasDecoration = false; this.decorationURL = null;
      const gl = canvas.getContext('webgl', { alpha: false, antialias: true, preserveDrawingBuffer: false });
      if (!gl) throw new Error('此浏览器无法启用 WebGL，实时预览不可用。请启用浏览器硬件加速后重新打开。');
      this.gl = gl;
      const shader = (type, text) => {
        const result = gl.createShader(type); gl.shaderSource(result, text); gl.compileShader(result);
        if (!gl.getShaderParameter(result, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(result));
        return result;
      };
      const vert = shader(gl.VERTEX_SHADER, 'attribute vec2 aPosition;attribute vec2 aUV;attribute float aGain;attribute float aResponse;uniform vec2 uScale;varying vec2 vUV;varying float vGain;varying float vResponse;void main(){vUV=aUV;vGain=aGain;vResponse=aResponse;gl_Position=vec4(aPosition*uScale,0.,1.);}');
      const frag = shader(gl.FRAGMENT_SHADER, `
        precision mediump float;
        varying vec2 vUV;
        varying float vGain;
        varying float vResponse;
        uniform sampler2D uSource;
        uniform sampler2D uDecoration;
        uniform bool uStandee;
        uniform bool uSimulateAngle;
        uniform vec4 uDecorationDomain;
        vec3 srgbToLinear(vec3 color) {
          return mix(color / 12.92, pow((color + .055) / 1.055, vec3(2.4)), step(vec3(.04045), color));
        }
        vec3 linearToSrgb(vec3 color) {
          return mix(color * 12.92, 1.055 * pow(color, vec3(1. / 2.4)) - .055, step(vec3(.0031308), color));
        }
        void main() {
          vec3 color = vec3(0.);
          if (uStandee) {
            vec2 decorationUV = (vUV + uDecorationDomain.xy) / uDecorationDomain.zw;
            if (decorationUV.x >= 0. && decorationUV.x <= 1. && decorationUV.y >= 0. && decorationUV.y <= 1.)
              color = texture2D(uDecoration, decorationUV).rgb;
          }
          if (vUV.x >= 0. && vUV.x <= 1. && vUV.y >= 0. && vUV.y <= 1.)
            color = texture2D(uSource, vUV).rgb;
          float factor = clamp(vGain * (uSimulateAngle ? vResponse : 1.), 0., 1.);
          // Identity is deliberately bit-preserving when both controls are off.
          if (factor < 1.) color = linearToSrgb(srgbToLinear(color) * factor);
          gl_FragColor = vec4(color, 1.);
        }
      `);
      this.program = gl.createProgram(); gl.attachShader(this.program, vert); gl.attachShader(this.program, frag); gl.linkProgram(this.program);
      if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(this.program));
      gl.deleteShader(vert); gl.deleteShader(frag); gl.useProgram(this.program);
      this.positionBuffer = gl.createBuffer(); this.uvBuffer = gl.createBuffer();
      this.gainBuffer = gl.createBuffer(); this.responseBuffer = gl.createBuffer();
      this.position = gl.getAttribLocation(this.program, 'aPosition'); this.uv = gl.getAttribLocation(this.program, 'aUV'); this.scale = gl.getUniformLocation(this.program, 'uScale');
      this.gain = gl.getAttribLocation(this.program, 'aGain'); this.response = gl.getAttribLocation(this.program, 'aResponse');
      this.standeeUniform = gl.getUniformLocation(this.program, 'uStandee');
      this.simulateAngleUniform = gl.getUniformLocation(this.program, 'uSimulateAngle');
      this.decorationDomainUniform = gl.getUniformLocation(this.program, 'uDecorationDomain');
      this.texture = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, this.texture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      // API UVs are v-up. Browser image/video memory is top-down; flip exactly once on GPU upload.
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true); gl.uniform1i(gl.getUniformLocation(this.program, 'uSource'), 0);
      this.decorationTexture = gl.createTexture(); gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, this.decorationTexture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0, 255]));
      gl.uniform1i(gl.getUniformLocation(this.program, 'uDecoration'), 1); gl.activeTexture(gl.TEXTURE0);
      canvas.addEventListener('webglcontextlost', event => { event.preventDefault(); notify('GPU 预览已中断，请刷新页面恢复。导出任务仍会继续。', true); });
      new ResizeObserver(() => this.draw()).observe(canvas);
    }
    setMesh(data, cfg) {
      const gl = this.gl, points = this.unfolded ? data.atlas : data.projected;
      this.standee = activeStandee(cfg) && (this.unfolded || comparison === 'corrected');
      this.decorationDomain = decorationDomain(source ? source.aspect || source.width / source.height : 16 / 9);
      const bounds = this.unfolded ? [0, 0, 1, 1] : data.bounds;
      const width = Math.max(0.00001, bounds[2] - bounds[0]), height = Math.max(0.00001, bounds[3] - bounds[1]);
      this.aspect = this.unfolded ? outputProfile.aspect : width / height;
      const coordinates = this.unfolded || comparison === 'corrected' ? data.uv : data.atlas;
      // Deindexing also supports meshes above WebGL1's unsigned-short index limit.
      const position = new Float32Array(data.triangles.length * 6), uv = new Float32Array(position.length);
      const gain = new Float32Array(position.length / 2), response = new Float32Array(position.length / 2);
      let offset = 0;
      for (const triangle of data.triangles) for (const index of triangle) {
        position[offset] = 2 * (points[index][0] - bounds[0]) / width - 1;
        position[offset + 1] = 2 * (points[index][1] - bounds[1]) / height - 1;
        uv[offset] = coordinates[index][0]; uv[offset + 1] = coordinates[index][1];
        gain[offset / 2] = data.brightness_gain ? data.brightness_gain[index] : 1;
        response[offset / 2] = data.angular_response ? data.angular_response[index] : 1;
        offset += 2;
      }
      this.count = position.length / 2;
      gl.bindBuffer(gl.ARRAY_BUFFER, this.positionBuffer); gl.bufferData(gl.ARRAY_BUFFER, position, gl.STATIC_DRAW);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.uvBuffer); gl.bufferData(gl.ARRAY_BUFFER, uv, gl.STATIC_DRAW);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.gainBuffer); gl.bufferData(gl.ARRAY_BUFFER, gain, gl.STATIC_DRAW);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.responseBuffer); gl.bufferData(gl.ARRAY_BUFFER, response, gl.STATIC_DRAW); this.draw();
    }
    upload(media) {
      const gl = this.gl;
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.texture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, media);
      this.hasTexture = true;
    }
    clearDecoration() { this.hasDecoration = false; this.decorationURL = null; }
    setDecoration(bitmap, url) {
      if (this.decorationURL !== url || !this.hasDecoration) {
        const gl = this.gl;
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, this.decorationTexture);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, bitmap);
        gl.activeTexture(gl.TEXTURE0); this.hasDecoration = true; this.decorationURL = url;
      }
      this.draw();
    }
    draw() {
      const gl = this.gl, canvas = this.canvas, dpr = Math.min(devicePixelRatio || 1, 2);
      const width = Math.max(1, Math.round(canvas.clientWidth * dpr)), height = Math.max(1, Math.round(canvas.clientHeight * dpr));
      if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
      gl.viewport(0, 0, width, height); gl.clearColor(0.035, 0.05, 0.064, 1); gl.clear(gl.COLOR_BUFFER_BIT);
      if (!this.count || !this.hasTexture || !source || this.standee && !this.hasDecoration) return;
      const fitted = fitRect(width, height, this.aspect);
      gl.useProgram(this.program); gl.uniform2f(this.scale, fitted.width / width * .92, fitted.height / height * .92);
      gl.uniform1i(this.standeeUniform, this.standee && this.hasDecoration ? 1 : 0);
      gl.uniform1i(this.simulateAngleUniform, !this.unfolded && simulateAngle ? 1 : 0);
      gl.uniform4fv(this.decorationDomainUniform, this.decorationDomain);
      for (const [buffer, attribute, size] of [[this.positionBuffer, this.position, 2], [this.uvBuffer, this.uv, 2], [this.gainBuffer, this.gain, 1], [this.responseBuffer, this.response, 1]]) {
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.enableVertexAttribArray(attribute); gl.vertexAttribPointer(attribute, size, gl.FLOAT, false, 0, 0);
      }
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.texture);
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, this.decorationTexture);
      gl.drawArrays(gl.TRIANGLES, 0, this.count); gl.activeTexture(gl.TEXTURE0);
    }
  }
  function mediaReady() {
    dirtyTexture = true; lastFrameTime = -1;
    document.querySelectorAll('.canvas-empty').forEach(el => { el.hidden = true; });
    drawOverlay(); updateTimeline();
  }
  function animationLoop() {
    try {
      const ready = source && (source.kind === 'image' ? image.complete && image.naturalWidth : video.readyState >= 2);
      if (ready && (dirtyTexture || source.kind !== 'image' && video.currentTime !== lastFrameTime)) {
        const media = source.kind === 'image' ? image : video;
        for (const view of glViews) { view.upload(media); view.draw(); }
        dirtyTexture = false; lastFrameTime = video.currentTime;
      }
    } catch (error) { if (dirtyTexture) { notify(`预览纹理读取失败：${error.message}`, true); dirtyTexture = false; } }
    requestAnimationFrame(animationLoop);
  }
  function sourceRect() {
    const canvas = $('source-overlay'); return fitRect(canvas.clientWidth, canvas.clientHeight, source ? source.aspect || source.width / source.height : 16 / 9);
  }
  function drawOverlay() {
    const canvas = $('source-overlay'), ctx = canvas.getContext('2d'), dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.round(canvas.clientWidth * dpr)); canvas.height = Math.max(1, Math.round(canvas.clientHeight * dpr));
    ctx.scale(dpr, dpr); ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
    if (!source || !mesh || !overlayVisible) return;
    const rect = sourceRect(), outline = mesh.boundary_uv.map(uv => uvToCanvas(uv, rect));
    if (!outline.length) return;
    ctx.save(); ctx.beginPath(); ctx.rect(rect.x, rect.y, rect.width, rect.height); ctx.clip();
    const path = new Path2D(); path.rect(rect.x, rect.y, rect.width, rect.height);
    path.moveTo(...outline[0]); for (const point of outline.slice(1)) path.lineTo(...point); path.closePath();
    ctx.fillStyle = 'rgba(2,9,13,0.58)'; ctx.fill(path, 'evenodd');
    ctx.beginPath(); ctx.moveTo(...outline[0]); for (const point of outline.slice(1)) ctx.lineTo(...point); ctx.closePath();
    ctx.lineWidth = 1.5; ctx.strokeStyle = '#7cf4df'; ctx.shadowColor = '#102e27'; ctx.shadowBlur = 5; ctx.stroke(); ctx.shadowBlur = 0;
    const center = outline.reduce((sum, point) => [sum[0] + point[0] / outline.length, sum[1] + point[1] / outline.length], [0, 0]);
    ctx.strokeStyle = '#adf8eacc'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(center[0] - 6, center[1]); ctx.lineTo(center[0] + 6, center[1]); ctx.moveTo(center[0], center[1] - 6); ctx.lineTo(center[0], center[1] + 6); ctx.stroke();
    ctx.restore();
  }

  function setSource(next, reset = true) {
    video.pause(); video.removeAttribute('src'); video.load(); image.removeAttribute('src');
    source = next; source.kind = source.kind || 'video';
    $('source-empty').hidden = true; $('source-label').hidden = false; $('stage-help').hidden = false;
    image.hidden = source.kind !== 'image'; video.hidden = source.kind === 'image';
    const media = source.kind === 'image' ? image : video;
    media.src = source.preview_url;
    $('source-label').textContent = source.kind === 'image' ? 'STILL IMAGE' : 'SOURCE VIDEO';
    $('play').disabled = source.kind === 'image'; $('timeline').disabled = source.kind === 'image';
    const duration = source.kind === 'image' ? 5 : source.duration;
    $('timeline').max = duration || 1; $('timeline').value = 0; $('trim-start').value = 0; $('trim-end').value = (duration || 5).toFixed(3);
    $('trim-start').max = source.kind === 'image' ? '' : duration; $('trim-end').max = source.kind === 'image' ? '' : duration;
    $('duration').textContent = timeLabel(duration); $('current-time').textContent = timeLabel(0);
    $('set-in').disabled = source.kind === 'image'; $('set-out').disabled = source.kind === 'image';
    const info = $('source-info'); info.replaceChildren();
    const name = document.createElement('strong'); name.textContent = source.name; name.title = source.name;
    const detail = document.createElement('small'); detail.textContent = `${source.width} × ${source.height}${source.kind === 'image' ? ' · 静态图像' : ` · ${Number(source.fps).toFixed(3).replace(/\.?0+$/, '')} fps · ${timeLabel(source.duration)}`}`;
    info.append(name, detail);
    for (const warning of source.warnings || []) { const line = document.createElement('p'); line.className = 'source-warning'; line.textContent = warning; info.append(line); }
    $('output-name').value = source.name.replace(/\.[^.]+$/, '').replace(/[\\/:*?"<>|]/g, '_') + '_panorama';
    if (reset) { config.zoom = 1; config.pan_x = 0; config.pan_y = 0; }
    for (const view of glViews) view.hasTexture = false;
    document.querySelectorAll('.canvas-empty').forEach(el => { el.hidden = false; });
    updateTrim(); syncControls(); dirtyTexture = true; scheduleMesh(true); updateRenderButton(); drawOverlay();
  }
  function busyImport(value, label = '') {
    importing = value; $('import-status').hidden = !value; $('import-status').querySelector('span').textContent = label;
    for (const id of ['pick-file', 'import-path', 'calibration', 'brightness-calibration', 'axis-calibration', 'empty-import']) $(id).disabled = value;
    updateRenderButton();
  }
  async function importRequest(path, body) {
    if (importing) return;
    busyImport(true, path === '/api/pick' ? '请选择素材，准备浏览器预览…' : '正在读取素材并准备预览…');
    $('import-status').querySelector('progress').removeAttribute('value');
    try { const result = await api(path, body); if (!result.cancelled) { setSource(result); notify('素材已载入，拖动轮廓即可调整取景。'); } }
    finally { busyImport(false); }
  }
  function uploadFile(file) {
    if (!file || importing) return;
    busyImport(true, '正在复制素材到本项目…');
    const progress = $('import-status').querySelector('progress'); progress.value = 0;
    const request = new XMLHttpRequest(); request.open('POST', '/api/upload');
    request.setRequestHeader('Content-Type', 'application/octet-stream'); request.setRequestHeader('X-Session-Token', sessionToken); request.setRequestHeader('X-Filename', encodeURIComponent(file.name));
    request.upload.onprogress = event => { if (event.lengthComputable) { progress.value = event.loaded / event.total * 100; if (event.loaded === event.total) { progress.removeAttribute('value'); $('import-status').querySelector('span').textContent = '正在生成浏览器预览…'; } } };
    request.onload = () => {
      try { const result = JSON.parse(request.responseText); if (request.status < 200 || request.status >= 300) throw new Error(result.error || '上传失败'); setSource(result); notify('素材已载入。'); }
      catch (error) { notify(error.message, true); } finally { busyImport(false); }
    };
    request.onerror = () => { notify('上传连接中断，请检查本地服务。', true); busyImport(false); };
    request.onabort = () => busyImport(false); request.send(file);
  }
  function updateTimeline() {
    if (!source || source.kind === 'image') return;
    $('timeline').value = video.currentTime; $('current-time').textContent = timeLabel(video.currentTime);
  }
  function updateTrim() {
    const duration = source && source.kind !== 'image' ? source.duration : 86400;
    const range = trimRange($('trim-start').value, $('trim-end').value, duration);
    $('trim-start').value = Number(range.start.toFixed(3)); $('trim-end').value = Number(range.end.toFixed(3));
    refreshTrimDisplay();
  }
  function refreshTrimDisplay() {
    const duration = source && source.kind !== 'image' ? source.duration : 86400;
    const draft = trimDraft($('trim-start').value, $('trim-end').value, duration);
    $('trim-duration').textContent = draft.valid ? `区间时长 ${draft.duration.toFixed(3)} 秒` : '区间无效，请检查入点与出点';
    const max = Number($('timeline').max) || 1;
    const start = Math.max(0, Math.min(100, Number($('trim-start').value) / max * 100));
    const end = Math.max(start, Math.min(100, Number($('trim-end').value) / max * 100));
    $('timeline').style.background = `linear-gradient(to right,#26343d ${start}%,#527f78 ${start}%,#527f78 ${end}%,#26343d ${end}%)`;
    updateRenderButton(); scheduleEstimate();
  }
  function updateRenderButton() {
    const active = jobs.some(job => activeStates.has(job.status));
    const duration = source && source.kind !== 'image' ? source.duration : 86400;
    const trimValid = trimDraft($('trim-start').value, $('trim-end').value, duration).valid;
    $('render').disabled = initializing || !source || !mesh || meshPending || importing || submitting || active || !trimValid || jobsDisconnected || !!(environmentState && !environmentState.ready);
    $('render').querySelector('span').textContent = jobsDisconnected ? '等待重新连接…' : submitting ? '正在提交…' : active ? '渲染进行中…' : '开始渲染';
  }
  function renderJobs(next) {
    jobs = next; $('job-count').textContent = next.length; updateRenderButton();
    if ($('environment-save')) syncEnvironmentLock();
    const host = $('jobs'); host.replaceChildren();
    if (!next.length) {
      const empty = document.createElement('div'); empty.className = 'jobs-empty';
      const title = document.createElement('p'); title.textContent = '任务将在这里显示';
      const detail = document.createElement('small'); detail.textContent = '完成后可下载成片与查看路径'; empty.append(title, detail); host.append(empty); return;
    }
    for (const job of newestJobs(next)) {
      const card = document.createElement('div'); card.className = `job ${job.status}`;
      const heading = document.createElement('div'); heading.className = 'job-heading';
      const title = document.createElement('strong'); title.textContent = `任务 ${String(job.id).slice(0, 8)}`;
      const feedback = jobFeedback(job);
      const status = document.createElement('small'); status.textContent = feedback.label + (jobsDisconnected ? ' · 上次状态' : ''); heading.append(title, status); card.append(heading);
      if (feedback.active) { const progress = document.createElement('progress'); progress.max = 1; if (feedback.progress !== null) progress.value = feedback.progress; else progress.removeAttribute('value'); card.append(progress); }
      const message = document.createElement('p'); message.textContent = job.message || ''; card.append(message);
      const resultLabel = jobResultLabel(job);
      if (resultLabel) { const result = document.createElement('p'); result.className = 'job-result'; result.textContent = resultLabel; card.append(result); }
      const timing = document.createElement('p'); timing.className = 'job-timing'; timing.textContent = elapsedLabel(job.elapsed_seconds);
      if (job.updated_at) {
        const numeric = Number(job.updated_at);
        const updated = new Date(Number.isFinite(numeric) ? numeric < 1e12 ? numeric * 1000 : numeric : job.updated_at);
        if (Number.isFinite(updated.getTime())) timing.textContent += `${timing.textContent ? ' · ' : ''}更新于 ${updated.toLocaleTimeString('zh-CN', {hour12:false})}`;
      }
      if (timing.textContent) card.append(timing);
      if (job.frame && job.total_frames && activeStates.has(job.status)) { const frames = document.createElement('p'); frames.textContent = `${job.frame} / ${job.total_frames} 帧`; card.append(frames); }
      if (job.error) { const error = document.createElement('p'); error.className = 'job-error'; error.textContent = job.error; card.append(error); }
      for (const warning of job.warnings || []) { const line = document.createElement('p'); line.className = 'source-warning'; line.textContent = warning; card.append(line); }
      if (job.output_path && job.status === 'completed') { const path = document.createElement('div'); path.className = 'job-path'; path.textContent = job.output_path; path.title = '可选择并复制本地文件路径'; card.append(path); }
      const actions = document.createElement('div'); actions.className = 'job-actions';
      if (activeStates.has(job.status)) { const cancel = document.createElement('button'); cancel.textContent = '取消任务'; cancel.disabled = jobsDisconnected; cancel.onclick = action(async () => { cancel.disabled = true; await api('/api/cancel', { job_id: job.id }); await pollJobs(); }); actions.append(cancel); }
      if (job.status === 'completed' && job.output_url) { const link = document.createElement('a'); link.href = job.output_url; link.download = ''; link.textContent = '下载成片 ↓'; actions.append(link); }
      if (job.log_url) { const link = document.createElement('a'); link.href = job.log_url; link.download = 'render.log'; link.textContent = '查看日志 ↓'; actions.append(link); }
      if (job.blend_path && job.status === 'completed') { const path = document.createElement('div'); path.className = 'job-path'; path.textContent = `Blender 工程：${job.blend_path}`; card.append(path); }
      if (actions.children.length) card.append(actions); host.append(card);
    }
  }
  function setJobsConnection(connected) {
    const changed = jobsDisconnected !== !connected;
    jobsDisconnected = !connected;
    let notice = $('job-connection-warning');
    if (!notice && !connected) {
      notice = document.createElement('div'); notice.id = 'job-connection-warning'; notice.className = 'job-connection-warning'; notice.setAttribute('role', 'status');
      const message = document.createElement('p'); message.textContent = '连接已断开，任务状态暂未更新；不要重复提交，检查服务或重新连接。';
      const retry = document.createElement('button'); retry.className = 'button compact'; retry.textContent = '重新连接';
      retry.onclick = async () => { retry.disabled = true; try { await pollJobs(); } catch (_) { /* Persistent connection notice remains visible. */ } finally { retry.disabled = false; } };
      notice.append(message, retry); $('jobs').before(notice);
    }
    if (notice) notice.hidden = connected;
    $('connection').textContent = connected ? connectionReadyLabel : '连接已断开 · 状态未更新'; $('connection').classList.toggle('offline', !connected);
    if (changed) renderJobs(jobs); else updateRenderButton();
  }
  async function pollJobs() {
    if (jobsPolling) return jobsPolling;
    jobsPolling = (async () => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 12000);
      try {
        const result = await api('/api/jobs', undefined, { signal: controller.signal });
        if (jobsDisconnected) {
          const state = await api('/api/state', undefined, { signal: controller.signal });
          sessionToken = state.session_token; connectionReadyLabel = `LOCAL · ${state.version || 'READY'}`;
          applyOutputProfile(state.output_profile);
        }
        setJobsConnection(true); renderJobs(result.jobs);
      } catch (error) { setJobsConnection(false); throw error; }
      finally { clearTimeout(timeout); }
    })();
    try { return await jobsPolling; } finally { jobsPolling = null; }
  }
  async function jobLoop() {
    try { if (!initializing) await pollJobs(); }
    catch (_) { /* pollJobs retains the last known states and displays a persistent reconnect notice. */ }
    setTimeout(jobLoop, jobs.some(job => activeStates.has(job.status)) ? 1000 : 3000);
  }
  function populatePresets(names) {
    presetNames = names; $('presets').replaceChildren(new Option('载入参数预设…', ''));
    for (const name of names) $('presets').append(new Option(name, name));
  }
  function syncEnvironmentLock() {
    const busy = jobs.some(job => activeStates.has(job.status));
    for (const id of ['environment-blender','environment-ffmpeg','environment-scan','environment-save']) $(id).disabled = busy || environmentPending || initializing || jobsDisconnected;
    if (busy) $('environment-badge').textContent = '任务进行中 · 已锁定';
    else if (environmentState) $('environment-badge').textContent = environmentState.ready ? '工具就绪' : '需要配置';
    if ($('detect-capabilities')) $('detect-capabilities').disabled = capabilitiesPending || !environmentState || !environmentState.ready || jobsDisconnected;
  }
  function renderEnvironment(result, replaceInputs = false) {
    environmentState = result;
    for (const tool of ['blender','ffmpeg']) {
      const state = result.tools[tool], input = $(`environment-${tool}`);
      if (replaceInputs || !environmentDirty) input.value = result.settings[`${tool}_path`] || '';
      input.placeholder = state.path ? `自动：${state.path}` : `粘贴 ${tool}.exe 完整路径，或留空后扫描`;
      $(`${tool}-path`).textContent = state.path || '尚未找到';
      const status = $(`${tool}-status`); status.classList.toggle('tool-missing',state.status !== 'ready');
      status.textContent = state.status === 'ready' ? `${tool === 'blender' ? 'Blender' : 'FFmpeg'} ${state.version} · 已验证${state.warning ? ` · ${state.warning}` : ''}` : state.error || '工具未就绪';
      const candidates = $(`${tool}-candidates`); candidates.replaceChildren();
      for (const candidate of state.candidates || []) {
        const option = document.createElement('option'); option.value = candidate.path; option.label = `${candidate.version || candidate.status} · ${candidate.origin}`; candidates.append(option);
      }
    }
    const nvenc = result.tools.ffmpeg.h264_nvenc;
    $('nvenc-status').textContent = nvenc === true ? '此 FFmpeg 包含 h264_nvenc；实际 GPU 可用性会在编码前验证。' : nvenc === false ? '此 FFmpeg 未列出 h264_nvenc，可使用 CPU 编码。' : 'GPU 编码器列表尚未确认。';
    $('environment-message').textContent = result.settings_error || (result.ready ? '工具已就绪。选择路径后点击“验证并保存”即可应用，无需重启面板。' : '工具未齐备，面板仍可使用；导入和导出前请完成所需工具配置。');
    $('environment-message').classList.toggle('error',!result.ready || !!result.settings_error);
    $('python-help').textContent = result.python.help;
    $('python-download').hidden = !!result.python.bundled;
    if (!result.ready) $('runtime-settings').open = true;
    syncEnvironmentLock(); updateRenderButton();
    const ffmpegPath = result.tools.ffmpeg.path;
    if (capabilitiesPath !== ffmpegPath) { capabilitiesPath = ffmpegPath; capabilitiesRequested = false; capabilitiesState = null; syncAccelerationChoices(); }
    if (result.ready && !capabilitiesRequested && !capabilitiesPending) { capabilitiesRequested = true; detectCapabilities(); }
  }
  function syncAccelerationChoices() {
    const master = $('quality').value === 'master';
    $('renderer').disabled = master; $('encoder').disabled = master;
    for (const option of $('renderer').options) option.disabled = !!capabilitiesState && option.value === 'gpu' && !capabilitiesState.gpu?.available;
    for (const option of $('encoder').options) option.disabled = !!capabilitiesState && option.value !== 'auto' && capabilitiesState.encoders?.[option.value]?.available === false;
    if (!master && $('renderer').selectedOptions[0]?.disabled) $('renderer').value = 'auto';
    if (!master && $('encoder').selectedOptions[0]?.disabled) $('encoder').value = 'auto';
  }
  async function detectCapabilities() {
    if (capabilitiesPending || !environmentState?.ready) return;
    capabilitiesPending = true; capabilitiesRequested = true; syncEnvironmentLock();
    const ffmpegPath = capabilitiesPath;
    $('capability-status').textContent = '正在检测实际画面加速与视频编码能力，首次检测可能需要约一分钟…';
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(),80000);
    try {
      const result = await api('/api/capabilities',undefined,{signal:controller.signal});
      if (ffmpegPath !== capabilitiesPath) return;
      capabilitiesState = result;
      $('capability-status').textContent = accelerationLabel(result);
      $('capability-status').title = [result.gpu?.vendor,result.gpu?.renderer].filter(Boolean).join(' · ');
      syncAccelerationChoices();
    } catch (_) {
      if (ffmpegPath === capabilitiesPath) $('capability-status').textContent = '加速检测暂未完成，可重新检测；自动模式会选择实际可用的硬件或软件。';
    } finally {
      clearTimeout(timeout); capabilitiesPending = false; syncEnvironmentLock();
      if (ffmpegPath !== capabilitiesPath && environmentState?.ready) detectCapabilities();
    }
  }
  function syncExportQuality() {
    const quality = $('quality').value, master = quality === 'master';
    if (master && previousQuality !== 'master') {
      highPreferences = {renderer:$('renderer').value,encoder:$('encoder').value};
      $('renderer').value = 'blender'; $('encoder').value = 'auto';
    } else if (!master && previousQuality === 'master') {
      $('renderer').value = highPreferences.renderer; $('encoder').value = highPreferences.encoder;
    }
    previousQuality = quality;
    $('quality-hint').textContent = master ? 'Blender 16 位渲染 · FFV1 无损存档 · 不供 KANALI 播放' : '兼容播放格式 · SDR BT.709 · 严格小于 500 MB';
    $('budget-heading').textContent = master ? 'LOSSLESS ARCHIVE' : 'MP4 DELIVERY BUDGET';
    $('export-note').textContent = master ? '母版自动使用 Blender 16 位渲染与 FFV1，不受 500 MB 限制，文件可能很大。' : '2× 超采样后降采样，画面更细腻，耗时更长。体积按十进制 MB 计算，最终文件小于 500,000,000 字节。';
    syncAccelerationChoices(); scheduleEstimate();
  }
  function scheduleEstimate() {
    if (!$('budget-info')) return;
    clearTimeout(estimateTimer); const serial = ++estimateSerial;
    const quality = $('quality').value;
    $('budget-info').textContent = estimateLabel(null,quality);
    if (quality === 'master' || !source || !sessionToken) return;
    const trim = exportTrim($('trim-start').value,$('trim-end').value,source.kind === 'image' ? 86400 : source.duration);
    if (!trim) { $('budget-info').textContent = '请输入有效导出区间，随后显示体积与码率预算。'; return; }
    const body = {source_id:source.id,start:trim.start,end:trim.end,fps:Number($('fps').value),quality};
    estimateTimer = setTimeout(async () => {
      try {
        const result = await api('/api/estimate',body);
        if (serial === estimateSerial) $('budget-info').textContent = estimateLabel(result,quality);
      } catch (_) {
        if (serial === estimateSerial) $('budget-info').textContent = '预算暂未取得；导出时仍会严格限制成片小于 500 MB，并自动调整超限文件。';
      }
    },300);
  }
  async function environmentRequest(path = '/api/environment', body) {
    if (environmentPending) return;
    environmentPending = true; syncEnvironmentLock();
    $('environment-message').textContent = path === '/api/discover' ? '正在有限范围扫描并验证版本，请稍候…' : path === '/api/settings' ? '正在验证工具并保存本项目设置…' : '正在读取工具环境…';
    try {
      const result = await api(path, body);
      if (path === '/api/settings') environmentDirty = false;
      renderEnvironment(result, path === '/api/settings');
      if (path === '/api/settings') notify('工具设置已保存到本项目并立即生效。');
    } catch (error) {
      $('environment-message').textContent = error.message; $('environment-message').classList.add('error'); $('runtime-settings').open = true; throw error;
    } finally { environmentPending = false; syncEnvironmentLock(); }
  }
  async function savePreset(event) {
    event.preventDefault(); const name = $('preset-name').value.trim(); if (!name) return;
    if (presetNames.includes(name)) {
      const dialog = $('replace-dialog'); dialog.showModal();
      const choice = await new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue), { once: true }));
      if (choice !== 'replace') return;
    }
    await api('/api/preset', { name, config });
    populatePresets(presetNames.includes(name) ? presetNames : [...presetNames, name]); $('presets').value = name; $('preset-form').hidden = true; notify(`已保存预设「${name}」。`);
  }
  function wireEvents() {
    $('detect-capabilities').onclick = () => detectCapabilities();
    $('fps').onchange = scheduleEstimate;
    $('environment-scan').onclick = action(() => environmentRequest('/api/discover', {}));
    $('environment-save').onclick = action(() => environmentRequest('/api/settings', {blender_path:$('environment-blender').value.trim(),ffmpeg_path:$('environment-ffmpeg').value.trim()}));
    for (const id of ['environment-blender','environment-ffmpeg']) $(id).addEventListener('input', () => { environmentDirty = true; });
    $('pick-file').onclick = action(() => importRequest('/api/pick', {})); $('empty-import').onclick = () => $('pick-file').click();
    $('import-path').onclick = action(() => { const path = $('source-path').value.trim(); if (!path) throw new Error('请输入素材的本地绝对路径。'); return importRequest('/api/import', { path }); });
    $('source-path').addEventListener('keydown', event => { if (event.key === 'Enter') $('import-path').click(); });
    $('calibration').onclick = action(() => importRequest('/api/calibration', {}));
    $('brightness-calibration').onclick = action(() => importRequest('/api/brightness-calibration', {}));
    $('axis-calibration').onclick = action(() => importRequest('/api/axis-calibration', {}));
    $('brightness-enabled').onchange = () => { config.brightness_enabled = $('brightness-enabled').checked; scheduleMesh(true); };
    $('simulate-angle').onchange = () => { simulateAngle = $('simulate-angle').checked; for (const view of glViews) if (!view.unfolded) view.draw(); };
    $('axis-preview-enabled').onchange = () => {
      axisPreviewEnabled = $('axis-preview-enabled').checked; syncAxisControls(); refreshPreviewMeshes(); scheduleMesh(true);
    };
    const actualAxisChanged = value => {
      if (!Number.isFinite(Number(value)) || value === '') return syncAxisControls();
      actualBendCenterU = axisCenter(config, Number(value) / 100); syncAxisControls(); scheduleMesh();
    };
    $('range-actual-bend-center').addEventListener('input', () => actualAxisChanged($('range-actual-bend-center').value));
    $('number-actual-bend-center').addEventListener('change', () => actualAxisChanged($('number-actual-bend-center').value));
    $('axis-start').onclick = () => {
      actualBendCenterU = config.bend_center_u; axisPreviewEnabled = true; comparison = 'corrected';
      document.querySelectorAll('[data-mode]').forEach(button => button.classList.toggle('active', button.dataset.mode === comparison));
      syncAxisControls(); scheduleMesh(true);
    };
    $('axis-apply').onclick = () => {
      if (!axisPreviewEnabled) return;
      config = frameForMode({...config, bend_center_u:actualBendCenterU}, config.fit || 'cover');
      comparison = 'corrected'; document.querySelectorAll('[data-mode]').forEach(button => button.classList.toggle('active', button.dataset.mode === comparison));
      syncControls(); scheduleMesh(true); notify('已将人工估计的实际轴应用到渲染并重置取景；请重新导出后在实屏验证。');
    };
    $('drop-zone').onclick = () => { if (!importing) $('file-input').click(); };
    $('drop-zone').onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); $('drop-zone').click(); } };
    $('file-input').onchange = () => { uploadFile($('file-input').files[0]); $('file-input').value = ''; };
    for (const name of ['dragenter', 'dragover']) $('drop-zone').addEventListener(name, event => { event.preventDefault(); $('drop-zone').classList.add('drag-over'); });
    for (const name of ['dragleave', 'drop']) $('drop-zone').addEventListener(name, event => { event.preventDefault(); $('drop-zone').classList.remove('drag-over'); });
    $('drop-zone').addEventListener('drop', event => uploadFile(event.dataTransfer.files[0]));
    window.addEventListener('dragover', event => event.preventDefault()); window.addEventListener('drop', event => { event.preventDefault(); if (!$('drop-zone').contains(event.target)) uploadFile(event.dataTransfer.files[0]); });
    image.onload = mediaReady; video.addEventListener('loadeddata', mediaReady); video.addEventListener('seeked', () => { dirtyTexture = true; });
    for (const media of [image, video]) media.addEventListener('error', () => { if (media.getAttribute('src')) notify('浏览器无法读取预览文件，请查看导入警告或重新导入素材。', true); });
    video.addEventListener('timeupdate', updateTimeline); video.addEventListener('play', () => { $('play').textContent = 'Ⅱ'; $('play').setAttribute('aria-label', '暂停'); });
    for (const name of ['pause', 'ended']) video.addEventListener(name, () => { $('play').textContent = '▶'; $('play').setAttribute('aria-label', '播放'); });
    $('play').onclick = action(() => video.paused ? video.play() : video.pause());
    video.muted = true; $('mute').onclick = () => { video.muted = !video.muted; $('mute').textContent = video.muted ? '静音' : '声音'; $('mute').classList.toggle('active', video.muted); $('mute').setAttribute('aria-pressed', String(video.muted)); };
    $('timeline').oninput = () => { video.currentTime = Number($('timeline').value); updateTimeline(); };
    $('trim-start').onchange = updateTrim; $('trim-end').onchange = updateTrim;
    $('trim-start').oninput = refreshTrimDisplay; $('trim-end').oninput = refreshTrimDisplay;
    $('set-in').onclick = () => { $('trim-start').value = video.currentTime.toFixed(3); updateTrim(); }; $('set-out').onclick = () => { $('trim-end').value = video.currentTime.toFixed(3); updateTrim(); };
    document.querySelectorAll('[data-fit]').forEach(button => { button.onclick = () => { config = frameForMode(config, button.dataset.fit); syncControls(); scheduleMesh(); }; });
    $('zoom').onchange = () => { config.zoom = zoomForFit($('zoom').value, config.fit); syncControls(); scheduleMesh(); };
    $('reset-frame').onclick = () => { config.zoom = 1; config.pan_x = 0; config.pan_y = 0; syncControls(); scheduleMesh(true); };
    $('reset-geometry').onclick = () => { config = { ...defaults, ...brightnessOptions(config) }; syncControls(); scheduleMesh(true); };
    $('toggle-overlay').onclick = () => { overlayVisible = !overlayVisible; $('toggle-overlay').classList.toggle('active', overlayVisible); $('toggle-overlay').setAttribute('aria-pressed', String(overlayVisible)); drawOverlay(); };
    document.querySelectorAll('[data-mode]').forEach(button => { button.onclick = () => {
      comparison = button.dataset.mode; document.querySelectorAll('[data-mode]').forEach(item => item.classList.toggle('active', item === button));
      refreshPreviewMeshes();
    }; });
    const overlay = $('source-overlay');
    overlay.addEventListener('pointerdown', event => { if (!source || !mesh || !overlayVisible || event.button !== 0) return; overlay.setPointerCapture(event.pointerId); drag = { x: event.clientX, y: event.clientY, panX: config.pan_x, panY: config.pan_y, rect: sourceRect(), span: meshUVSpan(mesh.uv) }; });
    overlay.addEventListener('pointermove', event => {
      if (!drag) return;
      // Moving the footprint across a stationary source means moving the image in the opposite direction.
      config.pan_x = Math.max(-1, Math.min(1, drag.panX - (event.clientX - drag.x) / drag.rect.width / drag.span[0]));
      config.pan_y = Math.max(-1, Math.min(1, drag.panY + (event.clientY - drag.y) / drag.rect.height / drag.span[1]));
      scheduleMesh();
    });
    for (const eventName of ['pointerup', 'pointercancel', 'lostpointercapture']) overlay.addEventListener(eventName, () => { if (drag) { drag = null; scheduleMesh(true); } });
    overlay.addEventListener('wheel', event => { if (!source) return; event.preventDefault(); config.zoom = zoomForFit(config.zoom * Math.exp(-event.deltaY * .0015), config.fit); syncControls(); scheduleMesh(); }, { passive: false });
    new ResizeObserver(drawOverlay).observe($('source-stage'));
    $('save-preset').onclick = () => { $('preset-form').hidden = !$('preset-form').hidden; if (!$('preset-form').hidden) $('preset-name').focus(); };
    $('preset-form').onsubmit = action(savePreset);
    $('presets').onchange = action(async () => { if (!$('presets').value) return; const result = await api(`/api/preset?name=${encodeURIComponent($('presets').value)}`); config = result.config; syncControls(); scheduleMesh(true); notify(`已载入预设「${result.name}」。`); });
    $('quality').onchange = syncExportQuality;
    $('render').onclick = action(async () => {
      if (!source || jobsDisconnected) return;
      const trim = exportTrim($('trim-start').value, $('trim-end').value, source.kind === 'image' ? 86400 : source.duration);
      if (!trim) throw new Error('导出区间无效，请检查入点与出点。');
      submitting = true; updateRenderButton();
      try {
        const acceleration = renderPreferences({quality:$('quality').value,renderer:$('renderer').value,encoder:$('encoder').value});
        const job = await api('/api/render', { source_id: source.id, config, start: trim.start, end: trim.end, fps: Number($('fps').value), quality: $('quality').value, supersample: Number($('supersample').value), name: $('output-name').value.trim() || 'panorama', ...acceleration });
        renderJobs([...jobs.filter(item => item.id !== job.id), job]); notify('渲染任务已提交，可继续调整预览参数。');
      } catch (error) { if (!error.status) setJobsConnection(false); throw error; }
      finally { submitting = false; updateRenderButton(); }
    });
    window.addEventListener('keydown', event => { if (event.code === 'Space' && !/INPUT|SELECT|TEXTAREA|BUTTON/.test(event.target.tagName) && source && source.kind !== 'image' && !$('replace-dialog').open) { event.preventDefault(); $('play').click(); } });
  }
  async function initialize() {
    buildControls(); wireEvents(); busyImport(true, '正在连接本地服务…');
    try { glViews = [new MeshPreview($('observer-preview'), false), new MeshPreview($('atlas-preview'), true)]; }
    catch (error) { notify(error.message, true); }
    try {
      const state = await api('/api/state'); sessionToken = state.session_token; defaults = { presentation: 'plain', ...(state.defaults || state.config) }; config = { presentation: 'plain', ...state.config };
      applyOutputProfile(state.output_profile);
      connectionReadyLabel = `LOCAL · ${state.version || 'READY'}`; $('connection').textContent = connectionReadyLabel; $('connection').classList.remove('offline');
      $('blender-path').textContent = state.blender_path || '未找到 Blender'; $('ffmpeg-path').textContent = state.ffmpeg_path || '未找到 FFmpeg'; $('output-dir').textContent = state.output_dir || '项目输出目录';
      populatePresets(state.presets || []); renderJobs(state.jobs || []); syncControls();
      if (state.source) setSource(state.source, false); else scheduleMesh(true);
      initializing = false; busyImport(false); updateRenderButton();
      try { await environmentRequest(); } catch (error) { notify(`工具环境读取失败：${error.message}`,true); }
    } catch (error) {
      $('connection').textContent = '本地服务未连接'; $('connection').classList.add('offline'); notify(`无法连接本地工作台：${error.message}。请通过启动脚本打开页面。`, true);
    }
    requestAnimationFrame(animationLoop); jobLoop();
  }
  initialize();
})();
