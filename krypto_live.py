"""
Krypto live: Trendwechsel sofort melden, nicht erst nach Tagesschluss.
======================================================================
Dieselben fünf Regeln wie krypto.py (SMA 50/100/200, Momentum 30/90 Tage, 3 % Puffer), aber mit dem
aktuellen Kurs statt nur dem Tagesschluss geprüft:
  SMA n      = (Schlusskurse der letzten n−1 abgeschlossenen Tage + aktueller Kurs) / n
  Momentum n = Schlusskurs vor n Tagen
Eine Regel geht an, wenn der Kurs 3 % über der Schwelle liegt, und aus, wenn er 3 % darunter liegt.

  python krypto_live.py test <ziel>                     Rückblick-Test: stündliche Prüfung gegen Prüfung nur am Tagesschluss
  python krypto_live.py live <ziel> <vorher> <signale>  Live-Prüfung (alle ~10 Minuten über GitHub Actions)
Daten: Binance (öffentlicher Daten-Spiegel data-api.binance.vision, ohne Schlüssel).
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
PAARE = {"BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT", "SOL-USD": "SOLUSDT", "XRP-USD": "XRPUSDT", "BNB-USD": "BNBUSDT",
         "ADA-USD": "ADAUSDT", "DOGE-USD": "DOGEUSDT", "AVAX-USD": "AVAXUSDT", "LINK-USD": "LINKUSDT", "LTC-USD": "LTCUSDT",
         "BCH-USD": "BCHUSDT", "TRX-USD": "TRXUSDT", "XLM-USD": "XLMUSDT", "DOT-USD": "DOTUSDT"}
NAMEN = {"BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana", "XRP-USD": "XRP", "BNB-USD": "BNB", "ADA-USD": "Cardano",
         "DOGE-USD": "Dogecoin", "AVAX-USD": "Avalanche", "LINK-USD": "Chainlink", "LTC-USD": "Litecoin", "BCH-USD": "Bitcoin Cash",
         "TRX-USD": "Tron", "XLM-USD": "Stellar", "DOT-USD": "Polkadot"}
REGELN = ["SMA 50", "SMA 100", "SMA 200", "Momentum 30 Tage", "Momentum 90 Tage"]
PUFFER = 0.03
KOSTEN = 0.0025
STUNDEN_JAHR = 24 * 365
H = 3_600_000


def stufe(anteil: float) -> str:
    return ("STARK KAUFEN" if anteil >= 0.999 else "KAUFEN" if anteil >= 0.599 else "HALTEN" if anteil >= 0.399
            else "VERKAUFEN" if anteil >= 0.199 else "STARK VERKAUFEN")


def hole(url: str):
    for versuch in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Signal-Radar"})
            return json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception:
            if versuch == 3:
                raise
            time.sleep(2 + 3 * versuch)


def kerzen(paar: str, intervall: str, start_ms: int, schritt_ms: int) -> pd.DataFrame:
    """Kerzen ab start_ms (seitenweise je 1000). Index = Eröffnungszeit (UTC); 'fertig' = Kerze abgeschlossen."""
    letzte = None
    for host in HOSTS:
        try:
            zeilen, start = [], start_ms
            while True:
                roh = hole(f"{host}/api/v3/klines?symbol={paar}&interval={intervall}&limit=1000&startTime={start}")
                if not roh:
                    break
                zeilen += roh
                start = int(roh[-1][0]) + schritt_ms
                if len(roh) < 1000:
                    break
                time.sleep(0.15)
            jetzt = time.time() * 1000
            df = pd.DataFrame({"close": [float(k[4]) for k in zeilen], "fertig": [int(k[6]) < jetzt for k in zeilen]},
                              index=pd.to_datetime([int(k[0]) for k in zeilen], unit="ms"))
            return df[~df.index.duplicated()].sort_index()
        except Exception as ex:
            letzte = ex
    raise RuntimeError(f"{paar} {intervall}: {letzte}")


# ------------------------------------------------------------------ Regeln auf Stundenbasis
def schwellen_stuendlich(p: pd.Series, tag_close: pd.Series) -> dict[str, pd.Series]:
    """Schwellen für jede Stunde: SMA mit dem aktuellen Kurs als heutigem Wert, Momentum gegen den Schluss vor n Tagen."""
    tag = p.index.floor("D")
    out = {}
    for n in (50, 100, 200):
        summe_vorher = tag_close.rolling(n - 1).sum().shift(1)        # Tage D−n+1 … D−1
        out[f"SMA {n}"] = (summe_vorher.reindex(tag).to_numpy() + p.to_numpy()) / n
    for n in (30, 90):
        out[f"Momentum {n} Tage"] = tag_close.shift(n).reindex(tag).to_numpy()
    return {k: pd.Series(v, index=p.index) for k, v in out.items()}


def zustand(p: pd.Series, schwelle: pd.Series, start: float | None = None) -> pd.Series:
    an = np.where(p > schwelle * (1 + PUFFER), 1.0, np.where(p < schwelle * (1 - PUFFER), 0.0, np.nan))
    s = pd.Series(an, index=p.index)
    if start is not None and len(s) and np.isnan(s.iloc[0]):
        s.iloc[0] = start
    return s.ffill().fillna(0.0)


def ensemble(p: pd.Series, tag_close: pd.Series, nur_tagesschluss: bool) -> tuple[pd.Series, dict]:
    sw = schwellen_stuendlich(p, tag_close)
    teile = {}
    for k, s in sw.items():
        if nur_tagesschluss:                                  # nur die letzte Stunde des Tages zählt
            maske = p.index.hour == 23
            z = zustand(p[maske], s[maske]).reindex(p.index).ffill().fillna(0.0)
        else:
            z = zustand(p, s)
        teile[k] = z
    return sum(teile.values()) / len(teile), teile


def kennzahlen(r: pd.Series, pos: pd.Series) -> dict:
    eq = (1 + r).cumprod()
    jahre = len(r) / STUNDEN_JAHR
    sh = lambda x: float(x.mean() / x.std() * math.sqrt(STUNDEN_JAHR)) if x.std() > 0 else 0.0
    h = len(r) // 2
    p = pos.reindex(r.index)
    return {"cagr": float(eq.iloc[-1] ** (1 / jahre) - 1), "mdd": float((eq / eq.cummax() - 1).min()), "sharpe": sh(r),
            "sharpe_haelfte1": sh(r.iloc[:h]), "sharpe_haelfte2": sh(r.iloc[h:]),
            "signale_pro_jahr": float((p.diff().fillna(0) != 0).sum() / jahre), "zeit_investiert": float(p.mean()), "jahre": jahre}


def test_coin(t: str) -> dict:
    df = kerzen(PAARE[t], "1h", 0, H)
    df = df[df["fertig"]]
    p = df["close"]
    tag_close = p.groupby(p.index.floor("D")).last()
    tag_close = tag_close.iloc[:-1] if p.index[-1].hour != 23 else tag_close
    ret = p.pct_change().fillna(0.0)
    start = tag_close.index[0] + pd.Timedelta(days=201)
    out = {"name": NAMEN[t], "von": str(start.date()), "bis": str(p.index[-1].date()), "stunden": int(len(p))}
    res = {}
    for name, nur_ts in (("Prüfung nur am Tagesschluss", True), ("Laufende Prüfung (stündlich)", False)):
        ens, _ = ensemble(p, tag_close, nur_ts)
        pos = ens.shift(2).fillna(0.0)                         # Handel eine Stunde nach dem Signal
        r = (pos * ret - KOSTEN * pos.diff().abs().fillna(0.0))[p.index >= start]
        res[name] = kennzahlen(r, pos[p.index >= start])
    halten = ret[p.index >= start]
    res["Kaufen und Halten"] = kennzahlen(halten, pd.Series(1.0, index=halten.index))
    res["Kaufen und Halten"]["signale_pro_jahr"] = 0.0
    out["kennzahlen"] = res
    return out


def test_main(ziel: str):
    os.makedirs(ziel, exist_ok=True)
    out = {"erstellt_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "puffer": PUFFER, "kosten": KOSTEN,
           "coins": {}, "fehler": []}
    for t in PAARE:
        try:
            out["coins"][t] = test_coin(t)
            k = out["coins"][t]["kennzahlen"]
            print(t, {n: (round(v["cagr"], 3), round(v["mdd"], 3), round(v["sharpe"], 2), round(v["signale_pro_jahr"])) for n, v in k.items()},
                  flush=True)
        except Exception as ex:
            out["fehler"].append(f"{t}: {type(ex).__name__}: {str(ex)[:150]}")
            print("Fehler", t, ex, flush=True)
    with open(os.path.join(ziel, "krypto_intraday.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    modus = sys.argv[1]
    if modus == "test":
        test_main(sys.argv[2])
