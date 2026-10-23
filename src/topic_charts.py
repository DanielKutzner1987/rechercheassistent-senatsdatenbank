"""Deterministic party/topic matrices and local SVG/CSV/source views; stdlib only."""
import csv
import hashlib
import html
import io
import json
import re
import textwrap
import time
from urllib.parse import quote, urlsplit

VERSION='party-topics-chart-v2'
UNKNOWN='Zuordnung offen'


def party_matrix(groups,catalog):
    by_id={row['id']:row for row in catalog}
    seen=set();parties={}
    for column,group in enumerate(groups):
        for source in group['documents']:
            if source not in by_id or source in seen:
                raise ValueError('invalid_chart_membership')
            seen.add(source)
            item=by_id[source]
            for party in sorted(set(item['parties'])) or [UNKNOWN]:
                row=parties.setdefault(party,{'party':party,'total':0,'counts':[0]*len(groups),'sources':[[] for _ in groups]})
                row['total']+=1;row['counts'][column]+=1
                row['sources'][column].append({'doc':item['doc'],'number':item['number'],'title':item['title']})
    if seen!=set(by_id): raise ValueError('incomplete_chart_inventory')
    rows=sorted(parties.values(),key=lambda r:(r['party']==UNKNOWN,-r['total'],r['party']))
    for row in rows:
        row['percentages']=[round(100*n/row['total'],1) for n in row['counts']]
    return {'version':VERSION,'kind':'party_topics','documents':len(catalog),'topics':[g['name'] for g in groups],
            'rows':rows,'unknown_documents':sum(not d['parties'] for d in catalog),
            'joint_documents':sum(len(set(d['parties']))>1 for d in catalog)}


def person_matrix(groups,catalog):
    converted=[dict(item,parties=sorted({p['name'] for p in item.get('people',[])})) for item in catalog]
    matrix=party_matrix(groups,converted)
    matrix['kind']='person_topics'
    for row in matrix['rows']:
        row['affiliations']=sorted({p['party'] for item in catalog for p in item.get('people',[])
                                   if p['name']==row['party'] and p.get('party')})
    return matrix


def group_label(matrix):
    return 'Person' if matrix.get('kind')=='person_topics' else 'Gruppe' if matrix.get('kind')=='topic_overview' else 'Fraktion'


def svg_chart(matrix,scope):
    if matrix.get('chart_type')=='bars':return svg_bars(matrix,scope)
    esc=html.escape
    overall=matrix.get('kind')=='topic_overview'
    person=matrix.get('kind')=='person_topics'
    label=group_label(matrix)
    left=365 if person else 245;top=215;cell_width=185;cell_height=86 if person else 65
    width=max(1300,left+cell_width*len(matrix['topics'])+35)
    cell_width=(width-left-35)/len(matrix['topics'])
    height=top+cell_height*len(matrix['rows'])+125
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">Themenschwerpunkte nach {label}</title>',
        f'<desc id="desc">Jede Zelle enthält Anzahl und Anteil der Anfragen je {label}. Quellen und Werte sind in der verlinkten Datentabelle enthalten.</desc>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        '<g font-family="Arial, sans-serif" fill="#172033">',
        '<text x="28" y="37" font-size="25" font-weight="700">'+('Themenschwerpunkte im Bestand' if overall else 'Themen der Anfragen nach '+label)+'</text>',
        f'<text x="28" y="65" font-size="17">{esc(scope)}</text>',
        '<text x="28" y="92" font-size="16">Farbintensität: '+('absolute Anzahl' if matrix.get('normalization')=='absolute' else 'Anteil am Bestand' if overall else 'Anteil je '+label)+'. Zelle: Anzahl · Prozent.</text>']
    for column,name in enumerate(matrix['topics']):
        for line,text in enumerate(textwrap.wrap(name,23)[:5]):
            parts.append(f'<text x="{left+column*cell_width+cell_width/2}" y="{125+line*19}" font-size="15" text-anchor="middle">{esc(text)}</text>')
    for index,row in enumerate(matrix['rows']):
        y=top+index*cell_height
        for line,name in enumerate(textwrap.wrap(row['party'],34) if person else [row['party']]):
            parts.append(f'<text x="28" y="{y+20+line*17}" font-size="16" font-weight="700">{esc(name)}</text>')
        detail=f'{row["total"]} Anfragen'+(' · '+', '.join(row['affiliations']) if person and row.get('affiliations') else '')
        parts.append(f'<text x="28" y="{y+cell_height-9}" font-size="13" fill="#526176">{esc(detail)}</text>')
        for column,(count,percent) in enumerate(zip(row['counts'],row['percentages'])):
            fraction=count/max(max(r['counts']) for r in matrix['rows']) if matrix.get('normalization')=='absolute' else percent/100
            color='#f3f5f8' if count==0 else '#'+''.join(f'{round(a+(b-a)*fraction):02x}' for a,b in zip((239,246,255),(29,78,216)))
            x=left+column*cell_width
            text_color='#ffffff' if fraction>=0.6 else '#172033'
            parts.append(f'<rect x="{x+3}" y="{y+3}" width="{cell_width-6}" height="{cell_height-6}" rx="6" fill="{color}"><title>{esc(row["party"])}: {esc(matrix["topics"][column])}; {count} von {row["total"]} ({percent:.1f} %)</title></rect>')
            parts.append(f'<text x="{x+cell_width/2}" y="{y+39}" fill="{text_color}" font-size="19" text-anchor="middle">{count} · {percent:.1f} %</text>')
    foot=top+cell_height*len(matrix['rows'])+31
    notes=['Nur der lokal eingelesene, thematisch ausgewählte Bestand; keine repräsentative Parteienanalyse.',
           'Kleine Fallzahlen sind nur eingeschränkt vergleichbar. Themengruppen: KI-gestützte Titel-/Kopfauszug-Auswertung.',
           'Jede Anfrage zählt in genau einem Hauptthema.' if overall else 'Gemeinsame Anfragen zählen je genannter Person; unklare oder rein kollektive Urheberschaft bleibt separat.' if person else 'Gemeinsame Anfragen zählen je beteiligter Fraktion; unbekannte Zuordnungen bleiben separat.']
    for i,note in enumerate(notes):parts.append(f'<text x="28" y="{foot+i*23}" font-size="14" fill="#526176">{esc(note)}</text>')
    parts.append('</g></svg>')
    return ''.join(parts)


