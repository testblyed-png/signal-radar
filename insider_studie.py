"""
Insider-Studie: Bringen Käufe von Managern (SEC Form 4, Code P) einen Vorsprung?
================================================================================
Daten:  SEC "Insider Transactions Data Sets" (alle Meldungen aller US-Firmen, quartalsweise, kostenlos)
Kurse:  Yahoo Finance (10 Jahre). Achtung: Für viele inzwischen verschwundene Firmen fehlen Kurse –
        die Abdeckung wird ausgewiesen und eine Rechnung mit pessimistischer Annahme mitgeliefert.
Test:   Ereignisstudie (Rendite nach Meldung minus Small-Cap-Index IWM) und Kalenderzeit-Portfolio.
Regeln: insider_regeln.py – vorab festgelegt, nicht optimiert.
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import insider_regeln as R
import kandidaten as K

UA = os.environ.get("SEC_IDENTITY") or "Signal-Radar Bot signal-radar-bot@users.noreply.github.com"
BASIS = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip"
KURZ = {"A": "Cluster", "B": "Chef-Großkauf", "C": "Einzelkauf"}
FORTSCHRITT = {}
TYPEN = {"A": "Cluster: 3+ Manager kaufen in 30 Tagen", "B": "Großkauf eines Chefs (ab 250.000 $)",
         "C": "Vergleich: irgendein Einzelkauf ab 25.000 $"}


def quartale(start=(2016, 4)):
    j, q = start
    heute = datetime.now(timezone.utc)
    while (j, q) <= (heute.year, (heute.month - 1) // 3 + 1):
        yield f"{j}q{q}"
        j, q = (j, q + 1) if q < 4 else (j + 1, 1)


def lade_quartal(q: str) -> pd.DataFrame | None:
    for versuch in range(3):
        try:
            roh = urllib.request.urlopen(urllib.request.Request(BASIS.format(q=q), headers={"User-Agent": UA}), timeout=180).read()
            break
        except urllib.error.HTTPError as ex:
            if ex.code == 404:
                return None
            time.sleep(5)
        except Exception:
            time.sleep(5)
    else:
        return None
    z = zipfile.ZipFile(io.BytesIO(roh))
    sub = pd.read_csv(z.open("SUBMISSION.tsv"), sep="\t", dtype=str,
                      usecols=["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK", "ISSUERNAME", "ISSUERTRADINGSYMBOL"])
    sub = sub[sub["DOCUMENT_TYPE"] == "4"]
    tr = pd.read_csv(z.open("NONDERIV_TRANS.tsv"), sep="\t", dtype=str,
                     usecols=["ACCESSION_NUMBER", "TRANS_CODE", "TRANS_SHARES", "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"])
    tr = tr[(tr["TRANS_CODE"] == "P") & (tr["TRANS_ACQUIRED_DISP_CD"] == "A")].copy()
    tr["stk"] = pd.to_numeric(tr["TRANS_SHARES"], errors="coerce")
    tr["preis"] = pd.to_numeric(tr["TRANS_PRICEPERSHARE"], errors="coerce")
    tr = tr[(tr["stk"] > 0) & (tr["preis"] >= R.MIN_PREIS)]
    tr["wert"] = tr["stk"] * tr["preis"]
    g = tr.groupby("ACCESSION_NUMBER").agg(wert=("wert", "sum"), stk=("stk", "sum")).reset_index()
    g = g[g["wert"] >= R.MIN_KAUF_USD]
    ow = pd.read_csv(z.open("REPORTINGOWNER.tsv"), sep="\t", dtype=str,
                     usecols=["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"])
    ow["rolle_ok"] = ow["RPTOWNER_RELATIONSHIP"].fillna("").str.contains(R.ROLLE_OK)
    ow["chef"] = ow["RPTOWNER_TITLE"].fillna("").str.contains(R.CHEF_TITEL)
    o = ow.groupby("ACCESSION_NUMBER").agg(owner=("RPTOWNERCIK", "first"), name=("RPTOWNERNAME", "first"),
                                           rolle_ok=("rolle_ok", "any"), chef=("chef", "any")).reset_index()
    d = g.merge(sub, on="ACCESSION_NUMBER").merge(o, on="ACCESSION_NUMBER")
    d["filed"] = pd.to_datetime(d["FILING_DATE"], format="%d-%b-%Y", errors="coerce")
    d["ticker"] = d["ISSUERTRADINGSYMBOL"].map(R.ticker_norm)
    d = d.dropna(subset=["filed", "ticker"])
    d = d[d["rolle_ok"]]
    return d[["ACCESSION_NUMBER", "ISSUERCIK", "ISSUERNAME", "ticker", "filed", "owner", "name", "chef", "wert"]]


def finde_ereignisse(k: pd.DataFrame) -> pd.DataFrame:
    """k: Käufe (eine Zeile je Meldung). Rückgabe: Ereignisse (typ, cik, ticker, datum, kaeufer, summe)."""
    ev = []
    fenster = pd.Timedelta(days=R.FENSTER_TAGE)
    pause = pd.Timedelta(days=R.PAUSE_TAGE)
    for cik, g in k.sort_values("filed").groupby("ISSUERCIK", sort=False):
        letzte = {}
        filed = g["filed"].values
        for d in g["filed"].drop_duplicates():
            tag = g[g["filed"] == d]
            ticker, name = tag["ticker"].iloc[-1], tag["ISSUERNAME"].iloc[-1]
            w = g[(g["filed"] > d - fenster) & (g["filed"] <= d)]
            n, summe = w["owner"].nunique(), float(w["wert"].sum())

            def neu(typ, kaeufer, betrag):
                if typ in letzte and d - letzte[typ] < pause:
                    return
                letzte[typ] = d
                ev.append({"typ": typ, "cik": cik, "ticker": ticker, "firma": name, "datum": d, "kaeufer": int(kaeufer), "summe": betrag})

            if n >= R.CLUSTER_KAEUFER and summe >= R.CLUSTER_SUMME:
                neu("A", n, summe)
            chef = tag[tag["chef"]].groupby("owner")["wert"].sum()
            if len(chef) and chef.max() >= R.CHEF_SUMME:
                neu("B", 1, float(chef.max()))
            einzel = tag.groupby("owner")["wert"].sum()
            if einzel.max() >= R.EINZEL_SUMME:
                neu("C", 1, float(einzel.max()))
    return pd.DataFrame(ev)


def lade_kurse(tickers: list[str], diag: dict):
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    C, V = {}, {}
    for i in range(0, len(tickers), 60):
        block = tickers[i:i + 60]
        for versuch in range(2):
            try:
                raw = yf.download(block, period="10y", interval="1d", auto_adjust=True, group_by="ticker", threads=True, progress=False)
            except Exception:
                raw = None
            if raw is not None and not raw.empty:
                break
            time.sleep(15)
        if raw is None or raw.empty or not isinstance(raw.columns, pd.MultiIndex):
            continue
        lt = 0 if set(block) & set(raw.columns.get_level_values(0)) else 1
        for t in block:
            try:
                sub = raw.xs(t, axis=1, level=lt)
                c = sub["Close"].dropna()
                if len(c) >= 60:
                    C[t] = c.astype("float32")
                    V[t] = sub["Volume"].reindex(c.index).fillna(0).astype("float32")
            except Exception:
                continue
        time.sleep(0.5)
    diag["ticker_angefragt"] = len(tickers)
    diag["ticker_mit_kursen"] = len(C)
    return C, V


def tstat(x) -> float:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    return float(x.mean() / x.std(ddof=1) * math.sqrt(len(x))) if len(x) > 2 and x.std(ddof=1) > 0 else 0.0


def main(ziel="insider_neu"):
    diag = {"fehler": [], "quartale": []}
    teile = []
    for q in quartale():
        d = lade_quartal(q)
        if d is None:
            diag["fehler"].append(f"Quartal {q} nicht verfügbar")
            continue
        teile.append(d)
        diag["quartale"].append(q)
        time.sleep(0.3)
    FORTSCHRITT["quartale"] = len(teile); FORTSCHRITT["t_quartale"] = time.time()
    k = pd.concat(teile, ignore_index=True).drop_duplicates("ACCESSION_NUMBER")
    diag["kaeufe"] = len(k)
    diag["kaeufe_bis"] = str(k["filed"].max().date())
    ev = finde_ereignisse(k)
    diag["ereignisse"] = ev["typ"].value_counts().to_dict()
    FORTSCHRITT.update(kaeufe=len(k), ereignisse=diag["ereignisse"], t_ereignisse=time.time(), ticker=int(ev["ticker"].nunique()))

    ticks = sorted(set(ev["ticker"]))
    C, V = lade_kurse(ticks + ["IWM", "SPY"], diag)
    FORTSCHRITT.update(kurse=len(C), t_kurse=time.time())
    if "IWM" not in C or "SPY" not in C:
        raise RuntimeError("Keine Benchmark-Kurse (IWM/SPY)")
    idx = C["IWM"].index
    iwm, spy = C["IWM"].values.astype(float), C["SPY"].reindex(idx).ffill().values.astype(float)
    H = max(R.HORIZONTE)

    zeilen, pfade = [], {t: [] for t in TYPEN}
    mitglieder = {t: [] for t in TYPEN}            # (ticker, i0) für Kalenderzeit-Portfolio
    for e in ev.itertuples():
        z = {"typ": e.typ, "ticker": e.ticker, "firma": e.firma, "datum": e.datum, "kaeufer": e.kaeufer, "summe": e.summe,
             "status": "ok"}
        i0 = int(idx.searchsorted(e.datum, side="right"))     # erster Handelstag NACH der Meldung
        if e.datum < idx[0] or i0 >= len(idx):
            z["status"] = "ausserhalb"
        elif e.ticker not in C:
            z["status"] = "keine_kurse"
        else:
            c = C[e.ticker].reindex(idx)
            if not np.isfinite(c.iloc[i0]):
                z["status"] = "keine_kurse"                    # zum Zeitpunkt nicht gehandelt (oder anderes Papier)
            else:
                v = V[e.ticker].reindex(idx).fillna(0)
                a = max(0, i0 - 20)
                umsatz = float(np.nanmedian((c.iloc[a:i0 + 1] * v.iloc[a:i0 + 1]).values))
                z["umsatz"] = umsatz
                seg = c.iloc[i0:i0 + H + 1].ffill()
                r = seg.pct_change(fill_method=None).iloc[1:]
                if c.iloc[i0] < R.MIN_PREIS * 0.5:
                    z["status"] = "preis_zu_niedrig"
                elif not umsatz >= R.MIN_UMSATZ_USD:
                    z["status"] = "zu_illiquide"
                elif len(r) and ((r > 3).any() or (r < -0.9).any()):
                    z["status"] = "datenbruch"
                else:
                    p0 = float(seg.iloc[0])
                    for h in R.HORIZONTE:
                        if i0 + h < len(idx):
                            roh = float(seg.iloc[h]) / p0 - 1
                            z[f"r{h}"] = roh
                            z[f"x{h}"] = roh - R.KOSTEN_RUNDE - (iwm[i0 + h] / iwm[i0] - 1)
                    if i0 + H < len(idx):
                        pfade[e.typ].append((seg.values / p0 - 1) - (iwm[i0:i0 + H + 1] / iwm[i0] - 1))
                    mitglieder[e.typ].append((e.ticker, i0))
                    z["seit"] = float(c.ffill().iloc[-1]) / p0 - 1
        zeilen.append(z)
    df = pd.DataFrame(zeilen)
    FORTSCHRITT.update(zeilen=len(df), t_zeilen=time.time())
    diag["status"] = df.groupby(["typ", "status"]).size().unstack(fill_value=0).to_dict("index")

    # ---- Ereignisstudie
    ergebnis = {}
    for typ in TYPEN:
        d = df[(df["typ"] == typ)]
        ok = d[d["status"] == "ok"]
        fehlend = int((d["status"] == "keine_kurse").sum())
        zt = {"name": TYPEN[typ], "ereignisse_gesamt": int(len(d[d["status"] != "ausserhalb"])), "mit_kursen_handelbar": int(len(ok)),
              "ohne_kurse": fehlend, "horizonte": {}}
        for h in R.HORIZONTE:
            x = ok[f"x{h}"].dropna() if f"x{h}" in ok else pd.Series(dtype=float)
            if len(x) < 20:
                continue
            m = ok.loc[x.index].assign(x=x)
            monat = m.groupby(m["datum"].dt.to_period("M"))["x"].mean()
            halb = m["datum"] < m["datum"].sort_values().iloc[len(m) // 2]
            terz = pd.qcut(m["umsatz"].rank(method="first"), 3, labels=["klein", "mittel", "groß"])
            pess = (x.sum() + fehlend * -0.30) / (len(x) + fehlend) if h == H else None
            zt["horizonte"][str(h)] = {
                "n": int(len(x)), "mittel": float(x.mean()), "median": float(x.median()), "treffer": float((x > 0).mean()),
                "roh_mittel": float(ok.loc[x.index, f"r{h}"].mean()),
                "t": tstat(x), "t_monate": tstat(monat), "monate": int(len(monat)),
                "haelfte1": float(x[halb].mean()), "haelfte2": float(x[~halb].mean()),
                "nach_liquiditaet": {str(kk): float(vv) for kk, vv in m.groupby(terz, observed=True)["x"].mean().items()},
                "pessimistisch": pess,
            }
        if pfade[typ]:
            p = np.nanmean(np.vstack(pfade[typ]), axis=0)
            zt["pfad"] = {"tag": list(range(0, H + 1, 3)), "ueberrendite": [float(p[i]) for i in range(0, H + 1, 3)]}
        zt["pro_jahr"] = {str(j): int(n) for j, n in ok.groupby(ok["datum"].dt.year).size().items()}
        ergebnis[typ] = zt

    # ---- Kalenderzeit-Portfolio (alle aktiven Ereignisse gleichgewichtet, je 126 Handelstage)
    port = {}
    RT = {t: C[t].reindex(idx).ffill().pct_change(fill_method=None).fillna(0.0).values.astype(float)
          for t in {m[0] for ms in mitglieder.values() for m in ms}}
    for typ in ("A", "B"):
        summe = np.zeros(len(idx)); anzahl = np.zeros(len(idx))
        for t, i0 in mitglieder[typ]:
            a, b = i0 + 1, min(i0 + H, len(idx) - 1)
            if a > b:
                continue
            r = RT[t][a:b + 1].copy()
            r[0] -= R.KOSTEN_RUNDE / 2
            if b == i0 + H:
                r[-1] -= R.KOSTEN_RUNDE / 2
            summe[a:b + 1] += r; anzahl[a:b + 1] += 1
        start = int(np.argmax(anzahl >= 5)) if (anzahl >= 5).any() else None
        if start is None:
            continue
        pr = pd.Series(np.where(anzahl > 0, summe / np.maximum(anzahl, 1), 0.0), index=idx).iloc[start:]
        ri = pd.Series(iwm, index=idx).pct_change(fill_method=None).fillna(0.0).iloc[start:]
        rs = pd.Series(spy, index=idx).pct_change(fill_method=None).fillna(0.0).iloc[start:]
        diff_m = ((1 + pr).groupby(pr.index.to_period("M")).prod() - (1 + ri).groupby(ri.index.to_period("M")).prod())
        port[typ] = {"von": str(pr.index[0].date()), "bis": str(pr.index[-1].date()),
                     "positionen_mittel": float(anzahl[start:].mean()), "positionen_max": int(anzahl.max()),
                     "t_monatliche_ueberrendite": tstat(diff_m), "ueberrendite_monat_mittel": float(diff_m.mean()),
                     "kennzahlen": {f"Insider-Portfolio ({KURZ[typ]})": K.kennzahlen(pr, "jaehrlich"),
                                    "Small Caps (IWM) halten": K.kennzahlen(ri, "ende"),
                                    "S&P 500 (SPY) halten": K.kennzahlen(rs, "ende")},
                     "kurven": {f"Insider-Portfolio ({KURZ[typ]})": K.kurve(pr),
                                "Small Caps (IWM) halten": K.kurve(ri), "S&P 500 (SPY) halten": K.kurve(rs)}}

    juengste = df[(df["typ"] == "A") & (df["status"] == "ok")].sort_values("datum").tail(20)
    out = {"erstellt_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "zeitraum": [str(idx[0].date()), str(idx[-1].date())], "typen": TYPEN, "ergebnis": ergebnis, "portfolio": port,
           "regeln": {k_: getattr(R, k_) for k_ in ["MIN_PREIS", "FENSTER_TAGE", "CLUSTER_KAEUFER", "CLUSTER_SUMME", "CHEF_SUMME",
                                                    "EINZEL_SUMME", "PAUSE_TAGE", "MIN_UMSATZ_USD", "KOSTEN_RUNDE"]},
           "juengste_cluster": [{"ticker": r.ticker, "firma": r.firma, "datum": str(r.datum.date()), "kaeufer": r.kaeufer,
                                 "summe": r.summe, "seit": r.seit} for r in juengste.itertuples()],
           "diagnose": diag}
    os.makedirs(ziel, exist_ok=True)
    with open(os.path.join(ziel, "studie.json"), "w", encoding="utf-8") as fh:
        json.dump(K.sauber(out), fh, ensure_ascii=False, indent=1)
    print(json.dumps(K.sauber({"diag": diag, "ergebnis": {t: {h: {k_: v for k_, v in e.items() if k_ in ("n", "mittel", "median", "treffer", "t_monate")}
                                                             for h, e in z["horizonte"].items()} for t, z in ergebnis.items()}}), ensure_ascii=False)[:5000])


if __name__ == "__main__":
    _ziel = sys.argv[1] if len(sys.argv) > 1 else "insider_neu"
    try:
        main(_ziel)
    except BaseException as _ex:            # Fehler sichtbar machen (Logs sind von außen nicht lesbar)
        import traceback
        os.makedirs(_ziel, exist_ok=True)
        with open(os.path.join(_ziel, "studie_fehler.json"), "w", encoding="utf-8") as _fh:
            json.dump({"zeit": datetime.now(timezone.utc).isoformat(), "fehler": repr(_ex)[:500],
                       "spur": traceback.format_exc()[-3000:], "fortschritt": FORTSCHRITT}, _fh, indent=1)
        print(traceback.format_exc())
