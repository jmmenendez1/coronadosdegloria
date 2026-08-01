#!/usr/bin/env python3
"""
Otra Coronación de Gloria — scraper.

Busca en Google News RSS noticias de argentinos/as saliendo 1°, 2° o 3° en
competencias mundiales. Sin dependencias externas (stdlib solamente).

Modos:
  python3 scraper/scrape.py                 # modo normal: ventana 3 días, actualiza data/
  python3 scraper/scrape.py --dry-run --days 30   # calibración: no toca estado, escribe candidates.json

Salidas (modo normal):
  data/podios.json    — feed público de eventos confirmados (lo lee la landing)
  data/seen.json      — estado de deduplicación
  new_events.json     — SOLO si hay eventos nuevos de alta confianza (dispara el email)
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
PODIOS_PATH = os.path.join(DATA_DIR, "podios.json")
SEEN_PATH = os.path.join(DATA_DIR, "seen.json")
NEW_EVENTS_PATH = os.path.join(ROOT, "new_events.json")
CANDIDATES_PATH = os.path.join(ROOT, "candidates.json")

UA = "Mozilla/5.0 (compatible; OtraCoronacionDeGloria/1.0; +https://github.com/jmmenendez1/otra-coronacion-de-gloria)"

# ---------------------------------------------------------------- queries ---

QUERIES_ES = [
    '"campeón mundial" argentino',
    '"campeona mundial" argentina',
    '"campeón del mundo" argentino',
    '"campeona del mundo" argentina',
    '"campeones del mundo" argentinos',
    'argentino "se consagró campeón" mundial',
    'argentina "se consagró campeona" mundial',
    '"subcampeón mundial" argentino',
    '"subcampeona mundial" argentina',
    'argentino "tercer puesto" mundial',
    'argentino "medalla de oro" mundial',
    'argentino "medalla de plata" mundial',
    'argentino "medalla de bronce" mundial',
    'argentina "medalla" "olimpiada internacional"',
    'argentino campeón "mundial de"',
    'argentino ganó "el mundial de"',
]
QUERIES_EN = [
    'argentine "world champion"',
    'argentinian wins "world championship"',
    'argentina "world title" wins',
    'argentine "bronze" OR "silver" "world championship"',
]
QUERIES = QUERIES_ES + QUERIES_EN

# ------------------------------------------------------------- normalizing --

def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def norm(s: str) -> str:
    s = strip_accents(s.lower())
    s = re.sub(r"[^a-z0-9ñ\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

STOPWORDS = set("""
de del la las el los un una unos unas y o en a al con por para que se su sus es fue son
tras ante como mas muy este esta estos estas ese esa hoy ayer the of in at on and or to for
a las los una del ante entre sobre desde hasta año años vez tambien también
""".split())

def sig_tokens(s: str):
    return {t for t in norm(s).split() if len(t) >= 4 and t not in STOPWORDS}

# --------------------------------------------------------------- patterns ---
# Todo se evalúa sobre texto normalizado (minúsculas, sin acentos).

ARG = r"(argentin\w+|albiceleste|los pumas|las leonas|los gladiadores|las panteras|los murcielagos)"
WORLD = r"(mundial\w*|del mundo|world|olimpiada internacional|olimpiada iberoamericana|international olympiad|planetari\w+)"

# Verbos de logro (conjugaciones frecuentes en titulares)
AV = r"(se consagr\w+|se coron\w+|se proclam\w+|conquist\w+|gan(?:o|aron)|logr(?:o|aron)|obtuv(?:o|ieron)|consigui(?:o|eron)|se qued(?:o|aron) con|se llev(?:o|aron))"
# "campeon" sin que sea sub/vice campeón
CHAMP = r"(?<!sub)(?<!vice)campeon"

# (regex, medalla) — patrones de PODIO logrado (no futuro). El ORDEN importa:
# plata/bronce primero para que "subcampeón" no matchee como "campeón".
PODIUM_PATTERNS = [
    # --- plata ---
    (rf"(subcampeon\w*|vicecampeon\w*).{{0,45}}{WORLD}", "plata"),
    (rf"{WORLD}.{{0,35}}(subcampeon\w*|vicecampeon\w*)", "plata"),
    (rf"runner.?up.{{0,40}}world", "plata"),
    (rf"(segundo puesto|segundo lugar|segunda posicion).{{0,50}}{WORLD}", "plata"),
    (rf"medalla\w* de plata.{{0,60}}{WORLD}", "plata"),
    (rf"{AV} la plata.{{0,60}}{WORLD}", "plata"),
    (rf"\bla plata (mundial|en el mundial\w*|del mundial\w*)", "plata"),
    # --- bronce ---
    (rf"(tercer puesto|tercer lugar|tercera posicion).{{0,50}}{WORLD}", "bronce"),
    (rf"{WORLD}.{{0,40}}(tercer puesto|tercer lugar)", "bronce"),
    (rf"medalla\w* de bronce.{{0,60}}{WORLD}", "bronce"),
    (rf"{AV} el bronce.{{0,60}}{WORLD}", "bronce"),
    (rf"\bel bronce (mundial|en el mundial\w*|del mundial\w*)", "bronce"),
    (rf"bronce para .{{0,40}}{WORLD}", "bronce"),
    # --- oro ---
    (rf"{CHAMP}\w* (mundial|del mundo)", "oro"),
    (rf"{CHAMP}\w* .{{0,30}}\bmundial\b", "oro"),
    (rf"{AV}.{{0,45}}(el )?(titulo )?(mundial\w*|del mundo)", "oro"),
    (rf"{AV} el (titulo|campeonato|mundial)", "oro"),
    (rf"(mundial\w*|del mundo).{{0,45}}(se consagr\w+|se coron\w+|conquist\w+|gan(?:o|aron)|{CHAMP}\w*)", "oro"),
    (rf"medalla\w* de oro.{{0,60}}{WORLD}", "oro"),
    (rf"{AV} el oro.{{0,60}}{WORLD}", "oro"),
    (rf"\bel oro (mundial|en el mundial\w*|del mundial\w*)", "oro"),
    (rf"world champion", "oro"),
    (rf"(wins?|won|clinch\w*|crowned|captur\w*|claim\w*|tak\w*|took).{{0,40}}world (title|championship|cup|crown)", "oro"),
    (rf"world (title|championship|cup).{{0,30}}(win|won|victory|champion)", "oro"),
    # --- genéricos ---
    (rf"{WORLD}.{{0,50}}medalla\w* de (oro|plata|bronce)", "medalla"),
    (rf"medalla\w* (de (oro|plata|bronce) )?en la olimpiada", "medalla"),
    (rf"(gold|silver|bronze) (medal )?at .{{0,30}}world", "medalla"),
    (rf"(subio al|se subio al|se subieron al) podio.{{0,45}}{WORLD}", "podio"),
    (rf"{WORLD}.{{0,35}}podio para", "podio"),
]
PODIUM_RE = [(re.compile(p), m) for p, m in PODIUM_PATTERNS]
ARG_RE = re.compile(rf"\b{ARG}\b")

# Rechazo duro: previa / futuro / historia / ruido
HARD_EXCLUDE = [
    r"\b(buscara|buscaran|ira por|iran por|va por|van por|va en busca|suena con|suenan con|aspira|quiere ser|puede (ser|salir)|podria\w?|podrian|podra|podran|intentara|jugara|jugaran|definira|definiran|enfrentara|enfrentaran|se mide|se miden|se enfrenta|chocara)\b",
    r"\b(donde ver|como ver|a que hora|hora y tv|en vivo|en directo|minuto a minuto|formaciones|posibles formaciones|fixture|calendario|sorteo|entradas|cuanto (sale|cuesta))\b",
    r"\b(previa|palpita|antesala|expectativa por|se prepara|se alista|rumbo al|de cara al|clasifico|clasificaron|clasifica)\b",
    r"\b(a \d+ anos|anos despues|aniversario|efemerides|se cumplen|recuerd\w+|recordo|homenaje\w*|murio|fallecio|fallecimiento|adios a|luto)\b",
    r"\b(apuestas|cuotas|pronostico\w*|simulador|videojuego|fifa \d+|quiniela)\b",
    r"\b(ranking|encuesta|segun la ia|inteligencia artificial elige|los mejores de la historia)\b",
    r"\b(semifinal\w*|cuartos de final|octavos de final|fase de grupos|debut\w*)\b",
    r"\b(horoscopo|receta|estreno|serie|pelicula|documental|trailer)\b",
    r"\b(visito|visita a|de visita|fue recibido|recibio a|agasaj\w+|caravana|desfil\w+|festej\w+ con los hinchas)\b",
    r"\b(sudamericano|panamericano|latinoamericano|continental)\b(?!.*\b(mundial|del mundo|world)\b)",
]
HARD_RE = [re.compile(p) for p in HARD_EXCLUDE]

# Rechazo blando: si matchea, el evento queda con confianza media (página sí, email no)
SOFT_EXCLUDE = [
    r"\b(serian|seria|casi|cerca de|a un paso)\b",
    r"\b(ex campeon|excampeon|leyenda|retiro|se retira)\b",
    r"\b(juvenil|sub ?\d+|cadete)\b",  # títulos juveniles: válidos pero verificar
]
SOFT_RE = [re.compile(p) for p in SOFT_EXCLUDE]

# ----------------------------------------------------------------- fetch ----

def fetch_rss(query: str, days: int):
    q = f"{query} when:{days}d"
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "es-419", "gl": "AR", "ceid": "AR:es-419"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def parse_items(xml_bytes: bytes):
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return items
    for item in root.iter("item"):
        def txt(tag):
            el = item.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""
        title = txt("title")
        link = txt("link")
        pub = txt("pubDate")
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None and src_el.text else ""
        desc = txt("description")
        desc = re.sub(r"<[^>]+>", " ", desc)
        try:
            dt = parsedate_to_datetime(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            dt = None
        items.append({"title": title, "link": link, "source": source, "desc": desc, "dt": dt})
    return items

# ---------------------------------------------------------------- classify --

def classify(title: str, desc: str):
    """Devuelve (verdict, medal, reasons). verdict: accept | soft | reject"""
    # Titulares interrogativos nunca son confirmaciones ("¿Argentina campeón?")
    if "?" in title or "¿" in title:
        return "reject", None, ["interrogative-title"]
    nt = norm(title)
    nd = norm(desc)[:400]
    reasons = []

    medal = None
    for rx, m in PODIUM_RE:
        if rx.search(nt):
            medal = m
            reasons.append(f"podium:title:{rx.pattern[:40]}")
            break
    title_hit = medal is not None
    if not medal:
        for rx, m in PODIUM_RE:
            if rx.search(nd):
                medal = m
                reasons.append(f"podium:desc:{rx.pattern[:40]}")
                break
    if not medal:
        return "reject", None, ["no-podium-pattern"]

    if not (ARG_RE.search(nt) or ARG_RE.search(nd)):
        return "reject", None, ["no-arg-marker"]

    for rx in HARD_RE:
        if rx.search(nt):
            return "reject", None, [f"hard:{rx.pattern[:50]}"]

    soft = [rx.pattern[:40] for rx in SOFT_RE if rx.search(nt)]
    if soft:
        return "soft", medal, [f"soft:{s}" for s in soft]
    if not title_hit:
        return "soft", medal, reasons + ["podium-only-in-desc"]
    return "accept", medal, reasons

# ------------------------------------------------------------------ dedup ---

def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

def event_id(title: str) -> str:
    return hashlib.sha1(" ".join(sorted(sig_tokens(title))).encode()).hexdigest()[:12]

# ------------------------------------------------------------------- main ---

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=None)
    args = ap.parse_args()
    days = args.days or (30 if args.dry_run else 3)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    raw = []
    for q in QUERIES:
        try:
            items = parse_items(fetch_rss(q, days))
            raw.extend(items)
            print(f"[q] {q!r}: {len(items)} items", file=sys.stderr)
        except Exception as e:
            print(f"[q] {q!r}: ERROR {e}", file=sys.stderr)
        time.sleep(1.2)

    # de-dup exacto por título normalizado
    by_title = {}
    for it in raw:
        key = norm(it["title"])
        if key and key not in by_title:
            by_title[key] = it
    print(f"[i] {len(raw)} items, {len(by_title)} únicos", file=sys.stderr)

    candidates = []
    for it in by_title.values():
        if it["dt"] and it["dt"] < cutoff:
            continue
        verdict, medal, reasons = classify(it["title"], it["desc"])
        if verdict == "reject" and reasons == ["no-podium-pattern"]:
            continue  # ni vale la pena listarlo
        candidates.append({
            "title": it["title"], "source": it["source"], "url": it["link"],
            "date": (it["dt"] or now).date().isoformat(),
            "verdict": verdict, "medal": medal, "reasons": reasons,
        })

    if args.dry_run:
        candidates.sort(key=lambda c: (c["verdict"] != "accept", c["date"]), reverse=False)
        with open(CANDIDATES_PATH, "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False, indent=2)
        acc = sum(1 for c in candidates if c["verdict"] == "accept")
        soft = sum(1 for c in candidates if c["verdict"] == "soft")
        rej = sum(1 for c in candidates if c["verdict"] == "reject")
        print(f"[dry-run] accept={acc} soft={soft} reject(listado)={rej} → candidates.json")
        return

    # ---- modo normal: estado + salida ----
    seen = {}
    if os.path.exists(SEEN_PATH):
        with open(SEEN_PATH, encoding="utf-8") as f:
            seen = json.load(f)
    podios = []
    if os.path.exists(PODIOS_PATH):
        with open(PODIOS_PATH, encoding="utf-8") as f:
            podios = json.load(f)

    recent_seen = {
        k: v for k, v in seen.items()
        if v.get("date", "1970-01-01") >= (now - timedelta(days=21)).date().isoformat()
    }
    recent_token_sets = [(k, set(v.get("tokens", []))) for k, v in recent_seen.items()]

    new_events = []
    for c in [c for c in candidates if c["verdict"] == "accept"]:
        toks = sig_tokens(c["title"])
        eid = event_id(c["title"])
        if eid in seen:
            continue
        dup_of = None
        for k, ts in recent_token_sets:
            if jaccard(toks, ts) >= 0.45:
                dup_of = k
                break
        if dup_of:
            seen[eid] = {"date": c["date"], "tokens": sorted(toks), "dup_of": dup_of, "title": c["title"]}
            continue
        # dedup entre los nuevos de esta corrida
        merged = False
        for ev in new_events:
            if jaccard(toks, set(ev["_tokens"])) >= 0.45:
                merged = True
                break
        if merged:
            seen[eid] = {"date": c["date"], "tokens": sorted(toks), "dup_of": "same-run", "title": c["title"]}
            continue
        ev = {
            "id": eid, "date": c["date"], "title": c["title"], "source": c["source"],
            "url": c["url"], "medal": c["medal"] or "podio", "_tokens": sorted(toks),
        }
        new_events.append(ev)
        seen[eid] = {"date": c["date"], "tokens": sorted(toks), "title": c["title"]}
        recent_token_sets.append((eid, toks))

    for ev in new_events:
        ev.pop("_tokens", None)
    if new_events:
        podios = new_events + podios
        with open(PODIOS_PATH, "w", encoding="utf-8") as f:
            json.dump(podios, f, ensure_ascii=False, indent=2)
        with open(NEW_EVENTS_PATH, "w", encoding="utf-8") as f:
            json.dump(new_events, f, ensure_ascii=False, indent=2)
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)

    print(f"[done] nuevos eventos: {len(new_events)}")
    for ev in new_events:
        print(f"  🏅 {ev['medal']}: {ev['title']}")

if __name__ == "__main__":
    main()
