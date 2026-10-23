"""Three local retrieval routes; document cards are extractive, never factual answers."""
from collections import OrderedDict
import hashlib
import math
import re
import time
import unicodedata
from bridge import api, COLLECTION
from search_terms import STOP

VERSION='document-search-v1'
EXTRA_STOP=set('moin hallo ich wir uns euch meine mein fasse dokument dokumente inhalt zusammen wichtigsten zahlen fakten insgesamt benenne erläutere erklaere erklär erklären laut unterlagen hamburg hamburger angaben betreibt betrieben ausgestattet'.split())
SYNONYMS=(
    ('photovoltaik','photovoltaikanlagen','photovoltaikanlage','photovolt','pv','solaranlagen','solaranlage'),
    ('mitarbeiter','beschäftigte','beschäftigten','personal','angestellte','belegschaft'),
    ('busse','bus','busflotte','busbestand','fahrzeugbestand'),
    ('taktreduzierung','taktkürzung','taktanpassung','buslinienfrequenz'),
    ('bettelverbot','betteln','bettel','musizierverbot','musizieren'),
    ('beschwerden','fahrgastbeschwerden','beschwerdemanagement'),
)


def normalize(value):
    value=value.lower().replace('ä','ae').replace('ö','oe').replace('ü','ue').replace('ß','ss')
    return ''.join(c for c in unicodedata.normalize('NFKD',value) if not unicodedata.combining(c))


def terms(query):
    stop={normalize(x) for x in STOP|EXTRA_STOP}
    return list(dict.fromkeys(w for w in re.findall(r'[^\W_]{2,}',normalize(query)) if w not in stop))[:18]


def query_variants(query):
    result=[query[:1000]]
    words=set(terms(query)); additions=[]
    for group in SYNONYMS:
        if words.intersection(normalize(x) for x in group):
            additions.extend(group)
    if additions:
        result.append(' '.join(terms(query)+list(dict.fromkeys(additions))))
    return list(dict.fromkeys(result))[:2]


def facets(query):
    # Split only explicit topic transitions, not names like Bettel- und Musizierverbot.
    parts=re.split(r'\s+(?:und|sowie)\s+(?=(?:zum|zur|zu|über|welche|wie)\b)',query,flags=re.I)
    return parts[:3] if len(parts)>1 else [query]


def expression(words):
    # Terms contain only letters/digits, never FTS operators from user input.
    return ' OR '.join('"'+w+'"*' for w in words)


def spellings(word):
    return {word,word.replace('ae','ä').replace('oe','ö').replace('ue','ü'),word.replace('ss','ß')}


def best_per_document(rows):
    result={}
    for row in rows:
        result.setdefault(row['doc'],dict(row))
    return list(result.values())


def fuse(routes,limit=12):
    """One RRF vote per document per route, regardless of chunks/query count."""
    results={}
    for route,rows in routes.items():
        maximum=max((r.get('value',0) for r in rows),default=0)
        for rank,row in enumerate(best_per_document(rows),1):
            item=results.setdefault(row['doc'],dict(row,score=0.0,routes=[]))
            strength=max(0,min(1,row.get('value',maximum)/maximum)) if maximum>0 else 1
            item['score']+=(0.25+0.75*strength)/(20+rank)
            item['routes'].append(route)
    return sorted(results.values(),key=lambda r:(-r['score'],r['path']))[:limit]