def svg_bars(matrix,scope):
    esc=html.escape
    colors=['#2563eb','#0f766e','#9333ea','#c2410c','#be185d','#475569','#4d7c0f','#0369a1']
    person=matrix.get('kind')=='person_topics'
    width=1520 if person else 1400;left=365 if person else 245;span=1050;top=145;row_height=86 if person else 70
    height=top+len(matrix['rows'])*row_height+100+len(matrix['topics'])*28
    absolute=matrix.get('normalization')=='absolute'
    denominator=max(r['total'] for r in matrix['rows']) if absolute else None
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        '<title>Themenschwerpunkte als Balkendiagramm</title>',f'<rect width="{width}" height="{height}" fill="white"/>',
        '<g font-family="Arial, sans-serif" fill="#172033"><text x="28" y="38" font-size="25" font-weight="700">Themenschwerpunkte der Anfragen</text>',
        f'<text x="28" y="68" font-size="17">{esc(scope)}</text>',
        '<text x="28" y="96" font-size="16">'+('Balkenlänge: absolute Zahl der Anfragen.' if absolute else 'Balkenlänge: 100 % der Anfragen je '+group_label(matrix)+'.')+'</text>']
    for i,row in enumerate(matrix['rows']):
        y=top+i*row_height
        for line,name in enumerate(textwrap.wrap(row['party'],34) if person else [row['party']]):
            parts.append(f'<text x="28" y="{y+20+line*17}" font-size="16" font-weight="700">{esc(name)}</text>')
        parts.append(f'<text x="28" y="{y+row_height-9}" font-size="14">{row["total"]} Anfragen</text>')
        x=float(left)
        for j,(count,percent) in enumerate(zip(row['counts'],row['percentages'])):
            segment=span*count/(denominator if absolute else row['total'])
            if count:
                parts.append(f'<rect x="{x}" y="{y+4}" width="{segment}" height="45" fill="{colors[j%len(colors)]}"><title>{esc(row["party"])}: {esc(matrix["topics"][j])}; {count} ({percent:.1f} %)</title></rect>')
                if segment>48:parts.append(f'<text x="{x+segment/2}" y="{y+33}" font-size="17" fill="white" text-anchor="middle">{count}</text>')
            x+=segment
    y=top+len(matrix['rows'])*row_height+30
    for j,topic in enumerate(matrix['topics']):
        parts.append(f'<rect x="28" y="{y+j*28-14}" width="16" height="16" fill="{colors[j%len(colors)]}"/><text x="55" y="{y+j*28}" font-size="16">{esc(topic)}</text>')
    parts.append(f'<text x="28" y="{height-25}" font-size="14">Lokaler thematisch ausgewählter Bestand; kleine Fallzahlen beachten. Keine repräsentative Parteienanalyse.</text></g></svg>')
    return ''.join(parts)


