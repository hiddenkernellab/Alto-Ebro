#!/usr/bin/env python3
"""Obtiene lecturas públicas de estaciones SAIH Ebro y conserva series observadas.
No es un sistema de alerta ni una fuente de decisiones operativas. Python 3.10+.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
OUT = BASE / 'data' / 'saih.json'
MADRID = ZoneInfo('Europe/Madrid')
STATIONS = {
  'A026': 'https://www.saihebro.com/tiempo-real/estacion-aforos-A026',
  'A279': 'https://www.saihebro.com/tiempo-real/estacion-aforos-A279-rudron-valdelateja',
  'A093': 'https://www.saihebro.com/tiempo-real/estacion-aforos-A093-oca-ona',
  'A092': 'https://www.saihebro.com/tiempo-real/estacion-aforos-A092-nela-trespaderne',
  'A283': 'https://www.saihebro.com/tiempo-real/estacion-aforos-A283-nela-villarcayo',
  'A161': 'https://www.saihebro.com/tiempo-real/estacion-aforos-A161-ebro-palazuelos',
}

class HtmlTables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside_row = False
        self.cell_level = 0
        self.cell = ''
        self.row = []
        self.rows = []
    def handle_starttag(self, tag, attrs):
        if tag == 'tr' and not self.inside_row:
            self.inside_row, self.row = True, []
        elif tag in ('th', 'td') and self.inside_row:
            self.cell_level += 1
            if self.cell_level == 1: self.cell = ''
    def handle_data(self, data):
        if self.inside_row and self.cell_level:
            self.cell += data + ' '
    def handle_endtag(self, tag):
        if tag in ('th', 'td') and self.inside_row and self.cell_level:
            self.cell_level -= 1
            if self.cell_level == 0: self.row.append(' '.join(self.cell.split()))
        elif tag == 'tr' and self.inside_row:
            if self.row: self.rows.append(self.row)
            self.inside_row = False

# A161 muestra 'Caudal Ebro ... (Rio)' y 'Caudal Total Ebro ... (Rio + Canal)'.
# Priorizamos solo el caudal del cauce, nunca el total combinado.
def parse_flow(html: str, code: str):
    p = HtmlTables(); p.feed(html)
    choices = []
    for row in p.rows:
        if len(row) != 4 or not re.search(r'\bcaudal\b', row[0], re.I):
            continue
        if code == 'A161' and re.search(r'\btotal\b', row[0], re.I):
            continue
        m = re.search(r'([-+]?\d[\d.,]*)\s*m\s*[³3]\s*/\s*s', row[1], re.I)
        d = re.search(r'\b(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})\b', row[2])
        if not m or not d: continue
        raw = m.group(1)
        if ',' in raw: raw = raw.replace('.', '').replace(',', '.')
        try:
            flow = float(raw)
            dt = datetime.strptime(d.group(1), '%d/%m/%Y %H:%M').replace(tzinfo=MADRID)
        except ValueError: continue
        if not 0 <= flow < 1_000_000: continue
        age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600
        # Lectura con marca temporal >2 h no se considera actual; nunca devolver como caudal vivo.
        fresh = -0.25 <= age <= 2.0
        choices.append({'flow': flow if fresh else None,
                        'observed_flow': flow,
                        'date': d.group(1), 'iso': dt.isoformat(),
                        'hours_old': round(age, 2),
                        'stale': not fresh, 'status':'ok' if fresh else 'antiguo_o_invalido'})
    if choices:
        return max(choices, key=lambda z:z['iso'])
    return {'flow': None, 'observed_flow': None, 'stale': True, 'status':'sin_datos', 'date':''}

def fetch_station(code: str, url: str):
    req = Request(url, headers={
      'User-Agent':'Mozilla/5.0 (compatible; AltoEbroPanel/0.3; publicly available data)',
      'Accept':'text/html,application/xhtml+xml;q=0.9,*/*;q=0.5',
      'Accept-Language':'es-ES,es;q=0.9',
    })
    with urlopen(req, timeout=18) as r:
        if r.status != 200: raise RuntimeError(f'HTTP {r.status}')
        return parse_flow(r.read(2_000_000).decode('utf-8', errors='replace'),code)

def save_archive(old, recent):
    now = datetime.now(timezone.utc)
    short = now - timedelta(days=7)
    long = now - timedelta(days=92)
    # Últimos 7 días: se conserva cada lectura; resto: una muestra por hora.
    samples = {}
    for p in old:
        try:
            t = datetime.fromisoformat(p['date']).astimezone(timezone.utc)
            q = float(p['flow'])
        except (TypeError, ValueError, KeyError): continue
        if long <= t <= now + timedelta(minutes=15) and 0 <= q < 1_000_000:
            key = p['date'] if t >= short else str(int(t.timestamp() // 3600))
            samples[key] = {'date':p['date'], 'flow':round(q, 3)}
    if recent.get('status') == 'ok':
        samples[recent['iso']] = {'date':recent['iso'], 'flow':round(recent['flow'],3)}
    return sorted(samples.values(), key=lambda p:p['date'])

def main():
    try: old = json.loads(OUT.read_text(encoding='utf-8'))
    except (OSError, ValueError): old = {}
    archive = old.get('history') or {}
    readings, errors = {}, {}
    for code, url in STATIONS.items():
        try:
            item = fetch_station(code,url)
            readings[code] = item
            print(f"{code}: {item.get('date', '?')} / {item.get('status', '?')} / {item.get('flow')}")
        except Exception as e:
            readings[code] = {'status':'error_consulta','stale':True,'flow':None,'date':''}
            errors[code] = str(e)[:180]
            print(f'{code}: ERROR {errors[code]}', file=sys.stderr)
        archive[code] = save_archive(archive.get(code,[]), readings[code])
    record = {'version':3, 'retrieved_at':datetime.now(timezone.utc).isoformat(),
              'source':'SAIH Ebro, lecturas de las fichas públicas',
              'sources':STATIONS, 'readings':readings, 'history':archive,
              'errors':errors, 'fresh_count':sum(x.get('status')=='ok' for x in readings.values()),
              'note':'Datos observados, no predicciones. No es una herramienta operativa.'}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUT.with_suffix('.tmp')
    temp.write_text(json.dumps(record,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    temp.replace(OUT)
    # El fallo total no invalida la web: publica estado con errores visibles para el usuario.
    if record['fresh_count'] == 0:
        print('AVISO: ninguna estación tiene lectura reciente verificable.',file=sys.stderr)
    return 0

if __name__ == '__main__': raise SystemExit(main())
