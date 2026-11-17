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

COINS = {"BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana", "XRP-USD": "XRP", "BNB-USD": "BNB",
         "ADA-USD": "Cardano", "DOGE-USD": "Dogecoin", "AVAX-USD": "Avalanche", "LINK-USD": "Chainlink"}
HAUPT = ("BTC-USD", "ETH-USD")          # lange Historie, Strategie ursprünglich dafür getestet
MIN_TAGE = 200 + 3 * 365     # mindestens 3 Jahre Test nach der Anlaufphase


def stufe(anteil: float) -> str:
    """Ensemble-Anteil (0–100 %) in dieselben Signalwörter wie bei Aktien übersetzen."""
    if anteil >= 0.999:
        return "STARK KAUFEN"
    if anteil >= 0.599:
        return "KAUFEN"
    if anteil >= 0.399:
        return "HALTEN"
    if anteil >= 0.199:
        return "VERKAUFEN"
    return "STARK VERKAUFEN"


def test_bestanden(e: dict, b: dict) -> bool:
    """Vorab festgelegtes Kriterium: bessere Rendite pro Risiko (gesamt und in beiden Hälften) und kleinerer größter Rückgang."""
    return bool(e["sharpe"] > b["sharpe"] and e["sharpe_haelfte1"] > b["sharpe_haelfte1"]
                and e["sharpe_haelfte2"] > b["sharpe_haelfte2"] and e["mdd"] > b["mdd"])
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


PUFFER = 0.03                           # Regel schaltet erst um, wenn die Schwelle um 3 % über-/unterschritten ist
RUHIG_VARIANTEN = ["Ensemble nur sonntags prüfen", "Ensemble mit 3 % Puffer", "Ensemble sonntags + 3 % Puffer"]


def mit_puffer(c: pd.Series, schwelle: pd.Series, p: float = PUFFER) -> pd.Series:
    an = np.where(c > schwelle * (1 + p), 1.0, np.where(c < schwelle * (1 - p), 0.0, np.nan))
    return pd.Series(an, index=c.index).ffill().fillna(0.0)


def ensemble_puffer(c: pd.Series) -> pd.Series:
    teile = [mit_puffer(c, c.rolling(n).mean()) for n in (50, 100, 200)] + [mit_puffer(c, c.shift(n)) for n in (30, 90)]
    return sum(teile) / len(teile)


def nur_sonntags(pos: pd.Series) -> pd.Series:
    return pos.where(pos.index.dayofweek == 6).ffill().fillna(0.0)


def ruhig_varianten(c: pd.Series, ens: pd.Series) -> dict[str, pd.Series]:
    ep = ensemble_puffer(c)
    return {RUHIG_VARIANTEN[0]: nur_sonntags(ens), RUHIG_VARIANTEN[1]: ep, RUHIG_VARIANTEN[2]: nur_sonntags(ep)}


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
    unten = r[r < 0]
    out = {"cagr": cagr, "mdd": mdd, "sharpe": sharpe,
           "sortino": r.mean() / math.sqrt((unten ** 2).sum() / len(r)) * math.sqrt(365) if len(unten) else None,
           "calmar": cagr / -mdd if mdd < 0 else None, "cagr_nach_steuer": nach ** (1 / jahre) - 1,
           "cagr_haelfte1": (1 + r.iloc[:h]).prod() ** (365 / h) - 1, "cagr_haelfte2": (1 + r.iloc[h:]).prod() ** (365 / (len(r) - h)) - 1,
           "sharpe_haelfte1": sh(r.iloc[:h]), "sharpe_haelfte2": sh(r.iloc[h:]),
           "mdd_haelfte1": float(((1 + r.iloc[:h]).cumprod() / (1 + r.iloc[:h]).cumprod().cummax() - 1).min()),
           "mdd_haelfte2": float(((1 + r.iloc[h:]).cumprod() / (1 + r.iloc[h:]).cumprod().cummax() - 1).min()),
           "jahre": jahre, "schlechtestes_jahr": float(jr.min()), "jahresrenditen": {str(j): float(v) for j, v in jr.items()}}
    if pos is not None:
        p = pos.shift(2).fillna(0.0).reindex(r.index)
        out["zeit_investiert"] = float(p.mean())
        out["umschichtungen_pro_jahr"] = float(p.diff().abs().sum() / jahre)
        out["signale_pro_jahr"] = float((p.diff().fillna(0.0) != 0).sum() / jahre)
    return out


