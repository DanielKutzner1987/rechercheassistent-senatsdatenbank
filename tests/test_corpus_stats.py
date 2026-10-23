import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent import Agent
from corpus_stats import count_parties,format_party_statistics,is_party_statistics,parse_header
from gemini_bridge import GeminiBridge


class CorpusStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.agent=Agent(self.root,self.root/'test.sqlite')

    def tearDown(self): self.temp.cleanup()

    def add(self,doc,number='23/1',authors='A und B (CDU)',kind='Schriftliche Kleine Anfrage',suffix=''):
        raw=b'%PDF-source-'+doc.encode();(self.root/(doc+'.pdf')).write_bytes(raw)
        text=f'Drucksache {number} 23. Wahlperiode 04.04.25 {kind} der Abgeordneten {authors} vom 01.04.25 und Antwort des Senats Betr.: Test. '+suffix
        with self.agent.db() as db:
            db.execute("INSERT INTO documents VALUES(?,?,?,'ready',1,NULL,0,1)",(doc,doc+'.pdf',hashlib.sha256(raw).hexdigest()))
            db.execute("INSERT INTO pages VALUES(?,1,?,'ok')",(doc,text))

    def test_typo_is_routed_but_topic_question_is_not(self):
        self.assertTrue(is_party_statistics('Kannst du mir sagen von welcher Partei die meisten Druckfragen sind?'))
        self.assertFalse(is_party_statistics('Welche Partei fordert die meisten Busse?'))

    def test_header_does_not_use_party_names_from_body_or_answer(self):
        self.add('a',authors='A (CDU)',suffix='Die Linke, SPD und AfD erwähnen den Senat.')
        report=count_parties(self.agent,'Von welcher Partei sind die meisten Anfragen?')
        self.assertEqual(report['counts'],{'CDU':1})

    def test_duplicate_files_and_multiple_authors_count_once(self):
        self.add('a');self.add('copy')
        report=count_parties(self.agent,'Welche Partei hat die meisten Anfragen?')
        self.assertEqual(report['counts'],{'CDU':1})
        self.assertEqual(report['total_inquiries'],1)
        self.assertEqual(report['duplicate_files'],1)

    def test_joint_inquiries_and_ties_are_explicit(self):
        self.add('a',authors='A (CDU) und B (SPD)')
        report=count_parties(self.agent,'Welche Partei hat die meisten Anfragen?')
        text=format_party_statistics(report,'http://localhost:8093')
        self.assertEqual(report['counts'],{'CDU':1,'SPD':1})
        self.assertIn('gleichauf',text);self.assertIn('einmal je beteiligter Fraktion',text)

    def test_unknown_and_conflicting_party_is_not_guessed(self):
        self.add('a',authors='A (unbekannt)');self.add('b',number='23/2');self.add('copy',number='23/2',authors='B (SPD)')
        report=count_parties(self.agent,'Welche Partei hat die meisten Anfragen?')
        self.assertEqual(report['counts'],{})
        self.assertEqual(len(report['unknown']),2)

    def test_filters_honor_term_year_and_kind(self):
        self.add('a');self.add('b',number='22/2',authors='B (Die Linke)');self.add('c',number='23/3',kind='Große Anfrage')
        self.assertEqual(count_parties(self.agent,'Welche Partei hat die meisten kleinen Anfragen der 23. Wahlperiode im Jahr 2025?')['total_inquiries'],1)
        self.assertEqual(count_parties(self.agent,'Welche Partei hat die meisten großen Anfragen?')['total_inquiries'],1)
        self.assertTrue(count_parties(self.agent,'Welche Partei hat seit 2020 die meisten Anfragen?').get('unsupported'))
        self.assertTrue(count_parties(self.agent,'Welche Partei hat die meisten Anfragen zu Verkehr?').get('unsupported'))

    def test_missing_changed_or_inactive_sources_cannot_count(self):
        self.add('a');self.add('b',number='23/2');self.add('c',number='23/3')
        (self.root/'a.pdf').unlink();(self.root/'b.pdf').write_bytes(b'changed')
        with self.agent.db() as db: db.execute("UPDATE documents SET active=0 WHERE id='c'")
        self.assertEqual(count_parties(self.agent,'Welche Partei hat die meisten Anfragen?')['total_inquiries'],0)

    def test_statistics_bypass_search_and_google_and_remain_auditable(self):
        self.add('a');bridge=GeminiBridge(self.agent,'EXAMPLE_PRIVATE_KEY')
        with patch.object(bridge,'generate') as generate,patch('gemini_bridge.DocumentSearch') as search:
            answer=bridge.answer('Von welcher Partei sind die meisten Druckfragen?')
        generate.assert_not_called();search.assert_not_called()
        self.assertIn('CDU',answer);self.assertIn('/documents/a#page=1',answer)
        self.assertIn('keine Gesamtstatistik',answer)
        with self.agent.db() as db: audit=json.loads(db.execute('SELECT report FROM gemini_runs').fetchone()[0])
        self.assertFalse(audit['provider_called']);self.assertNotIn('EXAMPLE_PRIVATE_KEY',str(audit))

    def test_faction_header_and_missing_number(self):
        result=parse_header('Drucksache 23/9 04.04.25 Große Anfrage der Fraktionen SPD und GRÜNE vom 01.04.25 Betr.: Test')
        self.assertEqual(result['parties'],['GRÜNE','SPD'])
        self.assertIsNone(parse_header('Schriftliche Kleine Anfrage ohne Drucksachennummer (CDU)'))


if __name__=='__main__': unittest.main()
