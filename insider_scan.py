"""
Insider-Scanner: liest ALLE Form-4-Meldungen der SEC (ganzer US-Markt) und findet aktuelle Kauf-Cluster.
=======================================================================================================
Quelle:  SEC-Tagesindex + die einzelnen Meldungen (offiziell, kostenlos, max. 10 Abrufe/Sekunde erlaubt)
Ablauf:  je Lauf höchstens MAX_MELDUNGEN neue Meldungen; die letzten 45 Tage werden so nach und nach aufgefüllt.
Regeln:  identisch zur Studie (insider_regeln.py).
Dateien: kaeufe.json (Speicher), insider.json (aktuelle Cluster für die App)
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import insider_regeln as R

UA = os.environ.get("SEC_IDENTITY") or "Signal-Radar Bot signal-radar-bot@users.noreply.github.com"
MAX_MELDUNGEN = int(os.environ.get("MAX_MELDUNGEN", "5000"))
RUECKBLICK_TAGE = 45
BEHALTEN_TAGE = 100
RATE = 7.0                      # Abrufe pro Sekunde (SEC erlaubt 10)
ZEILE = re.compile(r"^4\s+.*?\s+(\d+)\s+(\d{8})\s+(edgar/\S+)")
DOC = re.compile(r"<ownershipDocument>.*?</ownershipDocument>", re.S)

_sperre = threading.Lock()
_naechster = [0.0]
_abbruch = threading.Event()


def holen(url: str, timeout: int = 30) -> bytes | None:
    for versuch in range(3):
        if _abbruch.is_set():
            return None
        with _sperre:
            warte = _naechster[0] - time.monotonic()
            _naechster[0] = max(time.monotonic(), _naechster[0]) + 1.0 / RATE
        if warte > 0:
            time.sleep(warte)
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout).read()
        except urllib.error.HTTPError as ex:
            if ex.code == 404:
                return b""
            if ex.code == 403:
                try:
                    koerper = ex.read().decode("utf-8", "ignore")
                except Exception:
                    koerper = ""
                if not re.search(r"rate|threshold|undeclared|automated", koerper, re.I):
                    return b""              # Datei gibt es (noch) nicht – die SEC meldet das als 403
            if ex.code in (403, 429):
                time.sleep(20 * (versuch + 1))
                if versuch == 2:
                    _abbruch.set()          # SEC bremst – Lauf sauber beenden, später weitermachen
            else:
                time.sleep(2)
        except Exception:
            time.sleep(2)
    return None


def tagesindex(tag) -> list[str] | None:
    """Eindeutige Form-4-Dateien eines Tages. None = Abruf fehlgeschlagen, [] = kein Handelstag."""
    q = (tag.month - 1) // 3 + 1
    roh = holen(f"https://www.sec.gov/Archives/edgar/daily-index/{tag.year}/QTR{q}/form.{tag:%Y%m%d}.idx", timeout=60)
    if roh is None:
        return None
    dateien = {}
    for z in roh.decode("latin-1").splitlines():
        m = ZEILE.match(z)
        if m:
            dateien.setdefault(m.group(3).rsplit("/", 1)[-1], m.group(3))
    return list(dateien.values())


def txt(el, pfad: str) -> str:
    x = el.find(pfad)
    return (x.text or "").strip() if x is not None and x.text else ""


def wahr(s: str) -> bool:
    return s.strip().lower() in ("1", "true")


def lies_meldung(datei: str, tag: str) -> dict | None | bool:
    """Rückgabe: dict = Kauf gefunden, False = verarbeitet ohne Kauf, None = Fehler (später erneut)."""
    roh = holen("https://www.sec.gov/Archives/" + datei)
    if roh is None:
        return None
    m = DOC.search(roh.decode("utf-8", "ignore"))
    if not m:
        return False
    try:
        d = ET.fromstring(m.group(0))
    except ET.ParseError:
        return False
    if txt(d, "documentType") != "4":
        return False
    wert = stk = 0.0
    tdat = ""
    for t in d.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if txt(t, "transactionCoding/transactionCode") != "P" or txt(t, "transactionAmounts/transactionAcquiredDisposedCode/value") != "A":
            continue
        try:
            s = float(txt(t, "transactionAmounts/transactionShares/value"))
            p = float(txt(t, "transactionAmounts/transactionPricePerShare/value"))
        except ValueError:
            continue
        if s > 0 and p >= R.MIN_PREIS:
            wert += s * p
            stk += s
            tdat = max(tdat, txt(t, "transactionDate/value")[:10])
    if wert < R.MIN_KAUF_USD:
        return False
    besitzer = d.findall("reportingOwner")
    rolle = any(wahr(txt(o, "reportingOwnerRelationship/isDirector")) or wahr(txt(o, "reportingOwnerRelationship/isOfficer")) for o in besitzer)
    if not rolle or not besitzer:
        return False
    titel = "; ".join(x for x in (txt(o, "reportingOwnerRelationship/officerTitle") for o in besitzer) if x)
    ticker = R.ticker_norm(txt(d, "issuer/issuerTradingSymbol"))
    if not ticker:
        return False
    o = besitzer[0]
    ist_dir = any(wahr(txt(x, "reportingOwnerRelationship/isDirector")) for x in besitzer)
    return {"acc": datei.rsplit("/", 1)[-1].replace(".txt", ""), "cik": txt(d, "issuer/issuerCik").zfill(10),
            "firma": txt(d, "issuer/issuerName"), "ticker": ticker, "filed": tag, "trans": tdat,
            "owner": txt(o, "reportingOwnerId/rptOwnerCik"), "name": txt(o, "reportingOwnerId/rptOwnerName"),
            "titel": titel or ("Director" if ist_dir else "Officer"), "chef": bool(R.CHEF_TITEL.search(titel)),
            "wert": round(wert), "preis": round(wert / stk, 4)}


def main(ziel: str = "insider_neu"):
    try:
        with open(os.path.join(ziel, "kaeufe.json"), encoding="utf-8") as fh:
            sp = json.load(fh)
    except Exception:
        sp = {}
    kaeufe = {k["acc"]: k for k in sp.get("kaeufe", [])}
    fertig = set(sp.get("tage_fertig", []))
    teil = sp.get("teilweise", {})
    jetzt = datetime.now(timezone.utc)
    diag = {"meldungen_gelesen": 0, "kaeufe_neu": 0, "tage_neu": [], "fehler": 0, "abbruch": False}
    budget = MAX_MELDUNGEN

    for n in range(0, RUECKBLICK_TAGE + 1):
        tag = (jetzt - timedelta(days=n)).date()
        ts = tag.isoformat()
        if ts in fertig or tag.weekday() >= 5 or budget <= 0 or _abbruch.is_set():
            continue
        endgueltig = jetzt >= datetime(tag.year, tag.month, tag.day, tzinfo=timezone.utc) + timedelta(days=1, hours=7)
        dateien = tagesindex(tag)
        if dateien is None:
            diag["fehler"] += 1
            continue
        schon = set(teil.get(ts, []))
        offen = [f for f in dateien if f.rsplit("/", 1)[-1] not in schon]
        if len(offen) > budget and n > 0 and budget < MAX_MELDUNGEN:
            break                                   # Tag passt nicht mehr ins Budget – nächster Lauf
        offen = offen[:budget]
        with ThreadPoolExecutor(max_workers=6) as ex:
            ergebnisse = list(ex.map(lambda f: (f, lies_meldung(f, ts)), offen))
        ok = 0
        for f, r in ergebnisse:
            if r is None:
                diag["fehler"] += 1
                continue
            ok += 1
            schon.add(f.rsplit("/", 1)[-1])
            if r:
                if r["acc"] not in kaeufe:
                    diag["kaeufe_neu"] += 1
                kaeufe[r["acc"]] = r
        budget -= len(offen)
        diag["meldungen_gelesen"] += ok
        vollstaendig = len(schon) >= len(dateien)
        if endgueltig and vollstaendig:
            fertig.add(ts)
            teil.pop(ts, None)
            diag["tage_neu"].append(ts)
        else:
            teil[ts] = sorted(schon)
    diag["abbruch"] = _abbruch.is_set()

    grenze = (jetzt - timedelta(days=BEHALTEN_TAGE)).date().isoformat()
    kaeufe = {a: k for a, k in kaeufe.items() if k["filed"] >= grenze}
    fertig = {t for t in fertig if t >= (jetzt - timedelta(days=RUECKBLICK_TAGE + 5)).date().isoformat()}
    teil = {t: v for t, v in teil.items() if t >= (jetzt - timedelta(days=5)).date().isoformat()}

    # Abdeckung: lückenlos verarbeitete Werktage rückwärts ab gestern
    luecke_ab, abgedeckt = None, 0
    for n in range(1, RUECKBLICK_TAGE + 1):
        tag = (jetzt - timedelta(days=n)).date()
        if tag.weekday() >= 5:
            continue
        if tag.isoformat() in fertig:
            abgedeckt = n
        elif jetzt >= datetime(tag.year, tag.month, tag.day, tzinfo=timezone.utc) + timedelta(days=1, hours=7):
            luecke_ab = tag.isoformat()
            break

    # Ereignisse mit denselben Regeln wie in der Studie
    cluster = []
    if kaeufe:
        import insider_studie as S
        k = pd.DataFrame(list(kaeufe.values())).rename(columns={"cik": "ISSUERCIK", "firma": "ISSUERNAME"})
        k["filed"] = pd.to_datetime(k["filed"])
        ev = S.finde_ereignisse(k)
        if len(ev):
            ev = ev[(ev["typ"].isin(["A", "B"])) & (ev["datum"] >= pd.Timestamp((jetzt - timedelta(days=R.FENSTER_TAGE)).date()))]
            kurse = {}
            if len(ev):
                try:
                    import engine as E
                    kurse = E.lade_yahoo(sorted(set(ev["ticker"])) + ["SPY"], period="2y")
                except Exception:
                    kurse = {}
            for e in ev.sort_values(["typ", "datum"], ascending=[True, False]).itertuples():
                w = k[(k["ISSUERCIK"] == e.cik) & (k["filed"] > e.datum - pd.Timedelta(days=R.FENSTER_TAGE)) & (k["filed"] <= e.datum + pd.Timedelta(days=R.FENSTER_TAGE))]
                kaeufer = [{"name": r.name_, "titel": r.titel, "wert": r.wert, "preis": r.preis, "gemeldet": str(r.filed.date())}
                           for r in w.rename(columns={"name": "name_"}).sort_values("wert", ascending=False).itertuples()]
                z = {"typ": e.typ, "ticker": e.ticker, "firma": e.firma, "datum": str(e.datum.date()),
                     "kaeufer_anzahl": int(w["owner"].nunique()), "summe": float(w["wert"].sum()),
                     "kaufpreis_mittel": float((w["wert"].sum()) / (w["wert"] / w["preis"]).sum()), "kaeufer": kaeufer[:8],
                     "handelbar": None}
                d = kurse.get(e.ticker)
                if d is not None and len(d) > 25:
                    kurs = float(d["Close"].iloc[-1])
                    umsatz = float((d["Close"] * d["Volume"]).iloc[-20:].median())
                    z.update(kurs=kurs, umsatz_usd=umsatz, handelbar=bool(umsatz >= R.MIN_UMSATZ_USD and kurs >= R.MIN_PREIS),
                             zum_kaufpreis=kurs / z["kaufpreis_mittel"] - 1)
                    try:
                        import engine as E
                        if len(d) >= 260:
                            fm = E.merkmale(d, e.ticker, kurse.get("SPY"))
                            lv = E.levels(fm.iloc[-1])
                            z.update(stop=lv["stop"], ziel=lv["ziel"], ueber_200=bool(fm["close"].iloc[-1] > fm["sma200"].iloc[-1]))
                    except Exception:
                        pass
                cluster.append(z)

    def sauber(o):
        if isinstance(o, dict):
            return {str(a): sauber(b) for a, b in o.items()}
        if isinstance(o, list):
            return [sauber(v) for v in o]
        if isinstance(o, (np.floating, float)):
            return float(o) if np.isfinite(o) else None
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        return o

    os.makedirs(ziel, exist_ok=True)
    with open(os.path.join(ziel, "kaeufe.json"), "w", encoding="utf-8") as fh:
        json.dump({"kaeufe": sorted(kaeufe.values(), key=lambda x: x["filed"]), "tage_fertig": sorted(fertig), "teilweise": teil},
                  fh, ensure_ascii=False)
    aus = {"erstellt_utc": jetzt.isoformat(timespec="seconds"), "cluster": cluster, "kaeufe_im_speicher": len(kaeufe),
           "abgedeckte_tage": abgedeckt, "luecke_ab": luecke_ab, "vollstaendig": bool(abgedeckt >= R.FENSTER_TAGE and not luecke_ab),
           "diagnose": diag}
    with open(os.path.join(ziel, "insider.json"), "w", encoding="utf-8") as fh:
        json.dump(sauber(aus), fh, ensure_ascii=False, indent=1)
    print(json.dumps(sauber({k_: v for k_, v in aus.items() if k_ != "cluster"}), ensure_ascii=False))
    print("Cluster:", [(c["typ"], c["ticker"], c["kaeufer_anzahl"], round(c["summe"])) for c in cluster][:30])


if __name__ == "__main__":
    main(*sys.argv[1:])
