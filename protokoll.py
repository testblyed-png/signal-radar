"""
Echtzeit-Protokoll der Watchlist-Signale
========================================
Jedes Mal, wenn ein Wert auf STARK KAUFEN oder STARK VERKAUFEN springt, wird Datum und Schlusskurs festgehalten.
Ausgewertet wird nach 1 Monat (21 Handelstage) und 3 Monaten (63) gegen den Vergleichsindex
(S&P 500 für Aktien/ETFs, Bitcoin für Krypto). Kauf gedanklich zum Schlusskurs des Signaltags.
Dieser Test kann nicht in die Zukunft schauen und nicht nachträglich geschönt werden.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

SIGNALE = ("STARK KAUFEN", "STARK VERKAUFEN")
HORIZONTE = {"1M": 21, "3M": 63}


def _r(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 5)


def aktualisieren(werte: list, daten: dict, vorher: str, ziel: str) -> dict:
    try:
        with open(os.path.join(vorher, "protokoll.json"), encoding="utf-8") as fh:
            alt = json.load(fh)
    except Exception:
        alt = {}
    eintraege = alt.get("eintraege", [])
    letzte_signale = alt.get("letzte_signale", {})
    start = alt.get("start") or (werte[0]["datum"] if werte else None)

    for w in werte:
        t, s, d = w["ticker"], w["signal"], w["datum"]
        vorher_s = letzte_signale.get(t)
        if s in SIGNALE and vorher_s != s:
            if not any(e["ticker"] == t and e["signal"] == s and e["datum"] == d for e in eintraege):
                bench = "BTC-USD" if w["gruppe"] == "Krypto" else "SPY"
                eintraege.append({"ticker": t, "gruppe": w["gruppe"], "signal": s, "datum": d, "kurs": w["kurs"],
                                  "score": w["score"], "bench": bench, "erster_lauf": vorher_s is None})
        letzte_signale[t] = s

    def ab(ticker, datum):
        df = daten.get(ticker)
        if df is None:
            return None
        c = df["Close"]
        i = int(c.index.searchsorted(pd.Timestamp(datum), side="left"))
        return (c, i) if i < len(c) else None

    for e in eintraege:
        a, b = ab(e["ticker"], e["datum"]), ab(e["bench"], e["datum"])
        if not a or not b:
            continue
        (c, i), (cb, ib) = a, b
        e["seit"] = _r(c.iloc[-1] / c.iloc[i] - 1)
        e["seit_bench"] = _r(cb.iloc[-1] / cb.iloc[ib] - 1)
        e["tage"] = int(len(c) - 1 - i)
        for k, h in HORIZONTE.items():
            if i + h < len(c) and ib + h < len(cb):
                e[f"r_{k}"] = _r(c.iloc[i + h] / c.iloc[i] - 1)
                e[f"b_{k}"] = _r(cb.iloc[ib + h] / cb.iloc[ib] - 1)

    bilanz = {}
    for s in SIGNALE:
        echte = [e for e in eintraege if e["signal"] == s and not e.get("erster_lauf")]
        z = {"anzahl": len(echte), "anzahl_inkl_start": len([e for e in eintraege if e["signal"] == s])}
        for k in HORIZONTE:
            fertig = [e for e in eintraege if e["signal"] == s and e.get(f"r_{k}") is not None]
            if fertig:
                r = np.array([e[f"r_{k}"] for e in fertig]); b = np.array([e[f"b_{k}"] for e in fertig])
                vor = r - b if s == "STARK KAUFEN" else b - r      # Verkaufssignal richtig, wenn der Wert schlechter lief
                z[k] = {"fertig": len(fertig), "rendite": float(r.mean()), "vergleich": float(b.mean()),
                        "vorsprung": float(vor.mean()), "treffer": float((vor > 0).mean())}
        bilanz[s] = z

    aus = {"start": start, "eintraege": eintraege[-2000:], "letzte_signale": letzte_signale, "bilanz": bilanz}
    os.makedirs(ziel, exist_ok=True)
    with open(os.path.join(ziel, "protokoll.json"), "w", encoding="utf-8") as fh:
        json.dump(aus, fh, ensure_ascii=False, indent=1)
    return aus
