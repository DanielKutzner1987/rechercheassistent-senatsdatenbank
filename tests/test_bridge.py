import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent import Agent
from bridge import Bridge, numeric_values


class BridgeTests(unittest.TestCase):
    def test_number_and_date_formatting_does_not_discard_valid_counts(self):
        source=numeric_values('Stand 25.03.2025: 1.878 Busse')
        self.assertTrue(numeric_values('1878 Busse am 25. März 2025').issubset(source))
        self.assertFalse(numeric_values('2000 Busse').issubset(source))


    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.a=Agent(self.root,self.root/'test.sqlite')
        with self.a.db() as c:
            c.execute("INSERT INTO documents VALUES('doc','test.pdf','sha','ready',1,NULL,0,1)")
            c.execute("INSERT INTO blocks(id,doc,page,part,text) VALUES('b','doc',1,0,'Hochbahn Photovoltaik')")
            c.execute("INSERT INTO originals VALUES('b','test.pdf','Hochbahn Photovoltaik')")
        self.b=Bridge(self.a)


    def tearDown(self):
        self.temp.cleanup()


    def test_checkpoint_only_after_successful_upsert(self):
        def fake(url,body=None,method=None,timeout=180):
            if 'points?' in url:
                raise RuntimeError('write failed')
            return {'result':{'config':{'params':{'vectors':{'size':3,'distance':'Cosine'}}}}}
        with patch('bridge.api',side_effect=fake),patch.object(self.b,'embed',return_value=[[1,0,0]]):
            with self.assertRaises(RuntimeError):
                self.b.sync()
        with self.a.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM vector_sync').fetchone()[0],0)


    def test_empty_embedding_is_rejected(self):
        with patch('bridge.api',return_value={'embeddings':[[0,0,0]]}):
            with self.assertRaises(ValueError):
                self.b.embed(['text'])


    def test_point_ids_stable(self):
        self.assertEqual(self.b.point_id('block:b'),self.b.point_id('block:b'))
        self.assertNotEqual(self.b.point_id('block:b'),self.b.point_id('entry:b'))
