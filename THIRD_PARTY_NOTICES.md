# Third-party notices

The MIT license in `LICENSE` applies to PANORAMA Mapper's own source code.
It does not relicense Python, third-party packages, codecs, system libraries,
external applications, user media, or trademarks.

## Included in the portable Windows package

The build copies the installed distributions' actual license and notice files
into `licenses/` and writes a version manifest there. The runtime uses:

| Component | Upstream license / source |
| --- | --- |
| CPython, including its bundled libraries | PSF and incorporated third-party terms; [Python source](https://www.python.org/downloads/source/) |
| Tcl/Tk | Tcl/Tk terms; [Tcl/Tk source](https://www.tcl.tk/software/tcltk/download.html) |
| NumPy and its native dependencies | BSD and included component notices; [NumPy source](https://github.com/numpy/numpy) |
| Pillow and its image libraries | MIT-CMU and included component notices; [Pillow source](https://github.com/python-pillow/Pillow) |
| ModernGL | MIT; [ModernGL source](https://github.com/moderngl/moderngl) |
| glcontext | MIT; [glcontext source](https://github.com/moderngl/glcontext) |
| PyInstaller bootloader/runtime | GPL with the bootloader exception; [PyInstaller license and exception](https://pyinstaller.org/en/stable/license.html) |

The CPython license includes incorporated library notices and Microsoft runtime
redistribution terms. These terms apply to their respective components.
The package is intended for supported Microsoft Windows systems.
The build records native binary hashes in `licenses/native-binaries.json`.
License texts for pinned build tools and their dependencies are also retained in
`licenses/manifest.json`; inclusion in that manifest does not mean every build
tool is used by the application at runtime.

## Media runtime intentionally not redistributed

The portable build explicitly excludes `av`, `av.libs`, and PyAV's native media
libraries. It does not contain Blender or an FFmpeg executable either.

At the user's explicit first-run confirmation, `scripts/install_media.py`
downloads the pinned official PyAV wheel directly from PyPI's file service,
verifies the SHA-256 supplied by PyPI, and installs it into the user's package
directory `.deps`. No system Python or global package installation is required.
The upstream wheel's licenses and terms govern that separate download.

PyAV's Python bindings are BSD-3-Clause, but a wheel can include additional
FFmpeg/codec libraries under different terms. Its top-level BSD metadata must
not be treated as a license for every included native library.
The build therefore does not redistribute those DLLs or claim that they are MIT.
The exact downloaded wheel URL, version and SHA-256 are recorded locally in
`.deps/media-runtime.json` after installation.

Upstream references: [PyAV](https://github.com/PyAV-Org/PyAV),
[PyAV's FFmpeg build sources](https://github.com/PyAV-Org/pyav-ffmpeg),
[FFmpeg legal information](https://ffmpeg.org/legal.html),
[PyAV on PyPI](https://pypi.org/project/av/).

## External tools and user content

Blender and FFmpeg are discovered or selected by the user. They remain separate
installations and are not included in these archives. Obtain them from their
official download pages and retain the applicable license/source materials:
[Blender](https://www.blender.org/download/),
[FFmpeg](https://ffmpeg.org/download.html).

PANORAMA Mapper is an independent tool. References to TRYX, PANORAMA, KANALI,
GPU vendors and other products do not imply endorsement. Imported videos,
images, research/reference assets, presets and render outputs are not included
in the public distribution and retain their owners' rights.
