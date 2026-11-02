"""Rebuild the Windows x64 portable release and public source ZIP using project-local tools.

PyAV/native codecs are intentionally excluded. The user explicitly downloads the official
wheel on first run through install_media.py; the portable ZIP never redistributes it.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYINSTALLER_VERSION = '6.22.3'
HOOKS_VERSION = '2026.9'
BUILD_REQUIREMENTS = {'pyinstaller':PYINSTALLER_VERSION,'pyinstaller-hooks-contrib':HOOKS_VERSION,
                      'altgraph':'0.17.5','packaging':'26.3','pefile':'2024.8.26',
                      'pywin32-ctypes':'0.2.3','setuptools':'84.2.1'}
PUBLIC_ROOT_FILES = ('app.py','entrypoint.py','requirements.txt','settings.example.json',
                     'Start-Panorama.cmd','Stop-Panorama.cmd','README.md','LICENSE','THIRD_PARTY_NOTICES.md','.gitignore')
PUBLIC_DOCS = ('docs/CALIBRATION.md','docs/HARDWARE.md','docs/BRIGHTNESS.md','docs/VALIDATION-1.1.0.md','docs/VALIDATION-1.1.1.md','docs/VALIDATION-1.2.0.md','docs/API.md','research/SOURCES.md')
PUBLIC_EXAMPLES = ('examples/calibration-cover.mp4','examples/calibration-standee.mp4','examples/motion-demo.mp4','examples/calibration-2160x1080.png')
PUBLIC_SCRIPTS = ('pick_file.py','stop_server.py','setup_deps.py','install_media.py','build_release.py','verify_pipeline.py','verify_portable.py','test_release.py')
PRIVATE_NAMES = {'settings.json','inputs','outputs','presets','.cache','.deps','.build-deps','__pycache__','.git','.agents','.codex'}
TEXT_SUFFIXES = {'.py','.js','.cjs','.css','.html','.json','.md','.txt','.cmd'}


def require_child(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    if path == parent or not path.is_relative_to(parent):
        raise ValueError(f'Generated path escapes intended directory: {path}')
    return path


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): digest.update(chunk)
    return digest.hexdigest()


def version(root=ROOT):
    match = re.search(r'^VERSION\s*=\s*[\'\"]([0-9]+\.[0-9]+\.[0-9]+)[\'\"]', (Path(root)/'app.py').read_text(encoding='utf-8'), re.MULTILINE)
    if not match: raise ValueError('Cannot determine app VERSION')
    return match.group(1)


def frozen_input_hashes(root=ROOT):
    """Track executable inputs, distinct from web/docs copied beside the executable."""
    root=Path(root).resolve()
    paths=[root/'app.py',root/'entrypoint.py',root/'scripts'/'install_media.py',*sorted((root/'mapper').glob('*.py'))]
    missing=[path.relative_to(root).as_posix() for path in paths if not path.is_file()]
    if missing: raise ValueError('Frozen inputs missing: '+', '.join(missing)+'. Run a full build without --package-only.')
    return {path.relative_to(root).as_posix():sha256(path) for path in paths}


def check_frozen_inputs(expected, root=ROOT):
    current=frozen_input_hashes(root)
    changed=sorted(name for name in set(current)|set(expected) if current.get(name)!=expected.get(name))
    if changed:
        raise ValueError('Frozen inputs changed: '+', '.join(changed)+'. Run a full build without --package-only.')
    return current


def save_frozen_code(destination, inputs):
    destination=Path(destination)
    record={'format_version':1,'code_hashes':dict(inputs),'exe_sha256':sha256(destination/'PanoramaMapper.exe')}
    target=destination/'FROZEN-CODE.json'
    temporary=target.with_suffix('.json.tmp')
    try:
        temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        os.replace(temporary,target)
    finally:
        temporary.unlink(missing_ok=True)


def verify_frozen_code(destination, root=ROOT):
    destination=Path(destination)
    try:
        record=json.loads((destination/'FROZEN-CODE.json').read_text(encoding='utf-8'))
        if (not isinstance(record,dict) or record.get('format_version')!=1
                or not isinstance(record.get('code_hashes'),dict) or not record.get('exe_sha256')):
            raise ValueError('invalid manifest format')
    except (OSError,ValueError) as error:
        raise ValueError('FROZEN-CODE.json is missing or invalid. Run a full build without --package-only.') from error
    check_frozen_inputs(record['code_hashes'],root)
    try:
        executable=sha256(destination/'PanoramaMapper.exe')
    except OSError as error:
        raise ValueError('Frozen executable is missing. Run a full build without --package-only.') from error
    if executable!=record['exe_sha256']:
        raise ValueError('Frozen executable does not match FROZEN-CODE.json. Run a full build without --package-only.')
    return record


def public_files(root=ROOT, *, source=True, strict=False):
    root = Path(root).resolve()
    selected = [root/path for path in (*PUBLIC_ROOT_FILES,*PUBLIC_DOCS,*PUBLIC_EXAMPLES)]
    for folder in ('mapper','blender'):
        selected += list((root/folder).glob('*.py'))
    selected += [path for path in (root/'web').iterdir() if path.is_file() and path.suffix in {'.js','.css','.html'}]
    selected += [root/'scripts'/name for name in PUBLIC_SCRIPTS if source or name != 'test_release.py']
    if source:
        selected += list((root/'tests').glob('*.py'))
        selected += list((root/'web').glob('*.test.cjs'))
    if strict:
        missing = [str(path.relative_to(root)) for path in selected if not path.is_file()]
        if missing: raise ValueError('Public release files are missing: '+', '.join(missing))
    return sorted({path for path in selected if path.is_file()}, key=lambda path:path.relative_to(root).as_posix())


def audit_public(paths, root=ROOT):
    root = Path(root).resolve()
    home = str(Path.home()).replace('\\','/').lower()
    workspace = str(root).replace('\\','/').lower()
    for path in paths:
        relative = require_child(path, root).relative_to(root)
        if any(part in PRIVATE_NAMES for part in relative.parts):
            raise ValueError(f'Private path in release: {relative}')
        if path.suffix.lower() in TEXT_SUFFIXES or path.name == 'LICENSE':
            text = path.read_text(encoding='utf-8-sig').replace('\\\\','\\').replace('\\','/').lower()
            if home in text or workspace in text:
                raise ValueError(f'Local machine path in public text: {relative}')


def write_zip(paths, root, target, prefix):
    root, target = Path(root).resolve(), Path(target)
    temporary = target.with_suffix('.zip.tmp')
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            for path in sorted(paths,key=lambda item:Path(item).relative_to(root).as_posix()):
                relative = require_child(path,root).relative_to(root).as_posix()
                info = zipfile.ZipInfo(prefix+'/'+relative,date_time=(2026,1,1,0,0,0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o100644 << 16)
                with path.open('rb') as source, archive.open(info,'w',force_zip64=True) as output:
                    shutil.copyfileobj(source,output,1024*1024)
        with zipfile.ZipFile(temporary) as archive:
            error = archive.testzip()
            if error: raise ValueError('Corrupt archive member: '+error)
        os.replace(temporary,target)
    finally:
        temporary.unlink(missing_ok=True)


def local_environment(root=ROOT):
    root = Path(root).resolve()
    cache = root/'.cache'/'build-release'
    temp = cache/'tmp'
    temp.mkdir(parents=True,exist_ok=True)
    return dict(os.environ,PYTHONPATH=os.pathsep.join(map(str,[root/'.build-deps',root/'.deps',root])),
                PYTHONNOUSERSITE='1',PYTHONUTF8='1',PYINSTALLER_CONFIG_DIR=str(cache/'pyinstaller'),
                PIP_CACHE_DIR=str(cache/'pip'),PIP_DISABLE_PIP_VERSION_CHECK='1',TEMP=str(temp),TMP=str(temp),TMPDIR=str(temp))


def install_build_tools(environment):
    local = ROOT/'.build-deps'
    distributions = {dist.metadata['Name'].lower():dist.version for dist in importlib.metadata.distributions(path=[str(local)])}
    if all(distributions.get(name)==required for name,required in BUILD_REQUIREMENTS.items()):
        return
    local.mkdir(exist_ok=True)
    subprocess.run([sys.executable,'-m','pip','install','--upgrade','--only-binary=:all:',
                    '--target',str(local),*[f'{name}=={required}' for name,required in BUILD_REQUIREMENTS.items()]],
                   check=True,cwd=ROOT,env=environment)


def build_spec(folder):
    spec = folder/'PanoramaMapper.spec'
    text = f'''# Generated by scripts/build_release.py; never copied to a release.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all
root=Path({str(ROOT)!r})
datas=[];binaries=[];hiddenimports=['tkinter','tkinter.filedialog','PIL.ImageCms','PIL.ImageTk','_moderngl','glcontext.wgl','scripts.install_media','ssl','_ssl','urllib.request','zipfile']
for package in ['numpy','PIL','moderngl','glcontext']:
    package_datas,package_binaries,package_hidden=collect_all(package,include_py_files=False,
        filter_submodules=lambda name:not any(part in name for part in ['.tests','.testing','.f2py','._pyinstaller','.conftest','.testutils','.mypy_plugin']))
    datas+=package_datas;binaries+=package_binaries
    hiddenimports += [name for name in package_hidden if '.tests' not in name and '.testing' not in name]
for path in (root/'.deps'/'numpy.libs').glob('*.dll'):
    binaries.append((str(path),'numpy.libs'))
a=Analysis([str(root/'entrypoint.py')],pathex=[str(root/'.deps'),str(root)],binaries=binaries,datas=datas,
           hiddenimports=hiddenimports,hookspath=[],hooksconfig={{}},runtime_hooks=[],
           excludes=['av','numpy.tests','numpy.testing','numpy.f2py','numpy._pyinstaller','numpy.conftest','pytest','IPython','matplotlib','scipy','pandas'],noarchive=False,optimize=0)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='PanoramaMapper',debug=False,bootloader_ignore_signals=False,
        strip=False,upx=False,console=True,disable_windowed_traceback=False,argv_emulation=False,target_arch=None,
        codesign_identity=None,entitlements_file=None)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='PanoramaMapper')
'''
    spec.write_text(text,encoding='utf-8')
    return spec


def collect_licenses(destination):
    destination = Path(destination)
    if destination.exists():
        require_child(destination,ROOT/'dist')
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    manifest=[]
    for directory, names, category in ((ROOT/'.deps',{'numpy','pillow','moderngl','glcontext'},'runtime'),
                                        (ROOT/'.build-deps',set(BUILD_REQUIREMENTS),'build/runtime')):
        for dist in importlib.metadata.distributions(path=[str(directory)]):
            name=dist.metadata['Name'].lower()
            if name not in names: continue
            files=[]
            for item in dist.files or []:
                parts=Path(str(item)).parts
                filename=Path(str(item)).name.lower()
                legal_name=filename.startswith(('license','copying','notice','authors','copyright'))
                legal_directory=any('license' in part.lower() for part in parts[:-1])
                legal_text=Path(filename).suffix in {'','.txt','.md','.rst','.terms'}
                if '__pycache__' not in parts and legal_text and (legal_name or legal_directory):
                    source=Path(dist.locate_file(item))
                    if source.is_file():
                        target=destination/f'{name}-{dist.version}'/Path(*parts[1:])
                        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
                        files.append(target.relative_to(destination).as_posix())
            if not files: raise ValueError(f'No license found for {name} {dist.version}')
            manifest.append({'name':name,'version':dist.version,'category':category,'files':files})
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if not python_license.is_file(): raise ValueError('CPython LICENSE.txt is required')
    (destination/'python').mkdir();shutil.copy2(python_license,destination/'python'/'LICENSE.txt')
    for path in (Path(sys.base_prefix)/'tcl').glob('*/license*'):
        if path.is_file():
            target=destination/'tcl-tk'/path.parent.name/path.name
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    # PyInstaller's bootloader exception is included in its installed COPYING files.
    pyinstaller=ROOT/'.build-deps'/'PyInstaller'
    for path in pyinstaller.glob('COPYING*'):
        shutil.copy2(path,destination/f'PyInstaller-{path.name}')
    manifest.append({'name':'CPython','version':sys.version.split()[0],'files':['python/LICENSE.txt']})
    (destination/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def portable_files(directory, public):
    directory=Path(directory)
    paths=[directory/'PanoramaMapper.exe',directory/'BUILD-MANIFEST.json',directory/'FROZEN-CODE.json']
    paths += [directory/path.relative_to(ROOT) for path in public]
    for folder in ('_internal','licenses'):
        paths += [path for path in (directory/folder).rglob('*') if path.is_file()]
    paths=sorted(set(paths))
    for path in paths:
        relative=path.relative_to(directory)
        if any(part in PRIVATE_NAMES for part in relative.parts): raise ValueError('Private runtime path: '+str(relative))
        if relative.parts[0]=='_internal' and (any(part.lower() in {'av','av.libs'} or part.lower().startswith('av-16.') for part in relative.parts)
                or re.match(r'(avcodec|avdevice|avfilter|avformat|avutil|swresample|swscale|libx264|libx265)[-_.]',path.name,re.I)):
            raise ValueError('Excluded PyAV/native codec binary appeared in portable bundle: '+str(relative))
        if not path.is_file(): raise ValueError('Missing portable file: '+str(relative))
    return paths


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-only',action='store_true',help='Build/copy the portable folder, but defer final docs/archive checks.')
    parser.add_argument('--package-only',action='store_true',help='Reuse the already built executable; refresh public files, licenses and archives.')
    parser.add_argument('--skip-install',action='store_true',help='Require existing .build-deps instead of running project-local pip.')
    args=parser.parse_args()
    if os.name!='nt' or sys.maxsize<=2**32: raise SystemExit('Build with 64-bit CPython on Windows.')
    release_version=version()
    dist=ROOT/'dist'
    name=f'PanoramaMapper-v{release_version}-windows-x64'
    destination=require_child(dist/name,dist)
    if args.package_only:
        # Fail before copying side files, regenerating manifests, or deleting license folders.
        verify_frozen_code(destination,ROOT)
    dist.mkdir(exist_ok=True)
    environment=local_environment()
    public=public_files(source=False,strict=not args.build_only)
    source=public_files(source=True,strict=not args.build_only)
    audit_public(source)
    if not args.package_only:
        if not args.skip_install: install_build_tools(environment)
        build=ROOT/'build'/'release';build.mkdir(parents=True,exist_ok=True)
        spec=build_spec(build)
        frozen_inputs=frozen_input_hashes(ROOT)
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--distpath',str(build/'frozen'),
                        '--workpath',str(build/'work'),str(spec)],cwd=ROOT,env=environment,check=True)
        check_frozen_inputs(frozen_inputs,ROOT)
        generated=require_child(build/'frozen'/'PanoramaMapper',build/'frozen')
        if destination.exists():
            require_child(destination,dist);shutil.rmtree(destination)
        shutil.copytree(generated,destination)
        save_frozen_code(destination,frozen_inputs)
    for path in public:
        target=destination/path.relative_to(ROOT)
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    collect_licenses(destination/'licenses')
    binaries=[path for path in (destination/'_internal').rglob('*') if path.is_file() and path.suffix.lower() in {'.dll','.pyd'}]
    inventory=[{'path':path.relative_to(destination).as_posix(),'sha256':sha256(path)} for path in sorted(binaries)]
    (destination/'licenses'/'native-binaries.json').write_text(json.dumps(inventory,indent=2)+'\n',encoding='utf-8')
    manifest={'version':release_version,'platform':'windows-x64','python':sys.version.split()[0],
              'pyinstaller':PYINSTALLER_VERSION,'hooks':HOOKS_VERSION,'build_requirements':BUILD_REQUIREMENTS,
              'media_runtime':'excluded; user-confirmed official PyPI install',
              'frozen_code_manifest_sha256':sha256(destination/'FROZEN-CODE.json'),
              'public_files':{path.relative_to(ROOT).as_posix():sha256(path) for path in public},
              'recipe':'scripts/build_release.py','archive_timestamps':'2026-01-01T00:00:00Z',
              'note':'Pinned build recipe; OS/Python/native toolchain changes can change binary bytes.'}
    (destination/'BUILD-MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    portable=portable_files(destination,public)
    print('Portable folder: '+str(destination),flush=True)
    if args.build_only:
        print('Archives deferred (--build-only). Final README/public docs must be ready before --package-only.',flush=True)
        return
    portable_zip=dist/(name+'.zip')
    source_zip=dist/f'PanoramaMapper-v{release_version}-source.zip'
    write_zip(portable,destination,portable_zip,name)
    write_zip(source,ROOT,source_zip,f'PanoramaMapper-v{release_version}-source')
    checksums=dist/'SHA256SUMS'
    checksums.write_text(''.join(f'{sha256(path)}  {path.name}\n' for path in [portable_zip,source_zip]),encoding='ascii')
    print(checksums.read_text(),end='',flush=True)


if __name__=='__main__':
    main()
