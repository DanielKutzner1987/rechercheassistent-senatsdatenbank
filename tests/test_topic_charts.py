import csv
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import xml.etree.ElementTree as ET

from agent import Agent
from bridge import make_handler
from gemini_bridge import GeminiBridge
from corpus_topics import topic_request,run_topics
from topic_charts import party_matrix,svg_chart,csv_data,source_page,store_chart,format_party_topics


class TopicChartTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.agent=Agent(self.root,self.root/'test.sqlite')
        self.bridge=GeminiBridge(self.agent,'EXAMPLE_PRIVATE_KEY')
        self.catalog=[{'id':f'D{i}','doc':f'doc{i}','number':f'23/{i}','title':f'Originaltitel {i}',
                       'parties':parties,'sha256':'hash','kind':'Schriftliche Kleine Anfrage','excerpt':'text','term':23}
                      for i,parties in enumerate((['CDU','CDU'],['CDU','SPD'],[],['SPD']),1)]
        self.groups=[{'name':'Busverkehr','documents':['D1','D2'],'count':2},
                     {'name':'Schienenverkehr','documents':['D3','D4'],'count':2}]

    def tearDown(self):self.temp.cleanup()

    def test_matrix_counts_shared_and_unknown_authors_explicitly(self):
        matrix=party_matrix(self.groups,self.catalog)
        rows={r['party']:r for r in matrix['rows']}
        self.assertEqual(rows['CDU']['counts'],[2,0]);self.assertEqual(rows['CDU']['percentages'],[100,0])
        self.assertEqual(rows['SPD']['counts'],[1,1]);self.assertEqual(rows['SPD']['percentages'],[50,50])
        self.assertEqual(rows['Zuordnung offen']['counts'],[0,1])
        self.assertEqual(sum(r['total'] for r in rows.values()),5)
        self.assertEqual(matrix['documents'],4);self.assertEqual(matrix['joint_documents'],1);self.assertEqual(matrix['unknown_documents'],1)

    def test_invalid_memberships_do_not_produce_a_chart(self):
        for groups in ([{'name':'Bus','documents':['D1','D1']}],[{'name':'Bus','documents':['D1']}],
                       [{'name':'Bus','documents':['D1','D2','D3','D4','D99']}]):
            with self.assertRaises(ValueError):party_matrix(groups,self.catalog)

    def test_svg_sources_and_csv_escape_untrusted_labels(self):
        matrix=party_matrix(self.groups,self.catalog)
        matrix['topics'][0]='=SUM(1,2) <script>&'
        matrix['rows'][0]['sources'][0][0]['title']='<script>alert(1)</script>'
        for kind in ('heatmap','bars'):
            matrix['chart_type']=kind
            svg=svg_chart(matrix,'Bestand <2026>')
            ET.fromstring(svg)
            self.assertNotIn('<script>',svg)
            self.assertIn('&lt;script&gt;',svg)
        page=source_page(matrix,svg_chart(matrix,'Test'),'http://localhost:8093')
        self.assertNotIn('<script>',page);self.assertIn('/documents/doc1#page=1',page)
        rows=list(csv.reader(io.StringIO(csv_data(matrix)),delimiter=';'))
        self.assertTrue(rows[1][2].startswith("'="))

    def test_exact_screenshot_request_is_a_party_chart_and_scope_is_preserved(self):
        query='Kannst du mir bitte eine Grafik erstellen welche Parteien welche Themenschwerpunkte in Anfragen setzen?'
        plan=topic_request(query,[])
        self.assertTrue(plan['party_comparison']);self.assertTrue(plan['chart_requested'])
        history=[{'role':'user','content':'Liste aller Anfragen aus dem Jahr 2026 nach Themen'}]
        plan=topic_request(query,history)
        self.assertIn('2026',plan['scope_query'])
        self.assertTrue(topic_request('Bitte als Balkendiagramm',[{'role':'user','content':query}])['party_comparison'])
        self.assertEqual(topic_request('Bitte als Balkendiagramm',[{'role':'user','content':query}])['chart_type'],'bars')
        self.assertFalse(topic_request('Bitte nur als Tabelle',[{'role':'user','content':query}])['chart_requested'])
        self.assertIsNone(topic_request('Wie viele Busse gibt es 2026? Bitte als Grafik.',history))

    def test_cached_classification_is_used_for_a_new_view_not_replayed_text(self):
        report={'kind':None,'year':2026,'excluded':[]}
        model={'themes':[{'name':g['name'],'documents':g['documents']} for g in self.groups]}
        with patch('corpus_topics.inventory',return_value=(report,self.catalog)),patch.object(self.bridge,'generate_topics',return_value=(model,{},'test')) as generate:
            overview,_=run_topics(self.bridge,'Welche Themen decken die Anfragen im Jahr 2026 ab?',[])
            answer,audit=run_topics(self.bridge,'Erstelle eine Grafik: Welche Parteien setzen welche Themenschwerpunkte in Anfragen?',[])
            table,table_audit=run_topics(self.bridge,'Welche Parteien setzen welche Themenschwerpunkte in Anfragen?',[])
        self.assertEqual(generate.call_count,1)
        self.assertTrue(audit['cache_hit']);self.assertFalse(audit['provider_called'])
        self.assertNotIn('/topic-visualizations/',overview)
        self.assertIn('![',answer);self.assertIn('/topic-visualizations/',answer)
        self.assertIn('CDU',answer);self.assertIn('SPD',answer)
        self.assertEqual(audit['view'],'party_topic_chart')
        self.assertNotIn('![',table);self.assertIn('Schienenverkehr',table)
        self.assertEqual(table_audit['view'],'party_topic_comparison')

    def test_plain_topic_chart_does_not_invent_party_comparison(self):
        report={'kind':None,'year':2026,'excluded':[]}
        model={'themes':[{'name':g['name'],'documents':g['documents']} for g in self.groups]}
        with patch('corpus_topics.inventory',return_value=(report,self.catalog)),patch.object(self.bridge,'generate_topics',return_value=(model,{},'test')):
            text,audit=run_topics(self.bridge,'Bitte eine Grafik der Themenschwerpunkte aller Anfragen',[])
        self.assertEqual(audit['view'],'topic_chart');self.assertIn('![',text)
        self.assertEqual(audit['matrix']['rows'][0]['total'],4)

    def test_http_chart_routes_and_missing_assets(self):
        matrix=party_matrix(self.groups,self.catalog)
        identifier=store_chart(self.agent,matrix,'Testbestand','http://localhost:8093')
        self.assertEqual(identifier,store_chart(self.agent,matrix,'Testbestand','http://localhost:8093'))
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.bridge))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            for extension,mime in [('svg','image/svg+xml'),('html','text/html'),('csv','text/csv')]:
                conn.request('GET',f'/topic-visualizations/{identifier}.{extension}')
                response=conn.getresponse();body=response.read()
                self.assertEqual(response.status,200);self.assertIn(mime,response.getheader('Content-Type'))
                self.assertIn("default-src 'none'",response.getheader('Content-Security-Policy'))
                self.assertNotIn(b'EXAMPLE_PRIVATE_KEY',body)
                if extension=='svg':ET.fromstring(body)
                if extension=='csv':self.assertTrue(body.startswith(b'\xef\xbb\xbf'))
            conn.request('GET','/topic-visualizations/../../private.svg')
            response=conn.getresponse();response.read();self.assertEqual(response.status,404)
        finally:conn.close();server.shutdown();server.server_close()


if __name__=='__main__':unittest.main()
