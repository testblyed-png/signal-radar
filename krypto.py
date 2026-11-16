"""
Krypto-Strategie für Bitcoin und Ethereum: Test veröffentlichter Trendregeln über die ganze Kurshistorie.
=========================================================================================================
Regeln (vorab festgelegt, gängige Parameter aus der Literatur – kein Durchprobieren bis es passt):
  SMA 50 / 100 / 200, Kreuz 50/200, Momentum 30 und 90 Tage, Ausbruch 20/10 (Donchian),
  Ensemble = Durchschnitt aus SMA 50, SMA 100, SMA 200, Momentum 30, Momentum 90 (Anteil 0–100 %).
Ausführung: Signal aus dem Tagesschluss, Handel zum Schluss des Folgetags, 0,25 % Kosten je Umschichtung.
Zusätzlich: Robustheit über SMA-Längen 20–250, Fear-&-Greed-Auswertung.
Ausgabe: krypto.json (Branch "signale").
"""
from __future__ import annotations

import json
import math
import os
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import engine as E

COINS = {"BTC-USD": "Bitcoin", "ETH-USD": "Ethereum"}
KOSTEN = 0.0025
KEST = 0.275
ENSEMBLE = ["SMA 50", "SMA 100", "SMA 200", "Momentum 30 Tage", "Momentum 90 Tage"]
ENS_NAME = "Ensemble (5 Regeln)"
WARM = 200


def regeln(c: pd.Series) -> dict[str, pd.Series]:
    r = {}
    for n in (50, 100, 200):
        r[f"SMA {n}"] = (c > c.rolling(n).mean()).astype(float)
    r["Kreuz 50/200"] = (c.rolling(50).mean() > c.rolling(200).mean()).astype(float)
    for n in (30, 90):
        r[f"Momentum {n} Tage"] = (c > c.shift(n)).astype(float)
    rein = c > c.shift(1).rolling(20).max()
    raus = c < c.shift(1).rolling(10).min()
    r["Ausbruch 20/10"] = pd.Series(np.where(rein, 1.0, np.where(raus, 0.0, np.nan)), index=c.index).ffill().fillna(0.0)
    r[ENS_NAME] = sum(r[k] for k in ENSEMBLE) / len(ENSEMBLE)
    return r


def simuliere(c: pd.Series, pos: pd.Series) -> pd.Series:
    ret = c.pct_change(fill_method=None).fillna(0.0)
    p = pos.shift(2).fillna(0.0)                       # Signal t, Handel Schluss t+1, Rendite ab t+2
    return (p * ret - KOSTEN * p.diff().abs().fillna(0.0)).iloc[WARM + 2:]


def kennzahlen(r: pd.Series, pos: pd.Series | None = None, halten: bool = False) -> dict:
    eq = (1 + r).cumprod()
    jahre = len(r) / 365
    cagr = eq.iloc[-1] ** (1 / jahre) - 1
    mdd = (eq / eq.cummax() - 1).min()
    sharpe = r.mean() / r.std() * math.sqrt(365) if r.std() > 0 else 0.0
    jr = (1 + r).groupby(r.index.year).prod() - 1
    if halten:
        tot = float(eq.iloc[-1])
        nach = 1 + (tot - 1) * (1 - KEST) if tot > 1 else tot
    else:
        nach = float(np.prod([1 + x * (1 - KEST) if x > 0 else 1 + x for x in jr]))
    h = len(r) // 2
    sh = lambda x: float(x.mean() / x.std() * math.sqrt(365)) if x.std() > 0 else 0.0
    out = {"cagr": cagr, "mdd": mdd, "sharpe": sharpe, "cagr_nach_steuer": nach ** (1 / jahre) - 1,
           "cagr_haelfte1": (1 + r.iloc[:h]).prod() ** (365 / h) - 1, "cagr_haelfte2": (1 + r.iloc[h:]).prod() ** (365 / (len(r) - h)) - 1,
           "sharpe_haelfte1": sh(r.iloc[:h]), "sharpe_haelfte2": sh(r.iloc[h:]),
           "mdd_haelfte1": float(((1 + r.iloc[:h]).cumprod() / (1 + r.iloc[:h]).cumprod().cummax() - 1).min()),
           "mdd_haelfte2": float(((1 + r.iloc[h:]).cumprod() / (1 + r.iloc[h:]).cumprod().cummax() - 1).min()),
           "jahre": jahre, "schlechtestes_jahr": float(jr.min()), "jahresrenditen": {str(j): float(v) for j, v in jr.items()}}
    if pos is not None:
        p = pos.shift(2).fillna(0.0).reindex(r.index)
        out["zeit_investiert"] = float(p.mean())
        out["umschichtungen_pro_jahr"] = float(p.diff().abs().sum() / jahre)
    return out


