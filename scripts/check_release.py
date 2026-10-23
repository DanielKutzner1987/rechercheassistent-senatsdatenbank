"""Check tracked release files without opening ignored credentials or PDFs."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True)
    files = [name for name in result.stdout.decode('utf-8').split('\0') if name]
    if not files:
        raise SystemExit('No tracked files. Stage the curated release before running this check.')
    findings = []
    for name in files:
        path = ROOT / name
        forbidden = (name.startswith(('state/', 'docker-state/', 'work/', '.venv/'))
            or name.startswith('secrets/') and name != 'secrets/README.md'
            or name.startswith('data/') and name != 'data/README.md'
            or name.startswith('.env') and name != '.env.example'
            or path.suffix.lower() in ('.pdf', '.sqlite', '.db', '.log', '.zip', '.png', '.pem', '.key', '.pfx', '.p12')
            or re.search(r'\.(sqlite|db)(-wal|-shm|\.bak)$', name))
        if forbidden:
            findings.append((name, 'runtime/private/binary file must not be tracked'))
            continue
        text = path.read_text(encoding='utf-8')
        for pattern, reason in [
            (r'AIza[A-Za-z0-9_-]{30,}', 'possible Google API key'),
            (r'AQ\.[A-Za-z0-9._~+/=\-]{35,}', 'possible Google API key'),
            (r'\bsk-[A-Za-z0-9_-]{24,}', 'possible API credential'),
            (r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})', 'possible GitHub token'),
            (r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b', 'possible AWS access key'),
            (r'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----', 'private key'),
            (r'(?i)https?://[^\s/:]+:[^\s/@]+@', 'possible URL credentials'),
            (r'(?i)[A-Z]:[/\\]Users[/\\](?!Public\b)[^/\\\s]+', 'personal absolute Windows path'),
        ]:
            if re.search(pattern, text):
                findings.append((name, reason))
    if findings:
        # Never print a matching secret value.
        for name, reason in findings:
            print(f'{name}: {reason}')
        raise SystemExit('Release check failed.')
    print(f'Release check passed: {len(files)} tracked text files; no matching credentials or personal paths.')
    print('Pattern checks are not a guarantee. Review staged files before publishing.')


if __name__ == '__main__':
    main()
