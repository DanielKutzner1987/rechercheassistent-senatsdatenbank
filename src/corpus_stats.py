"""Count local inquiry documents from source headers, not retrieved chunks or LLM guesses."""
from collections import Counter
from datetime import date
import hashlib
import json
import re
from urllib.parse import quote

PARTIES={'cdu':'CDU','spd':'SPD','afd':'AfD','fdp':'FDP','die linke':'Die Linke',
         'linke':'Die Linke','grüne':'GRÜNE','bündnis 90/die grünen':'GRÜNE',
         'bündnis 90/die grüne':'GRÜNE','bündnis 90/die grünen/grüne':'GRÜNE',
         'fraktionslos':'fraktionslos'}


def is_party_statistics(query):
    text=query.casefold()
    return (bool(re.search(r'partei|fraktion',text))
        and bool(re.search(r'meist|anzahl|wie viele|verteil|aufteil|häufig|vergleich',text))
        and bool(re.search(r'anfrag|drucksach|druckfrag|dokument|bestand|unterlag',text)))


def parse_header(text):
    text=re.sub(r'\s+',' ',text).strip()
    # Parse only the actual document heading and author block, never quoted parties in prose.
    match=re.search(r'\bDrucksache\s+(\d{1,2})\s*/\s*(\d+)\b',text[:700],re.I)
    subject=re.search(r'\bBetr\s*\.?\s*:',text,re.I)
    heading=text[:min(subject.start() if subject else 1600,1600)]
    kind=re.search(r'\b(Schriftliche Kleine Anfrage|Große Anfrage)\b',heading,re.I)
    if not match or not kind: return None
    header=text[kind.end():]
    boundary=re.search(r'\bBetr\s*\.?\s*:|\bund Antwort des Senats\b|\bEinleitung\b',header,re.I)
    header=header[:boundary.start()] if boundary else header[:600]
    author=re.search(r'\b(?:der|des)\s+(?:Abgeordneten|Fraktion(?:en)?)\b',header,re.I)
    parties=[]
    if author:
        for raw in re.findall(r'\(([^()]{1,80})\)',header[author.start():]):
            normalized=re.sub(r'\s+',' ',raw).strip().casefold()
            if normalized in PARTIES: parties.append(PARTIES[normalized])
        # Explicit faction authors without a parenthesized party, e.g. "der Fraktionen SPD und GRÜNE".
        if re.match(r'\b(?:der|des)\s+Fraktion',header[author.start():],re.I):
            for name,label in PARTIES.items():
                if re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',header[author.start():].casefold()): parties.append(label)
    issued=None
    for raw in re.findall(r'\b\d{1,2}\.\s*\d{1,2}\.\s*\d{2,4}\b',text[:kind.start()]):
        day,month,year=[int(n) for n in re.findall(r'\d+',raw)]
        try: issued=date(year+2000 if year<100 else year,month,day).isoformat();break
        except ValueError: pass
    return {'number':f'{int(match[1])}/{int(match[2])}','term':int(match[1]),
            'kind':'Große Anfrage' if kind[1].lower().startswith('große') else 'Schriftliche Kleine Anfrage',
            'parties':sorted(set(parties)),'issued':issued,'evidence':header[:700]}


def count_parties(agent,query):
    from document_metadata import refresh_metadata
    refresh_metadata(agent)
    term_match=re.search(r'\b(\d{1,2})\.?\s*Wahlperiode|Wahlperiode\s*(\d{1,2})\b',query,re.I)
    requested_term=int(next(v for v in term_match.groups() if v)) if term_match else None
    requested_years=set(re.findall(r'\b(?:19|20)\d{2}\b',query))
    years={int(y) for y in requested_years}
    if len(years)>1 or re.search(r'\b(?:seit|bis|vor|nach|zwischen|quartal|monat)\b',query,re.I):
        return {'unsupported':'Zeitbereich bitte als einzelnes Jahr oder Wahlperiode angeben.'}
    # Topic-specific counts must not silently become counts of the entire corpus.
    if re.search(r'\b(?:zum Thema|zum Bereich|zum Verkehr|zur Hochbahn|zu|über|bezüglich|betreffend)\b',query,re.I):
        return {'unsupported':'Eine thematische Eingrenzung für Bestandsstatistiken ist noch nicht zuverlässig verfügbar.'}
    with agent.db() as db:
        rows=db.execute('''SELECT d.id,d.path,d.sha256,p.text,p.status,m.metadata FROM documents d
            LEFT JOIN pages p ON p.doc=d.id AND p.page=1
            LEFT JOIN inquiry_metadata m ON m.doc=d.id WHERE d.active=1 ORDER BY d.path''').fetchall()
    records=[];excluded=[]
    for row in rows:
        source=(agent.folder/row['path']).resolve()
        if not source.is_relative_to(agent.folder) or not source.is_file():
            excluded.append({'path':row['path'],'reason':'source_missing'});continue
        with source.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=row['sha256']:
            excluded.append({'path':row['path'],'reason':'source_changed'});continue
        if not row['text'] or row['status'] not in ('ok','needs_table_review'):
            excluded.append({'path':row['path'],'reason':'header_unreadable'});continue
        parsed=json.loads(row['metadata']) if row['metadata'] else None
        if not parsed or not parsed.get('number') or parsed.get('kind') not in ('Schriftliche Kleine Anfrage','Große Anfrage'):
            excluded.append({'path':row['path'],'reason':'not_identified_as_inquiry'});continue
        records.append(dict(parsed,doc=row['id'],path=row['path'],sha256=row['sha256']))
    if re.search(r'aktuell\w*\s+Wahlperiode|dies\w*\s+Wahlperiode',query,re.I):
        requested_term=max((r['term'] for r in records),default=None)
    records=[r for r in records if (requested_term is None or r['term']==requested_term)
             and (not years or r['issued'] and int(r['issued'][:4]) in years)]
    kind=None
    if re.search(r'\bklein\w*\s+Anfrag',query,re.I): kind='Schriftliche Kleine Anfrage'
    if re.search(r'\bgroß\w*\s+Anfrag',query,re.I): kind='Große Anfrage'
    if kind: records=[r for r in records if r['kind']==kind]
    unique={};conflicts=set()
    for row in records:
        if row['number'] in unique and unique[row['number']]['parties']!=row['parties']: conflicts.add(row['number'])
        unique.setdefault(row['number'],row)
    for number in conflicts: unique[number]['parties']=[]
    for row in records:
        if row['number'] in unique and row.get('people')!=unique[row['number']].get('people'):
            unique[row['number']]['people']=[]
            unique[row['number']]['author_status']='conflicting_duplicate'
    duplicate_files=len(records)-len(unique)
    records=list(unique.values())
    counts=Counter(p for row in records for p in row['parties'])
    unknown=[r['number'] for r in records if not r['parties']]
    return {'scope':'active_local_corpus','active_files':len(rows),'term':requested_term,
        'year':next(iter(years)) if years else None,'documents':records,'total_inquiries':len(records),
        'counts':dict(sorted(counts.items(),key=lambda r:(-r[1],r[0]))),'unknown':unknown,
        'terms':sorted({r['term'] for r in records}),'kind':kind,
        'excluded':excluded,'joint':sum(len(r['parties'])>1 for r in records),'duplicate_files':duplicate_files}


