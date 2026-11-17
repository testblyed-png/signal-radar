"""
Datenqualität: prüft jede Kursreihe auf typische Fehler und vergleicht Krypto mit einer zweiten Quelle (Binance).
=================================================================================================================
Prüfungen je Wert (letzte 2 Jahre):
  - doppelte Zeitstempel, fehlende Handelstage (gegen SPY-Kalender), nicht-positive Preise
  - unmögliche Kerzen (High < Low, Close/Open außerhalb High–Low)
  - Tagessprünge über 50 % (Aktien) bzw. 80 % (Krypto)
  - eingefrorene Kurse (5+ Tage exakt gleicher Schluss), Nullvolumen bei Aktien, extreme Volumenspitzen (> 20 x Median)
  - Aktualität: letzter Schlusskurs älter als erwartet
Krypto-Gegenprobe: Tagesschluss Yahoo vs. Binance (BTCUSDT/ETHUSDT, 00:00 UTC) der letzten 60 Tage.
Ergebnis: datenqualitaet.json (Branch "signale"), angezeigt im Reiter System-Status.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import engine as E

BINANCE = ["https://data-api.binance.vision", "https://api.binance.com"]   # erster Host ist der öffentliche Daten-Spiegel
PAARE = {"BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT", "SOL-USD": "SOLUSDT", "XRP-USD": "XRPUSDT"}


def pruefe_reihe(t: str, df: pd.DataFrame, kalender: pd.DatetimeIndex | None) -> dict:
    d = df[df.index >= df.index[-1] - pd.Timedelta(days=730)]
    krypto = E.ist_krypto(t)
    p = []
    dup = int(df.index.duplicated().sum())
    if dup:
        p.append(f"{dup} doppelte Tage")
    if (d[["Open", "High", "Low", "Close"]] <= 0).any().any():
        p.append("Preise ≤ 0")
    kaputt = int(((d["High"] < d["Low"]) | (d["Close"] > d["High"] * 1.001) | (d["Close"] < d["Low"] * 0.999)
                  | (d["Open"] > d["High"] * 1.001) | (d["Open"] < d["Low"] * 0.999)).sum())
    if kaputt:
        p.append(f"{kaputt} unmögliche Kerzen")
    r = d["Close"].pct_change(fill_method=None)
    sprung = r[r.abs() > (0.8 if krypto else 0.5)]
    if len(sprung):
        p.append("Sprünge: " + ", ".join(f"{i:%d.%m.%y} {v:+.0%}" for i, v in sprung.items())[:120])
    gleich = (d["Close"].diff() == 0).astype(int)
    lauf = int(gleich.groupby((gleich == 0).cumsum()).cumsum().max() or 0)
    if lauf >= 5:
        p.append(f"Kurs {lauf} Tage unverändert")
    if not krypto and "Volume" in d and d["Volume"].sum() > 0:
        null = int((d["Volume"] == 0).sum())
        if null > 3:
            p.append(f"{null} Tage ohne Volumen")
        med = d["Volume"].rolling(50, min_periods=20).median()
        spitzen = int((d["Volume"] > 20 * med).sum())
        if spitzen:
            p.append(f"{spitzen} extreme Volumenspitzen")
    fehlend = 0
    if kalender is not None and not krypto and not t.startswith("^"):
        k = kalender[(kalender >= d.index[0]) & (kalender <= d.index[-1])]
        fehlend = int(len(k.difference(d.index)))
        if fehlend > 2:
            p.append(f"{fehlend} fehlende Handelstage")
    if krypto:
        k = pd.date_range(d.index[0], d.index[-1], freq="D")
        fehlend = int(len(k.difference(d.index)))
        if fehlend > 1:
            p.append(f"{fehlend} fehlende Tage")
    return {"ticker": t, "letzter_tag": str(df.index[-1].date()), "tage_2j": int(len(d)), "probleme": p,
            "status": "ok" if not p else ("warnung" if len(p) == 1 and ("Volumen" in p[0] or "unverändert" in p[0]) else "pruefen")}


def binance_tage(paar: str, tage: int = 60) -> tuple[pd.Series | None, str]:
    for host in BINANCE:
        try:
            url = f"{host}/api/v3/klines?symbol={paar}&interval=1d&limit={tage + 1}"
            roh = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Signal-Radar"}), timeout=20).read())
            s = pd.Series({pd.Timestamp(int(k[0]), unit="ms"): float(k[4]) for k in roh if int(k[6]) < datetime.now(timezone.utc).timestamp() * 1000})
            return s, host
        except Exception as ex:
            fehler = f"{type(ex).__name__}: {str(ex)[:80]}"
    return None, fehler


def gegenprobe_krypto(daten: dict) -> dict:
    out = {}
    for t, paar in PAARE.items():
        df = daten.get(t)
        if df is None:
            continue
        b, quelle = binance_tage(paar)
        if b is None:
            out[t] = {"fehler": quelle}
            continue
        y = df["Close"]
        gem = pd.concat([y, b], axis=1, keys=["yahoo", "binance"]).dropna()
        if gem.empty:
            out[t] = {"fehler": "keine gemeinsamen Tage"}
            continue
        abw = (gem["yahoo"] / gem["binance"] - 1).abs()
        out[t] = {"quelle": quelle, "tage": int(len(gem)), "abweichung_median": float(abw.median()), "abweichung_max": float(abw.max()),
                  "tag_max": str(abw.idxmax().date()), "status": "ok" if abw.max() < 0.02 else "pruefen"}
    return out


def pruefe(daten: dict, ziel: str) -> dict:
    kal = daten["SPY"].index if "SPY" in daten else None
    werte = [pruefe_reihe(t, df, kal) for t, df in daten.items() if df is not None and len(df) > 30]
    try:
        gegen = gegenprobe_krypto(daten)
    except Exception as ex:
        gegen = {"fehler": f"{type(ex).__name__}: {str(ex)[:80]}"}
    aus = {"erstellt_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "anzahl": len(werte), "ok": sum(w["status"] == "ok" for w in werte),
           "werte": sorted(werte, key=lambda w: (w["status"] == "ok", w["ticker"])), "krypto_gegenprobe": gegen,
           "diagnose": {"fehler": [f"{w['ticker']}: {'; '.join(w['probleme'])}" for w in werte if w["status"] == "pruefen"][:10]}}
    os.makedirs(ziel, exist_ok=True)
    with open(os.path.join(ziel, "datenqualitaet.json"), "w", encoding="utf-8") as fh:
        json.dump(aus, fh, ensure_ascii=False, indent=1)
    return aus
