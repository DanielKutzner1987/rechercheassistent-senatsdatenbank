from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from agent import Agent
from document_metadata import refresh_metadata
from migrate_state import migrate


class StateMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'old.sqlite';self.target=self.root/'volume/state.sqlite'
        self.agent=Agent(self.root,self.source)
        with self.agent.db() as db:
            db.execute("INSERT INTO documents VALUES('a','a.pdf','hash','ready',1,NULL,0,1)")
            db.execute("INSERT INTO pages VALUES('a',1,'Example','ok')")
        refresh_metadata(self.agent)

    def tearDown(self):self.tmp.cleanup()

    def test_backup_keeps_source_and_verifies_metadata_and_documents(self):
        result=migrate(self.source,self.target)
        self.assertEqual(result,{'copied':True,'active_documents':1})
        self.assertTrue(self.source.exists())
        with closing(sqlite3.connect(self.target)) as db:
            self.assertEqual(db.execute('SELECT id,sha256 FROM documents').fetchall(),[('a','hash')])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inquiry_metadata').fetchone()[0],1)

    def test_repeat_never_overwrites_current_volume(self):
        migrate(self.source,self.target)
        with closing(sqlite3.connect(self.target)) as db:
            db.execute("UPDATE documents SET path='new.pdf'");db.commit()
        self.assertFalse(migrate(self.source,self.target)['copied'])
        with closing(sqlite3.connect(self.target)) as db:self.assertEqual(db.execute('SELECT path FROM documents').fetchone()[0],'new.pdf')

    def test_bad_source_or_existing_temporary_file_does_not_create_destination(self):
        self.target.parent.mkdir()
        self.target.with_suffix('.migrating.sqlite').write_bytes(b'preserve')
        with self.assertRaises(ValueError):migrate(self.source,self.target)
        self.assertFalse(self.target.exists())
        with self.assertRaises(ValueError):migrate(self.root/'absent.sqlite',self.root/'other.sqlite')

    def test_wal_commits_are_included_without_touching_the_original_files(self):
        with closing(sqlite3.connect(self.source)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute("INSERT INTO documents VALUES('b','b.pdf','second-hash','ready',1,NULL,0,1)")
            writer.commit()
            wal=Path(str(self.source)+'-wal')
            self.assertGreater(wal.stat().st_size,0)
            original={p:p.read_bytes() for p in (self.source,wal)}
            self.assertEqual(migrate(self.source,self.target)['active_documents'],2)
            for path,content in original.items():self.assertEqual(path.read_bytes(),content)
            with closing(sqlite3.connect(self.target)) as copied:
                self.assertEqual(copied.execute('SELECT sha256 FROM documents WHERE id=?',('b',)).fetchone()[0],'second-hash')

    def test_sqlite_never_opens_the_read_only_source(self):
        real_connect=sqlite3.connect
        def guarded_connect(path,*args,**kwargs):
            self.assertNotEqual(str(path),str(self.source))
            self.assertFalse(str(path).startswith(self.source.as_uri()))
            return real_connect(path,*args,**kwargs)
        with patch('migrate_state.sqlite3.connect',side_effect=guarded_connect):
            self.assertTrue(migrate(self.source,self.target)['copied'])


if __name__=='__main__':unittest.main()
