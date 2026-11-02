# Local panel HTTP interface

All paths relative to same localhost origin. No CDN. JSON POSTs require Content-Type application/json. Errors: non-2xx with {error:string}. Binary upload uses application/octet-stream. Server provides a random session token at GET /api/state; every POST sends X-Session-Token. User files remain local. No cookies or network access required.

## State / sources

GET /api/state -> {config, defaults, output_profile, source:null|Source, sources:[Source], jobs:[Job], presets:[string], blender_path, ffmpeg_path, output_dir, session_token, version}.

`output_profile = {width:2160,height:1080,aspect:2,label:'KANALI 2:1'}` is the upload raster contract. Unfolded preview and output labels use this profile. It is independent of physical screen millimetres and viewpoint calibration; observer-preview aspect still follows projected geometry.

Source = {id,name,width,height,aspect,duration,fps,frame_count,preview_url,thumbnail_url,kind:'video'|'image', warnings:[string]}. The preview_url is a browser-friendly MP4 proxy for videos and PNG for images, prepared on import; the original is used for export.

POST /api/import {path:string} -> Source. Reads chosen local source, never overwrites it.
POST /api/pick {} -> Source or {cancelled:true}. Native Windows file picker. Polling remains available.
POST /api/upload raw binary, X-Filename: encodeURIComponent(filename) -> Source. Use XMLHttpRequest upload progress if desired; project input cache holds copy.
POST /api/calibration {} -> Source (built-in labeled grid image).
POST /api/brightness-calibration {} -> Source (built-in full-frame neutral RGB(128,128,128) PNG, 2160×1080, 2:1). The 128 values are encoded 8-bit SDR values, not 50% linear luminance. Imports this image as the current source; use cover framing for a full-screen calibration export, then reimport the original video after calibration.
GET /media/<source_id>/preview, /media/<source_id>/thumbnail. Supports video Range requests. Use URLs in Source directly.

## Geometry and presets

POST /api/mesh {config,source_aspect:number,axis_preview?:{actual_bend_center_u:number}} -> {config,mesh,decoration_url:null|string,axis_preview:null|AxisPreview}.
mesh = {positions:[[x,y,z],...], atlas:[[u,v],...], uv:[[u,v],...], projected:[[x,y],...], triangles:[[a,b,c],...], boundary_uv:[[u,v],...], bounds:[xmin,ymin,xmax,ymax], brightness_gain:[number,...], angular_response:[number,...], brightness_summary:BrightnessSummary}.
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
| bend_center_u | 0.5 | finite number in [0.001,0.999]; formal fold center measured left-to-right in unfolded UV; full G2 transition must remain inside screen |
| view_azimuth_deg | 45 | viewing horizontally, 45=bisector |
| view_elevation_deg | 10 | eye above screen (degrees) |
| view_distance_mm | 650 | eye to corner center |
| fit | cover | cover / contain |
| presentation | plain | plain / standee; standee only active with contain |
| zoom | 1 | proportional image enlargement |
| pan_x | 0 | image translation in projected bounding-width units, positive right |
| pan_y | 0 | image translation in projected bounding-height units, positive up |
| brightness_enabled | false | strict JSON boolean; apply experimental attenuation to previews and exports |
| brightness_floor | 0.6 | finite number in [0,1]; relative luminance limit approaching a 90° grazing view |
| brightness_power | 2.0 | finite number in [0.25,8]; angular-response curve exponent |
| brightness_shoulder | 1.0 | finite number in [0.25,8]; angular shoulder steepness; 1 preserves the v1.2.0 model |
| brightness_strength | 1.0 | finite number in [0,1]; 0 is no compensation, 1 is full compensation |

UI should label azimuth45 as corner bisector, elevation10 as provisional. Recommend ranges azimuth15..75, elevation-20..35, distance250..2000, bend0..110, curve2..40, zoom0.25..4, pan +/-1. Backend normalizes/rejects out-of-range values. Geometry POST debounced 120ms; ignore out-of-order responses. On source import auto-fit; preserve geometry calibration, reset zoom/pan.

Cover framing is constrained: zoom>=1, pan clamped to available crop margin. Returned config and mesh.config are the effective parameters. Standee contains the entire extended decoration domain U=[-.15/aspect,1+.15/aspect], V=[-.30,1.14], with 1% safety inset. Both renderers sample the same PNG decoration outside source UV[0,1]. Switching either mode resets zoom/pan.

### Experimental angular brightness (v1.2.0)

Missing brightness keys use the defaults above, so older presets leave compensation disabled. Numeric strings, booleans in numeric fields, non-finite values and out-of-range brightness values are rejected. The 0.6 floor and exponent 2 are unmeasured experimental starting points, not a TRYX measurement or a universal AMOLED law. See [brightness model and calibration](BRIGHTNESS.md).

