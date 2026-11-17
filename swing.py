"""
Swing-Trades: komplette Trades mit Einstieg, Stop, Ziel und Ausstieg (Tage bis Wochen)
=====================================================================================
Regeln (vorab festgelegt, NICHT an Daten angepasst – identisch in Backtest und Live-Depot):
  Einstieg   Score >= 40 (STARK KAUFEN), Chance/Risiko >= 1,5, nicht überdehnt (< 90. Perzentil),
             Marktampel nicht RISK-OFF  ->  Kauf zur Eröffnung des nächsten Handelstags
  Stop       Start-Stop unter der nächsten Unterstützung (wie im Radar), Gap unter Stop = Ausführung zur Eröffnung
  Ziel       nächster Widerstand: dort HALBE Position verkaufen und Stop auf Einstand ziehen
  Nachziehen Stop = höchster Schlusskurs seit Einstieg minus 3 x ATR (wird nur angehoben)
  Ausstieg   Stop/Nachzieh-Stop, Score fällt unter -5 oder 60 Handelstage -> Verkauf zur nächsten Eröffnung
  Kosten     0,1 % je Kauf und Verkauf
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

import engine as E

EINSTIEG_SCORE = 40.0
MIN_CRV = 1.5
MAX_EXT = 0.90
AUSSTIEG_SCORE = -5.0
TRAIL_ATR = 3.0
MAX_TAGE = 60
KOSTEN = 0.001
RISIKO = 0.01          # Portfolio: 1 % Risiko je Trade
MAX_ANTEIL = 0.20      # höchstens 20 % des Depots je Position
MAX_POS = 10
NACHZIEH_MELDEN = 0.02  # Push, wenn der Stop um mindestens 2 % des Kurses steigt
REGELN = {k: v for k, v in dict(globals()).items() if k.isupper() and isinstance(v, (int, float))}


# ---------------------------------------------------------------- Levels für alle Tage (wie engine.levels)
def levels_vektor(f: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    c, a = f["close"].values, f["atr14"].values
    S = np.vstack([f[n].values for n in ["val", "poc", "avwap_tief", "sma50", "swing_tief"]])
    S = np.where(np.isfinite(S) & (S < c), S, -np.inf)
    smax = S.max(axis=0)
    stop = np.where(np.isfinite(smax), smax - 0.5 * a, c - 2.5 * a)
    stop = np.clip(stop, c - 4 * a, c - 1.5 * a)
    W = np.vstack([f["vah"].values, f["avwap_tief"].values + 2 * f["avwap_tief_sd"].values, f["avwap_hoch"].values,
                   f["hoch_52w"].values, f["swing_hoch"].values])
    W = np.where(np.isfinite(W) & (W > c + 0.5 * a), W, np.inf)
    wmin = W.min(axis=0)
    ziel = np.where(np.isfinite(wmin), wmin, c + 3 * a)
    ziel = np.maximum(ziel, c + 1.5 * a)
    ok = np.isfinite(c) & np.isfinite(a) & (a > 0)
    return np.where(ok, stop, np.nan), np.where(ok, ziel, np.nan)


# ---------------------------------------------------------------- Ein Handelstag für einen Trade (Backtest UND Live)
def neuer_trade(ticker: str, signal_tag: str, stop: float, ziel: float, score: float, kurs: float) -> dict:
    return {"ticker": ticker, "status": "geplant", "signal_tag": signal_tag, "bis": signal_tag, "kurs_signal": kurs,
            "score": score, "stop_start": stop, "stop": stop, "ziel": ziel, "entry": None, "einstieg_tag": None,
            "halb": False, "anteil": 1.0, "erloes": 0.0, "hoechst": None, "tage": 0, "ausstieg_geplant": None,
            "gemeldeter_stop": stop}


def _schliessen(tr: dict, preis: float, tag: str, grund: str, ev: list):
    tr["erloes"] += tr["anteil"] * preis / tr["entry"]
    tr["anteil"] = 0.0
    tr["ergebnis"] = tr["erloes"] - 1 - 2 * KOSTEN
    tr["ausstieg_preis"], tr["ausstieg_tag"], tr["grund"], tr["status"] = preis, tag, grund, "geschlossen"
    risiko = (tr["entry"] - tr["stop_start"]) / tr["entry"]
    tr["r"] = tr["ergebnis"] / risiko if risiko > 0 else None
    ev.append(("geschlossen", grund))


def schritt(tr: dict, tag: str, o: float, h: float, l: float, c: float, atr: float, score: float) -> list:
    """Verarbeitet einen abgeschlossenen Handelstag. Rückgabe: Liste von Ereignissen (art, info)."""
    ev = []
    if not all(np.isfinite([o, h, l, c])):
        return ev
    if tr["status"] == "geplant":
        if o <= tr["stop"] or o >= tr["ziel"]:
            tr["status"], tr["grund"] = "verworfen", ("Eröffnung unter Stop" if o <= tr["stop"] else "Eröffnung über Ziel")
            ev.append(("verworfen", tr["grund"]))
            return ev
        tr.update(status="offen", entry=float(o), einstieg_tag=tag, hoechst=float(o))
        ev.append(("eingestiegen", o))
    elif tr["ausstieg_geplant"]:
        _schliessen(tr, o, tag, tr["ausstieg_geplant"], ev)
        return ev
    if l <= tr["stop"]:
        grund = "Stop" if tr["stop"] <= tr["stop_start"] + 1e-9 else ("Einstand-Stop" if abs(tr["stop"] - tr["entry"]) < 1e-9 else "Nachzieh-Stop")
        _schliessen(tr, min(o, tr["stop"]), tag, grund, ev)
        return ev
    if not tr["halb"] and h >= tr["ziel"]:
        tr["erloes"] += 0.5 * max(o, tr["ziel"]) / tr["entry"]
        tr["anteil"], tr["halb"] = 0.5, True
        tr["stop"] = max(tr["stop"], tr["entry"])
        ev.append(("ziel", tr["ziel"]))
    tr["hoechst"] = max(tr["hoechst"], c)
    if np.isfinite(atr):
        trail = tr["hoechst"] - TRAIL_ATR * atr
        if trail > tr["stop"]:
            tr["stop"] = float(trail)
            ev.append(("nachgezogen", trail))
    tr["tage"] += 1
    tr["wert"] = tr["erloes"] + tr["anteil"] * c / tr["entry"] - KOSTEN
    if np.isfinite(score) and score < AUSSTIEG_SCORE:
        tr["ausstieg_geplant"] = "Signal schwach"
    elif tr["tage"] >= MAX_TAGE:
        tr["ausstieg_geplant"] = "Zeitlimit"
    if tr["ausstieg_geplant"]:
        ev.append(("ausstieg_geplant", tr["ausstieg_geplant"]))
    return ev


# ---------------------------------------------------------------- Backtest
def backtest_ticker(t: str, df: pd.DataFrame, f: pd.DataFrame, score: pd.Series, reg: pd.Series | None) -> list[dict]:
    idx = f.index
    o, h, l, c = (df[x].reindex(idx).values.astype(float) for x in ("Open", "High", "Low", "Close"))
    atr, s, ext = f["atr14"].values, score.reindex(idx).values, f["ext_pct"].values
    rg = reg.reindex(idx).ffill().values if reg is not None else np.zeros(len(idx))
    stopv, zielv = levels_vektor(f)
    tage = [d.strftime("%Y-%m-%d") for d in idx]
    trades, i, n = [], 252, len(idx)
    while i < n - 1:
        crv = (zielv[i] - c[i]) / (c[i] - stopv[i]) if c[i] > stopv[i] else np.nan
        if not (s[i] >= EINSTIEG_SCORE and crv >= MIN_CRV and (not np.isfinite(ext[i]) or ext[i] < MAX_EXT) and rg[i] > -0.2):
            i += 1
            continue
        tr = neuer_trade(t, tage[i], float(stopv[i]), float(zielv[i]), float(s[i]), float(c[i]))
        pfad, j = [], i + 1
        while j < n and tr["status"] in ("geplant", "offen"):
            schritt(tr, tage[j], o[j], h[j], l[j], c[j], atr[j], s[j])
            if tr["status"] == "offen":
                pfad.append(tr["wert"])
            elif tr["status"] == "geschlossen":
                pfad.append(1 + tr["ergebnis"])
            j += 1
        if tr["status"] == "geschlossen":
            trades.append({"ticker": t, "einstieg": tr["einstieg_tag"], "ausstieg": tr["ausstieg_tag"], "ergebnis": tr["ergebnis"],
                           "r": tr["r"], "tage": tr["tage"], "grund": tr["grund"], "halb": tr["halb"], "score": tr["score"],
                           "risiko": (tr["entry"] - tr["stop_start"]) / tr["entry"], "pfad": pfad})
        if tr["status"] == "geschlossen":
            i = j - 1                      # neues Signal frühestens am Schluss des Ausstiegstags
        elif tr["status"] == "verworfen":
            i += 1
        else:
            break                          # Trade am Datenende noch offen
    return trades


def trade_statistik(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    x = np.array([t["ergebnis"] for t in trades])
    g, v = x[x > 0], x[x <= 0]
    r = np.array([t["r"] for t in trades if t.get("r") is not None])
    daten = sorted(t["einstieg"] for t in trades)
    mitte = daten[len(daten) // 2]
    h1 = np.array([t["ergebnis"] for t in trades if t["einstieg"] < mitte])
    h2 = np.array([t["ergebnis"] for t in trades if t["einstieg"] >= mitte])
    gruende = pd.Series([t["grund"] for t in trades]).value_counts().to_dict()
    return {"trades": int(len(x)), "trefferquote": float((x > 0).mean()), "gewinn_mittel": float(g.mean()) if len(g) else None,
            "verlust_mittel": float(v.mean()) if len(v) else None, "erwartung": float(x.mean()), "median": float(np.median(x)),
            "profit_faktor": float(g.sum() / -v.sum()) if len(v) and v.sum() < 0 else None, "r_mittel": float(r.mean()) if len(r) else None,
            "tage_median": float(np.median([t["tage"] for t in trades])), "gruende": gruende,
            "erwartung_haelfte1": float(h1.mean()) if len(h1) else None, "erwartung_haelfte2": float(h2.mean()) if len(h2) else None,
            "trefferquote_haelfte1": float((h1 > 0).mean()) if len(h1) else None, "trefferquote_haelfte2": float((h2 > 0).mean()) if len(h2) else None}


def portfolio(trades: list[dict], idx: pd.DatetimeIndex) -> pd.Series:
    """Tägliche Depotrendite: 1 % Risiko je Trade, max. 20 % je Position, max. 10 Positionen, kein Kredit."""
    pos = {d: i for i, d in enumerate(idx.strftime("%Y-%m-%d"))}
    nach_tag = {}
    for t in trades:
        if t["einstieg"] in pos:
            nach_tag.setdefault(pos[t["einstieg"]], []).append(t)
    cash, offen, werte = 1.0, [], np.empty(len(idx))
    equity_vortag = 1.0
    for d in range(len(idx)):
        for t in sorted(nach_tag.get(d, []), key=lambda x: -x["score"]):
            anteil = min(RISIKO / max(t["risiko"], 1e-4), MAX_ANTEIL)
            betrag = equity_vortag * anteil
            if len(offen) < MAX_POS and betrag <= cash + 1e-12:
                cash -= betrag
                offen.append([t, betrag, d])
        rest = []
        wert = cash
        for t, betrag, d0 in offen:
            k = d - d0
            if k >= len(t["pfad"]) - 1:
                cash += betrag * t["pfad"][-1]
                wert += betrag * t["pfad"][-1]
            else:
                rest.append([t, betrag, d0])
                wert += betrag * t["pfad"][k]
        offen = rest
        werte[d] = wert
        equity_vortag = wert
    eq = pd.Series(werte, index=idx)
    return eq.pct_change().fillna(0.0)


def kennzahlen(r: pd.Series, steuer: str) -> dict:
    import kandidaten as K
    return K.kennzahlen(r, steuer)


def kurve(r: pd.Series) -> dict:
    import kandidaten as K
    return K.kurve(r)


def sauber(o):
    import kandidaten as K
    return K.sauber(o)


def teste(daten: dict, liste: list[str], fred, name: str, diag: dict) -> dict:
    reg = E.regime_aktien(daten, liste, fred)
    reg_s = reg["score"] if len(reg) else None
    spy = daten["SPY"]
    trades, t0 = [], time.time()
    for t in liste:
        df = daten.get(t)
        if df is None or len(df) < 400:
            continue
        try:
            f = E.merkmale(df, t, spy)
            s = E.score_aus(E.komponenten(f), reg_s)
            trades += backtest_ticker(t, df, f, s, reg_s)
        except Exception as ex:
            diag["fehler"].append(f"{name} {t}: {type(ex).__name__}: {str(ex)[:80]}")
    diag[f"sekunden_{name}"] = round(time.time() - t0)
    idx = spy.index
    start = idx.searchsorted(pd.Timestamp(min(t["einstieg"] for t in trades))) if trades else 260
    r_p = portfolio(trades, idx).iloc[start:]
    ret = pd.DataFrame({t: daten[t]["Close"] for t in liste if t in daten}).reindex(idx).pct_change(fill_method=None)
    r_ew = ret.mean(axis=1).fillna(0.0).iloc[start:]
    r_spy = spy["Close"].pct_change(fill_method=None).fillna(0.0).iloc[start:]
    reihen = {"Swing-Depot (Regeln)": (r_p, "jaehrlich"), "Alle Werte gleichgewichtet halten": (r_ew, "ende"),
              "S&P 500 (SPY) halten": (r_spy, "ende")}
    jahr = pd.Series([t["einstieg"][:4] for t in trades]).value_counts().sort_index().to_dict() if trades else {}
    beispiele = sorted(trades, key=lambda x: x["einstieg"])[-25:]
    return {"name": name, "werte": len([t for t in liste if t in daten]), "von": str(r_p.index[0].date()), "bis": str(r_p.index[-1].date()),
            "statistik": trade_statistik(trades), "trades_pro_jahr": jahr,
            "kennzahlen": {k: kennzahlen(r, st) for k, (r, st) in reihen.items()},
            "kurven": {k: kurve(r) for k, (r, _) in reihen.items()},
            "letzte_trades": [{k: v for k, v in t.items() if k != "pfad"} for t in beispiele]}


def backtest_main(ziel: str = "swing_neu"):
    import extras as X
    diag = {"fehler": []}
    try:
        uni = json.loads(urllib.request.urlopen(
            "https://raw.githubusercontent.com/testblyed-png/signal-radar/kandidaten/kandidaten.json", timeout=60).read()).get("universum", {})
    except Exception as ex:
        uni = {}
        diag["fehler"].append(f"Universum: {type(ex).__name__}")
    watch = E.STANDARD_AKTIEN + E.STANDARD_ETFS
    alle = list(dict.fromkeys(list(uni) + watch + ["SPY", "^VIX", "^VIX3M"]))
    daten = {}
    for i in range(0, len(alle), 50):
        block = alle[i:i + 50]
        for versuch in range(2):
            try:
                neu = E.lade_yahoo(block)
            except Exception:
                neu = {}
            if len(neu) >= 0.6 * len(block):
                break
            time.sleep(20)
        daten.update(neu)
        time.sleep(1)
    daten = E.nur_fertige_tage(daten)
    # Datenbrüche (unmögliche Tagessprünge) entfernen
    for t in list(daten):
        r = daten[t]["Close"].pct_change(fill_method=None)
        b = r[(r > 3) | (r < -0.9)]
        if len(b):
            daten[t] = daten[t].loc[daten[t].index > b.index[-1]]
    diag["mit_daten"] = len(daten)
    fred = X.lade_fred()
    out = {"erstellt_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "regeln": REGELN, "diagnose": diag}
    out["watchlist"] = teste(daten, [t for t in watch if t != "SPY"], fred, "watchlist", diag)
    uni_liste = [t for t in uni if t in daten]
    if len(uni_liste) >= 300:
        out["universum"] = teste(daten, uni_liste, fred, "universum", diag)
    diag["fehler"] = diag["fehler"][:30]
    os.makedirs(ziel, exist_ok=True)
    with open(os.path.join(ziel, "swing_test.json"), "w", encoding="utf-8") as fh:
        json.dump(sauber(out), fh, ensure_ascii=False, indent=1)
    for k in ("watchlist", "universum"):
        if k in out:
            print(k, json.dumps(sauber(out[k]["statistik"]), ensure_ascii=False))
            print({n: round(v["cagr"], 3) for n, v in out[k]["kennzahlen"].items() if v})
    print(diag)


# ---------------------------------------------------------------- Live-Modelldepot
def kurs(x):
    return "–" if x is None else (f"{x:,.4f}" if abs(x) < 1 else f"{x:,.2f}")


def live(ergebnisse: dict, werte: list, daten: dict, regime: dict, vorher: str, ziel: str) -> dict:
    try:
        with open(os.path.join(vorher, "swing_live.json"), encoding="utf-8") as fh:
            st = json.load(fh)
    except Exception:
        st = {}
    offen = st.get("offen", [])
    geschlossen = st.get("geschlossen", [])
    letzter = st.get("letzter_tag")
    D = str(daten["SPY"].index[-1].date())
    meldungen = []
    neuer_tag = letzter is None or D > letzter

    if neuer_tag:
        noch = []
        for tr in offen:
            r = ergebnisse.get(tr["ticker"])
            df = daten.get(tr["ticker"])
            if r is None or df is None:
                noch.append(tr)
                continue
            f, s = r.merkmale, r.score
            vor_stop = tr.get("gemeldeter_stop", tr["stop"])
            ziel_heute = False
            for d in df.index[df.index > pd.Timestamp(tr["bis"])]:
                ds = str(d.date())
                if ds > D:
                    break
                b = df.loc[d]
                for art, info in schritt(tr, ds, float(b["Open"]), float(b["High"]), float(b["Low"]), float(b["Close"]),
                                         float(f["atr14"].get(d, np.nan)), float(s.get(d, np.nan))):
                    t = tr["ticker"]
                    if art == "verworfen":
                        meldungen.append((3, f"{t}: Einstieg verworfen ({info}) – nicht kaufen"))
                    elif art == "ziel":
                        meldungen.append((4, f"🎯 {t}: Ziel {kurs(info)} erreicht – HALBE Position verkaufen (Teilverkauf), restlichen Stop mindestens auf Einstand {kurs(tr['entry'])}"))
                        ziel_heute = True
                    elif art == "geschlossen" and info in ("Stop", "Nachzieh-Stop", "Einstand-Stop"):
                        meldungen.append((4, f"🔴 {t}: {info} bei {kurs(tr['ausstieg_preis'])} ausgelöst – Trade beendet, Ergebnis {tr['ergebnis']:+.1%}"))
                    elif art == "ausstieg_geplant":
                        meldungen.append((4, f"🔴 SWING VERKAUFEN {t} zur nächsten Eröffnung – Grund: {info} (Stand {tr['wert'] - 1:+.1%})"))
                tr["bis"] = ds
                if tr["status"] not in ("geplant", "offen"):
                    break
            if tr["status"] == "offen" and not tr.get("ausstieg_geplant") and \
                    (ziel_heute or tr["stop"] >= vor_stop + NACHZIEH_MELDEN * float(df["Close"].iloc[-1])):
                meldungen.append((3, f"⬆ {tr['ticker']}: Stop beim Broker auf {kurs(tr['stop'])} anheben (vorher {kurs(vor_stop)})"))
                tr["gemeldeter_stop"] = tr["stop"]
            (noch if tr["status"] in ("geplant", "offen") else geschlossen).append(tr)
        offen = noch

        reg_label = (regime.get("Aktien") or {}).get("label") if isinstance(regime.get("Aktien"), dict) else None
        belegt = {t["ticker"] for t in offen}
        kand = [w for w in werte if w["gruppe"] in ("Aktien", "ETFs") and w["ticker"] not in belegt and w["datum"] == D
                and w["signal"] == "STARK KAUFEN" and (w.get("crv") or 0) >= MIN_CRV
                and (w.get("ext_pct") is None or w["ext_pct"] < MAX_EXT) and reg_label != "RISK-OFF"
                and not (w.get("extras") or {}).get("earnings_sperre")]
        try:
            import benachrichtigung as B
            top = {w["ticker"] for w in kand if B.ist_top(w, {"Aktien": regime.get("Aktien") or {}})}
        except Exception:
            top = set()
        for w in sorted(kand, key=lambda x: -(x["score"] or 0))[:max(0, MAX_POS - len(offen))]:
            tr = neuer_trade(w["ticker"], D, w["stop"], w["ziel"], w["score"], w["kurs"])
            tr["top"] = w["ticker"] in top
            offen.append(tr)
            meldungen.append((5 if tr["top"] else 4,
                              f"🟢 SWING KAUFEN {w['ticker']}{' ⭐' if tr['top'] else ''} zur nächsten Eröffnung (Schluss {kurs(w['kurs'])}) · "
                              f"Stop {kurs(w['stop'])} · Ziel {kurs(w['ziel'])} (dort halbe Position) · CRV {w['crv']:.1f}"))
        st["letzter_tag"] = D

    # aktuelle Kurse für die Anzeige
    for tr in offen:
        df = daten.get(tr["ticker"])
        if df is not None and len(df):
            tr["kurs_aktuell"] = float(df["Close"].iloc[-1])
    fertig = [t for t in geschlossen if t.get("status") == "geschlossen"]
    st.update({"version": 1, "regeln": REGELN, "offen": offen, "geschlossen": geschlossen[-300:],
               "bilanz": trade_statistik([{**t, "einstieg": t["einstieg_tag"], "tage": t["tage"]} for t in fertig]) if fertig else {"trades": 0},
               "start": st.get("start") or D, "letzte_meldungen": [m for _, m in meldungen] or st.get("letzte_meldungen", [])})
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if topic and meldungen and letzter is not None:
        try:
            import benachrichtigung as B
            prio = max(p for p, _ in meldungen)
            arten = []
            for zeichen, wort in (("🟢", "KAUFEN"), ("🔴", "VERKAUFEN"), ("🎯", "Teilverkauf"), ("⬆", "Stop anheben")):
                if any(m.startswith(zeichen) for _, m in meldungen):
                    arten.append(wort)
            titel = "Swing: " + (" · ".join(arten) or "Änderungen im Modell-Depot")
            B.senden(topic, titel, "\n".join(f"• {m}" for _, m in sorted(meldungen, key=lambda x: -x[0])) + "\n\nKeine Anlageberatung.", prio)
        except Exception as ex:
            print("Swing-Push fehlgeschlagen:", repr(ex)[:200])
    os.makedirs(ziel, exist_ok=True)
    with open(os.path.join(ziel, "swing_live.json"), "w", encoding="utf-8") as fh:
        json.dump(sauber(st), fh, ensure_ascii=False, indent=1)
    return st


if __name__ == "__main__":
    backtest_main(*sys.argv[1:])
