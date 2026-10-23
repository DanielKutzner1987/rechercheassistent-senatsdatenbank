"""Qdrant synchronization and OpenAI-compatible, evidence-first WebUI endpoint."""
import os
import hashlib
import json
import logging
import math
import queue
from pathlib import Path
import re
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from urllib import request, error
from agent import Agent, handler, normalize

LOG = logging.getLogger('knowledge-bridge')
MODEL = 'senat-wissen'
COLLECTION = os.environ.get('QDRANT_COLLECTION', 'senatswissen_demo_v1')


def numeric_values(text):
    """Compare German number formatting and date components without rejecting equivalent notation."""
    values=set()
    def date_parts(match):
        values.update(str(int(v)) for v in match.groups())
        return ' '
    text=re.sub(r'\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b',date_parts,text)
    for raw in re.findall(r'\d+(?:[.,]\d+)*',text):
        if re.fullmatch(r'\d{1,3}(?:\.\d{3})+(?:,\d+)?',raw):
            raw=raw.replace('.','')
        raw=raw.replace(',','.')
        try:
            values.add(str(float(raw)).removesuffix('.0'))
        except ValueError:
            values.add(raw)
    return values


def api(url, body=None, method=None, timeout=180):
    req = request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                          headers={'Content-Type': 'application/json'}, method=method)
    with request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class Bridge:
    @staticmethod
    def source_label(record):
        match=re.match(r'^(\d{2})_(\d+)_',Path(record['path']).name)
        name=f'Drucksache {match[1]}/{int(match[2])}' if match else Path(record['path']).stem.replace('_',' ')
        return name+f" · PDF-Seite {record['page']}"


    def __init__(self, agent, qdrant='http://localhost:6333', public_url='http://localhost:8091'):
        self.agent, self.qdrant, self.public_url = agent, qdrant.rstrip('/'), public_url.rstrip('/')
        self.model = os.environ.get('EMBEDDING_MODEL', 'mxbai-embed-large')
        self.embedding_lock = threading.Lock()
        self.research_lock = threading.Lock()
        self.query_waiting = threading.Event()
        self.last_error = None
        self.synced = 0
        self.last_sync = None
        self.ready = False
        with agent.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS vector_sync(id TEXT PRIMARY KEY, fingerprint TEXT, synced REAL)')

    def records(self):
        with self.agent.db() as c:
            entries = c.execute('''SELECT e.*,b.page,d.path,d.id AS document_id,d.sha256
                FROM entries e JOIN blocks b ON b.id=e.block JOIN documents d ON d.id=b.doc
                WHERE d.active=1 AND d.status IN ('ready','needs_review')''').fetchall()
            blocks = c.execute('''SELECT b.*,d.path,d.id AS document_id,d.sha256 FROM blocks b
                JOIN documents d ON d.id=b.doc WHERE d.active=1 AND d.status IN ('ready','needs_review')
                AND b.status!='table_extracted' ''').fetchall()
        result = {}
        for row in entries:
            r = dict(row)
            r['kind'] = 'entry'
            r['key'] = 'entry:' + r['id']
            r['embedding_text'] = normalize(r['subject']+' '+r['question']+' '+r['keywords']+' '+r['quote'])[:1600]
            result[r['key']] = r
        for row in blocks:
            r = dict(row)
            r['kind'] = 'original'
            r['key'] = 'block:' + r['id']
            r['embedding_text'] = normalize(r['text'])[:1600]
            result[r['key']] = r
        return result

    @staticmethod
    def point_id(key):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'senat-wissen-v1:' + key))

    def embed(self, texts):
        # Keep embedding work on CPU so background ingestion does not evict Qwen from the GPU.
        with self.embedding_lock:
            data = api(self.agent.ollama+'/api/embed', {'model':self.model, 'input':texts,
                        'truncate':True, 'options':{'num_gpu':0}})
        vectors = data.get('embeddings', [])
        if len(vectors) != len(texts):
            raise ValueError('Embedding-Anzahl stimmt nicht mit Eingaben überein')
        for vector in vectors:
            if not vector or not all(isinstance(v,(float,int)) and math.isfinite(v) for v in vector) or not any(vector):
                raise ValueError('Ungültiger oder leerer Vektor')
        return vectors

    def ensure_collection(self, dimension):
        url = self.qdrant+'/collections/'+COLLECTION
        try:
            current = api(url)['result']
        except error.HTTPError as exc:
            if exc.code != 404:
                raise
            api(url, {'vectors':{'size':dimension,'distance':'Cosine'}}, 'PUT')
            current = api(url)['result']
        config = current['config']['params']['vectors']
        if config.get('size') != dimension or config.get('distance') != 'Cosine':
            raise ValueError('Neue Qdrant-Sammlung hat inkompatible Vektorkonfiguration')
        self.ready = True

    def sync(self):
        records = self.records()
        with self.agent.db() as c:
            previous = dict(c.execute('SELECT id,fingerprint FROM vector_sync').fetchall())
        # A deleted/recreated collection must trigger a complete resync despite local checkpoints.
        try:
            api(self.qdrant+'/collections/'+COLLECTION)
        except error.HTTPError as exc:
            if exc.code != 404:
                raise
            previous = {}
        pending = []
        for key, record in records.items():
            fingerprint = hashlib.sha256((self.model+'\n'+record['embedding_text']+'\n'+record['sha256']).encode()).hexdigest()
            if previous.get(key) != fingerprint:
                pending.append((key,record,fingerprint))
        for start in range(0,len(pending),8):
            while self.query_waiting.is_set():
                time.sleep(0.1)
            batch = pending[start:start+8]
            vectors = self.embed([item[1]['embedding_text'] for item in batch])
            self.ensure_collection(len(vectors[0]))
            points = [{'id':self.point_id(key),'vector':vector,
                       'payload':{'key':key,'sha256':r['sha256'],'document_id':r['document_id'],
                                  'page':r['page'],'source':r['path'],'kind':r['kind']}}
                      for (key,r,fp),vector in zip(batch,vectors)]
            api(self.qdrant+'/collections/'+COLLECTION+'/points?wait=true', {'points':points}, 'PUT')
            with self.agent.db() as c:
                c.executemany('INSERT OR REPLACE INTO vector_sync VALUES(?,?,?)',
                              [(key,fp,time.time()) for key,r,fp in batch])
            self.synced = len(previous)+min(start+8,len(pending))
            LOG.info('Qdrant: %s/%s neue/geänderte Einträge synchronisiert', min(start+8,len(pending)),len(pending))
        stale = [key for key in previous if key not in records]
        if stale:
            api(self.qdrant+'/collections/'+COLLECTION+'/points/delete?wait=true',
                {'points':[self.point_id(key) for key in stale]}, 'POST')
            with self.agent.db() as c:
                c.executemany('DELETE FROM vector_sync WHERE id=?',[(key,) for key in stale])
        self.synced = len(records)
        self.last_sync = time.time()
        self.last_error = None





