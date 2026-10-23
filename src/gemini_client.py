"""Gemini transport, credential validation and redacted provider errors."""
import getpass
import json
import time
import re
import html
import sys
import os
from urllib import request, error

MODEL=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

def request_json(req,timeout):
    """Only retry explicit provider unavailability, never billing/auth/quota errors."""
    for attempt in range(3):
        try:
            with request.urlopen(req,timeout=timeout) as response:
                return json.load(response)
        except error.HTTPError as exc:
            if exc.code!=503 or attempt==2:
                raise
            exc.close()
            delay=(10,20)[attempt]
            print(f'Google ist ausgelastet (503). Neuer Versuch in {delay} Sekunden ({attempt+2}/3).',flush=True)
            time.sleep(delay)


def read_secret(from_stdin=False):
    if from_stdin:
        return sys.stdin.read().rstrip('\r\n')
    return getpass.getpass('Gemini API-Schlüssel (Eingabe unsichtbar, wird nicht gespeichert): ')


def prepare_key(raw):
    """Reject malformed HTTP credentials before sending anything to Google.

    getpass on legacy Windows consoles can capture Ctrl+V as a literal control
    character. Remove that paste marker, but never guess missing key content.
    Accept both standard and newer authorization key formats.
    """
    key=raw.replace('\x16','').strip()
    if len(key)>1 and key[0]==key[-1] and key[0] in ('"',"'"):
        key=key[1:-1].strip()
    valid=(key.startswith(('AIza','AQ.')) and bool(re.fullmatch(r'[A-Za-z0-9._~+/=\-]+',key))
           and len(key)>=20)
    info={'characters':len(key),'paste_marker_removed':'\x16' in raw,
          'format':'authorization' if key.startswith('AQ.') else 'standard' if key.startswith('AIza') else 'other',
          'valid_http_token':valid}
    return key,info


def safe_api_error(body, key, code):
    """Keep actionable provider fields, never headers or arbitrary metadata."""
    try:
        problem=json.loads(body.lstrip('\ufeff')).get('error',{})
        message=str(problem.get('message','Keine Fehlerbeschreibung erhalten.'))
        status=str(problem.get('status','UNKNOWN'))
    except (ValueError,AttributeError,TypeError):
        # HTML/plain-text errors can identify a proxy or gateway. Preserve only
        # readable, bounded text, never executable markup or request headers.
        message=re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>',' ',body,flags=re.I|re.S)
        message=html.unescape(re.sub(r'<[^>]+>',' ',message))
        message=re.sub(r'\s+',' ',message).strip() or 'Leere Fehlerantwort.'
        status='NON_JSON_RESPONSE'
    if key:
        message=message.replace(key,'[SCHLÜSSEL ENTFERNT]')
        status=status.replace(key,'[SCHLÜSSEL ENTFERNT]')
    message=re.sub(r'AIza[A-Za-z0-9_-]+','[SCHLÜSSEL ENTFERNT]',message)
    message=re.sub(r'(?i)(?:authorization\s*:\s*bearer|x-goog-api-key\s*[:=])\s*\S+',
                   '[ZUGANGSDATEN ENTFERNT]',message)
    message=re.sub(r'[\x00-\x08\x0b-\x1f\x7f]','',message)
    return {'code':code,'status':status[:100],'message':message[:2000]}
