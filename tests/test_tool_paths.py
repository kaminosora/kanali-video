from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock

from tests.tool_paths import find_tool


class ToolPathTests(unittest.TestCase):
    def setUp(self):
        directory = Path(__file__).resolve().parents[1] / '.cache' / 'tests-tool-paths'
        directory.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_environment_then_path_then_local_settings(self):
        env_tool, path_tool, settings_tool = [self.root / name for name in ('env.exe', 'path.exe', 'local tool.exe')]
        for path in (env_tool, path_tool, settings_tool):
            path.write_bytes(b'fixture only')
        (self.root / 'settings.json').write_text(json.dumps({'ffmpeg_path': settings_tool.name}), encoding='utf-8')
        self.assertEqual(find_tool('ffmpeg', root=self.root, environ={'FFMPEG_PATH': str(env_tool)}, which=lambda _: str(path_tool)), str(env_tool))
        self.assertEqual(find_tool('ffmpeg', root=self.root, environ={}, which=lambda _: str(path_tool)), str(path_tool))
        self.assertEqual(find_tool('ffmpeg', root=self.root, environ={}, which=lambda _: None), str(settings_tool))

    def test_no_machine_specific_fallback(self):
        self.assertIsNone(find_tool('ffmpeg', root=self.root, environ={}, which=lambda _: None))
        (self.root / 'settings.json').write_text('{bad json', encoding='utf-8')
        self.assertIsNone(find_tool('ffmpeg', root=self.root, environ={}, which=lambda _: None))


if __name__ == '__main__':
    unittest.main()