def kurve(r: pd.Series) -> dict:
    eq = (1 + r).cumprod()
    m = eq.groupby(eq.index.to_period("M")).last()
    return {"monat": [str(p) for p in m.index], "wert": [round(float(v), 4) for v in m.values]}


def fear_greed(diag: dict) -> pd.Series | None:
    try:
        req = urllib.request.Request("https://api.alternative.me/fng/?limit=0&format=json", headers={"User-Agent": "Mozilla/5.0 (Signal-Radar)"})
        d = json.loads(urllib.request.urlopen(req, timeout=30).read())["data"]
        s = pd.Series({pd.Timestamp(int(x["timestamp"]), unit="s").normalize(): float(x["value"]) for x in d}).sort_index()
        return s[~s.index.duplicated()]
    except Exception as ex:
        diag["fehler"].append(f"Fear & Greed: {type(ex).__name__}: {str(ex)[:100]}")
        return None


def fg_auswertung(fg: pd.Series, c: pd.Series) -> dict:
    df = pd.DataFrame({"fg": fg, "c": c}).dropna()
    stufen = [(0, 25, "Extreme Angst (0–24)"), (25, 45, "Angst (25–44)"), (45, 56, "Neutral (45–55)"),
              (56, 76, "Gier (56–75)"), (76, 101, "Extreme Gier (76–100)")]
    out = {"von": str(df.index[0].date()), "stufen": []}
    for h in (30, 90):
        df[f"f{h}"] = df["c"].shift(-h) / df["c"] - 1
    for lo, hi, name in stufen:
        m = df[(df["fg"] >= lo) & (df["fg"] < hi)]
        out["stufen"].append({"stufe": name, "tage": int(len(m)),
                              **{f"mittel_{h}": float(m[f"f{h}"].mean()) for h in (30, 90)},
                              **{f"median_{h}": float(m[f"f{h}"].median()) for h in (30, 90)},
                              **{f"treffer_{h}": float((m[f"f{h}"] > 0).mean()) for h in (30, 90)}})
    out["alle_tage"] = {f"mittel_{h}": float(df[f"f{h}"].mean()) for h in (30, 90)} | {f"median_{h}": float(df[f"f{h}"].median()) for h in (30, 90)}
    out["aktuell"] = float(fg.iloc[-1])
    out["aktuell_datum"] = str(fg.index[-1].date())
    return out