def bootstrap(r_s: pd.Series, r_h: pd.Series, n: int = 1000, block: int = 30, seed: int = 1) -> dict:
    """Block-Bootstrap: Tageswerte in 30-Tage-Blöcken neu zusammengewürfelt (gleiche Blöcke für Strategie und Halten).
    Wie oft ist die Strategie in 1.000 alternativen Kursverläufen besser? Zeigt, ob der Vorteil Zufall sein könnte."""
    a, b = r_s.values, r_h.reindex(r_s.index).fillna(0.0).values
    T = len(a)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, T - block, size=(n, T // block + 1))
    idx = (starts[:, :, None] + np.arange(block)).reshape(n, -1)[:, :T]
    A, B = a[idx], b[idx]

    def sharpe(x):
        return x.mean(axis=1) / x.std(axis=1) * math.sqrt(365)

    def mdd(x):
        eq = np.cumprod(1 + x, axis=1)
        return (eq / np.maximum.accumulate(eq, axis=1) - 1).min(axis=1)

    def cagr(x):
        return np.prod(1 + x, axis=1) ** (365 / T) - 1

    ds, dm, dc = sharpe(A) - sharpe(B), mdd(A) - mdd(B), cagr(A) - cagr(B)
    return {"durchlaeufe": n, "block_tage": block,
            "anteil_sharpe_besser": float((ds > 0).mean()), "anteil_rueckgang_kleiner": float((dm > 0).mean()),
            "anteil_rendite_hoeher": float((dc > 0).mean()),
            "sharpe_diff_p5": float(np.percentile(ds, 5)), "sharpe_diff_p50": float(np.percentile(ds, 50)), "sharpe_diff_p95": float(np.percentile(ds, 95)),
            "rueckgang_strategie_p50": float(np.percentile(mdd(A), 50)), "rueckgang_halten_p50": float(np.percentile(mdd(B), 50)),
            "rueckgang_strategie_p95_schlecht": float(np.percentile(mdd(A), 5))}


def kurve(r: pd.Series) -> dict:
    eq = (1 + r).cumprod()
    m = eq.groupby(eq.index.to_period("M")).last()
    return {"monat": [str(p) for p in m.index], "wert": [round(float(v), 4) for v in m.values]}


MVRV_HOCH, MVRV_TIEF = 3.5, 1.0        # klassische Zonen: über 3,5 überhitzt, unter 1 unter Einstandspreis aller Coins
MVRV_VARIANTEN = ["Ensemble + Gewinnmitnahme bei MVRV über 3,5", "Ensemble + Mindestanteil 40 % bei MVRV unter 1",
                  "Ensemble + beide MVRV-Regeln"]


def lade_mvrv(asset: str, diag: dict) -> pd.Series | None:
    """Market Value / Realized Value von Coin Metrics (kostenlose Community-Schnittstelle)."""
    try:
        url = (f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets={asset}"
               "&metrics=CapMVRVCur&frequency=1d&page_size=10000")
        werte = {}
        for _ in range(3):
            d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Signal-Radar)"}), timeout=40).read())
            for x in d.get("data", []):
                if x.get("CapMVRVCur") not in (None, ""):
                    werte[pd.Timestamp(x["time"][:10])] = float(x["CapMVRVCur"])
            url = d.get("next_page_url")
            if not url:
                break
        s = pd.Series(werte).sort_index()
        return s if len(s) > 500 else None
    except Exception as ex:
        diag["fehler"].append(f"MVRV {asset}: {type(ex).__name__}: {str(ex)[:100]}")
        return None


def mvrv_varianten(ens: pd.Series, mvrv: pd.Series) -> dict[str, pd.Series]:
    m = mvrv.reindex(ens.index).ffill().shift(1)            # Wert des Vortags (steht erst nach Tagesende fest)
    hoch, tief = (m > MVRV_HOCH).fillna(False), (m < MVRV_TIEF).fillna(False)
    v1 = ens.where(~hoch, ens * 0.5)
    v2 = ens.where(~tief, np.maximum(ens, 0.4))
    v3 = v1.where(~tief, np.maximum(v1, 0.4))
    return dict(zip(MVRV_VARIANTEN, [v1, v2, v3]))


