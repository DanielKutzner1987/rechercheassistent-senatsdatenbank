import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent import Agent


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.agent = Agent(self.root, self.root / 'knowledge.sqlite')

    def tearDown(self):
        self.temp.cleanup()

    def seed(self):
        text = 'Die Hochbahn betreibt eine Photovoltaikanlage mit einer Leistung von 50 kWp.'
        with self.agent.db() as c:
            c.execute("INSERT INTO documents VALUES('doc','test.pdf','hash','ready',1,NULL,0,1)")
            c.execute("INSERT INTO blocks(id,doc,page,part,text) VALUES('block','doc',3,0,?)", (text,))
            c.execute("INSERT INTO originals VALUES('block','test.pdf',?)", (text,))
        return text

    def entry(self):
        return dict(question='Welche PV-Leistung hat die Hochbahn?',
                    answer='Die Leistung beträgt 50 kWp.',
                    quote='Die Hochbahn betreibt eine Photovoltaikanlage mit einer Leistung von 50 kWp.',
                    keywords=['Photovoltaik', 'Hochbahn'], subject='Hochbahn', time_reference='')

    def test_persistence_and_search_with_page(self):
        self.seed()
        with patch.object(self.agent, 'analyze', return_value=[self.entry()]):
            self.assertTrue(self.agent.process_one())
            self.assertFalse(self.agent.process_one())
        result = self.agent.search('Photovoltaik Hochbahn')
        self.assertEqual(result[0]['review_status'], 'unreviewed')
        self.assertEqual(result[0]['page'], 3)
        self.assertTrue(result[0]['url'].endswith('#page=3'))

    def test_rejects_invented_quote_and_numbers(self):
        text = self.seed()
        e = self.entry()
        e['answer'] = 'Die Leistung beträgt 500 kWp.'
        self.assertFalse(self.agent.validate(e, text))
        e = self.entry()
        e['quote'] = 'Die Hochbahn betreibt fünf Anlagen mit einer Leistung von 500 kWp.'
        self.assertFalse(self.agent.validate(e, text))

    def test_removed_document_not_searchable(self):
        self.seed()
        with self.agent.db() as c:
            c.execute('UPDATE documents SET active=0')
        self.assertEqual(self.agent.search('Hochbahn'), [])

    def test_failed_model_call_is_visible_and_not_repeated_forever(self):
        self.seed()
        with patch.object(self.agent, 'analyze', side_effect=ValueError('invalid JSON')):
            self.agent.process_one()
            self.assertFalse(self.agent.process_one())
        self.assertEqual(self.agent.status()['blocks'], {'error': 1})

    def test_scan_is_idempotent_and_deleted_files_are_deactivated(self):
        from pypdf import PdfWriter
        pdf = self.root / 'sample.pdf'
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with pdf.open('wb') as f:
            writer.write(f)
        self.agent.scan()
        self.agent.scan()
        status = self.agent.status()
        self.assertEqual(len(status['documents']), 1)
        self.assertEqual(status['documents'][0]['status'], 'needs_review')
        pdf.unlink()
        self.agent.scan()
        self.assertEqual(self.agent.status()['documents'][0]['active'], 0)

    def test_number_substrings_are_not_evidence(self):
        e=self.entry()
        e['answer']='Die Leistung beträgt 5 kWp.'
        self.assertFalse(self.agent.validate(e,e['quote']))

    def test_source_spans_are_contiguous_and_export_is_readable(self):
        import json
        text=self.seed()
        for span in self.agent.evidence_spans(text).values():
            self.assertIn(span,text)
        with patch.object(self.agent,'analyze',return_value=[self.entry()]):
            self.agent.process_one()
        self.agent.export()
        data=json.loads((self.root/'knowledge.jsonl').read_text(encoding='utf-8').splitlines()[0])
        self.assertEqual(data['page'],3)
        self.assertEqual(data['review_status'],'unreviewed')


if __name__ == '__main__':
    unittest.main()