def make_handler(bridge):
    base=handler(bridge.agent)
    model_id = bridge.api_model_id
    workflow_id = bridge.workflow_id
    class Handler(base):
        def stream_verified(self, content, history):
            """Keep a long research request alive without inventing partial answers."""
            completed=queue.Queue()
            def work():
                try: completed.put((bridge.answer(content,history=history),None))
                except Exception as exc:
                    LOG.exception('Verifizierte Recherche fehlgeschlagen')
                    completed.put((None,str(exc)))
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream; charset=utf-8')
            self.send_header('Cache-Control','no-cache')
            self.send_header('X-Accel-Buffering','no')
            self.end_headers()
            threading.Thread(target=work,daemon=True).start()
            cid='chatcmpl-'+uuid.uuid4().hex
            created=int(time.time())
            while True:
                try:
                    answer,problem=completed.get(timeout=10)
                    break
                except queue.Empty:
                    self.wfile.write(b': research-in-progress\n\n')
                    self.wfile.flush()
            if problem:
                event={'error':{'message':'Die lokale Recherche konnte nicht abgeschlossen werden.','type':'research_error'}}
                self.wfile.write(('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode())
            else:
                event={'id':cid,'object':'chat.completion.chunk','created':created,'model':model_id,
                       'choices':[{'index':0,'delta':{'role':'assistant','content':answer},'finish_reason':None}]}
                self.wfile.write(('data: '+json.dumps(event,ensure_ascii=False)+'\n\n').encode())
                event['choices']=[{'index':0,'delta':{},'finish_reason':'stop'}]
                self.wfile.write(('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode())
            self.wfile.flush()

        def do_GET(self):
            if self.path=='/metadata-status':
                from document_metadata import metadata_status
                self.send_json(metadata_status(bridge.agent));return
            from topic_charts import serve_chart
            if serve_chart(self,bridge.agent): return
            if self.path.rstrip('/')=='/v1/models':
                model={'id':model_id,'object':'model','owned_by':'lokaler-pdf-agent'}
                if getattr(bridge,'display_name',None): model['name']=bridge.display_name
                self.send_json({'object':'list','data':[model]})
            elif self.path=='/rag-status':
                self.send_json({'workflow':workflow_id,'collection':COLLECTION,'synced':bridge.synced,'last_sync':bridge.last_sync,
                                'error':bridge.last_error,'agent':bridge.agent.status()})
            else:
                super().do_GET()

        def do_POST(self):
            if self.path.rstrip('/')!='/v1/chat/completions':
                self.send_error(404)
                return
            try:
                length=int(self.headers.get('Content-Length',0))
                if length<=0 or length>1000000:
                    raise ValueError('Ungültige Anfragegröße')
                data=json.loads(self.rfile.read(length))
                messages=[m for m in data.get('messages',[]) if m.get('role')=='user']
                if not messages:
                    raise ValueError('Keine Benutzerfrage')
                content=messages[-1].get('content','')
                if isinstance(content,list):
                    content=' '.join(p.get('text','') for p in content if p.get('type')=='text')
                if not isinstance(content,str) or not content.strip() or len(content)>8000:
                    raise ValueError('Frage fehlt oder ist zu lang')
                if (getattr(bridge,'verified_research',False) or getattr(bridge,'stream_keepalive',False)) and data.get('stream'):
                    self.stream_verified(content,data.get('messages',[])[:-1])
                    return
                answer=bridge.answer(content,history=data.get('messages',[])[:-1])
                reply={'id':'chatcmpl-'+uuid.uuid4().hex,'object':'chat.completion','created':int(time.time()),
                       'model':model_id,'choices':[{'index':0,'message':{'role':'assistant','content':answer},'finish_reason':'stop'}]}
                if data.get('stream'):
                    self.send_response(200)
                    self.send_header('Content-Type','text/event-stream; charset=utf-8')
                    self.send_header('Cache-Control','no-cache')
                    self.end_headers()
                    chunk={'id':reply['id'],'object':'chat.completion.chunk','created':reply['created'],
                           'model':model_id,'choices':[{'index':0,'delta':{'role':'assistant','content':answer},'finish_reason':None}]}
                    self.wfile.write(('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\n').encode())
                    chunk['choices']=[{'index':0,'delta':{},'finish_reason':'stop'}]
                    self.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())
                    self.wfile.flush()
                else:
                    self.send_json(reply)
            except ValueError as exc:
                self.send_error(400,str(exc))
            except Exception:
                LOG.exception('RAG-Anfrage fehlgeschlagen')
                self.send_error(502,'Lokale Wissenssuche oder Modellaufruf fehlgeschlagen')
    return Handler
