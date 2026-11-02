(function (root) {
  'use strict';
  const core = {
    fitRect(width, height, aspect) {
      const w = Math.min(width, height * aspect), h = w / aspect;
      return { x: (width - w) / 2, y: (height - h) / 2, width: w, height: h };
    },
    uvToCanvas(uv, rect) {
      return [rect.x + uv[0] * rect.width, rect.y + (1 - uv[1]) * rect.height];
    },
    meshUVSpan(uv) {
      const min = [Infinity, Infinity], max = [-Infinity, -Infinity];
      for (const p of uv) for (let i = 0; i < 2; i++) {
        min[i] = Math.min(min[i], p[i]); max[i] = Math.max(max[i], p[i]);
      }
      return max.map((n, i) => Math.max(0.00001, n - min[i]));
    },
    trimRange(start, end, duration) {
      start = Math.max(0, Math.min(duration, Number(start) || 0));
      return { start, end: Math.max(start, Math.min(duration, Number(end) || 0)) };
    },
    trimDraft(start, end, limit) {
      const range = core.exportTrim(start, end, limit);
      return { valid: range !== null, duration: range ? range.end - range.start : null };
    },
    exportTrim(start, end, limit) {
      const first = Number(start), last = Number(end);
      // The UI displays milliseconds; a rounded tail may exceed the exact media duration by half a millisecond.
      const tolerance = 0.0005 + Math.max(1, Math.abs(limit)) * Number.EPSILON * 4;
      const actualEnd = Math.min(last, limit);
      const valid = String(start).trim() !== '' && String(end).trim() !== '' && Number.isFinite(first) && Number.isFinite(last) && Number.isFinite(limit) && first >= 0 && actualEnd > first && last <= limit + tolerance;
      return valid ? {start:first,end:actualEnd} : null;
    },
    zoomForFit(value, fit) {
      const zoom = Number(value);
      return Math.max(fit === 'cover' ? 1 : 0.25, Math.min(4, Number.isFinite(zoom) ? zoom : 1));
    },
    decorationDomain(aspect) {
      return [0.15 / aspect, 0.30, 1 + 0.30 / aspect, 1.44];
    },
    frameForMode(config, fit) {
      return { ...config, fit, presentation: fit === 'contain' ? 'standee' : 'plain', zoom: 1, pan_x: 0, pan_y: 0 };
    },
    activeStandee(config) {
      return config.fit === 'contain' && config.presentation === 'standee';
    },
    brightnessOptions(config = {}) {
      const value = (key, fallback, min, max) => typeof config[key] === 'number' && Number.isFinite(config[key])
        ? Math.max(min, Math.min(max, config[key])) : fallback;
      return {brightness_enabled:config.brightness_enabled === true,
        brightness_floor:value('brightness_floor',.6,0,1),
        brightness_power:value('brightness_power',2,.25,8),
        brightness_strength:value('brightness_strength',1,0,1)};
    },
    brightnessSummaryLabel(summary) {
      if (!summary) return '调整后显示视角范围、预测亮度与补偿倍率。';
      const angle = n => Number(n).toFixed(1), percent = n => `${(Number(n)*100).toFixed(1)}%`;
      return `视角 ${angle(summary.angle_min_deg)}°–${angle(summary.angle_max_deg)}°\n预测亮度 ${percent(summary.response_min)}–${percent(summary.response_max)}\n${summary.enabled ? '补偿倍率' : '补偿未开启 · 倍率'} ${percent(summary.gain_min)}–${percent(summary.gain_max)}`;
    },
    newestJobs(jobs) {
      return [...jobs].sort((a,b)=>(b.created || 0)-(a.created || 0));
    },
    jobFeedback(job) {
      const names = {queued:'排队中',preparing:'准备素材',rendering:'渲染中',encoding:'编码中',completed:'已完成',cancelled:'已取消',failed:'失败'};
      const active = ['queued','preparing','rendering','encoding'].includes(job.status);
      const name = job.status === 'preparing' && job.phase === 'verify' ? '校验中间文件' : names[job.status] || job.status;
      const raw = job.status === 'preparing' ? job.phase_progress : job.progress;
      const measurable = active && job.status !== 'queued' && raw !== null && raw !== undefined && Number.isFinite(Number(raw));
      const progress = measurable ? Math.max(0, Math.min(1, Number(raw))) : null;
      const label = !active || job.status === 'queued' ? name : progress === null ? `${name}…` : `${name}${job.status === 'preparing' ? ' ' : ' · '}${Math.round(progress * 100)}%`;
      return { label, progress, active };
    },
    elapsedLabel(seconds) {
      if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return '';
      const total = Math.max(0, Math.floor(Number(seconds))), minutes = Math.floor(total / 60), remain = total % 60;
      return `已用时 ${minutes ? `${minutes} 分 ` : ''}${remain} 秒`;
    },
    renderPreferences(raw) {
      if (raw.quality === 'master') return {renderer:'blender',encoder:'auto'};
      return {renderer:['auto','gpu','blender'].includes(raw.renderer) ? raw.renderer : 'auto',
              encoder:['auto','h264_nvenc','h264_qsv','h264_amf','libx264'].includes(raw.encoder) ? raw.encoder : 'auto'};
    },
    estimateLabel(estimate, quality) {
      if (quality === 'master') return '无损母版不执行 500 MB 限制；用于存档，不供 KANALI 播放。';
      if (!estimate) return '目标 480 MB，成片严格小于 500 MB；体积超限自动调整码率，无需重跑画面渲染。';
      const mb = bytes => (Number(bytes)/1000000).toFixed(0);
      const bitrate = Number(estimate.budget_bitrate);
      const details = Number.isFinite(bitrate) ? ` · 码率预算 ${(bitrate/1000000).toFixed(2)} Mbps` : '';
      return `目标 ${mb(estimate.target_bytes)} MB · 严格小于 ${mb(estimate.limit_bytes)} MB${details}\n${estimate.frames} 帧 / ${Number(estimate.duration).toFixed(3)} 秒`;
    },
    jobResultLabel(job) {
      const backend = job.backend || job;
      const renderer = {gpu:'GPU 加速',blender:'Blender'}[backend.renderer];
      const encoder = {h264_nvenc:'NVIDIA NVENC',h264_qsv:'Intel QSV',h264_amf:'AMD AMF',libx264:'CPU H.264',ffv1:'FFV1 无损'}[backend.encoder];
      const size = Number(job.delivery && job.delivery.size_bytes);
      return [renderer,encoder,Number.isFinite(size) && size>0 ? `${(size/1000000).toFixed(2)} MB` : ''].filter(Boolean).join(' · ');
    },
    accelerationLabel(capabilities) {
      const encoders = capabilities.encoders || {};
      const names = {h264_nvenc:'NVIDIA NVENC',h264_qsv:'Intel QSV',h264_amf:'AMD AMF',libx264:'CPU H.264'};
      const selected = Object.keys(names).find(name => encoders[name] && encoders[name].available);
      if (!selected) return '未检测到可用 H.264 编码器，请检查 FFmpeg 或重新检测。';
      const rendering = capabilities.gpu && capabilities.gpu.available ? 'GPU 画面加速可用' : 'GPU 画面加速不可用，自动回退到 Blender';
      return `${rendering}；自动编码使用 ${names[selected]}。`;
    }
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = core;
  else root.PanoramaUI = core;
})(typeof window !== 'undefined' ? window : globalThis);
