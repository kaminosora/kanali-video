# v1.2.0 angular brightness validation

The experimental filter defaults to off. It uses a tunable empirical angular
luminance curve, not measurements of a TRYX panel. Physical uniformity remains
subject to calibration on the owner's actual screen and fixed viewing position.

## Verification

- Python geometry and pipeline regression suite: 140 tests passed.
- Node UI regression: 5 suites passed, including experimental controls, old
  presets, geometry reset, observer simulation isolation, and shader bindings.
- Actual OpenGL pixel tests: neutral/default settings are pixel identical;
  the gain is applied in linear light to gray, color, black and standee decoration.
- Actual Blender 4.5 rendering: default-off gray 180 stays 180; enabled gray is
  approximately 160 at the center and 180 near the wings. Default off and a unity
  gain reference are pixel identical. GPU/Blender interior difference is at most
  2 code values out of 255 in this gray-card test.
- The Blender mesh uses a linear FLOAT_COLOR POINT gain attribute. The export
  scene is 2160×1080 with square pixels. Its separate simulated scene contains
  gain times response, while the exported scene contains gain only.
- Independent geometry audit: 294 valid sampled configurations, 6003 sample
  points each, confirmed the four-corner minimum. Extreme grazing/zero-floor
  numerical behavior is covered by a regression.
- Chrome interface: actual gray-card import, simulated angular falloff,
  enabled compensation, and unchanged unfolded/simulation separation were
  visually checked. The corrected simulated gray became uniform; no browser
  console errors were reported.

- Portable ZIP extracted to a fresh directory with Chinese characters and spaces,
  launched with system Python removed from PATH, and installed its optional media
  runtime using the official-wheel bootstrap. Six actual MP4 exports passed:
  default-off image/video on NVIDIA NVENC, default-off Blender/x264, AMD AMF
  video, enabled gray on GPU/NVENC, and enabled gray on Blender/x264.
- Those MP4s were decoded and checked for 2160×1080, SAR 1:1, DAR 2:1,
  six frames and actual size below 500,000,000 bytes. Enabled gray 128 produced
  center 112 on GPU and about 113 on Blender, with both wings near 128. This
  specifically rejects accidentally exporting the extra observer simulation.
- A separate two-second gray clip submitted through the browser also passed:
  24,704 bytes, center 112, wings 128, native 2:1 square-pixel canvas.
- Release boundary / first-run installer suite: 13 tests passed. Public archives
  use an explicit file allowlist, frozen-code hashes and SHA-256 checksums;
  private settings, inputs, presets and validation outputs are excluded.

## Limits

No photometric measurement of the physical AMOLED has been performed. The 60%
grazing response and exponent 2 are adjustable starting values. The filter does
not compensate angular color shift, automatic brightness limiting, reflections,
temperature effects, or head motion. See [the calibration guide](BRIGHTNESS.md).
No new Intel QSV hardware result is claimed. Existing hardware detection and
software fallback behavior remain in place.
