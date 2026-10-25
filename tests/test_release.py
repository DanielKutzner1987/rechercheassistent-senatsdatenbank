"""Check packaging exclusions and redacted credential diagnostics in isolated Git trees."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'check_release.py'


class ReleaseChecks(unittest.TestCase):
    def check_file(self, name, content):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q'], cwd=root, check=True, capture_output=True)
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
            subprocess.run(['git', 'add', '.'], cwd=root, check=True, capture_output=True)
            spec = importlib.util.spec_from_file_location('release_check', SCRIPT)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.ROOT = root
            output = StringIO()
            failed = False
            with redirect_stdout(output):
                try:
                    module.main()
                except SystemExit:
                    failed = True
            return failed, output.getvalue()

    def test_credentials_rejected_without_printing_values(self):
        samples = ['AIza' + 'a' * 35, 'AQ.' + 'b' * 40, 'github_pat_' + 'c' * 40,
                   'sk-' + 'd' * 32, 'AKIA' + 'A' * 16,
                   'https://' + 'demo:password' + '@example.test']
        for sample in samples:
            with self.subTest(prefix=sample[:3]):
                failed, output = self.check_file('example.txt', sample)
                self.assertTrue(failed)
                self.assertNotIn(sample, output)

    def test_runtime_files_rejected_even_when_force_tracked(self):
        for name in ['secrets/key.txt', '.env', 'state/private.txt', 'sample.pdf',
                     'knowledge.sqlite-wal', 'certificate.pfx']:
            with self.subTest(name=name):
                self.assertTrue(self.check_file(name, 'private fixture')[0])

    def test_personal_path_rejected(self):
        path = 'C:' + '/Users/' + 'example/private.txt'
        failed, output = self.check_file('example.txt', path)
        self.assertTrue(failed)
        self.assertNotIn(path, output)

    def test_public_template_allowed(self):
        self.assertFalse(self.check_file('.env.example', 'API_PORT=8094\n')[0])

    def test_private_generator_import_is_rejected(self):
        self.assertTrue(self.check_file('src/runtime.py', 'from inquiry_generator import register_generator\n')[0])

    def test_deleting_private_file_does_not_hide_it_in_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def git(*args):
                return subprocess.run(['git', '-c', 'user.name=Release Test', '-c',
                    'user.email=release-test@example.invalid', *args],cwd=root,check=True,capture_output=True)
            git('init','-q')
            (root/'src').mkdir()
            (root/'src/inquiry_generator.py').write_text('# synthetic private extension',encoding='utf-8')
            git('add','.'); git('commit','-qm','Synthetic private commit')
            git('rm','src/inquiry_generator.py')
            (root/'README.md').write_text('Public project',encoding='utf-8')
            git('add','.'); git('commit','-qm','Remove private file from current tree')
            spec=importlib.util.spec_from_file_location('history_release_check',SCRIPT)
            module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            module.ROOT=root
            output=StringIO()
            with redirect_stdout(output),self.assertRaises(SystemExit): module.main()
            self.assertIn('publishable commit history',output.getvalue())


if __name__ == '__main__':
    unittest.main()