def sauber(o):
    if isinstance(o, dict):
        return {str(k): sauber(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [sauber(v) for v in o]
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return float(o) if math.isfinite(o) else None
    return o


def main(ziel: str = "ausgabe", vorher: str = "vorher"):
    diag = {"fehler": []}
    daten = E.lade_yahoo(list(COINS), period="max")
    heute = datetime.now(timezone.utc).date()
    out = {"erstellt_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "coins": {}, "regeln_ensemble": ENSEMBLE,
           "kosten": KOSTEN}
    fg = fear_greed(diag)
    for t, name in COINS.items():
        if t not in daten or len(daten[t]) < WARM + 400:
            diag["fehler"].append(f"{t}: zu wenig Daten")
            continue
        c = daten[t]["Close"].astype(float)
        if c.index[-1].date() >= heute:                 # laufenden (unfertigen) Tag weglassen
            c = c.iloc[:-1]
        R = regeln(c)
        r_halten = c.pct_change(fill_method=None).fillna(0.0).iloc[WARM + 2:]
        kz = {"Kaufen und Halten": kennzahlen(r_halten, halten=True)}
        kv = {"Kaufen und Halten": kurve(r_halten)}
        for rn, pos in R.items():
            r = simuliere(c, pos)
            kz[rn] = kennzahlen(r, pos)
            if rn in (ENS_NAME, "SMA 200"):
                kv[rn] = kurve(r)
        # Robustheit: jede SMA-Länge von 20 bis 250
        gitter = []
        for n in range(20, 260, 10):
            pos = (c > c.rolling(n).mean()).astype(float)
            k = kennzahlen(simuliere(c, pos), pos)
            gitter.append({"n": n, "cagr": k["cagr"], "sharpe": k["sharpe"], "mdd": k["mdd"]})
        b, e = kz["Kaufen und Halten"], kz[ENS_NAME]
        urteil = {"sharpe_besser": bool(e["sharpe"] > b["sharpe"]), "rueckgang_kleiner": bool(e["mdd"] > b["mdd"]),
                  "rendite_hoeher": bool(e["cagr"] > b["cagr"]), "nach_steuer_hoeher": bool(e["cagr_nach_steuer"] > b["cagr_nach_steuer"]),
                  "sharpe_besser_haelfte1": bool(e["sharpe_haelfte1"] > b["sharpe_haelfte1"]),
                  "sharpe_besser_haelfte2": bool(e["sharpe_haelfte2"] > b["sharpe_haelfte2"]),
                  "sma_laengen_besser_als_halten": int(sum(1 for g in gitter if g["sharpe"] > b["sharpe"])), "sma_laengen": len(gitter)}
        # Aktueller Stand
        letzte = c.index[-1]
        einzel = []
        for rn in ENSEMBLE:
            an = bool(R[rn].iloc[-1] > 0)
            if rn.startswith("SMA"):
                n = int(rn.split()[1]); schwelle = float(c.rolling(n).mean().iloc[-1])
            else:
                n = int(rn.split()[1]); schwelle = float(c.shift(n).iloc[-1])
            einzel.append({"regel": rn, "an": an, "schwelle": schwelle, "abstand": float(c.iloc[-1] / schwelle - 1)})
        ens = R[ENS_NAME]
        seit = letzte
        for d in reversed(ens.index):
            if ens[d] != ens.iloc[-1]:
                break
            seit = d
        out["coins"][t] = {"name": name, "von": str(r_halten.index[0].date()), "bis": str(letzte.date()), "kurs": float(c.iloc[-1]),
                           "kennzahlen": kz, "kurven": kv, "gitter": gitter, "urteil": urteil,
                           "aktuell": {"anteil": float(ens.iloc[-1]), "seit": str(seit.date()), "einzel": einzel,
                                       "ausbruch": bool(R["Ausbruch 20/10"].iloc[-1] > 0), "kreuz": bool(R["Kreuz 50/200"].iloc[-1] > 0)}}
        if t == "BTC-USD" and fg is not None:
            try:
                out["fear_greed"] = fg_auswertung(fg, c)
            except Exception as ex:
                diag["fehler"].append(f"F&G-Auswertung: {type(ex).__name__}: {str(ex)[:100]}")

    # Push bei geänderter Ensemble-Position
    try:
        with open(os.path.join(vorher, "krypto.json"), encoding="utf-8") as fh:
            alt = json.load(fh)
    except Exception:
        alt = {}
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    meldungen = []
    for t, d in out["coins"].items():
        a = ((alt.get("coins") or {}).get(t) or {}).get("aktuell", {}).get("anteil")
        n = d["aktuell"]["anteil"]
        if a is not None and abs(a - n) > 1e-9:
            richtung = "erhöhen" if n > a else "verringern"
            meldungen.append(f"Krypto-Strategie {d['name']}: Anteil {richtung} von {a:.0%} auf {n:.0%} (Kurs {d['kurs']:,.0f} $)")
    if topic and meldungen:
        try:
            import benachrichtigung as B
            B.senden(topic, "Signal-Radar: Krypto-Strategie ändert sich", "\n".join(f"• {m}" for m in meldungen) + "\n\nKeine Anlageberatung.", 3)
            diag["push"] = len(meldungen)
        except Exception as ex:
            diag["fehler"].append(f"Push: {type(ex).__name__}: {str(ex)[:80]}")
    out["diagnose"] = diag
    os.makedirs(ziel, exist_ok=True)
    with open(os.path.join(ziel, "krypto.json"), "w", encoding="utf-8") as fh:
        json.dump(sauber(out), fh, ensure_ascii=False, indent=1)
    print("Krypto:", json.dumps(sauber({t: d["aktuell"]["anteil"] for t, d in out["coins"].items()})), diag)


if __name__ == "__main__":
    import sys
    main(*sys.argv[1:])
