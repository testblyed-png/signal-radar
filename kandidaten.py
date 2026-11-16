"""
Signal-Radar – Potenzial-Kandidaten (läuft täglich in GitHub Actions)
=====================================================================
1. Universum: S&P 1500 (Large, Mid, Small – Listen von Wikipedia); Kandidaten nur aus Mid und Small
2. Faktoren aus Kurs und Volumen (10 Jahre) – alle kausal
3. Faktor-Check: Welche Faktoren hingen wirklich mit der Rendite des Folgemonats zusammen?
4. Portfolio-Test: jeden Monat die besten 15 kaufen – gegen gleichgewichtetes Universum, MDY, SPY
5. Stufe 2 für die besten 40 von heute: Gewinnüberraschung, Analysten-Revisionen, Insider, Wachstum
6. Ergebnis: kandidaten.json (Branch "kandidaten")

Die Gewichte sind vorab festgelegt (aus der Forschungsliteratur), NICHT auf diese Daten optimiert.
"""
from __future__ import annotations

import io
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import engine as E
import extras as X

TOP_N = 15
HALTE_TAGE = 21
KOSTEN = 0.001
MAX_PRO_SEKTOR = 3
STUFE2_N = 40
STUFE2_TTL_H = 20
KEST = 0.275
RADAR_SLOTS = 5

GEWICHTE = {"mom_12_1": 0.25, "hoch52": 0.20, "sprung": 0.20, "ruhe": 0.15, "mom_6_1": 0.10, "akkumulation": 0.10}
FAKTOR_NAMEN = {
    "mom_12_1": "Momentum 12 Monate (ohne letzten Monat)",
    "mom_6_1": "Momentum 6 Monate (ohne letzten Monat)",
    "hoch52": "Nähe zum 52-Wochen-Hoch",
    "sprung": "Kurssprung mit hohem Volumen (z. B. nach Quartalszahlen)",
    "ruhe": "Geringe Schwankung",
    "akkumulation": "Kaufdruck im Volumen",
    "umkehr_1m": "Verlierer des letzten Monats (Gegenbewegung)",
    "trend": "Abstand über der 200-Tage-Linie",
    "GESAMT": "GESAMT-SCORE (Mischung laut Gewichten)",
}
FALLBACK = ["WSM", "EME", "CSL", "RS", "FIX", "TOL", "BURL", "DKS", "CASY", "MANH", "SAIA", "WING", "TXRH", "PSTG", "DT",
            "NBIX", "EXEL", "ILMN", "DOCU", "OKTA", "TWLO", "DUOL", "GWRE", "COHR", "CIEN", "LSCC", "ONTO", "MKSI", "FN",
            "RMBS", "CW", "WWD", "ATI", "BWXT", "ITT", "LECO", "GGG", "RPM", "AFG", "EWBC", "UNM", "RGA", "SF", "JEF",
            "EQH", "IBKR", "WPC", "GLPI", "ELS", "OHI"]


def jetzt():
    return datetime.now(timezone.utc)


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
        o = float(o)
        return o if math.isfinite(o) else None
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


# ---------------------------------------------------------------- Universum
SEITEN = [("Large", "List_of_S%26P_500_companies"), ("Mid", "List_of_S%26P_400_companies"), ("Small", "List_of_S%26P_600_companies")]
KANDIDATEN_SEGMENTE = ("Mid", "Small")


