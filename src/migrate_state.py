"""Copy a stopped legacy SQLite database into Docker-owned storage using SQLite backup."""
import argparse
from contextlib import closing,contextmanager
from pathlib import Path
import sqlite3
import shutil
import tempfile


@contextmanager
def staged_database(source):
    """The application must be stopped. Never open the read-only source with SQLite.

    WAL recovery needs a writable shared-memory file. Copy DB + WAL, never SHM,
    into a private writable directory and let SQLite recover that copy instead.
    """
    files=[source]
    wal=Path(str(source)+'-wal')
    if wal.exists():files.append(wal)
    before={path:(path.stat().st_size,path.stat().st_mtime_ns) for path in files}
    with tempfile.TemporaryDirectory(prefix='agent-senatsdatenbank-import-') as directory:
        staged=Path(directory)/source.name
        for path in files:shutil.copyfile(path,Path(directory)/path.name)
        after={path:(path.stat().st_size,path.stat().st_mtime_ns) for path in files}
        if before!=after or wal.exists()!=(wal in files):
            raise ValueError('Source changed during copy; stop its application before migration')
        yield staged


def migrate(source,target):
    source,target=Path(source).resolve(),Path(target).resolve()
    if source==target:raise ValueError('Source and destination must differ')
    if target.exists():
        with closing(sqlite3.connect(target.as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Existing destination failed integrity check; preserved')
            count=db.execute('SELECT COUNT(*) FROM documents WHERE active=1').fetchone()[0]
        return {'copied':False,'active_documents':count}
    if not source.is_file():raise ValueError('Legacy database is missing; no destination created')
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.migrating.sqlite')
    if temporary.exists():raise ValueError('Previous migration file preserved; inspect before retrying')
    with staged_database(source) as staged,closing(sqlite3.connect(staged,timeout=30)) as src:
        if src.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Legacy integrity check failed; source preserved')
        with closing(sqlite3.connect(temporary)) as dst:
            src.backup(dst)
            dst.execute('PRAGMA journal_mode=DELETE')
            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Copied database failed integrity check; source preserved')
            for table in ('documents','pages','entries','corpus_topic_cache','gemini_runs','inquiry_metadata','inquiry_requesters'):
                if src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                    if src.execute('SELECT COUNT(*) FROM '+table).fetchone()!=dst.execute('SELECT COUNT(*) FROM '+table).fetchone():
                        raise ValueError('Migration count mismatch; source preserved')
            original=src.execute('SELECT id,path,sha256,active FROM documents ORDER BY id').fetchall()
            if original!=dst.execute('SELECT id,path,sha256,active FROM documents ORDER BY id').fetchall():
                raise ValueError('Document identities changed; source preserved')
            count=dst.execute('SELECT COUNT(*) FROM documents WHERE active=1').fetchone()[0]
    temporary.replace(target)
    return {'copied':True,'active_documents':count}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',default='/legacy/knowledge.sqlite')
    parser.add_argument('--target',default='/state/knowledge.sqlite')
    args=parser.parse_args()
    result=migrate(args.source,args.target)
    print(('Datenbank in Docker-Volume uebernommen' if result['copied'] else 'Vorhandenes Docker-Volume geprueft')+f': {result["active_documents"]} aktive PDFs. Bisherige Datenbank bleibt erhalten.')
