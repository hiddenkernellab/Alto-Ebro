#!/usr/bin/env python3
"""Alto Ebro: captador experimental de aforos publicados en tablas públicas.

NO es un servicio de alertas ni mide caudales en presas.
Las tablas no proporcionan aquí una hora individual verificable: la hora de
consulta NO equivale a hora de medición. Conservamos esa distinción.
"""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / 'data' / 'saih.json'
STATIONS = ('A026', 'A279', 'A093', 'A092', 'A283', 'A161')
PAGES = {
    'H1': '/tiempo-real/mapa-aforos-H1-alto-ebro-mi',
    'H15': '/tiempo-real/mapa-aforos-H15-alto-ebro-md',
}
# Hosts alternativos de la web oficial; las fichas individuales fallaron.
HOSTS = ('www.saihebro.com', 'internet.saihebro.com')
FLOW_RX = re.compile(r'(?<![\d.,])([0-9][\d.,]*)\s*m\s*[³3]\s*/\s*s', re.I)
CODE_RX = re.compile(r'\bA\d{3}\b')

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._row, self._cell = [], None, None
        self._depth = 0
        self.all_text = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'svg'):
            self.skip += 1
        if self.skip:
            return
        if tag == 'tr':
            self._row = []
        elif tag in ('td', 'th') and self._row is not None:
            if self._depth == 0:
                self._cell = []
            self._depth += 1

    def handle_data(self, data):
        if self.skip:
            return
        txt = data.strip()
        if txt:
            self.all_text.append(txt)
        if txt and self._cell is not None:
            self._cell.append(txt)

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'svg') and self.skip:
            self.skip -= 1
        if self.skip:
            return
        if tag in ('td', 'th') and self._depth:
            self._depth -= 1
            if self._depth == 0 and self._row is not None:
                self._row.append(' '.join(self._cell or []))
                self._cell = None
        elif tag == 'tr' and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def number(text):
    raw = text.replace(' ', '')
    if ',' in raw:
        raw = raw.replace('.', '').replace(',', '.')
    else:
        if raw.count('.') > 1:
            return None
    try:
        q = float(raw)
    except ValueError:
        return None
    return round(q, 3) if 0 <= q < 1_000_000 else None


def extract_flows(html):
    parser = TableParser()
    parser.feed(html)
    found = {}
    # Las filas de los mapas contienen código, nombre, nivel y caudal.
    # Solo extraer magnitudes m³/s: NUNCA confundir altura en m con caudal.
    for cells in parser.rows:
        if len(cells) < 3:
            continue
        code = next((m.group(0) for c in cells[:2] if (m := CODE_RX.search(c))), None)
        if code not in STATIONS:
            continue
        # La última celda correspondiente a 'Caudal' tiene la unidad m³/s.
        # Omitir las primeras celdas que contienen código/nombre/nivel.
        matches = [m for cell in cells[2:] for m in FLOW_RX.finditer(cell)]
        if matches:
            q = number(matches[-1].group(1))
            if q is not None:
                found[code] = q

    if found:
        return found

    # Plan B ante tablas implementadas en otros elementos HTML, pero se exige
    # el mismo orden visual entre códigos y una unidad explícita m³/s.
    texts = ' '.join(parser.all_text)
    codes = list(CODE_RX.finditer(texts))
    for i, match in enumerate(codes):
        code = match.group(0)
        if code not in STATIONS:
            continue
        end = codes[i + 1].start() if i + 1 < len(codes) else min(len(texts), match.end() + 400)
        segment = texts[match.end():min(end, match.end() + 400)]
        m = FLOW_RX.search(segment)
        if m:
            q = number(m.group(1))
            if q is not None:
                found[code] = q
    return found


def download(page):
    attempts = []
    for host in HOSTS:
        url = f'https://{host}{page}'
        try:
            req = Request(url, headers={
                'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/123.0 Safari/537.36',
                'Accept':'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8',
                'Accept-Language':'es-ES,es;q=0.9',
                'Cache-Control':'no-cache',
            })
            with urlopen(req, timeout=12) as res:
                if res.status != 200:
                    raise RuntimeError('HTTP '+str(res.status))
                html = res.read(2_500_000).decode('utf-8', errors='replace')
            values = extract_flows(html)
            if not values:
                raise RuntimeError('La página no contiene caudales reconocibles')
            return {'url':url, 'values':values, 'issues':attempts}
        except Exception as exc:
            attempts.append(f'{host}: {str(exc)[:130]}')
    return {'url':None, 'values':{}, 'issues':attempts}


