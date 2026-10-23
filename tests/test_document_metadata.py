import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent import Agent
from document_metadata import parse_requesters,refresh_metadata,parse_document_header
from corpus_stats import parse_header
from corpus_topics import topic_request,run_topics
from gemini_bridge import GeminiBridge
from topic_charts import person_matrix,svg_chart,csv_data
import xml.etree.ElementTree as ET


class DocumentMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.agent=Agent(self.root,self.root/'db.sqlite')

    def tearDown(self):self.tmp.cleanup()

    def add(self):
        content=b'public-pdf';(self.root/'test.pdf').write_bytes(content)
        text='Drucksache 23/9 04.04.25 Schriftliche Kleine Anfrage der Abgeordneten Dr. Anna Beispiel und Bernd Muster (CDU) und Carla Test (SPD) vom 01.04.25 und Antwort des Senats Betr.: Verkehr'
        with self.agent.db() as db:
            db.execute("INSERT INTO documents VALUES('doc','test.pdf',?,'ready',1,NULL,0,1)",(hashlib.sha256(content).hexdigest(),))
            db.execute("INSERT INTO pages VALUES('doc',1,?,'ok')",(text,))

    def test_joint_people_and_their_own_parties(self):
        people,status=parse_requesters(' der Abgeordneten Dr. Anna Beispiel und Bernd Muster (CDU) und Carla Test (SPD) vom 01.04.25 ')
        self.assertEqual(status,'parsed')
        self.assertEqual([(p['name'],p['party']) for p in people],[('Anna Beispiel','CDU'),('Bernd Muster','CDU'),('Carla Test','SPD')])
        self.assertEqual(people[0]['raw_name'],'Dr. Anna Beispiel')

    def test_collective_and_ambiguous_names_are_not_invented(self):
        self.assertEqual(parse_requesters(' der Fraktionen SPD und GRÜNE vom 01.04.25 ')[1],'collective_only')
        self.assertEqual(parse_requesters(' Einleitung: Anna Beispiel (CDU) vom 01.04.25 ')[0],[])
        self.assertEqual(parse_requesters(' der Abgeordneten Anna Beispiel ohne weitere Namen vom 01.04.25 ')[0],[])
        people,status=parse_requesters(' der Abgeordneten Anna Beispiel (CDU) und Fraktion vom 01.04.25 ')
        self.assertEqual(status,'parsed_with_collective');self.assertEqual(len(people),1)

    def test_motion_is_not_an_inquiry_because_one_is_mentioned_in_body(self):
        text='Drucksache 23/7 04.04.25 Antrag der Abgeordneten Anna Beispiel (CDU) und Fraktion Betr.: Verkehr Mit der Antwort auf die Schriftliche Kleine Anfrage der Fraktion hat der Senat erklärt ...'
        self.assertIsNone(parse_header(text))
        metadata=parse_document_header(text)
        self.assertEqual(metadata['kind'],'Antrag')
        people,status=parse_requesters(metadata['evidence'])
        self.assertEqual(people[0]['name'],'Anna Beispiel')
        self.assertEqual(status,'parsed_with_collective')

    def test_backfill_is_persistent_idempotent_and_refreshes_changed_extraction(self):
        self.add()
        self.assertEqual(refresh_metadata(self.agent),1);self.assertEqual(refresh_metadata(self.agent),0)
        reopened=Agent(self.root,self.root/'db.sqlite')
        with reopened.db() as db:
            metadata=db.execute('SELECT number,term,kind,issued,source_page,status,metadata FROM inquiry_metadata').fetchone()
            self.assertEqual(tuple(metadata)[:6],('23/9',23,'Schriftliche Kleine Anfrage','2025-04-04',1,'parsed'))
            self.assertEqual(len(json.loads(metadata['metadata'])['people']),3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inquiry_requesters').fetchone()[0],3)
            db.execute("UPDATE pages SET text='unreadable',status='needs_ocr_or_review'")
        self.assertEqual(refresh_metadata(reopened),1)
        with reopened.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM inquiry_requesters').fetchone()[0],0)

    def test_import_and_bridge_start_backfill_metadata(self):
        self.add();GeminiBridge(self.agent,'EXAMPLE_PRIVATE_KEY')
        with self.agent.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM inquiry_requesters').fetchone()[0],3)

    def test_person_request_and_followups_keep_person_dimension(self):
        question='Kannst du mir eine grafische Übersicht aller anfragenstellenden Personen und den Themenschwerpunkten anfertigen?'
        plan=topic_request(question,[])
        self.assertTrue(plan['person_comparison']);self.assertFalse(plan['party_comparison'])
        history=[{'role':'user','content':question}]
        for followup in ('Gerne für das Jahr 2026','Bitte als Balkendiagramm','Bitte nur als Tabelle'):
            plan=topic_request(followup,history)
            self.assertTrue(plan['person_comparison']);history.append({'role':'user','content':followup})

    def test_real_saved_metadata_drives_person_chart_without_new_classification(self):
        self.add();bridge=GeminiBridge(self.agent,'EXAMPLE_PRIVATE_KEY')
        result={'themes':[{'name':'Busverkehr','documents':['D1']}]}
        with patch.object(bridge,'generate_topics',return_value=(result,{},'test')) as generate:
            run_topics(bridge,'Welche Themenschwerpunkte haben alle Anfragen?',[])
            answer,audit=run_topics(bridge,'Grafische Übersicht aller anfragenstellenden Personen und ihrer Themenschwerpunkte',[])
        self.assertEqual(generate.call_count,1);self.assertTrue(audit['cache_hit'])
        self.assertEqual(audit['view'],'person_topic_chart')
        matrix=audit['matrix'];self.assertEqual(len(matrix['rows']),3)
        self.assertEqual(matrix['joint_documents'],1)
        self.assertIn('Anna Beispiel',answer);self.assertNotIn('Gesamtbestand',answer)
        self.assertIn('Person',csv_data(matrix));ET.fromstring(svg_chart(matrix,'Test'))

    def test_unknown_authors_stay_in_the_complete_person_matrix(self):
        catalog=[{'id':'D1','doc':'a','number':'23/1','title':'A','people':[{'name':'Anna Beispiel','party':'CDU'}]},
                 {'id':'D2','doc':'b','number':'23/2','title':'B','people':[]}]
        matrix=person_matrix([{'name':'Verkehr','documents':['D1','D2']}],catalog)
        self.assertEqual(matrix['unknown_documents'],1)
        self.assertEqual({r['party'] for r in matrix['rows']},{'Anna Beispiel','Zuordnung offen'})


if __name__=='__main__':unittest.main()