def trade_statistik(c: pd.Series, pos: pd.Series) -> dict:
    """Einzelne Trades einer An/Aus-Regel: Trefferquote, Ø Gewinn, Ø Verlust."""
    ret = c.pct_change(fill_method=None).fillna(0.0)
    p = pos.shift(2).fillna(0.0).iloc[WARM + 2:]
    r = ret.reindex(p.index)
    gruppe = (p != p.shift()).cumsum()
    trades = [(float((1 + r[g.index]).prod() - 1 - 2 * KOSTEN), len(g)) for _, g in p.groupby(gruppe) if g.iloc[0] == 1]
    if not trades:
        return {}
    x = np.array([t[0] for t in trades])
    gew, verl = x[x > 0], x[x <= 0]
    return {"trades": int(len(x)), "trefferquote": float((x > 0).mean()),
            "gewinn_mittel": float(gew.mean()) if len(gew) else None, "verlust_mittel": float(verl.mean()) if len(verl) else None,
            "groesster_gewinn": float(x.max()), "dauer_median_tage": float(np.median([t[1] for t in trades]))}


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


BINANCE_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
BINANCE_PAAR = {"BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT"}


def lade_binance(paar: str, diag: dict) -> pd.DataFrame | None:
    """Komplette Tageskerzen einer Börse (Binance Spot, 00:00–24:00 UTC), nur abgeschlossene Tage."""
    for host in BINANCE_HOSTS:
        try:
            zeilen, start = [], 0
            jetzt_ms = datetime.now(timezone.utc).timestamp() * 1000
            for _ in range(10):
                url = f"{host}/api/v3/klines?symbol={paar}&interval=1d&limit=1000&startTime={start}"
                roh = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Signal-Radar"}), timeout=30).read())
                if not roh:
                    break
                zeilen += roh
                start = int(roh[-1][0]) + 86_400_000
                if len(roh) < 1000:
                    break
            df = pd.DataFrame([{"Datum": pd.Timestamp(int(k[0]), unit="ms"), "Open": float(k[1]), "High": float(k[2]), "Low": float(k[3]),
                                "Close": float(k[4]), "Volume": float(k[5])} for k in zeilen if int(k[6]) < jetzt_ms])
            df = df.drop_duplicates("Datum").set_index("Datum").sort_index()
            diag.setdefault("binance_host", host)
            return df if len(df) > 500 else None
        except Exception as ex:
            diag["fehler"].append(f"Binance {paar} über {host}: {type(ex).__name__}: {str(ex)[:80]}")
    return None


