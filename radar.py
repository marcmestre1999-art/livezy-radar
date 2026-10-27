"""Radar de tendencias de producto de salud para Livezy.

Uso:
  python radar.py tiendas     -> foto diaria de catálogo, precios, stock y ranking de ventas de las tiendas Shopify
  python radar.py tendencias  -> Google Trends (Arabia Saudí, Emiratos y mundial) de palabras.json
  python radar.py informe     -> escribe docs/informe.md (lo que lee Claude) y docs/index.html
  python radar.py todo        -> tiendas + informe (y tendencias si es lunes o jueves)
"""
import json, re, sqlite3, sys, time, html, datetime as dt
from collections import Counter, defaultdict
from pathlib import Path
import requests

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
BASE = Path(__file__).parent
DB = BASE / 'radar.db'
DOCS = BASE / 'docs'
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36',
     'Accept-Language': 'en-US,en;q=0.9'}
HOY = dt.date.today().isoformat()

# lo que no es producto (tarjetas regalo, seguros de envío, merchandising…)
RUIDO = re.compile(r"gift ?card|insurance|shipping protection|order protection|\broute\b|package protection|e-?gift|\bmerch|t-?shirt|"
                   r"\bhoodie|\bhat\b|\bcap\b|tote|shaker|frother|bottle opener|sticker|\bmug\b|cat toy|hidden|test\b|"
                   r"upsell|digital upgrade|cashback|e-?book|\bbook\b", re.I)
INGREDIENTES = [
    ('Creatina', r"creatin"), ('Magnesio', r"magnesi"), ('Colágeno', r"collagen"), ('Ashwagandha', r"ashwagandha"),
    ('Setas funcionales', r"lion.?s mane|reishi|cordyceps|chaga|turkey tail|mushroom"), ('Berberina', r"berberin"),
    ('Calostro', r"colostrum"), ('Electrolitos', r"electrolyt|hydration"), ('Probióticos / digestión', r"probiotic|prebiotic|\bgut\b|digest|bloat"),
    ('Shilajit', r"shilajit"), ('Musgo marino', r"sea ?moss"), ('NAD / NMN', r"\bnad\+?|\bnmn\b"), ('GLP-1 / saciedad', r"glp-?1|appetite|satiety"),
    ('Inositol', r"inositol"), ('Vinagre de manzana', r"apple cider|\bacv\b"), ('Pelo / biotina', r"biotin|\bhair\b"),
    ('Sueño / melatonina', r"sleep|melatonin"), ('Omega 3', r"omega|fish oil|krill"), ('Vitamina D', r"vitamin d|\bd3\b"),
    ('Hierro', r"\biron\b"), ('Zinc', r"\bzinc\b"), ('Proteína', r"protein"), ('Greens / superalimentos', r"greens|superfood|spirulina|chlorella"),
    ('Hígado de ternera / órganos', r"beef liver|organ"), ('L-teanina / foco', r"theanine|focus|nootropic|brain"),
    ('Energía / cafeína', r"energy|caffeine|pre-?workout"), ('Glutatión', r"glutathione"), ('Azafrán', r"saffron"),
    ('Menopausia / hormonas', r"menopaus|hormone|pms|cycle"), ('Prenatal / fertilidad', r"prenatal|fertility|conception"),
    ('Niños', r"\bkids?\b|children"), ('Piel / belleza', r"\bskin\b|glow|beauty"), ('Inmunidad', r"immun|elderberry|vitamin c"),
    ('Estrés / ánimo', r"stress|mood|calm|cortisol|anxiety"), ('Peso / metabolismo', r"weight|metabol|fat burn|slim"),
    ('Libido / hombre', r"libido|testosteron|men.?s health"), ('Articulaciones', r"joint|glucosamine|turmeric|curcumin"),
]
FORMATOS = [
    ('Gominolas', r"gumm|chews?\b|chewable"), ('Polvo', r"powder|scoop|\btub\b"), ('Cápsulas', r"capsul|softgel|caps\b"),
    ('Sticks / sobres', r"\bsticks?\b|sachet|packets?\b|travel pack"), ('Bebida / shot', r"\bdrink|\bshot\b|ready to drink|\bcan\b|beverage"),
    ('Comprimidos', r"tablet|\btabs?\b"), ('Parches', r"patch"), ('Gotas / líquido', r"drops|liquid|tincture|spray"),
    ('Tiras orales', r"strip"), ('Barritas / snacks', r"\bbar\b|bars\b|cookie|snack"),
]
INGREDIENTES = [(n, re.compile(p, re.I)) for n, p in INGREDIENTES]
FORMATOS = [(n, re.compile(p, re.I)) for n, p in FORMATOS]


