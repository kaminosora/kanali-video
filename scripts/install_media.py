"""Explicitly install the official PyAV wheel locally; never redistributed in our portable ZIP."""
from __future__ import annotations

import hashlib
import importlib
import json
import ntpath
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

VERSION = '16.0.1'
PACKAGE = 'av'
MAX_BYTES = 512 * 1024 * 1024


def wheel_info(metadata, *, python_tag=None, platform_tag=None):
    python_tag = python_tag or f'cp{sys.version_info.major}{sys.version_info.minor}'
    platform_tag = platform_tag or ('win_amd64' if struct.calcsize('P') == 8 else 'win32')
    suffix = f'-{python_tag}-{python_tag}-{platform_tag}.whl'
    for item in metadata.get('urls', []):
        if item.get('filename', '').endswith(suffix) and item.get('packagetype') == 'bdist_wheel':
            url = urlsplit(item['url'])
            digest = item.get('digests', {}).get('sha256', '')
            if url.scheme != 'https' or url.hostname != 'files.pythonhosted.org' or len(digest) != 64:
                raise ValueError('PyPI wheel 来源或 SHA-256 信息无效')
            if not 0 < int(item.get('size', 0)) <= MAX_BYTES:
                raise ValueError('PyPI wheel 大小无效')
            return item
    raise ValueError(f'PyPI 未提供 {python_tag}/{platform_tag} 的 av=={VERSION} wheel；请使用受支持的便携包')


def safe_members(archive):
    """Preflight every ZIP entry before extracting any files, including Windows ADS/drives."""
    members, total = [], 0
    allowed = {'av', 'av.libs', f'av-{VERSION}.dist-info'}
    reserved = {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}
    for info in archive.infolist():
        name = info.filename.replace('\\', '/')
        parts = name.rstrip('/').split('/')
        if (not parts or name.startswith('/') or ntpath.splitdrive(name)[0]
                or any(part in {'', '.', '..'} or ':' in part or part.endswith(('.', ' '))
                       or part.split('.')[0].upper() in reserved or any(ord(char)<32 for char in part) for part in parts)
                or parts[0] not in allowed or stat.S_ISLNK(info.external_attr >> 16)):
            raise ValueError(f'wheel ZIP 路径无效：{info.filename}')
        total += info.file_size
        if len(members) >= 10000 or total > MAX_BYTES:
            raise ValueError('wheel 解压大小超过允许范围')
        members.append((info, parts))
    return members


def unpack_wheel(wheel, root):
    root = Path(root).resolve()
    cache = root / '.cache' / 'media-install'
    if not cache.resolve().is_relative_to(root): raise ValueError('安装缓存目录越界')
    cache.mkdir(parents=True, exist_ok=True)
    destination = root / '.deps'
    if not destination.resolve().is_relative_to(root): raise ValueError('安装目录越界')
    destination.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='unpack-', dir=cache) as temporary:
        staging = Path(temporary).resolve()
        if not staging.is_relative_to(cache.resolve()):
            raise ValueError('安装暂存目录越界')
        with zipfile.ZipFile(wheel) as archive:
            members = safe_members(archive)
            for info, parts in members:
                target = staging.joinpath(*parts)
                if not target.resolve().is_relative_to(staging):
                    raise ValueError('wheel 解压目录越界')
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open('wb') as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
        top_level = list(staging.iterdir())
        if not (staging / 'av' / '__init__.py').is_file():
            raise ValueError('wheel 缺少 PyAV 主模块')
        if any((destination / path.name).exists() for path in top_level):
            raise ValueError('包内 .deps 已有同名媒体文件；请使用新解压的便携包，避免覆盖已有安装')
        published = []
        try:
            for path in top_level:
                target = destination / path.name
                path.rename(target)
                published.append(target)
        except Exception:
            # Roll back only the folders just created by this install.
            for target in reversed(published):
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError('安装回滚路径越界')
                if target.is_dir(): shutil.rmtree(target)
                else: target.unlink(missing_ok=True)
            raise


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def installed_version(root):
    deps = Path(root).resolve() / '.deps'
    if not (deps / 'av' / '__init__.py').is_file():
        return None
    sys.path.insert(0, str(deps))
    importlib.invalidate_caches()
    module = importlib.import_module('av')
    if not Path(module.__file__).resolve().is_relative_to(deps):
        raise RuntimeError('发现的 PyAV 不在本便携包内')
    return module.__version__


