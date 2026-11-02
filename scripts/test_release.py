"""Release-boundary and first-run media installer regressions (no network required)."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import build_release,install_media


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.cache')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def file(self,name,data='public'):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(data,encoding='utf-8');return path

    def test_allowlist_excludes_settings_sources_outputs_and_private_docs(self):
        self.file('web/app.js');self.file('mapper/media.py');self.file('tests/test_portable.py');self.file('tests/tool_paths.py')
        for name in ['settings.json','inputs/user.mp4','outputs/user.mp4','presets/private.json','docs/old-private.md','research/private.c4d']:
            self.file(name)
        names=[path.relative_to(self.root).as_posix() for path in build_release.public_files(self.root)]
        self.assertIn('tests/test_portable.py',names)
        self.assertEqual(names,['mapper/media.py','tests/test_portable.py','tests/tool_paths.py','web/app.js'])

    def test_private_path_audit_and_boundary_guard_reject_leaks(self):
        path=self.file('README.md',str(self.root))
        with self.assertRaisesRegex(ValueError,'Local machine path'):
            build_release.audit_public([path],self.root)
        with self.assertRaises(ValueError):
            build_release.require_child(self.root.parent,self.root)

    def test_zip_order_and_timestamps_are_reproducible(self):
        one=self.file('a.txt','one');two=self.file('folder/b.txt','two')
        first=self.root/'one.zip';second=self.root/'two.zip'
        build_release.write_zip([two,one],self.root,first,'source')
        build_release.write_zip([one,two],self.root,second,'source')
        self.assertEqual(build_release.sha256(first),build_release.sha256(second))
        with zipfile.ZipFile(first) as archive:
            self.assertEqual(archive.namelist(),['source/a.txt','source/folder/b.txt'])

    def frozen_fixture(self):
        for name in ['app.py','entrypoint.py','mapper/jobs.py','scripts/install_media.py']:
            self.file(name,'frozen source')
        self.destination=self.root/'dist'/'portable'
        self.file('dist/portable/PanoramaMapper.exe','frozen executable')
        snapshot=build_release.frozen_input_hashes(self.root)
        build_release.save_frozen_code(self.destination,snapshot)
        return snapshot

    def test_package_only_accepts_side_files_but_rejects_changed_frozen_code(self):
        self.frozen_fixture()
        self.file('docs/API.md','new docs');self.file('web/app.js','new UI')
        build_release.verify_frozen_code(self.destination,self.root)
        self.file('mapper/jobs.py','modified after freeze')
        with self.assertRaisesRegex(ValueError,'full build'):
            build_release.verify_frozen_code(self.destination,self.root)

    def test_frozen_gate_catches_added_removed_modules_and_replaced_executable(self):
        for mutation in ['add','remove','exe']:
            with self.subTest(mutation=mutation):
                self.file('mapper/jobs.py','frozen source')
                (self.root/'mapper'/'new_module.py').unlink(missing_ok=True)
                self.frozen_fixture()
                if mutation=='add': self.file('mapper/new_module.py','new module')
                elif mutation=='remove': (self.root/'mapper'/'jobs.py').unlink()
                else: self.file('dist/portable/PanoramaMapper.exe','another binary')
                with self.assertRaisesRegex(ValueError,'full build'):
                    build_release.verify_frozen_code(self.destination,self.root)

    def test_old_or_invalid_frozen_manifest_requires_full_build(self):
        self.frozen_fixture()
        manifest=self.destination/'FROZEN-CODE.json'
        manifest.unlink()
        with self.assertRaisesRegex(ValueError,'full build'):
            build_release.verify_frozen_code(self.destination,self.root)
        manifest.write_text('{invalid',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'full build'):
            build_release.verify_frozen_code(self.destination,self.root)

    def test_source_changes_during_freeze_cannot_be_blessed_by_a_new_manifest(self):
        before=self.frozen_fixture()
        self.file('scripts/install_media.py','changed during freeze')
        with self.assertRaisesRegex(ValueError,'full build'):
            build_release.check_frozen_inputs(before,self.root)

    def test_license_directory_never_collects_python_code_or_cache(self):
        license=self.file('.build-deps/packaging-26.3.dist-info/licenses/LICENSE','license text')
        cache=self.file('.build-deps/packaging/licenses/__pycache__/__init__.pyc','compiled code')
        code=self.file('.build-deps/packaging/licenses/__init__.py','python code')
        self.file('Python/LICENSE.txt','CPython license')
        files=[path.relative_to(self.root/'.build-deps') for path in [license,cache,code]]
        dist=SimpleNamespace(metadata={'Name':'packaging'},version='26.3',files=files,locate_file=lambda item:self.root/'.build-deps'/item)
        with patch.object(build_release,'ROOT',self.root),patch.object(sys,'base_prefix',str(self.root/'Python')),patch.object(build_release.importlib.metadata,'distributions',return_value=[dist]):
            build_release.collect_licenses(self.root/'dist'/'licenses')
        collected=list((self.root/'dist'/'licenses').rglob('*'))
        self.assertFalse(any(path.suffix in {'.py','.pyc'} for path in collected))

    def wheel(self,names):
        path=self.root/'av.whl'
        with zipfile.ZipFile(path,'w') as archive:
            for name in names: archive.writestr(name,'__version__="16.0.1"')
        return path

    def test_wheel_rejects_zip_slip_drives_ads_and_unrelated_packages(self):
        for name in ['../outside.txt','av/../../outside.txt','C:/outside.txt','av/file:stream','numpy/overwritten.py','av/CON.txt','av/invalid.']:
            with self.subTest(name=name):
                path=self.wheel(['av/__init__.py',name])
                with self.assertRaises(ValueError):install_media.unpack_wheel(path,self.root)
                self.assertFalse((self.root/'.deps'/'av'/'__init__.py').exists())

    def test_existing_media_install_skips_prompt_and_network(self):
        with patch.object(install_media,'installed_version',return_value=install_media.VERSION),patch('builtins.input',side_effect=AssertionError('No prompt')),patch.object(install_media,'urlopen',side_effect=AssertionError('No network')):
            self.assertEqual(install_media.ensure_media(self.root),0)

    def test_declining_first_run_never_downloads(self):
        with patch.object(install_media,'installed_version',return_value=None),patch('builtins.input',return_value='n'),patch.object(install_media,'urlopen',side_effect=AssertionError('No network')):
            self.assertEqual(install_media.main(self.root),1)

    def test_download_checks_streamed_digest_and_cleans_partial_failure(self):
        data=b'official wheel bytes';info={'filename':'av.whl','url':'https://files.pythonhosted.org/av.whl','size':len(data),'digests':{'sha256':hashlib.sha256(data).hexdigest()}}
        with patch.object(install_media,'urlopen',return_value=io.BytesIO(data)):
            result=install_media.download_wheel(info,self.root/'downloads')
        self.assertEqual(result.read_bytes(),data)
        info['filename']='bad.whl';info['digests']['sha256']='0'*64
        with patch.object(install_media,'urlopen',return_value=io.BytesIO(data)),self.assertRaises(ValueError):
            install_media.download_wheel(info,self.root/'downloads')
        self.assertFalse((self.root/'downloads'/'bad.whl').exists())
        self.assertEqual(list((self.root/'downloads').glob('*.part')),[])

    def test_successful_install_records_exact_upstream_wheel(self):
        wheel=self.wheel(['av/__init__.py',f'av-{install_media.VERSION}.dist-info/METADATA'])
        info={'filename':f'av-{install_media.VERSION}-cp313-cp313-win_amd64.whl','url':'https://files.pythonhosted.org/av.whl','size':wheel.stat().st_size,'digests':{'sha256':install_media.sha256_file(wheel)},'packagetype':'bdist_wheel'}
        metadata=io.BytesIO(json.dumps({'urls':[info]}).encode())
        with patch.object(install_media,'installed_version',side_effect=[None,install_media.VERSION]),patch('builtins.input',return_value='y'),patch.object(install_media,'urlopen',return_value=metadata),patch.object(install_media,'download_wheel',return_value=wheel):
            self.assertEqual(install_media.main(self.root),0)
        state=json.loads((self.root/'.deps'/'media-runtime.json').read_text())
        self.assertEqual(state['sha256'],info['digests']['sha256'])
        self.assertEqual(state['status'],'installed')
        self.assertTrue((self.root/'.deps'/'av'/'__init__.py').is_file())


if __name__=='__main__':unittest.main()