def lade_universum(alt: dict, diag: dict) -> dict:
    """S&P 1500 (500 + 400 + 600). Das breite Universum ist nötig, damit Auf- und Absteiger zwischen den
    Indizes im Test bleiben – sonst fehlen genau die Gewinner, die in den S&P 500 aufgestiegen sind."""
    uni, zahl = {}, {}
    for segment, seite in SEITEN:
        try:
            req = urllib.request.Request(f"https://en.wikipedia.org/wiki/{seite}",
                                         headers={"User-Agent": "SignalRadar/1.0 (github.com/testblyed-png/signal-radar)"})
            html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
            for tab in pd.read_html(io.StringIO(html)):
                cols = {str(c).lower(): c for c in tab.columns}
                sym = next((cols[c] for c in cols if c in ("symbol", "ticker symbol", "ticker")), None)
                if sym is None or len(tab) < 300:
                    continue
                sek = next((cols[c] for c in cols if "sector" in c), None)
                name = next((cols[c] for c in cols if c in ("security", "company")), None)
                n = 0
                for _, z in tab.iterrows():
                    t = str(z[sym]).strip().upper().replace(".", "-")
                    if t and t != "NAN" and t not in uni:
                        uni[t] = {"name": str(z[name]) if name is not None else t,
                                  "sektor": str(z[sek]) if sek is not None else "?", "segment": segment}
                        n += 1
                zahl[segment] = n
                break
        except Exception as ex:
            diag["fehler"].append(f"Universum {segment}: {type(ex).__name__}: {str(ex)[:120]}")
        time.sleep(1)
    diag["universum_segmente"] = zahl
    if len(uni) >= 1000:
        diag["universum_quelle"] = "Wikipedia S&P 1500"
        return uni
    if alt.get("universum") and len(alt["universum"]) >= max(100, len(uni)):
        diag["universum_quelle"] = "Cache vom letzten Lauf"
        return alt["universum"]
    if len(uni) >= 300:
        diag["universum_quelle"] = "Wikipedia (unvollständig)"
        return uni
    diag["universum_quelle"] = "Notliste (50 Werte)"
    return {t: {"name": t, "sektor": "?", "segment": "Mid"} for t in FALLBACK}


# ---------------------------------------------------------------- Faktoren (kausal)
def faktoren(C: pd.DataFrame, V: pd.DataFrame):
    ret = C.pct_change(fill_method=None)
    f = {}
    f["mom_12_1"] = C.shift(21) / C.shift(252) - 1
    f["mom_6_1"] = C.shift(21) / C.shift(126) - 1
    f["hoch52"] = C / C.rolling(252, min_periods=200).max()
    f["ruhe"] = -ret.rolling(63, min_periods=40).std()
    medv = V.rolling(50, min_periods=30).median().shift(1)
    ereignis = (V > 2.5 * medv) & (ret.abs() > 0.04)
    f["sprung"] = ret.where(ereignis).ffill(limit=63).fillna(0.0).where(C.notna())
    up = V.where(ret > 0, 0.0).rolling(50, min_periods=30).sum()
    dn = V.where(ret < 0, 0.0).rolling(50, min_periods=30).sum()
    f["akkumulation"] = np.log((up + 1) / (dn + 1)).where(C.notna())
    f["umkehr_1m"] = -(C / C.shift(21) - 1)
    sma200 = C.rolling(200, min_periods=200).mean()
    f["trend"] = C / sma200 - 1
    return f, sma200, ret


def komposit(f: dict) -> pd.DataFrame:
    return sum(w * f[k].rank(axis=1, pct=True) for k, w in GEWICHTE.items())


