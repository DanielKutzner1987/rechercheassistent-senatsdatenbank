"""Reproducible import and serving entry point; no provider calls during import."""
import argparse
import json
import logging
import os
import time
from pathlib import Path
from http.server import ThreadingHTTPServer

from agent import Agent
from bridge import Bridge, api, make_handler
from document_search import DocumentSearch
from gemini_bridge import GeminiBridge
from gemini_client import prepare_key


def wait_for_local_services(ollama, qdrant):
    for attempt in range(30):
        try:
            api(ollama.rstrip('/') + '/api/version', timeout=2)
            api(qdrant.rstrip('/') + '/', timeout=2)
            return
        except Exception:
            if attempt == 29:
                raise SystemExit('Ollama or Qdrant is not ready. Inspect docker compose logs.') from None
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['demo', 'index', 'serve', 'status'])
    parser.add_argument('--folder', default=os.environ.get('PDF_FOLDER', 'data'))
    parser.add_argument('--database', default=os.environ.get('DATABASE_PATH', 'state/knowledge.sqlite'))
    parser.add_argument('--ollama', default=os.environ.get('OLLAMA_URL', 'http://localhost:11434'))
    parser.add_argument('--qdrant', default=os.environ.get('QDRANT_URL', 'http://localhost:6333'))
    parser.add_argument('--key-file', default=os.environ.get('GEMINI_KEY_FILE', 'secrets/gemini_api_key.txt'))
    parser.add_argument('--host', default=os.environ.get('BIND_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8094')))
    parser.add_argument('--public-url', default=os.environ.get('PUBLIC_URL', 'http://localhost:8094'))
    parser.add_argument('--lexical-only', action='store_true', help='Import without embeddings; no Ollama/Qdrant needed')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.command == 'demo':
        from demo import create_demo
        create_demo(Path(args.folder))
        return
    folder = Path(args.folder)
    if not folder.is_dir():
        parser.error('PDF folder does not exist. Create data/ or configure PDF_FOLDER.')
    agent = Agent(folder, args.database, ollama=args.ollama)
    if args.command == 'status':
        print(json.dumps(agent.status(), ensure_ascii=False, indent=2))
        return
    if args.command == 'index':
        agent.scan()
        with agent.db() as db:
            errors = db.execute("SELECT count(*) FROM documents WHERE active=1 AND status='error'").fetchone()[0]
            count = db.execute('SELECT count(*) FROM documents WHERE active=1').fetchone()[0]
        if not count or errors:
            raise SystemExit('Import empty or failed. Inspect status and PDF extraction before continuing.')
        bridge = Bridge(agent, qdrant=args.qdrant, public_url=args.public_url)
        DocumentSearch(bridge).refresh_cards()
        if not args.lexical_only:
            wait_for_local_services(args.ollama, args.qdrant)
            # Explicit import action downloads the local embedding model if necessary.
            api(args.ollama.rstrip('/') + '/api/pull', {'model': bridge.model, 'stream': False}, timeout=1800)
            bridge.sync()
        print(json.dumps(agent.status(), ensure_ascii=False, indent=2))
        print('Import complete. Restart app after reindexing. No Gemini request was sent.')
        return
    path = Path(args.key_file)
    if not path.is_file():
        parser.error('Missing key file. See README; never put the key in .env or Git.')
    key, info = prepare_key(path.read_text(encoding='utf-8-sig'))
    if not info['valid_http_token']:
        parser.error('Invalid key format. Key contents were not logged.')
    bridge = GeminiBridge(agent, key, public_url=args.public_url, qdrant=args.qdrant)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(bridge))
    print(f'Rechercheassistent - Senatsdatenbank ready: {args.public_url}; provider calls only when questions require them.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        bridge._key = None


if __name__ == '__main__':
    main()
