# Local panel HTTP interface

All paths relative to same localhost origin. No CDN. JSON POSTs require Content-Type application/json. Errors: non-2xx with {error:string}. Binary upload uses application/octet-stream. Server provides a random session token at GET /api/state; every POST sends X-Session-Token. User files remain local. No cookies or network access required.

## State / sources

GET /api/state -> {config, defaults, source:null|Source, sources:[Source], jobs:[Job], presets:[string], blender_path, ffmpeg_path, output_dir, session_token, version}.

Source = {id,name,width,height,aspect,duration,fps,frame_count,preview_url,thumbnail_url,kind:'video'|'image', warnings:[string]}. The preview_url is a browser-friendly MP4 proxy for videos and PNG for images, prepared on import; the original is used for export.

POST /api/import {path:string} -> Source. Reads chosen local source, never overwrites it.
POST /api/pick {} -> Source or {cancelled:true}. Native Windows file picker. Polling remains available.
POST /api/upload raw binary, X-Filename: encodeURIComponent(filename) -> Source. Use XMLHttpRequest upload progress if desired; project input cache holds copy.
POST /api/calibration {} -> Source (built-in labeled grid image).
GET /media/<source_id>/preview, /media/<source_id>/thumbnail. Supports video Range requests. Use URLs in Source directly.

## Geometry and presets

POST /api/mesh {config,source_aspect:number} -> {config,mesh,decoration_url:null|string}.
mesh = {positions:[[x,y,z],...], atlas:[[u,v],...], uv:[[u,v],...], projected:[[x,y],...], triangles:[[a,b,c],...], boundary_uv:[[u,v],...], bounds:[xmin,ymin,xmax,ymax]}.
All UVs use v-up. Canvas source overlays need y=1-v. WebGL source texture must align with these UVs explicitly. Draw corrected preview using projected positions + uv; comparison preview uses projected positions + atlas. Unfolded output preview uses atlas positions + uv. Source sampling outside [0,1] must be black, not clamped/repeated. mesh can have >65535 vertices, use uint or default small enough.

POST /api/preset {name:string,config} -> {name,config}. Saves project-local presets; existing preset explicitly named is replaced.
GET /api/preset?name=... -> {name,config}.

Default config and allowed values:

| key | default | meaning |
| --- | --- | --- |
| screen_height_mm | 73.6 | effective display height; estimated |
| screen_width_mm | 152.65185185 | unfolded arc length; estimated |
| bend_deg | 90 | total tangent turn, 0=flat; estimated |
| curve_length_mm | 14 | G2 transition arc length; estimated |
| view_azimuth_deg | 45 | viewing horizontally, 45=bisector |
| view_elevation_deg | 10 | eye above screen (degrees) |
| view_distance_mm | 650 | eye to corner center |
| fit | cover | cover / contain |
| presentation | plain | plain / standee; standee only active with contain |
| zoom | 1 | proportional image enlargement |
| pan_x | 0 | image translation in projected bounding-width units, positive right |
| pan_y | 0 | image translation in projected bounding-height units, positive up |

UI should label azimuth45 as corner bisector, elevation10 as provisional. Recommend ranges azimuth15..75, elevation-20..35, distance250..2000, bend0..110, curve2..40, zoom0.25..4, pan +/-1. Backend normalizes/rejects out-of-range values. Geometry POST debounced 120ms; ignore out-of-order responses. On source import auto-fit; preserve geometry calibration, reset zoom/pan.

Cover framing is constrained: zoom>=1, pan clamped to available crop margin. Returned config and mesh.config are the effective parameters. Standee contains the entire extended decoration domain U=[-.15/aspect,1+.15/aspect], V=[-.30,1.14], with 1% safety inset. Both renderers sample the same PNG decoration outside source UV[0,1]. Switching either mode resets zoom/pan.

## Export

POST /api/render {source_id,config,start:seconds,end:seconds|null,fps:0|number,quality:'high'|'master',supersample:1|2,name:string,renderer:'auto'|'gpu'|'blender',encoder:'auto'|'h264_nvenc'|'h264_qsv'|'h264_amf'|'libx264'} -> Job.
fps0=auto rational source rate <=60; high=high-quality H.264 MP4 with strict actual-size gate below 500000000 bytes and automatic bounded two-pass x264 re-encoding when necessary; master=lossless FFV1 RGB16 MKV archival file (not KANALI delivery, no size cap). end defaults source duration, still image default5s; output fixed2240×1080. Unique per-job output folder; no overwrite. renderer/encoder default auto; master always Blender/FFV1.

POST /api/estimate {source_id,start,end,fps,quality} -> {limit_bytes:500000000,target_bytes:480000000,budget_bitrate,frames,duration,enforced}. Duration reflects rounded output frames; estimate is not a file-size guarantee. Master returns enforced=false and budget_bitrate=null.

GET /api/capabilities -> {gpu:{available,renderer,vendor,version,error},encoders:{encoder_name:{available,error}}}. Real isolated GL drawing and FFmpeg two-frame encode probes, cached per FFmpeg path/mtime; allow up to 80 seconds. Driver crash/timeout triggers independent CPU encoding fallback probe.

GET /api/environment -> dependency status, configured settings, bundled-Python state and official download links. POST /api/discover {} performs bounded installed-program discovery. POST /api/settings {blender_path,ffmpeg_path} validates and saves project-local settings; rejects changes while a job is active. Empty paths request discovery.

Job = {id,status:'queued'|'preparing'|'rendering'|'encoding'|'completed'|'cancelled'|'failed',progress:0..1,message,frame,total_frames,output_path:null|string,output_url:null|string,blend_path:null|string,log_path:null|string,error:null|string,warnings:[string]}.
GET /api/jobs -> {jobs:[Job]}. Poll 1s during active exports.

Version 1.0.1 adds `phase` (`normalize`/`verify`/`render`), `phase_progress` (0..1 while preparing), `elapsed_seconds`, `updated_at`, and `log_url` to jobs. The preparation percentage is a stage percentage, separate from overall `progress`. Decoded-source preparation does not increment Blender's `frame`. `GET log_url` is allowed for queued/active/failed/cancelled/completed jobs once the log exists. No other unfinished artifact becomes downloadable. State also includes `service_log_path`; localhost disconnects are handled separately from application failures. On reconnect the frontend refreshes the session token without replacing the user's in-progress editing configuration.
POST /api/cancel {job_id} -> Job. Cancels only that job. One running export allowed.
POST /api/shutdown {} -> {stopping:true}. Gracefully stops this service and cancels owned active jobs; authenticated by the same session token.
GET /output/<job_id>/<filename> -> downloadable product artifact. Prefer show output_path and link; no auto-launch external app.

Start server: python app.py [--no-browser] [--port 0]. Defaults port dynamically allocated and opens system browser. GET root serves web/index.html. Server only binds 127.0.0.1; opening through returned exact origin is required.
