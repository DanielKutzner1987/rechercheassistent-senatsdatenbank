"""Persist source-bound inquiry metadata and one requester row per named person."""
import hashlib
import json
import re
import time

PARSER_VERSION='inquiry-metadata-v2'


def parse_requesters(header):
    # Only a leading author declaration is evidence, never names from the body.
    author_block=re.split(r'\s+vom\s+\d{1,2}\.',header,maxsplit=1,flags=re.I)[0]
    match=re.fullmatch(r'\s*(?:der|des)\s+Abgeordneten\s+(.+?)\s*',author_block,re.I)
    if not match:return [],'collective_only' if re.match(r'\s*(?:der|des)\s+Fraktion',header,re.I) else 'unresolved'
    from corpus_stats import PARTIES
    people=[];pending='';position=0
    for party_match in re.finditer(r'\(([^()]{1,80})\)',match[1]):
        pending+=match[1][position:party_match.start()]
        party=PARTIES.get(re.sub(r'\s+',' ',party_match[1]).strip().casefold())
        for raw in re.split(r'\s+und\s+|\s*,\s*|\s*;\s*',pending.strip(' ,;'),flags=re.I):
            raw=re.sub(r'^und\s+','',raw.strip(),flags=re.I)
            raw=re.sub(r'^(?:und\s+)?(?:der|des)\s+Abgeordneten\s+','',raw.strip(),flags=re.I)
            name=re.sub(r'\b(?:Prof\.|Dr\.)\s*','',raw).strip()
            # Keep spelling as printed; never infer identity or affiliation externally.
            if re.fullmatch(r"[^\W\d_][^\d()<>|,;]{1,100}",name) and len(name.split())>=2 and all(t[0].isupper() or t in ('von','van','de','der','den','zu','zur') for t in name.split()) and not re.search(r'\b(?:Fraktion|Senat|Antwort|Betr)\b',name,re.I):
                people.append({'name':name,'raw_name':raw,'party':party,'status':'parsed' if party else 'party_unresolved'})
            elif raw: return [],'unresolved'
        pending='';position=party_match.end()
    tail=match[1][position:].strip(' ,;')
    if tail and not re.fullmatch(r'(?:und\s+)?(?:die\s+)?Fraktion',tail,re.I):return [],'unresolved'
    unique={p['name'].casefold():p for p in people}
    if len(unique)!=len(people):return [],'unresolved'
    return people,('parsed_with_collective' if tail else 'parsed') if people else 'unresolved'


def parse_document_header(text):
    from corpus_stats import parse_header,PARTIES
    parsed=parse_header(text)
    if parsed:return parsed
    collapsed=re.sub(r'\s+',' ',text).strip()
    boundary=re.search(r'\bBetr\s*\.?\s*:',collapsed,re.I)
    head=collapsed[:boundary.start()] if boundary else collapsed[:1600]
    number=re.search(r'\bDrucksache\s+(\d{1,2})\s*/\s*(\d+)\b',head[:700],re.I)
    kind=re.search(r'\b(Antrag|Mitteilung des Senats|Bericht des .+?ausschusses)\b',head,re.I)
    if not number or not kind:return None
    evidence=head[kind.end():].strip()
    issued=None
    date_match=re.search(r'\b(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{2,4})\b',head[:kind.start()])
    if date_match:
        from datetime import date
        day,month,year=map(int,date_match.groups())
        try:issued=date(year+2000 if year<100 else year,month,day).isoformat()
        except ValueError:pass
    parties=sorted({PARTIES[raw.strip().casefold()] for raw in re.findall(r'\(([^()]{1,80})\)',evidence) if raw.strip().casefold() in PARTIES})
    return {'number':f'{int(number[1])}/{int(number[2])}','term':int(number[1]),'kind':kind[1],
            'parties':parties,'issued':issued,'evidence':evidence[:700]}