`brightness_gain` and `angular_response` each contain one scalar per `positions` entry, in the same vertex order. `angular_response` is the modeled screen response: `t=mu^power`, `T=t^shoulder/(t^shoulder+(1-t)^shoulder)`, `A=floor+(1-floor)*T`. Here `mu` is the clamped cosine between the outward screen normal and the local direction to the configured eye. At shoulder=1 the old exact formula is used. Response remains available when compensation is disabled, allowing a model-only preview. `brightness_gain` is the attenuation `g = 1 + effective_strength * (R/A - 1)`, with effective strength zero when disabled. Gains never exceed 1. Apply the interpolated gain to **linear RGB after compositing the source and standee decoration**, then encode the output color.

`R` is the minimum angular response at the current screen's four physical corners. For the supported convex extruded profile and valid front-facing eye positions this is the exact darkest response over the whole surface, independent of source crop, framing, mesh resolution and frame content. It is not recomputed from image pixels. Implementations guard division by zero and clamp the ratio to at most 1.

`BrightnessSummary = {angle_min_deg,angle_max_deg,response_min,response_max,gain_min,gain_max,target_response,enabled,floor,power,shoulder,strength}`. Angles are measured from the local normal: 0° is face-on, 90° is grazing. `response_min` and `target_response` both equal `R`; `gain_max` is 1. `response_max` and `angle_min_deg` use a fixed 2049-sample arc reference at the closest vertical screen position for UI estimates; this sampling does not determine `R`. `gain_min` is the corresponding estimated attenuation minimum. Model settings echo the validated config, with the configured strength retained even when disabled.

The observer preview has a separate, UI-only model-simulation toggle that multiplies by `angular_response` in linear light. It is not a config/export field, is not saved with presets, and never adds screen attenuation to the exported video. The unfolded preview always shows the actual preprocessed pixels, using `brightness_gain` only. At full strength, the model predicts `A*g = R`; apparent uniformity in this simulation does not establish uniformity on a real panel. Actual calibration uses the neutral-gray source on the physical display.

## Fold-axis hypothesis simulation (v1.3.0)

POST /api/axis-calibration {} -> Source. Native 2160×1080 image with multiple parallel horizontal, vertical and diagonal lines and asymmetric direction labels. This imports a source; export it with the normal render endpoint.

`bend_center_u` belongs to formal config and exports. A value of .52 means the physical bend is at 52% of the unfolded canvas from the left. For a bent display, `curve_length_mm/(2*screen_width_mm) <= bend_center_u <= 1-curve_length_mm/(2*screen_width_mm)` must also hold. World origin and eye coordinates stay tied to the physical bend; the two wing lengths change. Flat displays ignore this axis geometrically.

The optional `/api/mesh` field `axis_preview` is a separate request object, never part of config. When absent/null, its response is null. When provided, it must contain a finite numeric `actual_bend_center_u` satisfying the same geometry constraints. The returned `AxisPreview` has `projected`, `bounds`, `angular_response`, `actual_bend_center_u`, `render_bend_center_u`, `delta_u`, `delta_pixels`, `actual_center_px`, and `render_center_px`. Pixel positions use continuous coordinates from the left edge; `delta_pixels=(actual-render)*2160`.

For observer simulation, combine these positions/bounds/response with the **original render mesh's source UV and brightness gain at the same atlas indices**. The actual hypothesis is evaluated on the same panel samples even when the curve is adaptively subdivided. Unfolded preview and delivery always use the original mesh. Neither the actual-axis hypothesis nor simulation enabled state is saved to presets or jobs. Applying a hypothesis is an explicit frontend action that copies it into formal `bend_center_u` and resets framing. It is manual calibration, not a camera measurement or unique automatic inference; see [procedure and limits](AXIS-CALIBRATION.md).

## Export

POST /api/render {source_id,config,start:seconds,end:seconds|null,fps:0|number,quality:'high'|'master',supersample:1|2,name:string,renderer:'auto'|'gpu'|'blender',encoder:'auto'|'h264_nvenc'|'h264_qsv'|'h264_amf'|'libx264'} -> Job.
fps0=auto rational source rate <=60; high=high-quality H.264 MP4 with strict actual-size gate below 500000000 bytes and automatic bounded two-pass x264 re-encoding when necessary; master=lossless FFV1 RGB16 MKV archival file (not KANALI delivery, no size cap). end defaults source duration, still image default5s; output fixed 2160×1080, square pixels (SAR 1:1), DAR 2:1. Unique per-job output folder; no overwrite. renderer/encoder default auto; master always Blender/FFV1.

Export validation adds `output_width:2160` and `output_height:1080` to the returned job's `options` and saved render settings. These record the current fixed upload profile; they are not caller-selectable render dimensions. Supersampling changes the internal raster, then reduces the full unfolded UV domain to the same final profile without cropping its sides. Do not infer `screen_width_mm` from these pixel dimensions.

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