class DocumentSearch:
    def __init__(self,bridge):
        self.bridge=bridge
        self.agent=bridge.agent
        self.events=[]
        with self.agent.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS document_cards(
                doc TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, summary TEXT,
                organisations TEXT, dates TEXT, source_page INTEGER)''')
            db.execute('''CREATE VIRTUAL TABLE IF NOT EXISTS document_cards_fts USING fts5(
                doc UNINDEXED,title,summary,organisations,dates,tokenize='unicode61 remove_diacritics 2')''')

    def refresh_cards(self):
        changed=0
        with self.agent.db() as db:
            docs=db.execute("SELECT id,path,sha256 FROM documents WHERE active=1 AND status IN ('ready','needs_review')").fetchall()
            active={d['id'] for d in docs}
            for old in db.execute('SELECT doc FROM document_cards').fetchall():
                if old['doc'] not in active:
                    db.execute('DELETE FROM document_cards WHERE doc=?',(old['doc'],))
                    db.execute('DELETE FROM document_cards_fts WHERE doc=?',(old['doc'],))
            for doc in docs:
                fingerprint=hashlib.sha256((VERSION+doc['sha256']+doc['path']).encode()).hexdigest()
                old=db.execute('SELECT fingerprint FROM document_cards WHERE doc=?',(doc['id'],)).fetchone()
                if old and old[0]==fingerprint: continue
                pages=db.execute("SELECT page,text FROM pages WHERE doc=? AND status IN ('ok','needs_table_review') ORDER BY page",(doc['id'],)).fetchall()
                text='\n'.join(p['text'] for p in pages)
                title=re.sub(r'^\d{2}_\d+_','',doc['path']).removesuffix('.pdf').replace('_',' ')
                # A source extract, not an LLM summary or validated statement of fact.
                summary=pages[0]['text'][:2400] if pages else ''
                orgs=sorted(set(re.findall(r'\b[A-ZÄÖÜ][A-ZÄÖÜ0-9-]{1,20}\b',text)))
                orgs+= [r[0] for r in db.execute('SELECT DISTINCT subject FROM table_cells WHERE doc=?',(doc['id'],))]
                dates=sorted(set(re.findall(r'\b(?:\d{1,2}\.\d{1,2}\.\d{4}|(?:19|20)\d{2})\b',text)))
                db.execute('INSERT OR REPLACE INTO document_cards VALUES(?,?,?,?,?,?,?)',
                    (doc['id'],fingerprint,title,summary,' '.join(orgs),' '.join(dates),pages[0]['page'] if pages else 0))
                db.execute('DELETE FROM document_cards_fts WHERE doc=?',(doc['id'],))
                db.execute('INSERT INTO document_cards_fts VALUES(?,?,?,?,?)',
                    (doc['id'],normalize(title),normalize(summary),normalize(' '.join(orgs)),' '.join(dates)))
                changed+=1
        self.events.append({'tool':'document_cards','updated':changed,'kind':'extractive_first_page_and_metadata'})

    def lexical(self,queries):
        candidates={}
        with self.agent.db() as db:
            for query in queries:
                words=terms(query)
                if not words: continue
                # Original FTS preserves German spelling; include both transliterations.
                raw=re.findall(r'[^\W_]{2,}',query.lower())
                words=list(dict.fromkeys(words+[w for w in raw if normalize(w) in words]))
                rows=db.execute('''SELECT b.doc,b.page,d.path,b.text,bm25(originals,0,3,1) AS rank
                    FROM originals JOIN blocks b ON b.id=originals.block JOIN documents d ON d.id=b.doc
                    WHERE originals MATCH ? AND d.active=1 AND d.status IN ('ready','needs_review')
                    ORDER BY rank''',(expression(words),))
                seen=set()
                for row in rows:
                    if row['doc'] in seen: continue
                    seen.add(row['doc'])
                    # Query variants share one route; use the strongest rank only.
                    score=-row['rank']
                    if row['doc'] not in candidates or score>candidates[row['doc']]['value']:
                        candidates[row['doc']]={'doc':row['doc'],'page':row['page'],'path':row['path'],'value':score}
                    if len(seen)>=12: break
        return sorted(candidates.values(),key=lambda r:-r['value'])[:12]

    def metadata(self,queries):
        candidates={}
        with self.agent.db() as db:
            for query in queries:
                words=terms(query)
                if not words: continue
                rows=db.execute('''SELECT f.doc,d.path,c.source_page AS page,
                    bm25(document_cards_fts,0,4,1,0.3,0.2) AS rank
                    FROM document_cards_fts f JOIN documents d ON d.id=f.doc JOIN document_cards c ON c.doc=f.doc
                    WHERE document_cards_fts MATCH ? AND d.active=1 AND d.status IN ('ready','needs_review')
                    ORDER BY rank LIMIT 12''',(expression(words),)).fetchall()
                for row in rows:
                    score=-row['rank']
                    if row['doc'] not in candidates or score>candidates[row['doc']]['value']:
                        candidates[row['doc']]={'doc':row['doc'],'page':row['page'],'path':row['path'],'value':score}
        return sorted(candidates.values(),key=lambda r:-r['value'])

    def vectors(self,queries):
        cache=getattr(self.bridge,'search_embedding_cache',None)
        if cache is None:
            cache=self.bridge.search_embedding_cache=OrderedDict()
        now=time.monotonic()
        prompts=['Represent this sentence for searching relevant passages: '+q for q in queries]
        missing=[p for p in prompts if p not in cache or now-cache[p][0]>600]
        if missing:
            vectors=self.bridge.embed(missing)
            for prompt,vector in zip(missing,vectors): cache[prompt]=(now,vector)
            while len(cache)>64: cache.popitem(last=False)
        return [cache[p][1] for p in prompts]

    def semantic(self,queries):
        candidates={}
        for vector in self.vectors(queries):
            found=api(self.bridge.qdrant+'/collections/'+COLLECTION+'/points/search',
                {'vector':vector,'limit':32,'with_payload':True},timeout=30)['result']
            with self.agent.db() as db:
                for hit in found:
                    payload=hit.get('payload',{})
                    row=db.execute("SELECT id,path,sha256 FROM documents WHERE id=? AND active=1 AND status IN ('ready','needs_review')",(payload.get('document_id'),)).fetchone()
                    if not row or row['sha256']!=payload.get('sha256'): continue
                    page=payload.get('page')
                    if not db.execute('SELECT 1 FROM pages WHERE doc=? AND page=?',(row['id'],page)).fetchone(): continue
                    if row['id'] not in candidates or hit['score']>candidates[row['id']]['value']:
                        candidates[row['id']]={'doc':row['id'],'page':page,'path':row['path'],'value':hit['score']}
        return sorted(candidates.values(),key=lambda r:-r['value'])[:12]

    def search_facet(self,query):
        variants=query_variants(query)
        routes={'fulltext':self.lexical([query]),'metadata':self.metadata([query])}
        self.bridge.query_waiting.set()
        try:
            routes['semantic']=self.semantic([query])
        except Exception as exc:
            self.events.append({'tool':'semantic','error_type':type(exc).__name__})
        finally:
            self.bridge.query_waiting.clear()
        # Bounded second round: resolve vocabulary gaps without a planning LLM.
        initial=fuse(routes)
        second=bool(len(variants)>1 and (not initial or len(initial[0]['routes'])<3))
        if second:
            routes['fulltext']=self.lexical(variants)
            routes['metadata']=self.metadata(variants)
            # Expanded keywords feed lexical routes; the semantic route keeps natural language.
        result=fuse(routes)
        # Check topic coverage against original text, not generated Q&A or summaries.
        groups=[]
        for word in terms(query):
            group=next((tuple(normalize(x) for x in g) for g in SYNONYMS if word in {normalize(x) for x in g}),(word,))
            if group not in groups: groups.append(group)
        matches=[]
        with self.agent.db() as db:
            count=db.execute('SELECT count(*) FROM documents WHERE active=1').fetchone()[0]
            for group in groups:
                words={s for w in group for s in spellings(w[:max(4,len(w)-2)])}
                ids={r[0] for r in db.execute('''SELECT DISTINCT b.doc FROM originals JOIN blocks b ON b.id=originals.block
                    JOIN documents d ON d.id=b.doc WHERE originals MATCH ? AND d.active=1
                    AND d.status IN ('ready','needs_review')''',(expression(words),))}
                matches.append(ids)
        weights=[math.log(1+count/(1+len(ids))) for ids in matches]
        for hit in result:
            coverage=sum(w for ids,w in zip(matches,weights) if hit['doc'] in ids)/max(sum(weights),1e-9)
            hit['coverage']=round(coverage,3)
            semantic=routes.get('semantic',[])
            # Preserve a strong semantic-only result when wording has no lexical overlap.
            # This is a heuristic for the current embedding model, not a probability.
            fallback=(semantic and hit['doc']==semantic[0]['doc']
                and semantic[0]['value']>=0.78
                and (len(semantic)==1 or semantic[0]['value']-semantic[1]['value']>=0.04))
            hit['semantic_fallback']=bool(fallback)
            if fallback: coverage=max(coverage,0.75)
            hit['score']*=0.2+0.8*coverage
        result=[h for h in result if h['coverage']>=0.25 or h['semantic_fallback']]
        result.sort(key=lambda r:-r['score'])
        self.events.append({'tool':VERSION,'query':query,'queries':variants if second else variants[:1],
            'second_round':second,'routes':{k:[dict(r) for r in v] for k,v in routes.items()},
            'selected_candidates':result})
        return result

    def search(self,query):
        started=time.monotonic()
        self.refresh_cards()
        parts=facets(query)
        if len(parts)>1:
            self.bridge.query_waiting.set()
            try: self.vectors(parts)  # One batched embedding call for independent topics.
            except Exception as exc:
                self.events.append({'tool':'embedding_batch','error_type':type(exc).__name__})
            finally: self.bridge.query_waiting.clear()
        combined={}; anchors=[]
        for part in parts:
            hits=self.search_facet(part)
            if len(parts)>1 and hits: anchors.append(hits[0]['doc'])
            for hit in hits:
                if hit['doc'] not in combined or hit['score']>combined[hit['doc']]['score']:
                    combined[hit['doc']]=dict(hit,document_ranked=True)
        result=sorted(combined.values(),key=lambda r:-r['score'])
        for hit in result: hit['topic_anchor']=hit['doc'] in anchors
        self.events.append({'tool':'document_fusion','facets':parts,'anchors':anchors,
            'seconds':round(time.monotonic()-started,3),'candidates':result})
        return result[:12]