def refresh_metadata(agent):
    changed=0
    with agent.db() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS inquiry_metadata(
          doc TEXT PRIMARY KEY,source_hash TEXT,page_hash TEXT,parser_version TEXT,
          number TEXT,term INTEGER,kind TEXT,issued TEXT,title TEXT,source_page INTEGER,
          status TEXT,metadata TEXT,updated REAL);
        CREATE TABLE IF NOT EXISTS inquiry_requesters(
          doc TEXT,ordinal INTEGER,name TEXT,party TEXT,raw_name TEXT,source_page INTEGER,
          status TEXT,PRIMARY KEY(doc,ordinal));
        CREATE INDEX IF NOT EXISTS requester_name ON inquiry_requesters(name);
        ''')
        rows=db.execute('''SELECT d.id,d.path,d.sha256,p.text,p.status FROM documents d
            LEFT JOIN pages p ON p.doc=d.id AND p.page=1 WHERE d.active=1''').fetchall()
        for row in rows:
            text=row['text'] or ''
            page_hash=hashlib.sha256((str(row['status'])+'\0'+text).encode()).hexdigest()
            old=db.execute('SELECT source_hash,page_hash,parser_version FROM inquiry_metadata WHERE doc=?',(row['id'],)).fetchone()
            if old and tuple(old)==(row['sha256'],page_hash,PARSER_VERSION):continue
            parsed=parse_document_header(text) if row['status'] in ('ok','needs_table_review') else None
            people,status=parse_requesters(parsed['evidence']) if parsed else ([], 'not_identified')
            collapsed=re.sub(r'\s+',' ',text)
            title_match=re.search(r'\bBetr\s*\.?\s*:\s*(.*?)(?:Einleitung|Vor diesem Hintergrund|$)',collapsed,re.I)
            title=title_match[1].strip()[:650] if title_match else row['path'].removesuffix('.pdf').replace('_',' ')
            metadata=dict(parsed or {},people=people,author_status=status,source_page=1,title=title)
            requested=re.search(r'\bvom\s+(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{2,4})\b',metadata.get('evidence',''))
            metadata['requested_date']=None
            if requested:
                from datetime import date
                day,month,year=map(int,requested.groups())
                try:metadata['requested_date']=date(year+2000 if year<100 else year,month,day).isoformat()
                except ValueError:pass
            db.execute('INSERT OR REPLACE INTO inquiry_metadata VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (row['id'],row['sha256'],page_hash,PARSER_VERSION,metadata.get('number'),metadata.get('term'),
                 metadata.get('kind'),metadata.get('issued'),title,1,status,json.dumps(metadata,ensure_ascii=False),time.time()))
            db.execute('DELETE FROM inquiry_requesters WHERE doc=?',(row['id'],))
            for ordinal,person in enumerate(people):
                db.execute('INSERT INTO inquiry_requesters VALUES(?,?,?,?,?,?,?)',
                    (row['id'],ordinal,person['name'],person['party'],person['raw_name'],1,person['status']))
            changed+=1
    return changed


def metadata_status(agent):
    with agent.db() as db:
        kinds=dict(db.execute('''SELECT COALESCE(m.kind,'Unklar'),COUNT(*) FROM inquiry_metadata m
            JOIN documents d ON d.id=m.doc WHERE d.active=1 GROUP BY m.kind''').fetchall())
        names=db.execute('''SELECT COUNT(DISTINCT r.name) FROM inquiry_requesters r
            JOIN documents d ON d.id=r.doc JOIN inquiry_metadata m ON m.doc=d.id
            WHERE d.active=1 AND m.kind IN ('Schriftliche Kleine Anfrage','Große Anfrage')''').fetchone()[0]
        result={'parser_version':PARSER_VERSION,'document_kinds':kinds,'inquiry_names':names,
                'inquiries':sum(kinds.get(k,0) for k in ('Schriftliche Kleine Anfrage','Große Anfrage'))}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='gemini_runs'").fetchone():
            run=db.execute('SELECT status,report FROM gemini_runs ORDER BY created DESC LIMIT 1').fetchone()
            if run:
                report=json.loads(run['report']);topic=report.get('topic_analysis',{})
                result['last_analysis']={'status':run['status'],'workflow':report.get('workflow'),
                    'elapsed_seconds':report.get('elapsed_seconds'),'view':topic.get('view'),
                    'cache_hit':topic.get('cache_hit'),'provider_called':report.get('provider_called'),
                    'usage':topic.get('usage'),'documents':topic.get('matrix',{}).get('documents'),
                    'groups':len(topic.get('matrix',{}).get('rows',[]))}
    return result
