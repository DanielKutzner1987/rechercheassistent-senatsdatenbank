"""Keep private extensions out of the public release and its history."""
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('release_scope',Path(__file__).resolve().parents[1]/'scripts/check_release.py')
scope=importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


class ReleaseScopeTests(unittest.TestCase):
    def test_private_extensions_and_development_folders_are_rejected(self):
        for name in ('src/inquiry_generator.py','src/inquiry_web.py','src/answer_drafter.py',
                     'tests/test_inquiry_resume.py','tests/test_answer_drafter.py',
                     'Betrieb/compose.yaml','Archiv/private.txt','.agents/AGENTS.md',
                     'outputs/results.json','src\\answer_document.py'):
            with self.subTest(path=name): self.assertTrue(scope.private_extension_path(name))

    def test_research_assistant_and_user_installation_files_remain_public(self):
        for name in ('src/agent.py','src/gemini_bridge.py','src/runtime.py','src/bridge.py',
                     'tests/test_agent.py','scripts/setup.ps1','README.md','compose.yaml'):
            with self.subTest(path=name): self.assertFalse(scope.private_extension_path(name))


if __name__=='__main__': unittest.main()