def merge_history(previous, station, new_flow, checked_at):
    # Las series son FOTOGRAFÍAS DE LA TABLA tomadas al consultar, no medidas
    # originales de la estación. No inventar interpolación entre consultas.
    now = datetime.fromisoformat(checked_at)
    min_date = now - timedelta(days=90)
    points = {}
    for point in previous or []:
        try:
            d = datetime.fromisoformat(point['date'])
            q = float(point['flow'])
            if d.tzinfo and min_date <= d <= now and 0 <= q < 1_000_000:
                points[d.isoformat()] = {'date': d.isoformat(), 'flow':q, 'source_type':point.get('source_type','legacy_unknown')}
        except (ValueError, TypeError, KeyError):
            continue
    if new_flow is not None:
        points[checked_at] = {'date':checked_at,'flow':new_flow,'source_type':'snapshot_table_no_station_timestamp'}
    return sorted(points.values(), key=lambda p:p['date'])[-10000:]


def main():
    try:
        previous = json.loads(TARGET.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}

    checked_at = datetime.now(timezone.utc).isoformat()
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {k: pool.submit(download, p) for k,p in PAGES.items()}
        results = {k: fut.result() for k,fut in jobs.items()}

    # No combinar ni SUMAR valores de estaciones anidadas.
    # Coincidencia A026 en ambas páginas: tomar primera lectura y advertir
    # si no coincide; son snapshots tomados en momentos diferentes.
    values, station_pages, notices = {}, {}, []
    for group in ('H1','H15'):
        response = results[group]
        for code, q in response['values'].items():
            if code in values and abs(values[code] - q) > 0.001:
                notices.append(f'{code}: dos tablas discrepan ({values[code]} frente a {q}); sin reconciliación automática')
                continue
            values.setdefault(code,q)
            station_pages.setdefault(code,response['url'])

    oldhist = previous.get('history') or {}
    historical, readings = {}, {}
    for station in STATIONS:
        val = values.get(station)
        if val is None:
            readings[station] = {'status':'sin_datos','stale':True,'flow':None,'date':'',
                                 'observation_time_verified':False}
        else:
            readings[station] = {'status':'publicado_sin_hora','stale':True,
                 'flow':val, 'observed_flow':val, 'date':'',
                 'checked_at':checked_at,'source_url':station_pages[station],
                 'observation_time_verified':False}
        historical[station] = merge_history(oldhist.get(station,[]), station, val, checked_at)

    errors = {group:' | '.join(info['issues']) if info['issues'] else '' for group,info in results.items() if not info['values']}
    rec = {
        'version':3,
        'retrieved_at':checked_at,
        'source':'SAIH Ebro, tabla pública Alto Ebro (margen izquierda/derecha)',
        'source_pages': {k:v['url'] for k,v in results.items()},
        'readings': readings,
        'history': historical,
        'errors':errors,
        'fresh_count':0,  # ninguna lectura tiene hora original contrastada
        'published_count':len(values),
        'notes':notices,
        'note':'Valores de tablas públicas sin fecha de observación verificada. Las series son capturas fechadas al consultar, NO mediciones quinceminutales oficiales. NO uso operativo.'
    }
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    tmp = TARGET.with_suffix('.tmp')
    tmp.write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    tmp.replace(TARGET)
    for grp, result in results.items():
        print(f"{grp}: {len(result['values'])} estaciones obtenidas | fuente: {result['url']} | problemas: {result['issues']}")
    for st,val in values.items():
        print(f'{st}: {val} m³/s (hora individual de medida NO verificada)')
    if errors:
        print('ATENCIÓN: consultas sin datos: '+json.dumps(errors,ensure_ascii=False),file=sys.stderr)
    if not values:
        print('SIN MEDICIONES: revisar conectividad/estructura de las tablas.', file=sys.stderr)
    return 0  # publicar estado incluso si falla red, para inspección del JSON

if __name__ == '__main__':
    raise SystemExit(main())