def quellenvergleich(c_yahoo: pd.Series, df_b: pd.DataFrame) -> dict:
    """Gleiche Strategie, gleicher Zeitraum, zwei Datenquellen."""
    c_b = df_b["Close"]
    gem = c_yahoo.index.intersection(c_b.index)
    cy, cb = c_yahoo.loc[gem], c_b.loc[gem]
    ey, eb = regeln(cy)[ENS_NAME], regeln(cb)[ENS_NAME]
    ry, rb = simuliere(cy, ey), simuliere(cb, eb)
    hy = cy.pct_change(fill_method=None).fillna(0.0).iloc[WARM + 2:]
    hb = cb.pct_change(fill_method=None).fillna(0.0).iloc[WARM + 2:]
    abw = (cy / cb - 1).abs()
    gleich = float((ey.iloc[WARM:] == eb.iloc[WARM:]).mean())
    return {"von": str(gem[0].date()), "bis": str(gem[-1].date()), "tage": int(len(gem)),
            "kurs_abweichung_median": float(abw.median()), "kurs_abweichung_max": float(abw.max()), "tag_max": str(abw.idxmax().date()),
            "gleiche_position_tage": gleich,
            "aktuell_yahoo": float(ey.iloc[-1]), "aktuell_binance": float(eb.iloc[-1]),
            "kennzahlen": {"Strategie mit Yahoo": kennzahlen(ry, ey), "Strategie mit Binance": kennzahlen(rb, eb),
                           "Halten (Yahoo)": kennzahlen(hy, halten=True), "Halten (Binance)": kennzahlen(hb, halten=True)}}


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
        if t not in daten or len(daten[t]) < (WARM + 400 if t in HAUPT else MIN_TAGE):
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
            if rn != ENS_NAME:
                kz[rn]["trades"] = trade_statistik(c, pos)
            if rn in (ENS_NAME, "SMA 200"):
                kv[rn] = kurve(r)
        for vn, pos in ruhig_varianten(c, R[ENS_NAME]).items():
            kz[vn] = kennzahlen(simuliere(c, pos), pos)
        # MVRV-Varianten (vorab festgelegte Schwellen)
        mv = lade_mvrv(t.split("-")[0].lower(), diag) if t in HAUPT else None
        mvrv_info = None
        if mv is not None:
            mv = mv[mv.index <= c.index[-1]]
            for vn, pos in mvrv_varianten(R[ENS_NAME], mv).items():
                kz[vn] = kennzahlen(simuliere(c, pos), pos)
            tage = mv.reindex(c.index).ffill().iloc[WARM + 2:]
            mvrv_info = {"aktuell": float(mv.iloc[-1]), "datum": str(mv.index[-1].date()),
                         "tage_ueber_hoch": int((tage > MVRV_HOCH).sum()), "tage_unter_tief": int((tage < MVRV_TIEF).sum()),
                         "hoch": MVRV_HOCH, "tief": MVRV_TIEF}
            e0 = kz[ENS_NAME]
            mvrv_info["verbessert"] = {vn: bool(kz[vn]["sharpe"] > e0["sharpe"] and kz[vn]["sharpe_haelfte1"] >= e0["sharpe_haelfte1"]
                                                and kz[vn]["sharpe_haelfte2"] >= e0["sharpe_haelfte2"] and kz[vn]["mdd"] >= e0["mdd"])
                                       for vn in MVRV_VARIANTEN}
        try:
            boot = bootstrap(simuliere(c, R[ENS_NAME]), r_halten)
        except Exception as ex:
            boot = None
            diag["fehler"].append(f"Bootstrap {t}: {type(ex).__name__}")
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
        v_tage = c.index[-365:]
        verlauf = {"datum": [str(d.date()) for d in v_tage], "kurs": [float(x) for x in c.loc[v_tage]],
                   "sma200": [float(x) for x in c.rolling(200).mean().loc[v_tage]], "anteil": [float(x) for x in ens.loc[v_tage]]}
        # letzte Änderungen des Ziel-Anteils (für die Anzeige)
        wechsel = []
        d_ens = ens.diff().fillna(0.0)
        for d in d_ens[d_ens != 0].index[-8:][::-1]:
            wechsel.append({"datum": str(d.date()), "von": float(ens.shift(1)[d]), "auf": float(ens[d]), "kurs": float(c[d])})
        out["coins"][t] = {"name": name, "haupt": t in HAUPT, "test_bestanden": test_bestanden(e, b), "verlauf": verlauf,
                           "wechsel": wechsel, "signal": stufe(float(ens.iloc[-1])), "von": str(r_halten.index[0].date()), "bis": str(letzte.date()), "kurs": float(c.iloc[-1]),
                           "kennzahlen": kz, "kurven": kv, "gitter": gitter, "urteil": urteil, "mvrv": mvrv_info, "bootstrap": boot,
                           "aktuell": {"anteil": float(ens.iloc[-1]), "seit": str(seit.date()), "einzel": einzel,
                                       "ausbruch": bool(R["Ausbruch 20/10"].iloc[-1] > 0), "kreuz": bool(R["Kreuz 50/200"].iloc[-1] > 0)}}
        if t in BINANCE_PAAR:
            dfb = lade_binance(BINANCE_PAAR[t], diag)
            if dfb is not None:
                dfb = dfb[dfb.index < pd.Timestamp(heute)]
                try:
                    out["coins"][t]["quellenvergleich"] = quellenvergleich(c, dfb)
                except Exception as ex:
                    diag["fehler"].append(f"Quellenvergleich {t}: {type(ex).__name__}: {str(ex)[:80]}")
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
        alt_akt = ((alt.get("coins") or {}).get(t) or {}).get("aktuell", {})
        a, n = alt_akt.get("anteil"), d["aktuell"]["anteil"]
        if a is None or abs(a - n) < 1e-9 or not (d["haupt"] or d["test_bestanden"]):
            continue
        alt_an = {e["regel"]: e["an"] for e in alt_akt.get("einzel", [])}
        gekippt = [f"{e['regel']} {'an' if e['an'] else 'aus'}" for e in d["aktuell"]["einzel"] if alt_an.get(e["regel"]) != e["an"]]
        wort = "KAUFEN" if n > a else "VERKAUFEN"
        meldungen.append(f"{wort} {d['name']} (Signal jetzt {stufe(n)}): Anteil von {a:.0%} auf {n:.0%} deines Budgets für {d['name']} bringen "
                         f"(Kurs {d['kurs']:,.0f} $). Grund: {', '.join(gekippt) or 'Regel hat umgeschaltet'}. "
                         f"Beispiel bei 1.000 € Budget: {abs(n - a) * 1000:.0f} € {'kaufen' if n > a else 'verkaufen'}.")
    if topic and meldungen:
        try:
            import benachrichtigung as B
            B.senden(topic, "Krypto-Strategie: " + ("KAUFEN" if any(m.startswith("KAUFEN") for m in meldungen) else "VERKAUFEN"), "\n".join(f"• {m}" for m in meldungen) + "\n\nKeine Anlageberatung.", 3)
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
