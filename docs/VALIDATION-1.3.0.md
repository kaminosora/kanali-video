# v1.3.0 calibration verification

This release adds an angular shoulder parameter and a dual-axis error preview.
The actual-axis slider is a physical hypothesis supplied by the user; it is not
a measurement. No photometric or dimensional calibration of the physical TRYX
screen was performed by this software validation.

## Geometry and rendering evidence

- Formal axis .54 with actual axis .54: an actual GPU/Blender exported PNG atlas
  was reapplied to the physical curved model using PanelUV, then rendered from
  the target eye. Ten horizontal lines had maximum peak-to-peak residual of
  0.131 pixels (GPU atlas) and 0.139 pixels (Blender atlas).
- Formal axis .50 with actual axis .54: the same replay method produced clear
  S-shaped lines with maximum residual 15.16 pixels. Aligning axes removed the
  modeled error. Both atlases were native 2160×1080; Blender pixel aspect 1:1.
- Shifted .54 axis with shoulder 2: actual GPU/Blender gray output differs by at
  most 1/255. The darkest compensation region follows the shifted axis; it is
  not left at the center of the upload canvas.
- Narrow transition stress case: width 400mm, arc 2mm, shoulder 8, axis .53.
  Both renderers used 575 columns / 74,304 vertices. The maximum interior pixel
  difference was 2/255, mean difference .02145/255, and no black holes appeared.
- Independent continuous angular-response comparison at actual 2160 pixel
  centers: local refinement reduced maximum interpolation error from 6.48% to
  0.106% for a 2mm arc, from 12.04% to 0.0555% for width 400mm, and from 15.55%
  to 0.109% after offsetting that axis. This adds only 62–64 columns. Actual GPU
  gray pixels differ from the continuous ideal by at most 1/255 in these cases.
- Additional randomized geometry checks confirmed convex normals, projected
  bounds, cover UV and four-corner darkest reference under asymmetric axes.

## Software and delivery checks

- Python regression: 160 tests passed, including axis mismatch/alignment,
  continuity, validation, neutral/default behavior and local adaptive sampling.
- Node UI regression: 6 suites passed. These exercise control handlers, old
  presets, simulation/export isolation, matching atlas indices, geometry reset,
  apply-estimate behavior and exact 50% slider stepping.
- Chrome: parallel-line import, actual-axis 54% against render-axis 50%, the
  +86.4 pixel readout, and applying 54% to the formal render config were checked.
- A two-second baseline MP4 was submitted with the wrong-axis simulation active;
  the saved formal render config remained .50. Simulation is not delivery.
- Release-boundary and first-run media installer tests: 13 passed.

- Final portable bundle: extracted under a fresh Chinese-and-space path and
  launched with system Python removed from PATH. Official media-wheel bootstrap,
  line-chart route, two-axis API isolation and eight real MP4 exports passed.
  Cases include NVIDIA NVENC, AMD AMF, Blender/x264, default brightness, and
  combined axis .54 / shoulder 2 on both renderers. Each decoded output was
  2160×1080, SAR 1:1, DAR 2:1, six frames and below 500,000,000 bytes.
- The combined-axis MP4 gray center was approximately 109–110, with wings
  approximately 126–128. Side samples specifically rule out accidental export
  of the observer-only screen attenuation. Baseline UI export had 60 frames,
  353,240 bytes and formal axis .50 despite the active .54 preview hypothesis.
- Chrome also rendered the locally refined 2mm arc / shoulder 8 combination
  without console errors. The desktop calibration columns scroll independently;
  the two-column export row stays in normal document flow without overlap.

## Limits

The empirical shoulder parameter cannot compensate arbitrary fixed panel defects,
reflections, angular color shift, automatic brightness limiting or an incorrect
physical profile. Strong settings may turn bright bands into dark bands. Calibrate
geometry with the parallel-line source first, then brightness with neutral gray.
Keep the baseline export axis fixed while matching actual-axis hypotheses; free
movement of both axes, eye and arc parameters does not uniquely identify hardware.
No new Intel GPU hardware claim is made.
