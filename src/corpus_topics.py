"""Conversation-aware topic overview over a complete, explicitly scoped document inventory."""
import hashlib
import json
import re
import time
from urllib.parse import quote
from corpus_stats import count_parties,is_party_statistics,PARTIES

POLICY='corpus-topics-v3'
CATALOG_BUDGET=120000


class TopicValidationError(ValueError):
    def __init__(self,reason,metadata=None):
        super().__init__(reason)
        self.metadata=metadata or {}


def decode_assignments(result,catalog):
    """Required per-ID keys prevent an apparently complete but partial group list."""
    names=result.get('themes') if isinstance(result,dict) else None
    assignments=result.get('assignment') if isinstance(result,dict) else None
    allowed={row['id'] for row in catalog}
    if not isinstance(names,list) or not 1<=len(names)<=8 or not all(isinstance(n,str) for n in names):
        raise TopicValidationError('invalid_groups')
    if not isinstance(assignments,dict) or set(assignments)!=allowed:
        raise TopicValidationError('incomplete_inventory')
    groups=[{'name':name,'documents':[]} for name in names]
    for source,index in assignments.items():
        if type(index) is not int or not 0<=index<len(groups):
            raise TopicValidationError('invalid_assignment')
        groups[index]['documents'].append(source)
    converted={'themes':[g for g in groups if g['documents']]}
    validate_topics(converted,catalog)
    return converted


def scope_refinement(text):
    """Only a short filter reply, not a fresh factual question containing a year."""
    if len(text)>180: return False
    rest=text.casefold()
    has_filter=bool(re.search(r'\b(?:19|20)\d{2}\b|wahlperiode|alle\s+(?:fraktionen|parteien)',rest))
    for name in sorted(PARTIES,key=len,reverse=True):
        pattern=r'(?<!\w)'+re.escape(name)+r'(?!\w)'
        if re.search(pattern,rest):
            has_filter=True
            rest=re.sub(pattern,' ',rest)
    rest=re.sub(r'\b(?:19|20)\d{2}\b|\b\d{1,2}\.?\s*wahlperiode\b|\bwahlperiode\s*\d{1,2}\b',' ',rest)
    allowed=set('gerne gern bitte nur fuer für das die der den dem des im in aus jahr jahre jahres und dann doch stattdessen alle fraktionen fraktion partei parteien aktuelle aktuellen dieser diese wahlperiode seit bis zwischen auch liste dieselbe gleiche beschränke beschraenke auf kleine kleinen große großen anfragen'.split())
    return has_filter and all(word in allowed for word in re.findall(r'[^\W_]+',rest))


def presentation_request(text):
    if len(text)>180 or not re.search(r'grafik|grafisch|diagramm|visualisier|heatmap|balken|tabelle|tabellarisch',text,re.I):return False
    allowed=set('kannst du mir das daraus bitte diese übersicht uebersicht auswertung ergebnis nur als ein eine einen diagramm grafik grafisch visualisieren visualisiere darstellen anzeigen erstellen erstelle zeige zeig zeigen mach mache machst machen ohne mit tabelle tabellarisch stattdessen auch jetzt doch in prozent absoluten absolute zahlen heatmap balkendiagramm balkengrafik säulendiagramm saeulendiagramm'.split())
    return all(word.casefold() in allowed for word in re.findall(r'[^\W_]+',text))


