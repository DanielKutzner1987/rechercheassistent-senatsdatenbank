"""Exercise Windows starter decisions without Docker, network, browser or real secrets."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows starter')
class SetupTests(unittest.TestCase):
    def run_setup(self, action, existing_pdf=False):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            (root / 'data').mkdir()
            (root / 'secrets').mkdir()
            shutil.copy(source / 'scripts' / 'setup.ps1', root / 'scripts' / 'setup.ps1')
            shutil.copy(source / '.env.example', root / '.env.example')
            (root / 'secrets' / 'gemini_api_key.txt').write_text('synthetic-test-value')
            if existing_pdf:
                (root / 'data' / 'own.pdf').write_text('synthetic PDF fixture')
            harness = root / 'harness.ps1'
            harness.write_text('''
function global:docker {
    Add-Content -LiteralPath $env:SETUP_TEST_LOG -Value ($args -join ' ')
    $global:LASTEXITCODE = 0
}
function global:Invoke-RestMethod { @{data=@(@{id='test'})} }
function global:Invoke-WebRequest { @{StatusCode=200} }
function global:Start-Process { throw 'Browser must not open during test' }
function global:Read-Host { throw 'Existing key must not be requested again' }
& (Join-Path $PSScriptRoot 'scripts/setup.ps1') -Action $env:SETUP_TEST_ACTION -NoBrowser
''', encoding='utf-8')
            environment = dict(os.environ, SETUP_TEST_ACTION=action,
                               SETUP_TEST_LOG=str(root / 'commands.txt'))
            result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(harness)], env=environment, capture_output=True,
                                    text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            return (root / 'commands.txt').read_text(), result.stdout

    def test_first_start_creates_demo(self):
        commands, output = self.run_setup('Start')
        self.assertIn('compose run --rm demo', commands)
        self.assertIn('Bereit:', output)

    def test_existing_pdfs_and_import_do_not_add_demo(self):
        for action in ['Start', 'Import']:
            with self.subTest(action=action):
                commands, output = self.run_setup(action, existing_pdf=True)
                self.assertNotIn('compose run --rm demo', commands)
                self.assertIn('compose run --rm index', commands)
                self.assertLess(commands.index('compose stop app webui'),
                                commands.index('compose run --rm index'))

    def test_stop_never_builds_or_imports(self):
        commands, output = self.run_setup('Stop')
        self.assertIn('compose stop', commands)
        self.assertNotIn('compose build', commands)
        self.assertNotIn('compose run', commands)


if __name__ == '__main__':
    unittest.main()