# ---------------------------------------------------------------- Faktor-Check
def faktor_check(f: dict, C: pd.DataFrame, komp: pd.DataFrame) -> list[dict]:
    fwd = C.shift(-HALTE_TAGE) / C - 1
    tage = list(range(260, len(C) - HALTE_TAGE, HALTE_TAGE))
    min_n = min(50, max(10, C.shape[1] // 3))
    zeilen = []
    for name, fr in list(f.items()) + [("GESAMT", komp)]:
        ics, spreads = [], []
        for i in tage:
            x, y = fr.iloc[i], fwd.iloc[i]
            m = x.notna() & y.notna()
            if m.sum() < min_n:
                continue
            xr, yv = x[m].rank(method="first"), y[m]
            yv = yv.clip(yv.quantile(0.01), yv.quantile(0.99))   # Ausreißer kappen
            ic = np.corrcoef(x[m].rank(), yv.rank())[0, 1]
            if not np.isfinite(ic):
                continue
            q = pd.qcut(xr, 5, labels=False)
            ics.append(ic)
            spreads.append(yv[q == 4].mean() - yv[q == 0].mean())
        if len(ics) < 12:
            continue
        ics = np.array(ics)
        h = len(ics) // 2
        t = ics.mean() / ics.std(ddof=1) * math.sqrt(len(ics)) if ics.std(ddof=1) > 0 else 0.0
        ic1, ic2 = ics[:h].mean(), ics[h:].mean()
        if t > 2 and ic1 > 0 and ic2 > 0:
            urteil = "wirkt (in beiden Hälften)"
        elif t < -2 and ic1 < 0 and ic2 < 0:
            urteil = "wirkt umgekehrt"
        elif abs(t) > 2:
            urteil = "instabil (nur in einer Hälfte)"
        else:
            urteil = "kein belegter Effekt"
        zeilen.append({"faktor": name, "name": FAKTOR_NAMEN.get(name, name), "gewicht": GEWICHTE.get(name),
                       "ic": ics.mean(), "t": t, "ic_haelfte1": ic1, "ic_haelfte2": ic2,
                       "spread_monat": float(np.mean(spreads)), "monate": len(ics), "urteil": urteil})
    return zeilen


# ---------------------------------------------------------------- Simulation
def simuliere(ret: pd.DataFrame, plan: list, slots: int) -> pd.Series:
    """Signal am Schluss von Tag i, Kauf zum Schluss von Tag i+1, Rendite ab Tag i+2. Kosten je Umschichtung."""
    n = len(ret)
    out = pd.Series(0.0, index=ret.index)
    alt = {}
    for j, (i, ausw) in enumerate(plan):
        a = i + 2
        b = plan[j + 1][0] + 2 if j + 1 < len(plan) else n
        if a >= n:
            break
        neu = {t: 1.0 / slots for t in ausw}
        umsatz = sum(abs(neu.get(t, 0.0) - alt.get(t, 0.0)) for t in set(neu) | set(alt))
        seg = ret.iloc[a:b]
        r = (seg[ausw].fillna(0.0).sum(axis=1) / slots) if ausw else pd.Series(0.0, index=seg.index)
        r = r.copy()
        if len(r):
            r.iloc[0] -= KOSTEN * umsatz
        out.iloc[a:b] = r.values
        alt = neu
    return out.iloc[plan[0][0] + 2:]


def kennzahlen(r: pd.Series, steuer: str) -> dict:
    r = r.dropna()
    if len(r) < 60:
        return {}
    eq = (1 + r).cumprod()
    jahre = len(r) / 252
    cagr = eq.iloc[-1] ** (1 / jahre) - 1
    mdd = (eq / eq.cummax() - 1).min()
    sharpe = r.mean() / r.std() * math.sqrt(252) if r.std() > 0 else 0.0
    if steuer == "jaehrlich":   # Gewinne werden jedes Jahr realisiert und mit KESt belastet (grobe Näherung)
        jr = (1 + r).groupby(r.index.year).prod() - 1
        nach = float(np.prod([1 + x * (1 - KEST) if x > 0 else 1 + x for x in jr]))
    else:                       # Kaufen & Halten: Steuer erst am Ende
        tot = float(eq.iloc[-1])
        nach = 1 + (tot - 1) * (1 - KEST) if tot > 1 else tot
    h = len(r) // 2
    c1 = (1 + r.iloc[:h]).prod() ** (252 / h) - 1
    c2 = (1 + r.iloc[h:]).prod() ** (252 / (len(r) - h)) - 1
    return {"cagr": cagr, "mdd": mdd, "sharpe": sharpe, "cagr_nach_steuer": nach ** (1 / jahre) - 1,
            "cagr_haelfte1": c1, "cagr_haelfte2": c2, "jahre": jahre}


def kurve(r: pd.Series) -> dict:
    eq = (1 + r.fillna(0)).cumprod()
    m = eq.groupby(eq.index.to_period("M")).last()
    return {"monat": [str(p) for p in m.index], "wert": [round(float(v), 4) for v in m.values]}


def spy_regel(spy: pd.Series) -> pd.Series:
    r = spy.pct_change(fill_method=None).fillna(0.0)
    pos = (spy > spy.rolling(200).mean()).astype(float).shift(2).fillna(0.0)
    return pos * r - KOSTEN * pos.diff().abs().fillna(0.0)


def auswahl_heute(score: pd.Series, ok: pd.Series, sektor: dict, n: int) -> list[str]:
    s = score.dropna()
    s = s[ok.reindex(s.index).fillna(False).astype(bool)].sort_values(ascending=False)
    ausw, zahl = [], {}
    for t in s.index:
        sk = sektor.get(t, "?")
        if sk != "?" and zahl.get(sk, 0) >= MAX_PRO_SEKTOR:
            continue
        ausw.append(t)
        zahl[sk] = zahl.get(sk, 0) + 1
        if len(ausw) == n:
            break
    return ausw


def portfolio_test(C, ret, komp, sma200, sektor, spy, mdy) -> dict:
    min_n = min(50, max(10, C.shape[1] // 3))
    start = next((i for i in range(260, len(C)) if komp.iloc[i].notna().sum() >= min_n), None)
    if start is None:
        return {}
    spy_ok = spy > spy.rolling(200).mean()
    plan, plan_f = [], []
    for i in range(start, len(C) - 2, HALTE_TAGE):
        ausw = auswahl_heute(komp.iloc[i], C.iloc[i] > sma200.iloc[i], sektor, TOP_N)
        plan.append((i, ausw))
        plan_f.append((i, ausw if bool(spy_ok.iloc[i]) else []))
    r_s = simuliere(ret, plan, TOP_N)
    r_f = simuliere(ret, plan_f, TOP_N)
    idx = r_s.index
    r_ew = ret.mean(axis=1).reindex(idx).fillna(0.0)
    reihen = {
        "Top 15 nach Score": (r_s, "jaehrlich"),
        "Top 15 nach Score + Marktfilter": (r_f, "jaehrlich"),
        "Alle Aktien des Universums (gleichgewichtet)": (r_ew, "ende"),
        "S&P 500 (SPY) halten": (spy.pct_change(fill_method=None).reindex(idx).fillna(0.0), "ende"),
        "S&P 500 nur über 200-Tage-Linie": (spy_regel(spy).reindex(idx).fillna(0.0), "jaehrlich"),
    }
    if mdy is not None:
        reihen["MidCap-ETF (MDY) halten"] = (mdy.pct_change(fill_method=None).reindex(idx).fillna(0.0), "ende")
    return {"von": str(idx[0].date()), "bis": str(idx[-1].date()),
            "kennzahlen": {k: kennzahlen(r, st) for k, (r, st) in reihen.items()},
            "kurven": {k: kurve(r) for k, (r, _) in reihen.items()}}


def radar_test(daten: dict, fred) -> dict:
    """Jeden Monat die 5 Watchlist-Werte mit dem höchsten Radar-Score (>= Kaufschwelle), sonst Cash."""
    gruppen = {"Aktien": E.STANDARD_AKTIEN, "ETFs": E.STANDARD_ETFS}
    erg, _ = E.analysiere(daten, gruppen, fred)
    if len(erg) < 10 or "SPY" not in daten:
        return {}
    idx = daten["SPY"].index
    S = pd.DataFrame({t: r.score for t, r in erg.items()}).reindex(idx)
    Cw = pd.DataFrame({t: r.merkmale["close"] for t, r in erg.items()}).reindex(idx).ffill(limit=3)
    ret = Cw.pct_change(fill_method=None)
    start = next((i for i in range(260, len(idx)) if S.iloc[i].notna().sum() >= 10), None)
    if start is None:
        return {}
    plan = []
    for i in range(start, len(idx) - 2, HALTE_TAGE):
        s = S.iloc[i].dropna()
        s = s[s >= E.KAUF_SCHWELLE].sort_values(ascending=False)
        plan.append((i, list(s.index[:RADAR_SLOTS])))
    r = simuliere(ret, plan, RADAR_SLOTS)
    spy = daten["SPY"]["Close"]
    reihen = {
        "Radar Top 5 (monatlich)": (r, "jaehrlich"),
        "Watchlist gleichgewichtet": (ret.mean(axis=1).reindex(r.index).fillna(0.0), "ende"),
        "S&P 500 (SPY) halten": (spy.pct_change(fill_method=None).reindex(r.index).fillna(0.0), "ende"),
        "S&P 500 nur über 200-Tage-Linie": (spy_regel(spy).reindex(r.index).fillna(0.0), "jaehrlich"),
    }
    return {"von": str(r.index[0].date()), "bis": str(r.index[-1].date()),
            "kennzahlen": {k: kennzahlen(x, st) for k, (x, st) in reihen.items()},
            "kurven": {k: kurve(x) for k, (x, _) in reihen.items()}}


# ---------------------------------------------------------------- Stufe 2 (Fundamentaldaten, nur heutige Top 40)
def stufe2(t: str, identitaet: str) -> dict:
    import yfinance as yf
    tk = yf.Ticker(t)
    out = {}
    try:
        i = tk.info or {}
        out["info"] = {k: i.get(k) for k in ["marketCap", "revenueGrowth", "earningsGrowth", "profitMargins",
                                             "returnOnEquity", "debtToEquity", "industry", "shortName"]}
    except Exception as ex:
        out["fehler_info"] = f"{type(ex).__name__}: {str(ex)[:80]}"
    try:
        ed = tk.get_earnings_dates(limit=8)
        if ed is not None and len(ed):
            nun = pd.Timestamp.now(tz=ed.index.tz)
            sp = next((c for c in ed.columns if "surprise" in str(c).lower()), None)
            rep = next((c for c in ed.columns if "reported" in str(c).lower()), None)
            verg = ed[(ed.index <= nun) & (ed[rep].notna() if rep else True)].sort_index()
            if len(verg):
                out["zahlen_datum"] = str(verg.index[-1].date())
                if sp is not None and pd.notna(verg[sp].iloc[-1]):
                    out["ueberraschung_pct"] = float(verg[sp].iloc[-1])
            zuk = ed[ed.index > nun].sort_index()
            if len(zuk):
                out["naechste_zahlen"] = str(zuk.index[0].date())
    except Exception as ex:
        out["fehler_earnings"] = f"{type(ex).__name__}: {str(ex)[:80]}"
    try:
        rv = tk.eps_revisions
        if rv is not None and len(rv):
            cols = {str(c).lower(): c for c in rv.columns}
            up = next((cols[c] for c in cols if "uplast30" in c), None)
            dn = next((cols[c] for c in cols if "downlast30" in c), None)
            zeile = "0y" if "0y" in rv.index else rv.index[0]
            if up is not None and dn is not None:
                out["rev_hoch"] = int(rv.loc[zeile, up] or 0)
                out["rev_runter"] = int(rv.loc[zeile, dn] or 0)
    except Exception as ex:
        out["fehler_revisionen"] = f"{type(ex).__name__}: {str(ex)[:80]}"
    try:
        out["insider"] = X.insider_kaeufe(t, identitaet, max_filings=12)
    except Exception as ex:
        out["fehler_insider"] = f"{type(ex).__name__}: {str(ex)[:80]}"
    out["_zeit"] = jetzt().isoformat(timespec="seconds")
    return out


def zusatz_punkte(d: dict) -> tuple[float, list, list]:
    """Punkte aus Fundamentaldaten + Begründungen + Warnhinweise."""
    p, gruende, hinweise = 0.0, [], []
    heute = jetzt().date()
    s, zd = d.get("ueberraschung_pct"), d.get("zahlen_datum")
    if s is not None and zd:
        alter = (heute - datetime.fromisoformat(zd).date()).days
        if alter <= 70:
            if s >= 10:
                p += 10; gruende.append(("+", f"Gewinn deutlich über Erwartung ({s:+.0f} %, Zahlen vom {zd})"))
            elif s >= 3:
                p += 6; gruende.append(("+", f"Gewinn über Erwartung ({s:+.0f} %, Zahlen vom {zd})"))
            elif s <= -3:
                p -= 8; gruende.append(("-", f"Gewinn unter Erwartung ({s:+.0f} %, Zahlen vom {zd})"))
    up, dn = d.get("rev_hoch"), d.get("rev_runter")
    if up is not None and dn is not None and (up or dn):
        if up > dn:
            p += 8; gruende.append(("+", f"Analysten heben Gewinnschätzungen an ({up} rauf, {dn} runter in 30 Tagen)"))
        elif dn > up:
            p -= 6; gruende.append(("-", f"Analysten senken Gewinnschätzungen ({dn} runter, {up} rauf in 30 Tagen)"))
    ip, itxt = X.insider_punkte(d.get("insider"))
    if itxt:
        p += ip; gruende.append(("+", itxt))
    i = d.get("info") or {}
    wg, mg = i.get("revenueGrowth"), i.get("profitMargins")
    if wg is not None:
        if wg >= 0.15 and (mg or 0) > 0:
            p += 8; gruende.append(("+", f"Umsatz wächst {wg:+.0%} und die Firma ist profitabel (Marge {mg:.0%})"))
        elif wg >= 0.08 and (mg or 0) > 0:
            p += 4; gruende.append(("+", f"Solides Umsatzwachstum {wg:+.0%}, profitabel"))
        elif wg < 0:
            p -= 4; gruende.append(("-", f"Umsatz schrumpft ({wg:+.0%})"))
    roe, de = i.get("returnOnEquity"), i.get("debtToEquity")
    if roe is not None and roe >= 0.15 and (de is None or de < 150):
        p += 4; gruende.append(("+", f"Hohe Eigenkapitalrendite ({roe:.0%}) bei tragbaren Schulden"))
    nz = d.get("naechste_zahlen")
    if nz:
        tage = (datetime.fromisoformat(nz).date() - heute).days
        if 0 <= tage <= 7:
            hinweise.append(f"Quartalszahlen am {datetime.fromisoformat(nz):%d.%m.} – erst danach entscheiden")
    return p, gruende, hinweise


def preis_gruende(t: str, f: dict, rang: dict) -> list:
    g = []
    m = f["mom_12_1"][t].iloc[-1]
    if rang["mom_12_1"] >= 0.7:
        g.append(("+", f"Starkes Momentum: {m:+.0%} in 12 Monaten"))
    h = f["hoch52"][t].iloc[-1]
    if h >= 0.95:
        g.append(("+", f"Nahe am 52-Wochen-Hoch ({h - 1:+.0%})"))
    s = f["sprung"][t].iloc[-1]
    if s >= 0.04:
        g.append(("+", f"Kurssprung {s:+.0%} mit hohem Volumen in den letzten 3 Monaten"))
    elif s <= -0.04:
        g.append(("-", f"Kurseinbruch {s:+.0%} mit hohem Volumen in den letzten 3 Monaten"))
    if rang["ruhe"] >= 0.7:
        g.append(("+", "Ruhiger Kursverlauf (geringe Schwankung)"))
    elif rang["ruhe"] <= 0.2:
        g.append(("-", "Sehr schwankungsfreudig"))
    if rang["akkumulation"] >= 0.75:
        g.append(("+", "Volumen deutet auf Käufer"))
    return g


# ---------------------------------------------------------------- Hauptlauf
def main(ziel: str = "kandidaten_neu", vorher: str = "kandidaten_alt"):
    try:
        with open(os.path.join(vorher, "kandidaten.json"), encoding="utf-8") as fh:
            alt = json.load(fh)
    except Exception:
        alt = {}
    diag = {"fehler": [], "stufe2_neu": 0}
    uni = lade_universum(alt, diag)
    extra = ["SPY", "MDY", "^VIX", "^VIX3M"] + E.STANDARD_AKTIEN + E.STANDARD_ETFS
    alle = list(dict.fromkeys(list(uni) + extra))
    daten = {}
    for i in range(0, len(alle), 50):
        block = alle[i:i + 50]
        for versuch in range(2):
            try:
                neu = E.lade_yahoo(block)
            except Exception as ex:
                neu = {}
                diag["fehler"].append(f"Download Block {i}: {type(ex).__name__}: {str(ex)[:80]}")
            if len(neu) >= 0.6 * len(block):
                break
            time.sleep(20)          # Yahoo bremst – kurz warten und Block wiederholen
        daten.update(neu)
        time.sleep(1)
    tick = [t for t in uni if t in daten and len(daten[t]) >= 300]
    diag.update(universum=len(uni), mit_daten=len(tick))
    if "SPY" not in daten or len(tick) < 30:
        print("Zu wenige Daten:", json.dumps(diag, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    idx = daten["SPY"].index
    C = pd.DataFrame({t: daten[t]["Close"] for t in tick}).reindex(idx).ffill(limit=3)
    V = pd.DataFrame({t: daten[t]["Volume"] for t in tick}).reindex(idx).fillna(0.0)
    spy = daten["SPY"]["Close"]
    mdy = daten["MDY"]["Close"].reindex(idx) if "MDY" in daten else None
    sektor = {t: uni[t].get("sektor", "?") for t in tick}

    # Datenbrüche bereinigen: Tagessprung über +300 % oder unter -90 % ist kein echter Handel
    # (z. B. Neuauflage nach Insolvenz) -> Historie davor für diesen Wert verwerfen
    r_roh = C.pct_change(fill_method=None)
    bruch = (r_roh > 3.0) | (r_roh < -0.9)
    diag["datenbrueche"] = []
    for t in C.columns[bruch.any()]:
        d = bruch.index[bruch[t]][-1]
        diag["datenbrueche"].append(f"{t} {d.date()} {r_roh.loc[d, t]:+.0%}")
        C.loc[C.index < d, t] = np.nan
        V.loc[V.index < d, t] = 0.0
    r0 = C.pct_change(fill_method=None)
    st_ = r0.stack()
    gr = st_.abs().sort_values(ascending=False).head(25)
    diag["groesste_tagesspruenge"] = [f"{t} {d.date()} {st_[(d, t)]:+.0%} ({C[t].shift(1)[d]:.3f}->{C[t][d]:.3f})" for (d, t) in gr.index]
    diag["anzahl_tage_ueber_50pct"] = int((st_.abs() > 0.5).sum())
    diag["min_kurs"] = float(C.min().min())
    diag["ticker_min_kurs_unter_1"] = [f"{t}:{C[t].min():.4f}" for t in C.columns if C[t].min() < 1][:30]
    m21 = (C.shift(-21) / C - 1).iloc[::21]
    diag["monatsrendite_mittel_vs_median"] = [float(m21.mean(axis=1).mean()), float(m21.median(axis=1).mean())]
    diag["startjahr_verteilung"] = pd.Series([daten[t].index[0].year for t in tick]).value_counts().sort_index().to_dict()

    f, sma200, ret = faktoren(C, V)
    komp = komposit(f)
    check = faktor_check(f, C, komp)
    test = portfolio_test(C, ret, komp, sma200, sektor, spy, mdy)

    fred = X.lade_fred()
    try:
        rtest = radar_test(daten, fred)
    except Exception as ex:
        rtest = {}
        diag["fehler"].append(f"Radar-Test: {type(ex).__name__}: {str(ex)[:120]}")
    reg = E.regime_aktien(daten, E.STANDARD_AKTIEN, fred)
    regime = E.regime_label(float(reg["score"].iloc[-1])) if len(reg) else "UNBEKANNT"

    # Heutige Kandidaten
    heute = komp.iloc[-1]
    rang_heute = heute.rank(pct=True)
    erlaubt = pd.Series({t: uni[t].get("segment", "Mid") in KANDIDATEN_SEGMENTE for t in tick})
    pool = auswahl_heute(heute, (C.iloc[-1] > sma200.iloc[-1]) & erlaubt.reindex(C.columns).fillna(False), {}, STUFE2_N)
    identitaet = os.environ.get("SEC_IDENTITY") or "Signal-Radar Bot signal-radar-bot@users.noreply.github.com"
    cache = alt.get("stufe2_cache", {})
    s2 = {}
    for t in pool:
        c = cache.get(t)
        try:
            frisch = c and jetzt() - datetime.fromisoformat(c["_zeit"]) < timedelta(hours=STUFE2_TTL_H)
        except Exception:
            frisch = False
        if frisch:
            s2[t] = c
        else:
            try:
                s2[t] = stufe2(t, identitaet)
                diag["stufe2_neu"] += 1
            except Exception as ex:
                s2[t] = {}
                diag["fehler"].append(f"Stufe 2 {t}: {type(ex).__name__}: {str(ex)[:80]}")
            time.sleep(0.4)
    diag["stufe2_teilfehler"] = {k: sum(1 for d in s2.values() if k in d)
                                 for k in ["fehler_info", "fehler_earnings", "fehler_revisionen", "fehler_insider"]}
    diag["stufe2_beispiel_fehler"] = next(({k: v for k, v in d.items() if k.startswith("fehler_")}
                                           for d in s2.values() if any(k.startswith("fehler_") for k in d)), {})

    zeilen = []
    for t in pool:
        d = s2.get(t, {})
        zp, zg, hinw = zusatz_punkte(d)
        rang = {k: float(f[k].iloc[-1].rank(pct=True)[t]) for k in GEWICHTE}
        preis = 60.0 * float(rang_heute[t])
        info = d.get("info") or {}
        erste = daten[t].index[0]
        jahre_boerse = (idx[-1] - erste).days / 365.25
        zeilen.append({
            "ticker": t, "name": info.get("shortName") or uni[t].get("name", t), "sektor": sektor.get(t, "?"),
            "segment": uni[t].get("segment"),
            "branche": info.get("industry"), "punkte": float(np.clip(preis + zp, 0, 100)),
            "preis_punkte": preis, "zusatz_punkte": zp,
            "marktwert_mrd": (info.get("marketCap") or 0) / 1e9 or None,
            "ueberraschung_pct": d.get("ueberraschung_pct"), "zahlen_datum": d.get("zahlen_datum"),
            "rev_hoch": d.get("rev_hoch"), "rev_runter": d.get("rev_runter"),
            "insider": d.get("insider"), "naechste_zahlen": d.get("naechste_zahlen"),
            "umsatzwachstum": info.get("revenueGrowth"), "marge": info.get("profitMargins"),
            "boerse_seit_jahren": round(jahre_boerse, 1) if jahre_boerse < 9.5 else None,
            "gruende": [{"richtung": a, "text": b} for a, b in preis_gruende(t, f, rang) + zg],
            "hinweise": hinw,
        })
    zeilen.sort(key=lambda z: -z["punkte"])
    top, zahl = [], {}
    for z in zeilen:
        if z["sektor"] != "?" and zahl.get(z["sektor"], 0) >= MAX_PRO_SEKTOR:
            continue
        zahl[z["sektor"]] = zahl.get(z["sektor"], 0) + 1
        top.append(z)
        if len(top) == TOP_N:
            break
    for z in top:  # Kurs, Stop, Ziel
        try:
            fm = E.merkmale(daten[z["ticker"]], z["ticker"], daten["SPY"])
            lv = E.levels(fm.iloc[-1])
            z.update(kurs=float(fm["close"].iloc[-1]), stop=lv["stop"], ziel=lv["ziel"], crv=lv["crv"],
                     tag_pct=float(fm["ret1"].iloc[-1]), ext_pct=float(fm["ext_pct"].iloc[-1]))
            if z.get("ext_pct") is not None and z["ext_pct"] > 0.95:
                z["hinweise"].append("Kurzfristig überdehnt – Rücksetzer abwarten")
            if lv["crv"] is not None and np.isfinite(lv["crv"]) and lv["crv"] < 1.5:
                z["hinweise"].append("Chance/Risiko unter 1,5 – nächstes Ziel liegt nah")
        except Exception as ex:
            diag["fehler"].append(f"Levels {z['ticker']}: {type(ex).__name__}: {str(ex)[:80]}")
    if regime == "RISK-OFF":
        for z in top:
            z["hinweise"].insert(0, "Markt im RISK-OFF – keine neuen Käufe")

    # Push: neue Namen unter den besten 5
    alt_top = [k["ticker"] for k in alt.get("kandidaten", [])][:10]
    neu5 = [z for z in top[:5] if z["ticker"] not in alt_top]
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if topic and alt_top and neu5 and regime != "RISK-OFF":
        try:
            import benachrichtigung as B
            txt = "\n".join(f"• {z['ticker']} ({z['name']}): {z['punkte']:.0f} Punkte · Kurs {B.kurs(z.get('kurs'))} · "
                            f"Stop {B.kurs(z.get('stop'))}" + (f" · {z['hinweise'][0]}" if z["hinweise"] else "") for z in neu5)
            B.senden(topic, f"Signal-Radar: {len(neu5)} neue Potenzial-Kandidaten", txt + "\n\nKeine Anlageberatung.", 3)
            diag["push"] = len(neu5)
        except Exception as ex:
            diag["fehler"].append(f"Push: {type(ex).__name__}: {str(ex)[:80]}")

    diag["fehler"] = diag["fehler"][:30]
    out = {"erstellt_utc": jetzt().isoformat(timespec="seconds"), "daten_bis": str(idx[-1].date()), "regime": regime,
           "universum_anzahl": len(tick), "gewichte": GEWICHTE, "faktor_namen": FAKTOR_NAMEN,
           "kandidaten": top, "faktor_check": check, "portfolio_test": test, "radar_test": rtest,
           "diagnose": diag, "universum": uni, "stufe2_cache": s2}
    os.makedirs(ziel, exist_ok=True)
    with open(os.path.join(ziel, "kandidaten.json"), "w", encoding="utf-8") as fh:
        json.dump(sauber(out), fh, ensure_ascii=False, indent=1)
    print("Diagnose:", json.dumps(sauber(diag), ensure_ascii=False)[:6000])
    print("Top:", [(z["ticker"], round(z["punkte"])) for z in top])


if __name__ == "__main__":
    main(*sys.argv[1:])