def csv_data(matrix):
    def safe(value):
        text=str(value)
        return "'"+text if text.startswith(('=','+','-','@')) else text
    buffer=io.StringIO(newline='')
    writer=csv.writer(buffer,delimiter=';')
    writer.writerow([group_label(matrix),'Anfragen der Gruppe','Themenschwerpunkt','Anzahl','Anteil in Prozent'])
    for row in matrix['rows']:
        for topic,count,percent in zip(matrix['topics'],row['counts'],row['percentages']):
            writer.writerow([safe(row['party']),row['total'],safe(topic),count,f'{percent:.1f}'])
    return buffer.getvalue()


def source_page(matrix,svg,public_url):
    esc=html.escape
    overall=matrix.get('kind')=='topic_overview'
    parts=['<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>Fraktionen und Themen – Daten und Quellen</title><style>body{font-family:Arial,sans-serif;color:#172033;margin:24px}svg{max-width:100%;height:auto}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #ccd4df}li{margin:8px 0}a{color:#174ea6}</style>',
        svg,'<h1>Daten und Quellen</h1><p>'+('Anteile beziehen sich auf die ausgewählten Anfragen. Jede Anfrage zählt einmal.' if overall else 'Anteile beziehen sich auf die Anfragen derselben '+group_label(matrix)+'. Gemeinsame Anfragen zählen einmal je genannter '+group_label(matrix)+'. Fehlende oder rein kollektive Zuordnungen werden nicht geraten.')+'</p>',
        '<table><tr><th>'+group_label(matrix)+'</th><th>Anfragen</th>']
    parts.extend('<th>'+esc(t)+'</th>' for t in matrix['topics']);parts.append('</tr>')
    for row in matrix['rows']:
        parts.append(f'<tr><th>{esc(row["party"])}</th><td>{row["total"]}</td>')
        parts.extend(f'<td>{n} ({p:.1f} %)</td>' for n,p in zip(row['counts'],row['percentages']));parts.append('</tr>')
    parts.append('</table>')
    for row in matrix['rows']:
        parts.append('<h2>'+esc(row['party'])+'</h2>')
        for topic,documents in zip(matrix['topics'],row['sources']):
            if not documents:continue
            parts.append('<h3>'+esc(topic)+'</h3><ul>')
            for doc in documents:
                url=public_url+'/documents/'+quote(doc['doc'],safe='')+'#page=1'
                parts.append('<li><a href="'+esc(url,quote=True)+'">'+esc(doc['number']+' – '+doc['title'])+'</a></li>')
            parts.append('</ul>')
    parts.append('</html>')
    return ''.join(parts)


def store_chart(agent,matrix,scope,public_url):
    packet=json.dumps([matrix,scope,public_url],ensure_ascii=False,sort_keys=True)
    identifier=hashlib.sha256(packet.encode()).hexdigest()
    svg=svg_chart(matrix,scope)
    page=source_page(matrix,svg,public_url)
    with agent.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS topic_visualizations(id TEXT PRIMARY KEY,svg TEXT,html TEXT,csv TEXT,created REAL)')
        db.execute('INSERT OR IGNORE INTO topic_visualizations VALUES(?,?,?,?,?)',(identifier,svg,page,csv_data(matrix),time.time()))
    return identifier