def etiquetas(texto, lista):
    return [n for n, rx in lista if rx.search(texto)]


def get(url, intentos=4, ses=None, **kw):
    for i in range(intentos):
        try:
            r = (ses or requests).get(url, headers=None if ses else H, timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(30 * (i + 1)); continue
            return r
        except requests.RequestException:
            time.sleep(3)
    return None


def db():
    c = sqlite3.connect(DB)
    c.executescript("""
    create table if not exists productos(
      tienda text, pid text, handle text, titulo text, tipo text, tags text, url text, imagen text,
      creado text, publicado text, primera_vez text, ultima_vez text, primary key(tienda, pid));
    create table if not exists fotos(
      fecha text, tienda text, pid text, precio real, precio_tachado real, disponibles int, variantes int,
      ranking int, primary key(fecha, tienda, pid));
    create table if not exists tiendas_log(fecha text, tienda text, productos int, con_ranking int, error text,
      primary key(fecha, tienda));
    create table if not exists tendencias(palabra text, zona text, semana text, valor int, recogido text,
      primary key(palabra, zona, semana));
    create table if not exists relacionadas(fecha text, palabra text, zona text, consulta text, valor text,
      primary key(fecha, palabra, zona, consulta));
    create table if not exists tendencias_log(fecha text, palabra text, zona text, error text,
      primary key(fecha, palabra, zona));
    """)
    return c


# ---------- Tiendas Shopify ----------
def shopify_catalogo(d):
    out, page = [], 1
    while page <= 8:
        r = get(f"https://{d}/products.json?limit=250&page={page}")
        if r is None or not r.ok: break
        ps = r.json().get('products', [])
        if not ps: break
        out += ps; page += 1; time.sleep(1)
    filas = []
    for p in out:
        vs = p.get('variants') or [{}]
        precios = [float(v['price']) for v in vs if v.get('price')]
        tach = [float(v['compare_at_price']) for v in vs if v.get('compare_at_price')]
        img = (p.get('images') or [{}])[0]
        filas.append(dict(
            pid=str(p['id']), handle=p['handle'], titulo=p['title'], tipo=p.get('product_type') or '',
            tags=', '.join(p['tags']) if isinstance(p.get('tags'), list) else (p.get('tags') or ''),
            url=f"https://{d}/products/{p['handle']}", imagen=img.get('src', '') if isinstance(img, dict) else '',
            creado=(p.get('created_at') or '')[:10], publicado=(p.get('published_at') or '')[:10],
            precio=min(precios) if precios else None, precio_tachado=max(tach) if tach else None,
            disponibles=sum(1 for v in vs if v.get('available')), variantes=len(vs)))
    return [f for f in filas if not RUIDO.search(f"{f['titulo']} {f['tipo']}")]


def shopify_ranking(d, handles):
    """Orden de 'más vendidos' de /collections/all, sacado del HTML.
    Los productos enlazados desde el menú o el pie salen igual en el carrito y en una página de error: se descartan."""
    enlaces = lambda txt: list(dict.fromkeys(re.findall(r'/products/([A-Za-z0-9\-_%]+)', txt)))
    refs = [get(f"https://{d}{ruta}") for ruta in ('/cart', '/pages/radar-x404')]
    refs = [set(enlaces(r.text)) for r in refs if r is not None and r.status_code in (200, 404)]
    fijos = set.intersection(*refs) if refs else set()
    orden = []
    for page in range(1, 4):
        r = get(f"https://{d}/collections/all?sort_by=best-selling&page={page}")
        if r is None or not r.ok: break
        nuevos = [h for h in enlaces(r.text) if h not in fijos and h not in orden and h in handles]
        if not nuevos: break
        orden += nuevos; time.sleep(1)
    return {h: i + 1 for i, h in enumerate(orden)}


def tiendas(solo=None):
    c = db()
    lista = json.loads((BASE / 'tiendas.json').read_text(encoding='utf-8'))
    if solo: lista = [t for t in lista if t['dominio'] in solo]
    for t in lista:
        d = t['dominio']
        try:
            ps = shopify_catalogo(d)
            rk = shopify_ranking(d, {p['handle'] for p in ps}) if ps else {}
            for p in ps:
                c.execute("""insert into productos values(?,?,?,?,?,?,?,?,?,?,?,?)
                  on conflict(tienda,pid) do update set titulo=excluded.titulo, tipo=excluded.tipo, tags=excluded.tags,
                  url=excluded.url, imagen=excluded.imagen, publicado=excluded.publicado, ultima_vez=excluded.ultima_vez""",
                          (d, p['pid'], p['handle'], p['titulo'], p['tipo'], p['tags'], p['url'], p['imagen'],
                           p['creado'], p['publicado'], HOY, HOY))
                c.execute("insert or replace into fotos values(?,?,?,?,?,?,?,?)",
                          (HOY, d, p['pid'], p['precio'], p['precio_tachado'], p['disponibles'], p['variantes'],
                           rk.get(p['handle'])))
            c.execute("insert or replace into tiendas_log values(?,?,?,?,?)", (HOY, d, len(ps), len(rk), None))
            print(f"{d:28} {len(ps):4} productos  {len(rk):3} con ranking")
        except Exception as e:
            c.execute("insert or replace into tiendas_log values(?,?,?,?,?)", (HOY, d, 0, 0, repr(e)[:300]))
            print(f"{d:28} ERROR {e!r}")
        c.commit()
    c.close()


# ---------- Google Trends ----------
def _json(r):
    return json.loads(r.text[r.text.index('{'):])


def tendencias(solo=None):
    cfg = json.loads((BASE / 'palabras.json').read_text(encoding='utf-8'))
    c = db()
    ses = requests.Session(); ses.headers.update(H)
    ses.get('https://trends.google.com/trends/explore?geo=SA', timeout=30)  # cookie NID
    base = 'https://trends.google.com/trends/api'
    arabe = re.compile(r'[؀-ۿ]')
    for palabra in solo or cfg['palabras']:
        for zona in cfg['zonas']:
            if arabe.search(palabra) and zona == '': continue  # las palabras en árabe solo en el Golfo
            try:
                req = {"comparisonItem": [{"keyword": palabra, "geo": zona, "time": "today 5-y"}], "category": 0, "property": ""}
                r = get(f"{base}/explore", ses=ses, params={'hl': 'en-US', 'tz': '-180', 'req': json.dumps(req)})
                if r is None or not r.ok: raise RuntimeError(f"explore {getattr(r, 'status_code', 'sin respuesta')}")
                w = {x['id']: x for x in _json(r)['widgets']}
                ts = w['TIMESERIES']
                r = get(f"{base}/widgetdata/multiline", ses=ses,
                        params={'hl': 'en-US', 'tz': '-180', 'req': json.dumps(ts['request']), 'token': ts['token']})
                for p in _json(r)['default']['timelineData']:
                    if p.get('isPartial'): continue
                    semana = dt.datetime.fromtimestamp(int(p['time']), dt.timezone.utc).date().isoformat()
                    c.execute("insert or replace into tendencias values(?,?,?,?,?)", (palabra, zona, semana, p['value'][0], HOY))
                if zona in cfg['relacionadas_en'] and 'RELATED_QUERIES' in w:
                    time.sleep(2)
                    rq = w['RELATED_QUERIES']
                    r = get(f"{base}/widgetdata/relatedsearches", ses=ses,
                            params={'hl': 'en-US', 'tz': '-180', 'req': json.dumps(rq['request']), 'token': rq['token']})
                    listas = _json(r)['default']['rankedList']
                    for k in (listas[1]['rankedKeyword'] if len(listas) > 1 else [])[:15]:
                        c.execute("insert or replace into relacionadas values(?,?,?,?,?)",
                                  (HOY, palabra, zona, k['query'], k['formattedValue']))
                c.execute("insert or replace into tendencias_log values(?,?,?,?)", (HOY, palabra, zona, None))
                print(f"{palabra:28} {zona or 'mundo':5} ok")
            except Exception as e:
                c.execute("insert or replace into tendencias_log values(?,?,?,?)", (HOY, palabra, zona, repr(e)[:200]))
                print(f"{palabra:28} {zona or 'mundo':5} ERROR {e!r}")
            c.commit()
            time.sleep(5)
    c.close()


# ---------- Informe ----------
def pct(a, b):
    return None if not b else round((a - b) / b * 100)


def fmt_pct(v):
    return '—' if v is None else f"{v:+d} %"


def informe():
    c = db()
    q = lambda s, *a: c.execute(s, a).fetchall()
    fechas = [r[0] for r in q("select distinct fecha from fotos order by fecha")]
    if not fechas:
        print('Sin datos todavía'); return
    ultima = fechas[-1]
    hace7 = next((f for f in reversed(fechas) if f <= (dt.date.fromisoformat(ultima) - dt.timedelta(days=7)).isoformat()), fechas[0])
    hace90 = (dt.date.fromisoformat(ultima) - dt.timedelta(days=90)).isoformat()
    hace30 = (dt.date.fromisoformat(ultima) - dt.timedelta(days=30)).isoformat()
    cfg = json.loads((BASE / 'palabras.json').read_text(encoding='utf-8'))
    L = []
    L.append(f"# Radar de producto Livezy — {ultima}\n")
    n_t = q("select count(*), sum(productos) from tiendas_log where fecha=? and productos>0", ultima)[0]
    L.append(f"- Tiendas leídas hoy: **{n_t[0]}**, con {n_t[1] or 0} productos.")
    L.append(f"- Histórico de tiendas: **{len(fechas)} días** (desde {fechas[0]}). Comparación semanal contra {hace7}.")
    sem = q("select min(semana), max(semana), max(recogido) from tendencias")[0]
    if sem[0]:
        L.append(f"- Google Trends: semanas del {sem[0]} al {sem[1]} (actualizado {sem[2]}).")
    L.append("- Cómo leerlo: el «ranking» es el orden de «más vendidos» que muestra cada tienda (1 = el que más vende). "
             "Los lanzamientos usan la fecha de alta del producto en Shopify. Google Trends es interés relativo (0-100) "
             "de cada búsqueda por separado: sirve para ver la dirección, no para comparar volúmenes entre palabras.\n")

    actuales = q("""select p.tienda, p.pid, p.titulo, p.tipo, p.tags, p.url, p.creado, f.precio, f.ranking, f.disponibles
                    from fotos f join productos p using(tienda, pid) where f.fecha=?""", ultima)

    # 1. ingredientes y formatos en lo que se lanza
    L.append("## 1. Qué se está lanzando (últimos 90 días)\n")
    nuevos = [r for r in actuales if r[6] and r[6] >= hace90]
    ci, cf, ti = Counter(), Counter(), defaultdict(set)
    for r in nuevos:
        txt = f"{r[2]} {r[3]} {r[4]}"
        for n in etiquetas(txt, INGREDIENTES): ci[n] += 1; ti[n].add(r[0])
        for n in etiquetas(f"{r[2]} {r[3]}", FORMATOS): cf[n] += 1
    L.append(f"{len(nuevos)} productos nuevos en {len({r[0] for r in nuevos})} tiendas.\n")
    L.append("| Ingrediente / necesidad | Lanzamientos | Tiendas distintas |\n|---|---|---|")
    for n, k in ci.most_common(20): L.append(f"| {n} | {k} | {len(ti[n])} |")
    L.append("\n| Formato | Lanzamientos |\n|---|---|")
    for n, k in cf.most_common(): L.append(f"| {n} | {k} |")

    L.append("\n### Lanzamientos de los últimos 30 días\n")
    L.append("| Fecha | Tienda | Producto | Precio | Ranking |\n|---|---|---|---|---|")
    for r in sorted([r for r in actuales if r[6] and r[6] >= hace30], key=lambda r: r[6], reverse=True)[:60]:
        L.append(f"| {r[6]} | {r[0]} | [{r[2]}]({r[5]}) | {r[7] if r[7] is not None else '—'} | {r[8] or '—'} |")

    # 2. más vendidos y movimientos
    L.append("\n## 2. Más vendidos por tienda (top 5) y cambio de puesto en la semana\n")
    antes = {(t, pid): rk for t, pid, rk in q("select tienda, pid, ranking from fotos where fecha=? and ranking is not null", hace7)}
    por_t = defaultdict(list)
    for r in actuales:
        if r[8]: por_t[r[0]].append(r)
    for t in sorted(por_t):
        top = sorted(por_t[t], key=lambda r: r[8])[:5]
        cosas = []
        for r in top:
            a = antes.get((t, r[1]))
            mov = '' if hace7 == ultima else (' (nuevo en el top)' if a is None else (f' (↑{a - r[8]})' if a > r[8] else (f' (↓{r[8] - a})' if a < r[8] else '')))
            cosas.append(f"{r[8]}. [{r[2]}]({r[5]}){mov}")
        L.append(f"- **{t}**: " + ' · '.join(cosas))

    subidas = []
    for r in actuales:
        a = antes.get((r[0], r[1]))
        if r[8] and a and a - r[8] >= 3: subidas.append((a - r[8], r))
    if subidas:
        L.append("\n### Los que más suben en el ranking esta semana\n")
        for s, r in sorted(subidas, key=lambda x: -x[0])[:20]:
            L.append(f"- {r[0]}: [{r[2]}]({r[5]}) de {r[8] + s} a {r[8]}")

    # 3. cambios de la semana
    if hace7 != ultima:
        L.append(f"\n## 3. Cambios desde {hace7}\n")
        prev = {(t, pid): (pr, disp) for t, pid, pr, disp in q("select tienda, pid, precio, disponibles from fotos where fecha=?", hace7)}
        hoy_t = {r[0] for r in actuales}
        aparecidos = [r for r in actuales if (r[0], r[1]) not in prev and r[0] in {k[0] for k in prev}]
        retirados = q("""select p.tienda, p.titulo, p.url from fotos f join productos p using(tienda, pid)
                         where f.fecha=? and not exists(select 1 from fotos g where g.fecha=? and g.tienda=f.tienda and g.pid=f.pid)""",
                      hace7, ultima)
        retirados = [r for r in retirados if r[0] in hoy_t]
        precios = [(r, prev[(r[0], r[1])][0]) for r in actuales if (r[0], r[1]) in prev and r[7] and prev[(r[0], r[1])][0]
                   and abs(r[7] - prev[(r[0], r[1])][0]) >= 0.01]
        agotados = [r for r in actuales if (r[0], r[1]) in prev and prev[(r[0], r[1])][1] and not r[9]]
        repuestos = [r for r in actuales if (r[0], r[1]) in prev and not prev[(r[0], r[1])][1] and r[9]]
        for titulo, filas, f in (
                ('Aparecen en la tienda', aparecidos, lambda r: f"{r[0]}: [{r[2]}]({r[5]}) a {r[7]}"),
                ('Retirados', retirados, lambda r: f"{r[0]}: [{r[1]}]({r[2]})"),
                ('Cambios de precio', precios, lambda x: f"{x[0][0]}: [{x[0][2]}]({x[0][5]}) {x[1]} → {x[0][7]}"),
                ('Agotados (señal de que se vende o de que lo retiran)', agotados, lambda r: f"{r[0]}: [{r[2]}]({r[5]})"),
                ('Repuestos', repuestos, lambda r: f"{r[0]}: [{r[2]}]({r[5]})")):
            L.append(f"**{titulo}: {len(filas)}**\n")
            L += [f"- {f(x)}" for x in filas[:25]]
            L.append('')

    # 4. Google Trends
    if sem[0]:
        L.append("\n## 4. Google Trends: búsquedas que suben\n")
        L.append("Últimas 12 semanas frente a las 12 anteriores, y frente a las mismas 12 semanas del año pasado.\n")
        for zona, nombre in cfg['zonas'].items():
            filas = []
            for (palabra,) in q("select distinct palabra from tendencias where zona=?", zona):
                v = [x[0] for x in q("select valor from tendencias where palabra=? and zona=? order by semana", palabra, zona)]
                if len(v) < 64: continue
                ult, ant, año = sum(v[-12:]) / 12, sum(v[-24:-12]) / 12, sum(v[-64:-52]) / 12
                filas.append((pct(ult, ant), pct(ult, año), palabra, round(ult)))
            if not filas: continue
            L.append(f"### {nombre}\n\n| Búsqueda | Interés medio (0-100) | vs 12 semanas antes | vs hace un año |\n|---|---|---|---|")
            for t3, t12, palabra, u in sorted(filas, key=lambda x: (x[1] is None, -(x[1] or 0))):
                L.append(f"| {palabra} | {u} | {fmt_pct(t3)} | {fmt_pct(t12)} |")
            L.append('')
        fr = q("select max(fecha) from relacionadas")[0][0]
        if fr:
            L.append(f"### Búsquedas relacionadas en auge ({fr})\n")
            L.append("«Breakout» = ha crecido más de un 5000 %. Es lo más útil para descubrir productos nuevos.\n")
            for zona in cfg['relacionadas_en']:
                filas = q("select palabra, consulta, valor from relacionadas where fecha=? and zona=?", fr, zona)
                if not filas: continue
                L.append(f"**{cfg['zonas'][zona]}**\n")
                por = defaultdict(list)
                for p, cons, val in filas: por[p].append(f"{cons} ({val})")
                for p in por: L.append(f"- *{p}*: " + ', '.join(por[p][:8]))
                L.append('')

    errores = q("select tienda, error from tiendas_log where fecha=? and productos=0", ultima)
    if errores:
        L.append("\n## Tiendas que no se pudieron leer hoy\n")
        L += [f"- {t}: {e or 'sin productos'}" for t, e in errores]
    c.close()

    DOCS.mkdir(exist_ok=True)
    md = '\n'.join(L) + '\n'
    (DOCS / 'informe.md').write_text(md, encoding='utf-8')
    (DOCS / 'index.html').write_text(PAGINA.replace('{{MD}}', html.escape(md)).replace('{{FECHA}}', ultima), encoding='utf-8')
    print(f"Informe escrito: {len(md)} caracteres")


PAGINA = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Radar Livezy</title><meta name="robots" content="noindex">
<style>body{font:15px/1.55 system-ui,sans-serif;max-width:980px;margin:0 auto;padding:16px;color:#1d1d1f;background:#fff}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}td,th{border-bottom:1px solid #e5e5e5;padding:4px 6px;text-align:left}
h1{color:#AD182C}h2{margin-top:32px;border-bottom:2px solid #F9D4E4}a{color:#AD182C}.w{overflow-x:auto}</style>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script></head>
<body><div id="c">Cargando…</div><pre id="md" hidden>{{MD}}</pre>
<script>const t=document.getElementById('md').textContent;document.getElementById('c').innerHTML=window.marked?marked.parse(t).replace(/<table>/g,'<div class="w"><table>').replace(/<\\/table>/g,'</table></div>'):'<pre>'+t+'</pre>';</script>
</body></html>"""


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'todo'
    resto = sys.argv[2:] or None
    if cmd in ('tiendas', 'todo'): tiendas(resto if cmd == 'tiendas' else None)
    if cmd == 'tendencias' or (cmd == 'todo' and dt.date.today().weekday() in (0, 3)):
        tendencias(resto if cmd == 'tendencias' else None)
    if cmd in ('informe', 'todo'): informe()
