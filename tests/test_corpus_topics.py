import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent import Agent
from corpus_topics import topic_request,inventory,validate_topics,TopicValidationError,run_topics,compact_catalog,topic_packet,scope_refinement,decode_assignments
from gemini_bridge import GeminiBridge


class CorpusTopicTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.agent=Agent(self.root,self.root/'test.sqlite');self.bridge=GeminiBridge(self.agent,'EXAMPLE_PRIVATE_KEY')
        self.history=[{'role':'user','content':'Moin, welche Partei hat die meisten kleinen Anfragen gestellt?'},
            {'role':'assistant','content':'Auswertung des lokalen Bestands.'}]
        for doc,kind,topic in (('bus','Schriftliche Kleine Anfrage','Busverkehr'),('train','Schriftliche Kleine Anfrage','Bahnverkehr'),('pv','Große Anfrage','Photovoltaik')):
            raw=b'%PDF-'+doc.encode();(self.root/(doc+'.pdf')).write_bytes(raw)
            number={'bus':1,'train':2,'pv':3}[doc]
            with self.agent.db() as db:
                db.execute("INSERT INTO documents VALUES(?,?,?,'ready',1,NULL,0,1)",(doc,doc+'.pdf',hashlib.sha256(raw).hexdigest()))
                db.execute("INSERT INTO pages VALUES(?,1,?,'ok')",(doc,f'Drucksache 23/{number} 04.04.25 {kind} der Abgeordneten A (CDU) vom 01.04.25 und Antwort des Senats Betr.: {topic} Einleitung: Test'))

    def tearDown(self): self.temp.cleanup()

    def result(self,catalog): return {'themes':[{'name':'Bus und Bahn','documents':[d['id'] for d in catalog]}]}

    def test_exact_followup_preserves_small_inquiry_scope_and_all_documents(self):
        plan=topic_request('Welche Themen sind dabei die relevantesten',self.history)
        report,catalog=inventory(self.agent,plan)
        self.assertEqual(plan['scope_query'],'Kleine Anfragen')
        self.assertEqual({r['doc'] for r in catalog},{'bus','train'})
        self.assertEqual(report['total_inquiries'],2)

    def test_new_subject_breaks_reference_and_missing_context_asks(self):
        history=self.history+[{'role':'user','content':'Welche Tochtergesellschaften hat die HOCHBAHN?'}]
        self.assertIn('clarification',topic_request('Welche Themen sind dabei relevant?',history))
        self.assertIsNone(topic_request('Wie viele Busse betreibt die VHH?',self.history))
        self.assertIn('clarification',topic_request('Welche Themen sind dabei relevant?',[]))

    def test_explicit_scope_and_party_are_applied(self):
        plan=topic_request('Welche Themen decken die Großen Anfragen der CDU in der 23. Wahlperiode im Jahr 2025 ab?',[])
        _,catalog=inventory(self.agent,plan)
        self.assertEqual([r['doc'] for r in catalog],['pv'])
        self.assertEqual(plan['party_filter'],['CDU'])

    def test_assignment_requires_complete_unique_known_inventory(self):
        _,catalog=inventory(self.agent,topic_request('Welche Themen sind dabei relevant?',self.history))
        for ids in (['D1'],['D1','D1'],['D1','D99']):
            with self.assertRaises(TopicValidationError): validate_topics({'themes':[{'name':'Verkehr','documents':ids}]},catalog)
        result={'themes':[{'name':'Verkehr','documents':['D1','D2'],'count':999}]}
        self.assertEqual(validate_topics(result,catalog)[0]['count'],2)

    def test_cache_and_document_hash_refresh(self):
        with patch.object(self.bridge,'generate_topics',side_effect=lambda packet:(self.result(packet),{'totalTokenCount':10},'test')) as generate:
            first=run_topics(self.bridge,'Welche Themen sind dabei relevant?',self.history)
            second=run_topics(self.bridge,'Welche Themen sind dabei die relevantesten?',self.history)
            self.assertEqual(generate.call_count,1)
            self.assertTrue(first[1]['provider_called']);self.assertFalse(second[1]['provider_called'])
            raw=b'%PDF-changed';(self.root/'bus.pdf').write_bytes(raw)
            with self.agent.db() as db: db.execute("UPDATE documents SET sha256=? WHERE id='bus'",(hashlib.sha256(raw).hexdigest(),))
            run_topics(self.bridge,'Welche Themen sind dabei relevant?',self.history)
            self.assertEqual(generate.call_count,2)

    def test_bridge_uses_entire_inventory_instead_of_top_k_and_counts_locally(self):
        with patch.object(self.bridge,'generate_topics',side_effect=lambda packet:(self.result(packet),{},'test')),patch('gemini_bridge.DocumentSearch') as search,patch.object(self.bridge,'generate') as normal:
            text=self.bridge.answer('Welche Themen sind dabei die relevantesten',self.history)
        search.assert_not_called();normal.assert_not_called()
        self.assertIn('2 von 2',text);self.assertIn('Häufigkeit im Bestand',text)
        self.assertIn('/documents/bus#page=1',text);self.assertNotIn('/documents/pv',text)
        with self.agent.db() as db: report=json.loads(db.execute('SELECT report FROM gemini_runs').fetchone()[0])
        self.assertEqual(report['execution'],'corpus_topic_analysis')
        self.assertNotIn('EXAMPLE_PRIVATE_KEY',str(report))

    def test_invalid_mapping_is_not_published(self):
        with patch.object(self.bridge,'generate_topics',return_value=({'themes':[{'name':'Erfundene Rangfolge','documents':['D999']}]},{},'test')):
            text=self.bridge.answer('Welche Themen sind dabei relevant?',self.history)
        self.assertNotIn('Erfundene Rangfolge',text)
        self.assertIn('unvollständig',text)

    def test_topic_payload_uses_structured_output_without_key_in_body(self):
        candidate={'themes':['Verkehr'],'assignment':{'D1':0}}
        fake={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(candidate)}]}}]}
        with patch('gemini_bridge.request_json',return_value=fake) as call:
            self.bridge.generate_topics([{'id':'D1','title':'Verkehr'}])
        req=call.call_args[0][0];body=json.loads(req.data)
        self.assertNotIn('EXAMPLE_PRIVATE_KEY',str(body))
        self.assertEqual(body['generationConfig']['responseFormat']['text']['mimeType'],'APPLICATION_JSON')
        required=body['generationConfig']['responseFormat']['text']['schema']['properties']['assignment']['required']
        self.assertEqual(required,['D1'])
        self.assertNotIn('sha256',body['contents'][0]['parts'][0]['text'])

    def test_required_assignments_cover_every_known_id_and_reject_invalid_indices(self):
        catalog=[{'id':'D1'},{'id':'D2'}]
        result=decode_assignments({'themes':['Busverkehr','Bahnverkehr'],'assignment':{'D1':0,'D2':1}},catalog)
        self.assertEqual([g['documents'] for g in result['themes']],[['D1'],['D2']])
        for assignment in ({'D1':0},{'D1':0,'D2':2},{'D1':0,'D2':True},{'D1':0,'D2':1,'D3':0}):
            with self.assertRaises(TopicValidationError):decode_assignments({'themes':['Busverkehr','Bahnverkehr'],'assignment':assignment},catalog)

    def test_provider_output_limit_is_diagnosed_without_publishing_partial_groups(self):
        fake={'candidates':[{'finishReason':'MAX_TOKENS'}],'usageMetadata':{'totalTokenCount':8192}}
        with patch('gemini_bridge.request_json',return_value=fake):
            with self.assertRaises(TopicValidationError) as failure:self.bridge.generate_topics([{'id':'D1','title':'Busverkehr'}])
        self.assertEqual(failure.exception.metadata['finish_reason'],'MAX_TOKENS')
        history=[{'role':'user','content':'Liste aller Anfragen nach Themen'}]
        with patch.object(self.bridge,'generate_topics',side_effect=failure.exception):
            self.bridge.answer('Gerne für das Jahr 2025',history)
        with self.agent.db() as db: report=json.loads(db.execute('SELECT report FROM gemini_runs ORDER BY created DESC LIMIT 1').fetchone()[0])
        self.assertEqual(report['topic_error']['reason'],'incomplete_model_result')
        self.assertEqual(report['topic_error']['finish_reason'],'MAX_TOKENS')

    def test_year_only_followup_uses_complete_inventory_and_lists_every_title(self):
        history=[{'role':'user','content':'Kannst du mir eine Liste aller Anfragen geben und nach Themenschwerpunkt sortieren?'},
                 {'role':'assistant','content':'Bitte auf ein Jahr eingrenzen.'}]
        with self.agent.db() as db:
            db.execute("UPDATE pages SET text=replace(text,'04.04.25','04.04.26') WHERE doc IN ('bus','train')")
        plan=topic_request('Gerne für das Jahr 2026',history)
        self.assertEqual(plan['scope_query'],'Anfragen im Jahr 2026')
        self.assertTrue(plan['full_list'])
        with patch.object(self.bridge,'generate_topics',side_effect=lambda packet:(self.result(packet),{},'test')) as generate,patch('gemini_bridge.DocumentSearch') as search,patch.object(self.bridge,'generate') as answer:
            text=self.bridge.answer('Gerne für das Jahr 2026',history)
        search.assert_not_called();answer.assert_not_called()
        self.assertEqual(len(generate.call_args[0][0]),2)
        self.assertIn('Busverkehr',text);self.assertIn('Bahnverkehr',text)
        self.assertIn('veröffentlicht 2026',text)
        self.assertNotIn('nicht heruntergeladenen',text)
        self.assertNotIn('/documents/pv',text)

    def test_refinements_preserve_kind_and_party_and_replace_previous_year(self):
        history=[{'role':'user','content':'Liste aller Großen Anfragen der CDU nach Themen aus der 23. Wahlperiode im Jahr 2025'}]
        plan=topic_request('Gerne für das Jahr 2026',history)
        self.assertEqual(plan['scope_query'],'Große Anfragen der 23. Wahlperiode im Jahr 2026')
        self.assertEqual(plan['party_filter'],['CDU'])
        self.assertFalse(plan['unsupported_time'])
        history.extend([{'role':'assistant','content':'Auswertung'}, {'role':'user','content':'Gerne für das Jahr 2026'}])
        plan=topic_request('Bitte nur Die Linke',history)
        self.assertEqual(plan['party_filter'],['Die Linke'])
        self.assertIn('2026',plan['scope_query'])
        self.assertEqual(topic_request('Und alle Fraktionen',history)['party_filter'],[])

    def test_year_in_factual_question_does_not_become_inventory_refinement(self):
        history=[{'role':'user','content':'Liste aller Anfragen nach Themenschwerpunkt'}]
        self.assertIsNone(topic_request('Wie viele Busse betreibt die VHH im Jahr 2026?',history))
        self.assertIsNone(topic_request('Gerne für das Jahr 2026',[{'role':'user','content':'Wie viele Busse betreibt die VHH?'}]))
        self.assertIsNone(topic_request('Gerne für das Jahr 2026',self.history))
        self.assertTrue(topic_request('Seit 2025 bis 2026',history)['unsupported_time'])

    def test_large_catalog_reduces_excerpts_without_losing_documents_or_titles(self):
        rows=[{'id':f'D{i}','number':f'23/{i}','sha256':'a'*64,'title':f'Originaltitel zum Busverkehr {i}',
               'excerpt':'Langer Originalauszug. '*100,'parties':['CDU'],'kind':'Schriftliche Kleine Anfrage'} for i in range(1,126)]
        compact,context=compact_catalog(rows)
        self.assertEqual(len(compact),125)
        self.assertEqual([r['id'] for r in compact],[r['id'] for r in rows])
        self.assertEqual([r['title'] for r in compact],[r['title'] for r in rows])
        self.assertLess(context['excerpt_limit'],1800)
        self.assertLessEqual(len(json.dumps(topic_packet(compact),ensure_ascii=False)),120000)
        self.assertEqual(validate_topics({'themes':[{'name':'Busverkehr','documents':[r['id'] for r in compact]}]},compact)[0]['count'],125)

    def test_catalog_that_cannot_fit_titles_is_explicitly_rejected(self):
        row={'id':'D1','number':'23/1','sha256':'a'*64,'title':'x'*1000,'excerpt':'context','parties':[],'kind':'Anfrage'}
        compact,report=compact_catalog([row],budget=500)
        self.assertEqual(compact,[])
        self.assertIn('unsupported',report)


if __name__=='__main__': unittest.main()