def format_party_topics(agent,groups,catalog,report,plan,public_url):
    person=plan.get('person_comparison',False)
    matrix=person_matrix(groups,catalog) if person else party_matrix(groups,catalog)
    label=group_label(matrix)
    matrix.update(chart_type=plan.get('chart_type','heatmap'),normalization=plan.get('normalization','within_party'))
    scope=f'{len(catalog)} lokal eingelesene Anfragedrucksachen'
    if report.get('year'):scope+=f' aus {report["year"]}'
    if plan.get('party_filter'):scope+='; '+', '.join(plan['party_filter'])
    identifier=store_chart(agent,matrix,scope,public_url)
    base=public_url+'/topic-visualizations/'+identifier
    answer=f'Hier ist die **Verteilung der Themenschwerpunkte nach {label}** für **{scope}**. '
    answer+=(f'Dargestellt werden **absolute Anzahlen** und zusätzlich die Anteile je {label}. ' if plan.get('normalization')=='absolute' else f'Die Anteile beziehen sich auf die **eigenen Anfragen derselben {label}**. ')
    answer+='Die unterschiedliche Zahl eingelesener Anfragen wird jeweils angegeben.\n\n'
    if plan.get('chart_requested'):
        answer+=f'![Themenschwerpunkte der Anfragen nach {label}]({base}.svg)\n\n'
    if plan.get('chart_requested'):
        answer+=f'| {label} laut Dokumentkopf | Anfragen | Häufigste Themen |\n|---|---:|---|\n'
        for row in matrix['rows']:
            maximum=max(row['counts'])
            top=[f'{name} ({count}; {percent:.1f} %)' for name,count,percent in zip(matrix['topics'],row['counts'],row['percentages']) if count==maximum]
            answer+=f'| {row["party"]} | {row["total"]} | '+ '; '.join(top)+' |\n'
    else:
        answer+='| '+label+' | Anfragen | '+' | '.join(matrix['topics'])+' |\n|---|---:|'+ '|'.join('---:' for _ in matrix['topics'])+'|\n'
        for row in matrix['rows']:
            answer+=f'| {row["party"]} | {row["total"]} | '+' | '.join(f'{n} ({p:.1f} %)' for n,p in zip(row['counts'],row['percentages']))+' |\n'
    answer+='\n'+(f'[Grafik öffnen / als SVG speichern]({base}.svg) · ' if plan.get('chart_requested') else '')+f'[Zahlen als CSV]({base}.csv) · [Vollständige Tabelle und PDF-Quellen]({base}.html)\n\n'
    answer+='**Einordnung:** Das ist ein thematisch ausgewählter lokaler Bestand, keine repräsentative Analyse politischer Prioritäten. Bei sehr wenigen Dokumenten sind Prozentwerte besonders vorsichtig zu interpretieren. Die Themenzuordnung stammt aus Titeln und Kopfauszügen; gezählt wurde lokal. '
    if matrix['joint_documents']:answer+=f'{matrix["joint_documents"]} gemeinsame Anfrage(n) zählen einmal je genannter {label}. '
    if matrix['unknown_documents']:answer+=f'{matrix["unknown_documents"]} Anfrage(n) bleiben unter „{UNKNOWN}“, weil keine eindeutige '+('Personenzuordnung vorliegt oder nur eine Fraktion als Urheber genannt ist. ' if person else 'Fraktionszuordnung vorliegt. ')
    if person:answer+='Namen und damalige Fraktion sind aus dem jeweiligen Dokumentkopf gespeichert; namensgleiche Personen werden nicht extern identifiziert. '
    return answer,{'view':('person' if person else 'party')+('_topic_chart' if plan.get('chart_requested') else '_topic_comparison'),
                   'visualization_id':identifier,'matrix':matrix}


def format_overview_chart(agent,groups,catalog,report,plan,public_url):
    by_id={r['id']:r for r in catalog}
    counts=[len(g['documents']) for g in groups]
    total=len(catalog)
    matrix={'version':VERSION,'kind':'topic_overview','documents':total,'topics':[g['name'] for g in groups],
            'unknown_documents':0,'joint_documents':0,'chart_type':plan.get('chart_type','heatmap'),
            'normalization':plan.get('normalization','within_party'),
            'rows':[{'party':'Gesamtbestand','total':total,'counts':counts,'percentages':[round(n*100/total,1) for n in counts],
                     'sources':[[{k:by_id[s][k] for k in ('doc','number','title')} for s in g['documents']] for g in groups]}]}
    scope=f'{total} lokal eingelesene Anfragen'+(f' aus {report["year"]}' if report.get('year') else '')
    identifier=store_chart(agent,matrix,scope,public_url)
    base=public_url+'/topic-visualizations/'+identifier
    return f'![Themenschwerpunkte der Anfragen]({base}.svg)\n\n[Grafik öffnen]({base}.svg) · [Zahlen als CSV]({base}.csv) · [Daten und Quellen]({base}.html)',{'view':'topic_chart','visualization_id':identifier,'matrix':matrix}


def serve_chart(handler,agent):
    path=urlsplit(handler.path).path
    if not path.startswith('/topic-visualizations/'):return False
    match=re.fullmatch(r'/topic-visualizations/([0-9a-f]{64})\.(svg|html|csv)',path)
    row=None
    if match:
        with agent.db() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='topic_visualizations'").fetchone():
                row=db.execute('SELECT svg,html,csv FROM topic_visualizations WHERE id=?',(match[1],)).fetchone()
    if not row:
        handler.send_error(404);return True
    extension=match[2]
    body=row[extension].encode('utf-8-sig' if extension=='csv' else 'utf-8')
    handler.send_response(200)
    handler.send_header('Content-Type',{'svg':'image/svg+xml','html':'text/html','csv':'text/csv'}[extension]+'; charset=utf-8')
    handler.send_header('Content-Length',str(len(body)))
    handler.send_header('X-Content-Type-Options','nosniff')
    handler.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'")
    handler.send_header('Cache-Control','private, max-age=86400')
    if extension=='csv':handler.send_header('Content-Disposition','attachment; filename="fraktionen-themen.csv"')
    handler.end_headers();handler.wfile.write(body)
    return True
