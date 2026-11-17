"""
Signal-Radar – Wächter (läuft alle 15 Minuten in GitHub Actions)
================================================================
Prüft offizielle, kostenlose Quellen und schickt bei NEUEN wichtigen Ereignissen eine Push-Nachricht (ntfy):
  1. SEC 8-K-Pflichtmeldungen der US-Watchlist (Übernahme, CEO-Wechsel, Insolvenz, Bilanzkorrektur …)
  2. Fed-Pressemitteilungen zur Geldpolitik
  3. Google-News-Schlagzeilen mit Alarmbegriffen (Handelsstopp, Insolvenz, SEC-Klage, Börsen-Hack …)
Gesehene Meldungen werden im Branch "waechter" gespeichert, damit nichts doppelt kommt.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

import engine as E

UA = os.environ.get("SEC_IDENTITY") or "Signal-Radar Bot signal-radar-bot@users.noreply.github.com"
APP_URL = "https://testblyed-png-signal-radar-app-2f1kx1.streamlit.app/"

# 8-K-Punkte: Bedeutung und Priorität (5 = dringend). Nicht gelistete Punkte (7.01, 8.01, 9.01 …) werden ignoriert.
ITEMS_8K = {
    "1.01": ("Wichtiger Vertrag abgeschlossen", 3),
    "1.02": ("Wichtiger Vertrag beendet", 3),
    "1.03": ("INSOLVENZ / Gläubigerschutz", 5),
    "1.05": ("Cyberangriff gemeldet", 4),
    "2.01": ("Übernahme/Verkauf abgeschlossen", 3),
    "2.02": ("Quartalszahlen veröffentlicht", 3),
    "2.04": ("Kreditklausel ausgelöst / Schulden fällig gestellt", 5),
    "2.05": ("Restrukturierung / Stellenabbau", 3),
    "2.06": ("Wertberichtigung (Abschreibung)", 4),
    "3.01": ("Delisting-Warnung der Börse", 5),
    "4.01": ("Wirtschaftsprüfer gewechselt", 4),
    "4.02": ("Frühere Bilanzen nicht mehr verlässlich (Korrektur)", 5),
    "5.01": ("Kontrollwechsel im Unternehmen", 4),
    "5.02": ("Wechsel im Vorstand/Aufsichtsrat", 3),
}

NAMEN = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "AMZN": "Amazon", "GOOGL": "Alphabet", "META": "Meta Platforms",
    "AVGO": "Broadcom", "TSLA": "Tesla", "BRK-B": "Berkshire Hathaway", "JPM": "JPMorgan", "LLY": "Eli Lilly",
    "V": "Visa", "MA": "Mastercard", "UNH": "UnitedHealth", "XOM": "Exxon Mobil", "COST": "Costco", "NFLX": "Netflix",
    "AMD": "AMD", "ORCL": "Oracle", "CRM": "Salesforce", "ADBE": "Adobe", "PLTR": "Palantir", "WMT": "Walmart",
    "HD": "Home Depot", "PG": "Procter & Gamble", "JNJ": "Johnson & Johnson", "ASML": "ASML", "TSM": "TSMC",
    "SAP": "SAP", "NVO": "Novo Nordisk",
}

ALARM_FIRMA = ['"trading halted"', '"halts trading"', 'bankruptcy', '"chapter 11"', '"SEC charges"', '"SEC sues"',
               'fraud', '"accounting irregularities"', '"CEO resigns"', '"CEO steps down"', '"recall"',
               '"antitrust lawsuit"', '"guidance cut"', '"profit warning"', '"short seller report"']
ALARM_MARKT = ['"circuit breaker"', '"market-wide trading halt"', '"emergency rate cut"', '"stock market plunges"',
               '"Dow plunges"', '"S&P 500 plunges"']
ALARM_KRYPTO = ['"exchange hack"', '"hacked"', 'exploit', 'depeg', '"withdrawals halted"', '"withdrawals paused"',
                '"SEC sues"', '"crypto crash"']
# Spekulative Schlagzeilen ausfiltern
NEWS_MAX_ALTER_H = 6
ALARM_REGEX = {
    "firma": re.compile(r"trading halted|halts trading|bankruptcy|chapter 11|SEC (charges|sues)|fraud|accounting irregularit|"
                        r"CEO (resigns|steps down|ousted|fired)|recall|antitrust lawsuit|(cuts|slashes|lowers) (guidance|forecast)|"
                        r"profit warning|short[- ]seller", re.I),
    "markt": re.compile(r"market-wide (circuit breaker|(trading )?halt)|(level [123]|S&P 500|Wall Street|NYSE|Nasdaq|U\.S\. stocks?)[^.]{0,40}circuit breaker|emergency rate cut|(stocks?|stock market|Dow|S&P 500|Nasdaq) "
                        r"(plunges?|crash(es)?|tumbles?|sinks?)", re.I),
    "krypto": re.compile(r"hack(ed)?|exploit|depeg|withdrawals (halted|paused|suspended)|SEC (sues|charges)|crypto crash", re.I),
}
# Krypto: nur Meldungen zu Watchlist-Coins, großen Börsen oder Stablecoins – und ab 100 Mio. $ Schaden
KRYPTO_RELEVANT = re.compile(r"\b(bitcoin|btc|ethereum|ether|eth|solana|sol|xrp|ripple|bnb|binance|cardano|ada|dogecoin|doge|"
                             r"avalanche|avax|chainlink|coinbase|kraken|okx|bybit|tether|usdt|usdc|circle|crypto market)\b", re.I)
BETRAG = re.compile(r"\$\s?([\d.,]+)\s?(k|m|mn|million|b|bn|billion)\b", re.I)
KRYPTO_MIN_USD = 100e6


def krypto_wichtig(titel: str) -> bool:
    if not KRYPTO_RELEVANT.search(titel):
        return False
    m = BETRAG.search(titel)
    if m:
        try:
            wert = float(m.group(1).replace(",", ""))
        except ValueError:
            return True
        faktor = {"k": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}[m.group(2).lower()]
        return wert * faktor >= KRYPTO_MIN_USD
    return True


# Staatsaufträge: Titel muss Auftragsvergabe UND staatlichen Auftraggeber nennen
AUFTRAG = re.compile(r"\b(wins?|won|awarded|secures?|lands?|receives?|gets?|nabs?|inks?|signs?|selected)\b.*"
                     r"\b(contract|order|deal|award|agreement|program)\b", re.I)
STAAT = re.compile(r"Pentagon|\bArmy\b|\bNavy\b|Air Force|Space Force|NASA|DARPA|\bDoD\b|\bDOE\b|Department of|"
                   r"government|federal|military|Missile Defense|Homeland|NATO|\bU\.S\.|\bUS\b|Marine Corps|\bVA\b", re.I)
KAND_URL = "https://raw.githubusercontent.com/testblyed-png/signal-radar/kandidaten/kandidaten.json"
SUFFIX = re.compile(r",?\s+(inc\.?|incorporated|corp\.?|corporation|company|co\.?|ltd\.?|plc|holdings?|group|n\.v\.|s\.a\.|"
                    r"\(the\)|class [abc]|trust)\b\.?", re.I)


def kurzname(name: str) -> str:
    n = SUFFIX.sub("", name or "").strip(" ,.")
    return SUFFIX.sub("", n).strip(" ,.")


def lade_kandidaten_daten(status: dict, diag: dict) -> None:
    """Holt alle 12 h Kandidaten-Namen und das Universum (Name -> Ticker, Größe) aus dem Kandidaten-Lauf."""
    alt = status.get("kand_namen_zeit")
    if status.get("kand_namen") and alt and datetime.fromisoformat(alt) > datetime.now(timezone.utc) - timedelta(hours=12):
        return
    try:
        d = json.loads(holen(KAND_URL, timeout=60))
        uni = d.get("universum", {})
        namen = {}
        for k in d.get("kandidaten", []):
            n = kurzname((uni.get(k["ticker"]) or {}).get("name") or k.get("name") or "")
            if len(n) >= 4:
                namen[k["ticker"]] = n
        status["kand_namen"] = namen
        status.pop("uni_index", None)
        status["kand_namen_zeit"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    except Exception as ex:
        diag["fehler"].append(f"Kandidaten-Daten: {type(ex).__name__}: {str(ex)[:80]}")


def kandidaten_namen(status: dict, diag: dict) -> dict:
    lade_kandidaten_daten(status, diag)
    return status.get("kand_namen") or {}


def pruefe_auftraege(namen: dict, gesehen: set, diag: dict) -> list:
    """Schlagzeilen zu Staatsaufträgen für Watchlist und Kandidaten."""
    ev = []
    paare = list(namen.items())
    jetzt = datetime.now(timezone.utc)
    for i in range(0, len(paare), 8):
        block = paare[i:i + 8]
        q = ("(" + " OR ".join(f'"{n}"' for _, n in block) + ") (contract OR awarded) "
             "(Pentagon OR Army OR Navy OR \"Air Force\" OR NASA OR \"Space Force\" OR government OR federal)")
        try:
            for titel, link, pub in google_news(q):
                key = titel_key(titel)
                if key in gesehen:
                    continue
                try:
                    if jetzt - parsedate_to_datetime(pub) > timedelta(hours=NEWS_MAX_ALTER_H):
                        continue
                except Exception:
                    continue
                if SPEKULATIV.search(titel) or not AUFTRAG.search(titel) or not STAAT.search(titel):
                    continue
                betroffen = [t for t, n in block if re.search(r"\b" + re.escape(n) + r"\b", titel, re.I)]
                if not betroffen:
                    continue
                gesehen.add(key)
                ev.append((3, f"Staatsauftrag {', '.join(betroffen)}: {titel}", link))
            diag["auftrag_ok"] = diag.get("auftrag_ok", 0) + 1
        except Exception as ex:
            diag["fehler"].append(f"Aufträge: {type(ex).__name__}: {str(ex)[:100]}")
        time.sleep(1.0)
    return ev


SPEKULATIV = re.compile(r"\?|\b(could|might|may|will|should|would|what if|why|how to|prediction|predict)\b", re.I)


def holen(url: str, sec: bool = False, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA if sec else "Mozilla/5.0 (Signal-Radar RSS reader)",
                                               "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def lade_status(pfad: str) -> dict:
    try:
        with open(pfad, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


# ------------------------------------------------------------ 1. SEC 8-K
def cik_karte(status: dict) -> dict:
    karte = status.get("cik_karte") or {}
    alt = status.get("cik_karte_zeit")
    if karte and alt and datetime.fromisoformat(alt) > datetime.now(timezone.utc) - timedelta(days=7):
        return karte
    d = json.loads(holen("https://www.sec.gov/files/company_tickers.json", sec=True))
    karte = {v["ticker"].upper(): int(v["cik_str"]) for v in d.values()}
    status["cik_karte"] = karte
    status["cik_karte_zeit"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return karte


def pruefe_8k(tickers: list[str], status: dict, gesehen: set, diag: dict) -> list:
    ev = []
    karte = cik_karte(status)
    grenze = (datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat()
    for t in tickers:
        cik = karte.get(t.upper()) or karte.get(t.upper().replace("-", "."))
        if not cik:
            continue
        try:
            d = json.loads(holen(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", sec=True))
            r = d["filings"]["recent"]
            for i in range(min(len(r["form"]), 40)):
                if r["form"][i] not in ("8-K", "8-K/A") or r["filingDate"][i] < grenze:
                    continue
                acc = r["accessionNumber"][i]
                if acc in gesehen:
                    continue
                gesehen.add(acc)
                items = [x.strip() for x in (r.get("items", [""] * 99)[i] or "").split(",") if x.strip()]
                wichtig = [(ITEMS_8K[x][0], ITEMS_8K[x][1]) for x in items if x in ITEMS_8K]
                if not wichtig:
                    continue
                link = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{r['primaryDocument'][i]}"
                prio = max(p for _, p in wichtig)
                txt = "; ".join(w for w, _ in wichtig)
                ev.append((prio, f"{t} ({NAMEN.get(t, t)}) – SEC-Meldung: {txt}", link))
            diag["sec_ok"] += 1
        except Exception as ex:
            diag["fehler"].append(f"8-K {t}: {type(ex).__name__}: {str(ex)[:100]}")
        time.sleep(0.15)
    return ev


# ------------------------------------------------------------ 2. Fed
def pruefe_fed(gesehen: set, diag: dict) -> list:
    ev = []
    try:
        root = ET.fromstring(holen("https://www.federalreserve.gov/feeds/press_all.xml"))
        for it in root.iter("item"):
            titel = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            kat = " ".join((c.text or "") for c in it.findall("category"))
            key = "fed:" + link
            if key in gesehen:
                continue
            gesehen.add(key)
            if re.search(r"minutes|schedule|meeting calendar", titel, re.I):
                continue
            if re.search(r"FOMC statement|emergency|liquidity facility|announces .*(facility|action)|approves .*discount rate|implementation note", titel, re.I):
                ev.append((4, f"Fed: {titel}", link))
        diag["fed_ok"] = True
    except Exception as ex:
        diag["fehler"].append(f"Fed: {type(ex).__name__}: {str(ex)[:100]}")
    return ev


# ------------------------------------------------------------ 3. Google News
def google_news(query: str) -> list[tuple[str, str, str]]:
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query + " when:1d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    root = ET.fromstring(holen(url))
    out = []
    for it in root.iter("item"):
        out.append(((it.findtext("title") or "").strip(), (it.findtext("link") or "").strip(),
                    (it.findtext("pubDate") or "").strip()))
    return out


US_MARKT = re.compile(r"S&P 500|S&P|\bDow\b|Nasdaq|Wall Street|NYSE|U\.S\. (stocks?|markets?)|US (stocks?|markets?)|American stocks", re.I)


def ignorierliste() -> list:
    """waechter_ignorieren.txt: ein Suchbegriff oder regulärer Ausdruck pro Zeile (# = Kommentar)."""
    try:
        with open("waechter_ignorieren.txt", encoding="utf-8") as fh:
            return [re.compile(z.strip(), re.I) for z in fh if z.strip() and not z.lstrip().startswith("#")]
    except Exception:
        return []


IGNORIEREN = ignorierliste()


def woerter(titel: str) -> set:
    t = re.sub(r"\s+-\s+[^-]+$", "", titel.lower())
    return {w for w in re.findall(r"[a-z0-9]{3,}", t) if w not in {"the", "and", "for", "with", "after", "from", "amid", "over"}}


def aehnlich_gemeldet(titel: str, status: dict) -> bool:
    """True, wenn in den letzten 24 h schon eine Schlagzeile mit ≥ 60 % gleichen Wörtern gemeldet wurde."""
    w = woerter(titel)
    grenze = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    for e in status.get("gemeldet_24h", []):
        if e["zeit"] >= grenze and w:
            alt = set(e["woerter"])
            if len(w & alt) / max(1, min(len(w), len(alt))) >= 0.6:
                return True
    return False


def titel_key(titel: str) -> str:
    t = re.sub(r"\s+-\s+[^-]+$", "", titel)          # Quelle am Ende entfernen
    return "news:" + re.sub(r"[^a-z0-9]+", "", t.lower())[:70]


def pruefe_news(aktien: list[str], krypto: list[str], gesehen: set, diag: dict) -> list:
    ev = []
    anfragen = []
    namen = [f'"{NAMEN.get(t, t)}"' for t in aktien]
    for i in range(0, len(namen), 8):  # Firmen in Blöcken
        anfragen.append(("firma", f"({' OR '.join(namen[i:i + 8])}) ({' OR '.join(ALARM_FIRMA)})"))
    anfragen.append(("markt", f"({' OR '.join(ALARM_MARKT)})"))
    coins = ["bitcoin", "ethereum", "crypto", "Binance", "Coinbase", "Solana", "XRP"]
    anfragen.append(("krypto", f"({' OR '.join(coins)}) ({' OR '.join(ALARM_KRYPTO)})"))
    for art, q in anfragen:
        try:
            jetzt = datetime.now(timezone.utc)
            for titel, link, pub in google_news(q):
                key = titel_key(titel)
                if key in gesehen:
                    continue
                try:
                    if jetzt - parsedate_to_datetime(pub) > timedelta(hours=NEWS_MAX_ALTER_H):
                        continue
                except Exception:
                    continue
                if SPEKULATIV.search(titel) or not ALARM_REGEX[art].search(titel):
                    continue
                if art == "markt" and re.search(r"equity-based|single[- ]stock|\b[A-Z0-9]{3,6}\.[A-Z]\b", titel):
                    continue                     # Handelsstopp einer einzelnen (oft ausländischen) Aktie, kein Marktereignis
                if art == "krypto" and not krypto_wichtig(titel):
                    continue
                prio = 4 if art in ("markt", "krypto") else 3
                betroffen = [t for t in aktien if NAMEN.get(t, t).lower() in titel.lower()]
                vorsatz = f"{', '.join(betroffen)}: " if betroffen else ("Markt: " if art == "markt" else "Krypto: " if art == "krypto" else "")
                if art == "firma" and not betroffen:
                    continue
                if art == "markt" and not betroffen and not US_MARKT.search(titel):
                    continue                     # Börsen anderer Länder ohne Bezug zur Watchlist
                if art == "markt" and betroffen and not US_MARKT.search(titel):
                    prio = 3
                gesehen.add(key)
                ev.append((prio, f"{vorsatz}{titel}", link))
            diag["news_ok"] += 1
        except Exception as ex:
            diag["fehler"].append(f"News {art}: {type(ex).__name__}: {str(ex)[:100]}")
        time.sleep(1.0)
    return ev


# ------------------------------------------------------------ Senden
def senden(topic: str, ev: list) -> None:
    ev = sorted(ev, key=lambda e: -e[0])
    prio = max(p for p, _, _ in ev)
    titel = "Signal-Radar Wächter: " + (ev[0][1][:70] if len(ev) == 1 else f"{len(ev)} neue Meldungen")
    text = "\n".join(f"• {t}" for _, t, _ in ev[:12])
    body = {"topic": topic, "title": titel, "message": text, "priority": int(prio), "tags": ["rotating_light"],
            "click": ev[0][2] or APP_URL,
            "actions": [{"action": "view", "label": "Signal-Radar", "url": APP_URL}]}
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def main(status_pfad: str = "waechter_status/status.json", ziel: str = "waechter_neu"):
    status = lade_status(status_pfad)
    erster_lauf = not status.get("gesehen")
    gesehen = set(status.get("gesehen", []))
    diag = {"sec_ok": 0, "fed_ok": False, "news_ok": 0, "fehler": [], "neu": 0, "gesendet": False}

    aktien_us = [t for t in E.STANDARD_AKTIEN if t not in ("ASML", "TSM", "SAP", "NVO")]
    ev = []
    try:
        ev += pruefe_8k(aktien_us, status, gesehen, diag)
    except Exception as ex:
        diag["fehler"].append(f"8-K gesamt: {type(ex).__name__}: {str(ex)[:100]}")
    ev += pruefe_fed(gesehen, diag)
    ev += pruefe_news(E.STANDARD_AKTIEN, E.STANDARD_KRYPTO, gesehen, diag)
    namen = {t: NAMEN.get(t, t) for t in E.STANDARD_AKTIEN if len(NAMEN.get(t, t)) >= 4}
    namen.update(kandidaten_namen(status, diag))
    ev += pruefe_auftraege(namen, gesehen, diag)
    diag["neu"] = len(ev)

    gefiltert, aussortiert = [], []
    for e in ev:
        if any(r.search(e[1]) for r in IGNORIEREN) or aehnlich_gemeldet(e[1], status):
            aussortiert.append(e[1][:120])
            continue
        gefiltert.append(e)
        status.setdefault("gemeldet_24h", []).append({"zeit": datetime.now(timezone.utc).isoformat(), "woerter": sorted(woerter(e[1]))})
    grenze = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    status["gemeldet_24h"] = [x for x in status.get("gemeldet_24h", []) if x["zeit"] >= grenze][-300:]
    diag["aussortiert"] = aussortiert[:20]
    ev = gefiltert
    diag["neu"] = len(ev)
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if ev and topic and not erster_lauf:
        try:
            senden(topic, ev)
            diag["gesendet"] = True
        except Exception as ex:
            diag["fehler"].append(f"ntfy: {type(ex).__name__}: {str(ex)[:100]}")
    if erster_lauf:
        diag["hinweis"] = "Erster Lauf: bestehende Meldungen nur gemerkt, nichts gesendet."

    status["gesehen"] = list(gesehen)[-5000:]
    status["letzter_lauf"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    status["diagnose"] = diag
    status["letzte_meldungen"] = [{"prio": p, "text": t, "link": l} for p, t, l in ev][:30] or status.get("letzte_meldungen", [])
    os.makedirs(ziel, exist_ok=True)
    with open(os.path.join(ziel, "status.json"), "w", encoding="utf-8") as fh:
        json.dump(status, fh, ensure_ascii=False, indent=1)
    print(json.dumps(diag, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main(*sys.argv[1:])
