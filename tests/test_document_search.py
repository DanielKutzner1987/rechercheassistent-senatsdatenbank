from collections import OrderedDict
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent import Agent
from bridge import Bridge
from document_search import DocumentSearch,facets,fuse,query_variants,terms
from gemini_bridge import rank_documents


class DocumentSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.agent=Agent(self.temp.name,Path(self.temp.name)/'test.sqlite')
        self.bridge=Bridge(self.agent)
        with self.agent.db() as db:
            for doc,title,text in (
                ('solar','23_00546_photovoltaikausbau.pdf','Photovoltaik auf öffentlichen Gebäuden. SOLAR AG, Stand 2025.'),
                ('music','23_00195_bettelverbot.pdf','Das Bettelverbot betrifft Betteln und Musizieren. HVV, 2004.'),
                ('other','23_01000_baustellen.pdf','Baustellen und Sperrungen. HVV und HOCHBAHN.')):
                db.execute("INSERT INTO documents VALUES(?,?,?,'ready',1,NULL,0,1)",(doc,title,'sha-'+doc))
                db.execute("INSERT INTO pages VALUES(?,1,?,'ok')",(doc,text))
                db.execute('INSERT INTO blocks(id,doc,page,part,text) VALUES(?,?,1,0,?)',(doc,doc,text))
                db.execute('INSERT INTO originals VALUES(?,?,?)',(doc,title,text))

    def tearDown(self): self.temp.cleanup()

    def test_cards_refresh_changed_and_remove_inactive_sources(self):
        search=DocumentSearch(self.bridge); search.refresh_cards(); search.refresh_cards()
        self.assertEqual(search.events[-1]['updated'],0)
        with self.agent.db() as db:
            db.execute("UPDATE documents SET active=0 WHERE id='music'")
            db.execute("UPDATE documents SET sha256='changed' WHERE id='solar'")
        search.refresh_cards()
        self.assertEqual(search.events[-1]['updated'],1)
        with self.agent.db() as db:
            self.assertFalse(db.execute("SELECT 1 FROM document_cards_fts WHERE doc='music'").fetchone())
            card=db.execute("SELECT summary,source_page FROM document_cards WHERE doc='solar'").fetchone()
        self.assertIn('Photovoltaik',card[0]); self.assertEqual(card[1],1)

    def test_many_chunks_and_variants_do_not_multiply_route_votes(self):
        a={'doc':'a','path':'a.pdf','page':1,'value':1}
        b={'doc':'b','path':'b.pdf','page':1,'value':1}
        first=fuse({'text':[a,b],'metadata':[b]})
        repeated=fuse({'text':[a]*100+[b],'metadata':[b]*20})
        self.assertEqual(first,repeated)
        self.assertEqual(first[0]['doc'],'b')

    def test_semantic_failure_keeps_lexical_and_metadata_and_no_exception_message(self):
        search=DocumentSearch(self.bridge)
        with patch.object(search,'semantic',side_effect=RuntimeError('SECRET SHOULD NOT BE LOGGED')):
            hits=search.search('Photovoltaik Gebäude')
        self.assertEqual(hits[0]['doc'],'solar')
        self.assertNotIn('SECRET',str(search.events))

    def test_expanded_query_finds_solar_synonym(self):
        search=DocumentSearch(self.bridge)
        with patch.object(search,'semantic',return_value=[]):
            hits=search.search('Solaranlagen')
        self.assertEqual(hits[0]['doc'],'solar')
        self.assertTrue(any(e.get('second_round') for e in search.events))

    def test_unrelated_semantic_hits_are_not_sources(self):
        search=DocumentSearch(self.bridge)
        with patch.object(search,'semantic',return_value=[{'doc':'other','path':'other.pdf','page':1,'value':0.4}]):
            self.assertEqual(search.search('Pinguine Antarktis'),[])

    def test_strong_semantic_paraphrase_survives_missing_keywords(self):
        search=DocumentSearch(self.bridge)
        with patch.object(search,'semantic',return_value=[{'doc':'music','path':'music.pdf','page':1,'value':0.85}]):
            hits=search.search('Almosen erbitten')
        self.assertEqual(hits[0]['doc'],'music')
        self.assertTrue(hits[0]['semantic_fallback'])

    def test_single_shared_general_word_does_not_establish_topic(self):
        with self.agent.db() as db:
            db.execute("UPDATE originals SET text=text || ' Menschen leben hier.' WHERE block='other'")
        search=DocumentSearch(self.bridge)
        with patch.object(search,'semantic',return_value=[]):
            self.assertEqual(search.search('Pinguine Population Eisschollen Antarktis leben'),[])

    def test_multi_topic_selection_preserves_both_anchors(self):
        search=DocumentSearch(self.bridge)
        with patch.object(search,'vectors',return_value=[]),patch.object(search,'semantic',return_value=[]):
            hits=search.search('Photovoltaik und zum Bettelverbot im hvv')
        self.assertEqual({r['doc'] for r in rank_documents(hits,'')},{'solar','music'})
        self.assertEqual(len(facets('Bettel- und Musizierverbot')),1)

    def test_fts_operators_are_never_executed_as_query_syntax(self):
        search=DocumentSearch(self.bridge)
        with patch.object(search,'semantic',return_value=[]):
            search.search('"Photovoltaik" OR * NOT (Gebäude)')
        self.assertIn('gebaeude',terms('Gebäude'))
        self.assertEqual(query_variants('Solaranlagen')[0],'Solaranlagen')

    def test_semantic_rejects_stale_payload_and_missing_page_and_caches_vectors(self):
        self.bridge.search_embedding_cache=OrderedDict()
        search=DocumentSearch(self.bridge)
        payload={'document_id':'solar','sha256':'sha-solar','page':1}
        data={'result':[{'score':.7,'payload':dict(payload,sha256='stale')},
            {'score':.8,'payload':dict(payload,page=999)},{'score':.6,'payload':payload}]}
        with patch.object(self.bridge,'embed',return_value=[[1,2]]) as embed,patch('document_search.api',return_value=data):
            self.assertEqual(len(search.semantic(['PV'])),1)
            search.semantic(['PV'])
            self.assertEqual(embed.call_count,1)


if __name__=='__main__': unittest.main()
