"""Create original, synthetic PDFs; no real politicians or company facts."""
from pathlib import Path

CASES = [
    ('23_90001_demo_busbestand.pdf', [
        'DEMO - Synthetische Testdaten, keine echte Drucksache.',
        'Drucksache 23/90001 - 01.04.2025',
        'Schriftliche Kleine Anfrage',
        'des Abgeordneten Beispiel (CDU) vom 01.04.2025',
        'Betr.: Busbestand der Musterverkehr AG und Beispielbus GmbH',
        'Antwort des Senats - vollstaendig erfundener Testtext.',
        'Am 01.04.2025 verfuegen beide Unternehmen zusammen ueber 120 Busse.',
        'Eine Aufteilung nach Unternehmen liegt nicht vor.',
        'Die Zahl 120 ist eine gemeinsame Gesamtzahl, keine Einzelzahl.',
    ]),
    ('23_90002_demo_personal.pdf', [
        'DEMO - Synthetische Testdaten, keine echte Drucksache.',
        'Drucksache 23/90002 - 01.04.2025',
        'Schriftliche Kleine Anfrage',
        'der Abgeordneten Beispiel (DIE LINKE) vom 01.04.2025',
        'Betr.: Personal der Musterverkehr AG',
        'Antwort des Senats - vollstaendig erfundener Testtext.',
        'Die Musterverkehr AG hat insgesamt 450 Beschaeftigte am 01.04.2025.',
        'Davon arbeiten 7 Beschaeftigte im Beschwerdemanagement.',
        'Die Zahl 7 bezeichnet eine Abteilung, nicht das gesamte Unternehmen.',
    ]),
    ('23_90003_demo_solar.pdf', [
        'DEMO - Synthetische Testdaten, keine echte Drucksache.',
        'Drucksache 23/90003 - 01.04.2025',
        'Schriftliche Kleine Anfrage',
        'des Abgeordneten Beispiel (CDU) vom 01.04.2025',
        'Betr.: Photovoltaik und Solarenergie auf oeffentlichen Gebaeuden',
        'Antwort des Senats - vollstaendig erfundener Testtext.',
        'Am 01.04.2025 sind 12 oeffentliche Gebaeude mit Photovoltaik ausgestattet.',
        'Eine Erweiterung auf 20 Gebaeude ist fuer 2027 geplant.',
        'Die Planung fuer 2027 belegt keine bereits erfolgte Umsetzung.',
    ]),
]


def pdf_bytes(lines):
    """Small deterministic one-page PDF with an extractable text layer."""
    def escaped(text):
        return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    commands = ['BT /F1 11 Tf 45 790 Td 20 TL']
    for number, line in enumerate(lines):
        if number:
            commands.append('T*')
        commands.append(f'({escaped(line)}) Tj')
    commands.append('ET')
    stream = '\n'.join(commands).encode('latin-1')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    result = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n')
    start = len(result)
    result.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        result.extend(f'{offset:010d} 00000 n \n'.encode())
    result.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
    return bytes(result)


def create_demo(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for name, lines in CASES:
        target = folder / name
        content = pdf_bytes(lines)
        if target.exists() and target.read_bytes() != content:
            raise ValueError(f'Refusing to overwrite changed file: {name}')
        target.write_bytes(content)
    print('Three synthetic demo PDFs created; no real documents or network access used.')
