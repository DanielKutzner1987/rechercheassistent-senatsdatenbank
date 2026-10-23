"""Fresh synthetic PDFs -> real extraction -> search -> mocked answer -> HTTP PDF."""
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from unittest.mock import patch

from agent import Agent
from bridge import Bridge, make_handler
from corpus_stats import count_parties
from demo import create_demo
from document_search import DocumentSearch
from gemini_bridge import GeminiBridge


class FreshInstallSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / 'pdfs'
        create_demo(self.folder)
        self.agent = Agent(self.folder, self.root / 'state/knowledge.sqlite')
        self.agent.scan()

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_import_search_source_links_and_local_statistics(self):
        bridge = GeminiBridge(self.agent, 'EXAMPLE_PRIVATE_KEY')
        search = DocumentSearch(bridge)
        with patch.object(bridge, 'embed', side_effect=RuntimeError('no external services in this test')):
            hits = search.search('Wie viele Mitarbeiter hat die Musterverkehr AG?')
        self.assertTrue(hits)
        self.assertEqual(hits[0]['path'], '23_90002_demo_personal.pdf')
        with self.agent.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM documents WHERE active=1').fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT count(*) FROM pages WHERE status='ok'").fetchone()[0], 3)
        stats = count_parties(self.agent, 'Welche Partei hat die meisten Kleinen Anfragen im Bestand?')
        self.assertEqual(stats['total_inquiries'], 3)
        self.assertEqual(stats['counts'], {'CDU': 2, 'Die Linke': 1})
        draft = 'Die Musterverkehr AG hat insgesamt **450 Beschäftigte** am 01.04.2025. Davon arbeiten 7 im Beschwerdemanagement [D1, S. 1].'
        with patch('gemini_bridge.DocumentSearch.search', return_value=hits), patch.object(bridge, 'generate', return_value=(draft, {'totalTokenCount': 123}, 'offline-test')) as provider:
            answer = bridge.answer('Wie viele Mitarbeiter hat die Musterverkehr AG?')
        self.assertIn('450', answer)
        self.assertIn('#page=1', answer)
        self.assertNotIn('nicht mit gültigen', answer)
        packet = provider.call_args[0][1]
        self.assertIn('450', packet[0]['text'])
        self.assertIn('Beschwerdemanagement', packet[0]['text'])
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(bridge))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            connection.request('GET', '/documents/' + packet[0]['doc'])
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader('Content-Type'), 'application/pdf')
            self.assertEqual(hashlib.sha256(response.read()).hexdigest(), packet[0]['sha256'])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
        self.agent.scan()
        self.assertEqual(len(self.agent.status()['documents']), 3)

    def test_vector_sync_against_local_http_stubs_without_existing_collection(self):
        stored = []
        collection = {}
        class Stub(BaseHTTPRequestHandler):
            def reply(self, body, status=200):
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def do_GET(self):
                if not collection:
                    self.reply({'status': 'not found'}, 404)
                else:
                    self.reply({'result': {'config': {'params': collection}}})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/api/embed':
                    self.reply({'embeddings': [[1.0, 0.5, 0.1] for text in body['input']]})
                else:
                    self.reply({'result': True})

            def do_PUT(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if 'points?' in self.path:
                    stored.extend(body['points'])
                else:
                    collection.update(body)
                self.reply({'result': True})

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        endpoint = f'http://127.0.0.1:{server.server_port}'
        self.agent.ollama = endpoint
        bridge = Bridge(self.agent, qdrant=endpoint)
        try:
            bridge.sync()
            self.assertEqual(len(stored), 3)
            self.assertEqual(len({point['payload']['document_id'] for point in stored}), 3)
            self.assertEqual(collection['vectors']['size'], 3)
            bridge.sync()
            self.assertEqual(len(stored), 3, 'second import should reuse unchanged vectors')
        finally:
            server.shutdown()
            server.server_close()

    def test_demo_does_not_overwrite_changed_files(self):
        target = next(self.folder.glob('*.pdf'))
        target.write_bytes(b'user content')
        with self.assertRaises(ValueError):
            create_demo(self.folder)
        self.assertEqual(target.read_bytes(), b'user content')


if __name__ == '__main__':
    unittest.main()