def topic_request(query,history):
    topic=bool(re.search(r'\b(?:themen\w*|schwerpunkt\w*|themenbereich\w*|worum)\b',query,re.I))
    chart_requested=bool(re.search(r'grafik|grafisch|diagramm|visualisier|heatmap|balken',query,re.I))
    table_requested=bool(re.search(r'tabelle|tabellarisch',query,re.I))
    presentation=presentation_request(query)
    named_parties={label for name,label in PARTIES.items() if re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',query.casefold())}
    comparison_requested=topic and (bool(re.search(r'partei|fraktion',query,re.I)) or bool(named_parties))
    person_requested=topic and bool(re.search(r'person\w*|abgeordnet\w*|fragestell\w*|anfrag\w*stell\w*',query,re.I))
    refinement=scope_refinement(query)
    if not topic and not refinement and not presentation: return None
    previous=None
    filters=[]
    previous_is_topics=False
    for message in reversed((history or [])[-10:]):
        if message.get('role')!='user' or not isinstance(message.get('content'),str): continue
        text=message['content']
        if scope_refinement(text) or presentation_request(text):
            filters.append(text)
            continue
        if re.search(r'themen|schwerpunkte',text,re.I) and re.search(r'bestand|anfragen|drucksachen|unterlagen|dokumente|personen|abgeordnet|fragestell|anfrag\w*stell',text,re.I):
            previous=text;previous_is_topics=True;break
        if is_party_statistics(text): previous=text;break
        # A new concrete subject breaks the reference to an older corpus analysis.
        if not (re.search(r'themen|schwerpunkte|worum',text,re.I) and re.search(r'\b(?:dabei|dazu|davon|diese\w*)\b',text,re.I)): break
    refers=bool(re.search(r'\b(?:dabei|dazu|davon|diese\w*|deren|darunter)\b',query,re.I))
    if previous_is_topics and (chart_requested or comparison_requested or person_requested):refers=True
    if re.search(r'gesamte\w*\s+bestand|alle\s+(?:jahre|wahlperioden)',query,re.I):refers=False
    if not topic:
        if not previous_is_topics or not (refinement or presentation): return None
        refers=True
    inventory=person_requested or comparison_requested or bool(re.search(r'bestand|eingelesen|hochgeladen|anfragen|drucksachen|unterlagen|dokumente|pdfs',query,re.I))
    if refers and not previous and not inventory:
        return {'clarification':'Auf welche Unterlagen beziehst du dich mit „dabei“ – auf den gesamten eingelesenen Bestand oder eine bestimmte Auswahl?'}
    if not inventory and not (refers and previous): return None
    # Preserve explicit scope from the preceding user request, never from an assistant's guessed answer.
    contexts=([previous]+list(reversed(filters))+[query]) if previous and refers else [query]
    kind_scope=next((s for s in reversed(contexts) if re.search(r'(?:klein|groß)\w*\s+anfrag',s,re.I)),'')
    small=bool(re.search(r'klein\w*\s+anfrag',kind_scope,re.I))
    large=bool(re.search(r'groß\w*\s+anfrag',kind_scope,re.I))
    neutral='Kleine Anfragen' if small and not large else 'Große Anfragen' if large and not small else 'Anfragen'
    period_scope=next((s for s in reversed(contexts) if re.search(r'wahlperiode',s,re.I)),'')
    periods=re.findall(r'\b(\d{1,2})\.?\s*Wahlperiode|Wahlperiode\s*(\d{1,2})\b',period_scope,re.I)
    if periods: neutral+=' der '+next(p for p in periods[-1] if p)+'. Wahlperiode'
    if re.search(r'aktuell\w*\s+Wahlperiode|dies\w*\s+Wahlperiode',period_scope,re.I): neutral+=' der aktuellen Wahlperiode'
    time_scope=next((s for s in reversed(contexts) if re.search(r'\b(?:19|20)\d{2}\b|\b(?:seit|bis|zwischen|quartal|monat)\b',s,re.I)),'')
    years=re.findall(r'\b(?:19|20)\d{2}\b',time_scope)
    if years: neutral+=' im Jahr '+years[-1]
    # Do not silently narrow complex date filters to the last year.
    unsupported=bool(len(set(years))>1 or re.search(r'\b(?:seit|bis|zwischen|quartal|monat)\b',time_scope,re.I))
    parties=set()
    for text in contexts:
        found={label for name,label in PARTIES.items() if re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',text.casefold())}
        if re.search(r'alle\s+(?:parteien|fraktionen)',text,re.I): parties=set()
        elif found: parties=found
    if comparison_requested and not any(re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',query.casefold()) for name in PARTIES):parties=set()
    party_comparison=comparison_requested or bool(refers and previous and re.search(r'partei|fraktion',previous,re.I) and re.search(r'themen|schwerpunkt',previous,re.I))
    person_comparison=person_requested or bool(not comparison_requested and refers and previous and re.search(r'person\w*|abgeordnet\w*|fragestell\w*|anfrag\w*stell\w*',previous,re.I))
    if person_comparison:party_comparison=False
    display_scope=query
    if not topic and not presentation:
        display_scope=next((s for s in reversed(contexts) if re.search(r'grafik|grafisch|diagramm|visualisier|heatmap|balken|tabelle|tabellarisch',s,re.I)),query)
        chart_requested=bool(re.search(r'grafik|grafisch|diagramm|visualisier|heatmap|balken',display_scope,re.I))
    if table_requested and not chart_requested:chart_requested=False
    return {'scope_query':neutral,'previous_request':previous if refers else None,
            'party_filter':sorted(parties),'unsupported_time':unsupported,'question':query,
            'party_comparison':party_comparison,'person_comparison':person_comparison,'chart_requested':chart_requested,
            'chart_type':'bars' if re.search(r'balken|säulen|saeulen',display_scope,re.I) else 'heatmap',
            'normalization':'absolute' if re.search(r'absolut',display_scope,re.I) else 'within_party',
            'full_list':any(re.search(r'liste|auflist|alle[rn]?\s+anfragen',s,re.I) for s in contexts)}


def topic_packet(catalog):
    return [{k:d[k] for k in ('id','number','sha256','title','excerpt','parties','kind')} for d in catalog]


def compact_catalog(catalog,budget=CATALOG_BUDGET):
    """Keep every document and full title; reduce only classification excerpts."""
    for width in (1800,1200,800,400,160,0):
        candidate=[dict(row,excerpt=row['excerpt'][:width]) for row in catalog]
        size=len(json.dumps(topic_packet(candidate),ensure_ascii=False))
        if size<=budget:
            return candidate,{'characters':size,'excerpt_limit':width,'documents':len(candidate)}
    return [],{'unsupported':'Der Katalog ist auch ohne Kopfauszüge für einen Durchlauf zu groß. Bitte Fraktion, Jahr oder Wahlperiode eingrenzen.'}


def inventory(agent,plan):
    if plan['unsupported_time']: return {'unsupported':'Der gewünschte Zeitbereich ist für diese Auswertung noch nicht eindeutig unterstützt.'},[]
    report=count_parties(agent,plan['scope_query'])
    if report.get('unsupported'): return report,[]
    rows=report['documents']
    if plan['party_filter']:
        rows=[r for r in rows if set(r['parties']).intersection(plan['party_filter'])]
    catalog=[]
    with agent.db() as db:
        for row in rows:
            text=db.execute('SELECT text FROM pages WHERE doc=? AND page=1',(row['doc'],)).fetchone()[0]
            collapsed=re.sub(r'\s+',' ',re.sub(r'(\w)-\s*\n\s*(\w)',r'\1\2',text)).strip()
            subject=re.search(r'\bBetr\.?\s*:\s*(.*?)(?:Einleitung|Vor diesem Hintergrund|$)',collapsed,re.I)
            title=subject[1].strip()[:650] if subject else row['path'].removesuffix('.pdf').replace('_',' ')
            catalog.append(dict(row,id='D'+str(len(catalog)+1),title=title,excerpt=collapsed[:1800]))
    catalog,context=compact_catalog(catalog)
    if context.get('unsupported'): return context,[]
    report['catalog_context']=context
    return report,catalog


def validate_topics(result,catalog):
    allowed={d['id'] for d in catalog};seen=set();groups=[]
    themes=result.get('themes') if isinstance(result,dict) else None
    if not isinstance(themes,list) or not 1<=len(themes)<=8: raise TopicValidationError('invalid_groups')
    names=set()
    for group in themes:
        if not isinstance(group,dict): raise TopicValidationError('invalid_group')
        name=group.get('name');ids=group.get('documents')
        if not isinstance(name,str) or not 3<=len(name.strip())<=100 or re.search(r'[\[\]<>\n|]',name): raise TopicValidationError('invalid_name')
        if name.casefold() in names or not isinstance(ids,list) or not ids: raise TopicValidationError('invalid_membership')
        names.add(name.casefold())
        for source in ids:
            if not isinstance(source,str) or source not in allowed or source in seen: raise TopicValidationError('unknown_or_duplicate_source')
            seen.add(source)
        groups.append({'name':name.strip(),'documents':ids,'count':len(ids)})
    if seen!=allowed: raise TopicValidationError('incomplete_inventory')
    return sorted(groups,key=lambda g:(-g['count'],g['name']))


def format_topics(groups,catalog,report,plan,public_url):
    by_id={d['id']:d for d in catalog}
    kind=report.get('kind') or 'Kleinen und Großen Anfragen'
    scope=f"{len(catalog)} lokal eingelesenen Anfragedrucksachen ({kind})"
    if plan['party_filter']: scope+=' der Fraktion(en) '+', '.join(plan['party_filter'])
    periods=sorted({r['term'] for r in catalog})
    scope+=' aus Wahlperiode '+', '.join(map(str,periods))
    if report.get('year'): scope+=f", veröffentlicht {report['year']}"
    top=groups[0]
    leaders=[g for g in groups if g['count']==top['count']]
    answer=f"Ich beziehe mich auf die **{scope}**. Als Schwerpunkt werte ich hier die **Häufigkeit im Bestand**; daraus folgt keine politische Wichtigkeitsrangfolge.\n\n"
    if len(leaders)==1:
        answer+=f"Am stärksten vertreten ist **{top['name']}** mit **{top['count']} von {len(catalog)} Anfragen**. "
    else:
        answer+='Die häufigsten Schwerpunkte sind **'+'**, **'.join(g['name'] for g in leaders)+'** mit jeweils '+str(top['count'])+' Anfragen. '
    answer+='Die Einordnung ergibt folgende Übersicht:\n\n'
    answer+='| Thematischer Schwerpunkt | Anfragen | Beispiele aus den Dokumenttiteln |\n|---|---:|---|\n'
    for group in groups:
        examples='; '.join(by_id[s]['title'].replace('|','/').replace('\n',' ').replace('[',r'\[').replace(']',r'\]').replace('<','&lt;').replace('>','&gt;')[:145] for s in group['documents'][:2])
        answer+=f"| {group['name']} | {group['count']} | {examples} |\n"
    answer+='\n**Einordnung:** Die Themengruppen sind eine KI-gestützte Auswertung der Originaltitel und der ersten Seitenabschnitte, keine fertige Statistik aus einer einzelnen PDF. Jede Anfrage ist genau einem Hauptthema zugeordnet; die Anzahlen wurden aus diesen Zuordnungen gezählt. Andere Gruppierungen sind möglich.\n\n'
    answer+='**Vollständige Liste nach Themen** (Quellen jeweils PDF-Seite 1):\n' if plan.get('full_list') else '**Zuordnung und Quellen** (jeweils PDF-Seite 1):\n'
    for group in groups:
        if plan.get('full_list'):
            answer+=f"\n### {group['name']} ({group['count']})\n"
            for source in sorted(group['documents'],key=lambda s:tuple(int(n) for n in by_id[s]['number'].split('/'))):
                item=by_id[source]
                title=item['title'].replace('[',r'\[').replace(']',r'\]').replace('<','&lt;').replace('>','&gt;').replace('\n',' ')
                answer+=f"\n- [{item['number']} – {title}]({public_url}/documents/{quote(item['doc'],safe='')}#page=1)"
            answer+='\n'
        else:
            links=', '.join(f"[{by_id[s]['number']}]({public_url}/documents/{quote(by_id[s]['doc'],safe='')}#page=1)" for s in group['documents'])
            answer+=f"\n- **{group['name']}:** {links}"
    if report.get('excluded'): answer+=f"\n\n{len(report['excluded'])} weitere aktive Dateien waren für die Anfragen-Auswertung nicht geeignet."
    answer+='\n\nDie Auswertung beschreibt ausschließlich die aktuell eingelesenen und für diese Auswahl erkannten Anfragedrucksachen, nicht sämtliche Vorgänge der Parlamentsdatenbank.'
    return answer


def run_topics(bridge,query,history):
    plan=topic_request(query,history)
    if plan is None: return None
    if plan.get('clarification'): return plan['clarification'],{'plan':plan,'provider_called':False}
    report,catalog=inventory(bridge.agent,plan)
    audit={'plan':plan,'scope':{k:v for k,v in report.items() if k!='documents'},'provider_called':False}
    if report.get('unsupported'): return report['unsupported'],audit
    if not catalog: return 'Für den angesprochenen Teil des lokalen Bestands liegen keine aktuell geprüften Anfragedokumente vor.',audit
    packet=topic_packet(catalog)
    fingerprint=hashlib.sha256(json.dumps([POLICY,packet],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    with bridge.agent.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS corpus_topic_cache(fingerprint TEXT PRIMARY KEY,result TEXT,created REAL)')
        row=db.execute('SELECT result FROM corpus_topic_cache WHERE fingerprint=?',(fingerprint,)).fetchone()
    if row:
        model_result=json.loads(row[0]);audit['cache_hit']=True
    else:
        model_result,usage,version=bridge.generate_topics(packet)
        audit.update(provider_called=True,usage=usage,modelVersion=version,cache_hit=False)
        validate_topics(model_result,catalog)
        with bridge.agent.db() as db:
            db.execute('INSERT OR REPLACE INTO corpus_topic_cache VALUES(?,?,?)',(fingerprint,json.dumps(model_result,ensure_ascii=False),time.time()))
    groups=validate_topics(model_result,catalog)
    audit.update(groups=groups,inventory=[{k:d[k] for k in ('id','number','doc','sha256','kind','parties')} for d in catalog],fingerprint=fingerprint)
    if plan.get('party_comparison') or plan.get('person_comparison'):
        from topic_charts import format_party_topics
        answer,view_audit=format_party_topics(bridge.agent,groups,catalog,report,plan,bridge.public_url)
        audit.update(view_audit)
        return answer,audit
    answer=format_topics(groups,catalog,report,plan,bridge.public_url)
    if plan.get('chart_requested'):
        from topic_charts import format_overview_chart
        chart,view_audit=format_overview_chart(bridge.agent,groups,catalog,report,plan,bridge.public_url)
        answer=chart+'\n\n'+answer
        audit.update(view_audit)
    return answer,audit
