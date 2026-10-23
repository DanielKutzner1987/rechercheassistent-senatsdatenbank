import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from agent import Agent
from bridge import make_handler
from gemini_bridge import GeminiBridge,load_documents,rank_documents,render_sources,retrieval_query


class GeminiBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.agent=Agent(self.root,self.root/'test.sqlite')
        raw=b'%PDF-1.4 test source'
        (self.root/'rahlstedt.pdf').write_bytes(raw)
        sha=hashlib.sha256(raw).hexdigest()
        with self.agent.db() as db:
            db.execute("INSERT INTO documents VALUES('doc','rahlstedt.pdf',?,'ready',2,NULL,0,1)",(sha,))
            db.execute("INSERT INTO pages VALUES('doc',1,'FIRST ORIGINAL PAGE','ok')")
            db.execute("INSERT INTO pages VALUES('doc',2,'SECOND ORIGINAL PAGE','ok')")
        self.bridge=GeminiBridge(self.agent,'EXAMPLE_PRIVATE_KEY')
        self.hits=[{'doc':'doc','path':'rahlstedt.pdf','page':1,'score':0.2}]

    def tearDown(self): self.temp.cleanup()

    def test_complete_document_is_loaded_and_oversized_one_is_not_clipped(self):
        docs,excluded=load_documents(self.agent,self.hits)
        self.assertEqual(len(docs),1)
        self.assertIn('SECOND ORIGINAL PAGE',docs[0]['text'])
        self.assertEqual(docs[0]['pages'],2)
        docs,excluded=load_documents(self.agent,self.hits,budget=10)
        self.assertEqual(docs,[])
        self.assertEqual(excluded[0]['reason'],'context_budget_exceeded')

    def test_modified_pdf_is_not_sent_using_stale_index_text(self):
        (self.root/'rahlstedt.pdf').write_bytes(b'changed')
        with patch('gemini_bridge.DocumentSearch.search',return_value=self.hits),patch.object(self.bridge,'generate') as generate:
            self.bridge.answer('Rahlstedt')
        generate.assert_not_called()

    def test_ocr_gap_prevents_claim_of_complete_document(self):
        with self.agent.db() as db:
            db.execute("UPDATE pages SET status='needs_ocr_or_review' WHERE page=2")
        docs,excluded=load_documents(self.agent,self.hits)
        self.assertFalse(docs)
        self.assertEqual(excluded[0]['reason'],'incomplete_extraction')

    def test_duplicate_page_hits_do_not_multiply_document_rank(self):
        hits=self.hits*20+[{'doc':'other','path':'other.pdf','page':1,'score':0.3}]
        result=rank_documents(hits,'Busverkehr')
        self.assertEqual(result[0]['doc'],'other')
        self.assertEqual(len({r['doc'] for r in result}),len(result))

    def test_only_followup_questions_use_previous_topic(self):
        history=[{'role':'user','content':'Photovoltaik HOCHBAHN'}]
        self.assertEqual(retrieval_query('Wie viele Mitarbeiter hat die VHH?',history),'Wie viele Mitarbeiter hat die VHH?')
        self.assertIn('Photovoltaik',retrieval_query('Und welche Leistung haben diese?',history))

    def test_invalid_page_or_document_does_not_publish_draft(self):
        docs,_=load_documents(self.agent,self.hits)
        for citation in ('[D1, S. 999]','[D9, S. 1]','[D1, S. 2–1]','[D1, S. 1–3]',
                         '[D1, S. 1, S. 999]','[D1, S. 2–S. 1]','[D1, S. 1, D9, S. 2]'):
            text,invalid=render_sources('WRONG DRAFT '+citation,docs,'http://localhost:8093')
            self.assertTrue(invalid)
            self.assertNotIn('WRONG DRAFT',text)
        text,invalid=render_sources('Gültige Aussage [D1, S. 2]',docs,'http://localhost:8093')
        self.assertIn('/documents/doc#page=2',text)
        self.assertFalse(invalid)

    def test_real_model_page_ranges_and_lists_link_every_valid_page(self):
        docs,_=load_documents(self.agent,self.hits)
        for citation in ('[D1, S. 1–2]','[D1, S. 1-2]','[D1, S. 1, 2]','[D1, S. 1; D1, S. 2]',
                         '[D1, S. 1, S. 2]','[D1, S. 1–S. 2]','[D1, S. 1, S. 1, S. 2]'):
            answer,invalid=render_sources('Aussage '+citation,docs,'http://localhost:8093')
            self.assertFalse(invalid,citation)
            self.assertIn('#page=1',answer)
            self.assertIn('#page=2',answer)

    def test_http_models_stream_answer_pdf_and_audit_without_key(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.bridge))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            connection.request('GET','/v1/models')
            models=json.loads(connection.getresponse().read())
            self.assertEqual(models['data'][0]['id'],'senatswissen-gemini')
            self.assertEqual(models['data'][0]['name'],'Rechercheassistent - Senatsdatenbank')
            with patch('gemini_bridge.DocumentSearch.search',return_value=self.hits),patch.object(self.bridge,'generate',return_value=('Die Aussage steht hier [D1, S. 2]',{'totalTokenCount':10},'test-model')):
                connection.request('POST','/v1/chat/completions',json.dumps({'stream':True,'messages':[{'role':'user','content':'Rahlstedt?'}]}),{'Content-Type':'application/json'})
                response=connection.getresponse()
                answer=response.read().decode()
                self.assertEqual(response.status,200)
                self.assertIn('#page=2',answer)
                self.assertTrue(answer.endswith('data: [DONE]\n\n'))
            connection.request('GET','/documents/doc')
            response=connection.getresponse()
            self.assertEqual(response.getheader('Content-Type'),'application/pdf')
            self.assertTrue(response.read().startswith(b'%PDF'))
            with self.agent.db() as db:
                record=db.execute('SELECT report FROM gemini_runs').fetchone()[0]
            self.assertNotIn('EXAMPLE_PRIVATE_KEY',record)
            self.assertNotIn('EXAMPLE_PRIVATE_KEY',answer)
        finally:
            connection.close(); server.shutdown(); server.server_close()


if __name__=='__main__': unittest.main()