def format_party_statistics(report,public_url):
    if report.get('unsupported'):
        return report['unsupported']+' Ich habe deshalb keine ungefilterte Statistik als Antwort ausgegeben.'
    scope='deinem aktuell eingelesenen lokalen Bestand'
    if report['term'] is not None: scope+=f" zur {report['term']}. Wahlperiode"
    if report['year'] is not None: scope+=f" mit Veröffentlichungsdatum im Jahr {report['year']}"
    counts=report['counts'];total=report['total_inquiries']
    if not total:
        return f'In {scope} konnte ich keine eindeutig erkennbaren Anfragedrucksachen für diese Auswertung erfassen. Daraus lässt sich keine Parteiverteilung ableiten.'
    if counts:
        maximum=max(counts.values());leaders=[p for p,n in counts.items() if n==maximum]
        lead=(f"**{leaders[0]}** ist mit **{maximum} Anfragedrucksachen** am häufigsten vertreten."
              if len(leaders)==1 else f"**{' und '.join(leaders)}** liegen mit jeweils **{maximum} Anfragedrucksachen** gleichauf.")
        answer=f'In {scope}: {lead}\n\n'
        answer+='| Fraktion laut Dokumentkopf | Anzahl |\n|---|---:|\n'
        answer+='\n'.join(f'| {p} | {n} |' for p,n in counts.items())
    else: answer=f'In {scope} sind Anfragedrucksachen vorhanden; ihre Fraktionszuordnung konnte ich aber nicht zuverlässig aus den Dokumentköpfen ermitteln.'
    label=report.get('kind') or 'Kleine und Große Anfragen'
    answer+=f'\n\nGezählt wurden **{total} unterschiedliche Anfragedrucksachen** ({label}).'
    if report.get('terms'):
        answer+=' Enthaltene Wahlperioden: '+', '.join(str(t) for t in report['terms'])+'.'
    answer+=' Mehrere Abgeordnete oder mehrere Dateien derselben Drucksache zählen nicht mehrfach. Gezählt wird die Fraktion der Fragesteller, nicht der antwortende Senat.'
    if report['unknown']:
        answer+=f" **{len(report['unknown'])} Anfragen sind keiner Fraktion eindeutig zugeordnet**; die Rangfolge gilt nur für die klar zugeordneten Anfragen."
    if report['joint']:
        answer+=' Gemeinsame Anfragen werden einmal je beteiligter Fraktion gezählt; die Summe der Fraktionszahlen kann deshalb höher als die Dokumentzahl sein.'
    answer+='\n\n**Das ist keine Gesamtstatistik der Hamburgischen Bürgerschaft.** Nicht heruntergeladene oder nicht eingelesene Drucksachen sind nicht enthalten.'
    if report['excluded']:
        answer+=f" {len(report['excluded'])} weitere aktive Dateien wurden nicht mitgezählt, etwa weil sie keine erkennbare Anfragedrucksache sind oder nicht aktuell geprüft werden konnten."
    answer+='\n\n**Belege der Auswertung – jeweils PDF-Seite 1:**\n'
    for row in report['documents']:
        answer+=f"\n- [{row['number']} · {', '.join(row['parties']) or 'Zuordnung offen'}]({public_url}/documents/{quote(row['doc'],safe='')}#page=1)"
    return answer