def download_wheel(info, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / Path(info['filename']).name
    expected = info['digests']['sha256'].lower()
    if final.is_file() and sha256_file(final) == expected:
        return final
    deadline, received, digest = time.monotonic() + 180, 0, hashlib.sha256()
    with tempfile.NamedTemporaryFile(prefix='download-', suffix='.part', dir=directory, delete=False) as stream:
        partial = Path(stream.name)
        try:
            request = Request(info['url'], headers={'User-Agent':'PANORAMA-Mapper-media-installer/1.1'})
            with urlopen(request, timeout=30) as response:
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError('PyAV 下载超过 180 秒，请检查网络后重试')
                    chunk = response.read(1024 * 1024)
                    if not chunk: break
                    received += len(chunk)
                    if received > info['size'] or received > MAX_BYTES:
                        raise ValueError('PyAV 下载大小与 PyPI 信息不符')
                    stream.write(chunk); digest.update(chunk)
            stream.flush()
            if received != info['size'] or digest.hexdigest() != expected:
                raise ValueError('PyAV 下载校验失败，未安装任何内容；请重试')
        except Exception:
            stream.close(); partial.unlink(missing_ok=True); raise
    os.replace(partial, final)
    return final


def main(root=None):
    if root is None:
        from mapper.paths import app_root
        root = app_root()
    root = Path(root).resolve()
    try:
        version = installed_version(root)
        if version == VERSION:
            print(f'媒体依赖 PyAV {VERSION} 已就绪。', flush=True)
            return 0
        if version is not None:
            raise ValueError(f'包内 PyAV 版本为 {version}，需要 {VERSION}；请在新解压的便携包中安装')
        print(f'首次使用需要下载官方 PyAV {VERSION} Windows wheel（含其媒体运行库）。\n'
              '来源：PyPI / files.pythonhosted.org；将校验官方 SHA-256，仅安装到本包 .deps。\n'
              '这些第三方媒体库由用户直接从上游获取，不包含在 PANORAMA Mapper 发行包中。\n'
              '需要联网，失败可再次运行；不会安装全局 Python 包或修改网络设置。', flush=True)
        if input('是否现在下载并安装？[y/N] ').strip().lower() not in {'y','yes'}:
            print('尚未安装媒体依赖；需要时重新启动即可。', flush=True)
            return 1
        request = Request(f'https://pypi.org/pypi/{PACKAGE}/{VERSION}/json', headers={'User-Agent':'PANORAMA-Mapper-media-installer/1.1'})
        with urlopen(request, timeout=30) as response:
            metadata = json.loads(response.read(2 * 1024 * 1024))
        info = wheel_info(metadata)
        print(f'正在下载 {info["filename"]}（{info["size"]/1000000:.1f} MB）…', flush=True)
        wheel = download_wheel(info, root / '.cache' / 'media-downloads')
        unpack_wheel(wheel, root)
        if installed_version(root) != VERSION:
            raise RuntimeError('媒体模块版本验证失败')
        state = {'status':'installed','package':PACKAGE,'version':VERSION,'filename':info['filename'],
                 'source_url':info['url'],'sha256':info['digests']['sha256'],'installed_at':int(time.time())}
        target = root / '.deps' / 'media-runtime.json'
        temporary = target.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        os.replace(temporary, target)
        print('媒体依赖安装完成，可离线使用。', flush=True)
        return 0
    except Exception as error:
        print(f'媒体依赖安装失败：{error}\n请检查网络或安装目录权限，然后重新运行启动程序；无需更改系统代理。', file=sys.stderr, flush=True)
        return 1


def ensure_media(root=None):
    """Return 0 when ready; otherwise explicitly prompt/install or return a retryable failure."""
    return main(root)


if __name__ == '__main__':
    raise SystemExit(main())
