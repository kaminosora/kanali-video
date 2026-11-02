import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from mapper.paths import app_root,worker_command


class RuntimePathTests(unittest.TestCase):
    def test_source_root_does_not_depend_on_current_directory(self):
        self.assertEqual(app_root(),ROOT)
        self.assertEqual(Path(worker_command('gpu-worker','job.json')[1]),ROOT/'entrypoint.py')

    def test_frozen_workers_use_the_moved_executable_not_python_or_build_paths(self):
        executable=ROOT/'other location 中文'/'PanoramaMapper.exe'
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(executable)):
            self.assertEqual(app_root(),executable.parent)
            self.assertEqual(worker_command('gpu-worker','job.json'),[str(executable),'--gpu-worker','job.json'])
