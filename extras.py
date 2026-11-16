"""
Signal-Radar – Zusatzdaten (alle kostenlos, alle fehlertolerant)
================================================================
- FRED (ohne Schlüssel):  Hochzins-Kreditaufschlag (BAMLH0A0HYM2) und Zinskurve (T10Y2Y) -> Marktampel
- edgartools (SEC):       Insiderkäufe am offenen Markt (Form 4, Code "P") der letzten 90 Tage
- yfinance:               nächster Earnings-Termin, Qualität (ROE, Marge, Verschuldung), Sektor
- ccxt (OKX/Bitget/Gate): Funding Rates der Perpetual-Futures -> Überhitzung im Kryptomarkt

Fällt eine Quelle aus, bleibt der Faktor neutral – das Radar funktioniert immer.
Diese Zusatzfaktoren gibt es nur für den aktuellen Tag (keine saubere Historie),
daher fließen sie NICHT in den Backtest ein, sondern als begrenzte Zusatzpunkte.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import engine as E

# Zusatzpunkte (maximal) auf den Score
PUNKTE_INSIDER = 8
PUNKTE_QUALITAET = 6
PUNKTE_SEKTOR = 6
PUNKTE_FUNDING = 8
EARNINGS_SPERRE_TAGE = 7          # Kalendertage vor Quartalszahlen: kein Neukauf
INSIDER_TAGE = 90

# Ablaufzeiten des Caches (Stunden)
TTL = {"insider": 20, "info": 24, "earnings": 12}

SEKTOR_ETF = {
    "Technology": "XLK", "Financial Services": "XLF", "Healthcare": "XLV", "Energy": "XLE",
    "Consumer Cyclical": "XLY", "Communication Services": "XLC", "Industrials": "XLI",
    "Basic Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE", "Consumer Defensive": "XLP",
}
# Ausländische ADRs reichen kein Form 4 ein
KEIN_FORM4 = {"ASML", "TSM", "SAP", "NVO"}


def _jetzt():
    return datetime.now(timezone.utc)


def _frisch(eintrag, art):
    try:
        t = datetime.fromisoformat(eintrag["_zeit"])
        return _jetzt() - t < timedelta(hours=TTL[art])
    except Exception:
        return False


def _stempel(d):
    d = dict(d)
    d["_zeit"] = _jetzt().isoformat(timespec="seconds")
    return d


# ---------------------------------------------------------------- FRED
def lade_fred(serien=("BAMLH0A0HYM2", "T10Y2Y")) -> pd.DataFrame:
    teile = []
    for s in serien:
        try:
            df = pd.read_csv(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={s}")
            df.columns = ["datum", s]
            df["datum"] = pd.to_datetime(df["datum"])
            df[s] = pd.to_numeric(df[s], errors="coerce")
            teile.append(df.set_index("datum")[s])
        except Exception:
            continue
    return pd.concat(teile, axis=1).sort_index() if teile else pd.DataFrame()


# ---------------------------------------------------------------- Insider (SEC Form 4)
def insider_kaeufe(ticker: str, identitaet: str, max_filings: int = 25) -> dict:
    from edgar import Company, set_identity
    set_identity(identitaet)
    start = (_jetzt() - timedelta(days=INSIDER_TAGE)).strftime("%Y-%m-%d")
    firma = Company(ticker)
    eigene_cik = str(int(firma.cik))
    fs = firma.get_filings(form="4", filing_date=f"{start}:", trigger_full_load=False)
    kaeufer, summe, n_kauf, n_filings = set(), 0.0, 0, 0
    for i, f in enumerate(fs):
        if i >= max_filings:
            break
        n_filings += 1
        try:
            f4 = f.obj()
            # Nur Meldungen über Aktien DIESER Firma (nicht Käufe der Firma in anderen Unternehmen)
            try:
                if str(int(f4.issuer.cik)) != eigene_cik:
                    continue
            except Exception:
                continue
            mt = f4.market_trades
            if mt is None or mt.empty or "Code" not in mt.columns:
                continue
            p = mt[mt["Code"] == "P"]
            if p.empty:
                continue
            wert = float((pd.to_numeric(p["Shares"], errors="coerce") * pd.to_numeric(p["Price"], errors="coerce")).sum())
            kaeufer.add(str(getattr(f4, "insider_name", "") or f"#{i}"))
            summe += wert if np.isfinite(wert) else 0.0
            n_kauf += 1
        except Exception:
            continue
        time.sleep(0.12)  # SEC: max. 10 Anfragen/Sekunde
    return {"kaeufer": len(kaeufer), "summe_usd": round(summe), "kaeufe": n_kauf, "filings_geprueft": n_filings}


def insider_punkte(d: dict | None) -> tuple[float, str | None]:
    if not d:
        return 0.0, None
    k, s = d.get("kaeufer", 0), d.get("summe_usd", 0)
    if k >= 2 or s >= 1_000_000:
        return PUNKTE_INSIDER, f"Insiderkäufe: {k} Manager kauften in 90 Tagen für ${s:,.0f} am offenen Markt"
    if k == 1 and s >= 100_000:
        return PUNKTE_INSIDER / 2, f"Insiderkauf: ein Manager kaufte für ${s:,.0f} am offenen Markt"
    return 0.0, None


# ---------------------------------------------------------------- yfinance: Earnings + Info
def naechste_earnings(ticker: str) -> str | None:
    import yfinance as yf
    tk = yf.Ticker(ticker)
    heute = _jetzt().date()
    try:
        cal = tk.calendar
        ds = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if ds:
            ds = [pd.Timestamp(d).date() for d in (ds if isinstance(ds, (list, tuple)) else [ds])]
            zuk = sorted(d for d in ds if d >= heute)
            if zuk:
                return zuk[0].isoformat()
    except Exception:
        pass
    try:
        ed = tk.get_earnings_dates(limit=6)
        if ed is not None and len(ed):
            zuk = sorted(d.date() for d in ed.index if d.date() >= heute)
            if zuk:
                return zuk[0].isoformat()
    except Exception:
        pass
    return None


def firmen_info(ticker: str) -> dict:
    import yfinance as yf
    i = yf.Ticker(ticker).info or {}
    return {k: i.get(k) for k in ["sector", "returnOnEquity", "profitMargins", "debtToEquity", "grossMargins"]}


def qualitaet_scores(infos: dict[str, dict]) -> dict[str, float]:
    """Rang innerhalb der Watchlist: hohe Rendite/Marge, wenig Schulden -> +1 ... -1."""
    df = pd.DataFrame({t: d for t, d in infos.items() if d}).T
    if df.empty:
        return {}
    teile = []
    for col, hoch_gut in [("returnOnEquity", True), ("profitMargins", True), ("debtToEquity", False)]:
        if col in df.columns:
            s = pd.to_numeric(df[col], errors="coerce")
            teile.append(s.rank(pct=True, ascending=hoch_gut))
    if not teile:
        return {}
    r = pd.concat(teile, axis=1).mean(axis=1, skipna=True)
    return {t: float(2 * v - 1) for t, v in r.items() if np.isfinite(v)}


# ---------------------------------------------------------------- ccxt: Funding Rates
def funding_raten(coins: list[str]) -> dict[str, dict]:
    import ccxt
    out = {}
    for ex_name in ["okx", "bitget", "gate"]:
        fehlend = [c for c in coins if c not in out]
        if not fehlend:
            break
        try:
            ex = getattr(ccxt, ex_name)({"enableRateLimit": True, "timeout": 15000})
            ex.load_markets()
        except Exception:
            continue
        for c in fehlend:
            sym = f"{c}/USDT:USDT"
            if sym not in ex.markets:
                continue
            try:
                hist = ex.fetch_funding_rate_history(sym, limit=21)
                raten = [h["fundingRate"] for h in hist if h.get("fundingRate") is not None]
                if raten:
                    out[c] = {"rate_8h_avg": float(np.mean(raten[-21:])), "boerse": ex_name, "n": len(raten)}
            except Exception:
                continue
    return out


def funding_punkte(d: dict | None) -> tuple[float, str | None]:
    if not d:
        return 0.0, None
    r = d["rate_8h_avg"]
    jahr = r * 3 * 365
    if r > 0.0003:
        return -PUNKTE_FUNDING, f"Futures überhitzt: Funding {jahr:+.0%} p.a. – viele gehebelte Käufer, Rückschlaggefahr"
    if r > 0.00015:
        return -PUNKTE_FUNDING / 2, f"Futures warm: Funding {jahr:+.0%} p.a."
    if r < -0.0001:
        return PUNKTE_FUNDING / 2, f"Negatives Funding ({jahr:+.0%} p.a.) – viele Leerverkäufer, Short-Squeeze möglich"
    return 0.0, None


# ---------------------------------------------------------------- Sammeln (für GitHub Actions)
def sammle(aktien: list[str], krypto: list[str], cache: dict | None, identitaet: str) -> dict:
    cache = cache or {}
    alt = cache.get("werte", {})
    werte, diag = {}, {"insider_neu": 0, "info_neu": 0, "earnings_neu": 0, "fehler": []}
    us = [t for t in aktien if "." not in t]
    for t in aktien:
        e = dict(alt.get(t, {}))
        # Insider
        if t in us and t not in KEIN_FORM4:
            if not (e.get("insider") and _frisch(e["insider"], "insider")):
                try:
                    e["insider"] = _stempel(insider_kaeufe(t, identitaet))
                    diag["insider_neu"] += 1
                except Exception as ex:
                    diag["fehler"].append(f"insider {t}: {type(ex).__name__}: {str(ex)[:120]}")
        # Firmeninfo
        if not (e.get("info") and _frisch(e["info"], "info")):
            try:
                e["info"] = _stempel(firmen_info(t))
                diag["info_neu"] += 1
            except Exception as ex:
                diag["fehler"].append(f"info {t}: {type(ex).__name__}: {str(ex)[:120]}")
        # Earnings
        if not (e.get("earnings") and _frisch(e["earnings"], "earnings")):
            try:
                e["earnings"] = _stempel({"datum": naechste_earnings(t)})
                diag["earnings_neu"] += 1
            except Exception as ex:
                diag["fehler"].append(f"earnings {t}: {type(ex).__name__}: {str(ex)[:120]}")
        werte[t] = e
    # Qualität (Rang innerhalb der Watchlist)
    q = qualitaet_scores({t: (w.get("info") or {}) for t, w in werte.items()})
    for t, v in q.items():
        werte[t]["qualitaet"] = round(v, 3)
    # Funding
    coins = [k.replace("-USD", "") for k in krypto]
    try:
        fr = funding_raten(coins)
    except Exception as ex:
        fr = {}
        diag["fehler"].append(f"funding: {type(ex).__name__}: {str(ex)[:120]}")
    for k in krypto:
        c = k.replace("-USD", "")
        werte[k] = {"funding": fr.get(c)}
    diag["funding_coins"] = len(fr)
    diag["fehler"] = diag["fehler"][:40]
    return {"erstellt_utc": _jetzt().isoformat(timespec="seconds"), "werte": werte, "diagnose": diag}


# ---------------------------------------------------------------- Anwenden (App + Export)
def anwenden(ergebnisse: dict, extras: dict | None, daten: dict) -> None:
    """Ergänzt Score, Signal und Begründung um die Zusatzfaktoren (nur aktueller Tag)."""
    werte = (extras or {}).get("werte", {})
    heute = _jetzt().date()
    for t, r in ergebnisse.items():
        basis = float(r.score.dropna().iloc[-1])
        punkte, gruende, info = 0.0, [], {}
        e = werte.get(t, {})

        # Insider
        p, txt = insider_punkte(e.get("insider"))
        if txt:
            punkte += p
            gruende.append(("+", txt))
        if e.get("insider"):
            info["insider"] = {k: v for k, v in e["insider"].items() if not k.startswith("_")}

        # Qualität
        q = e.get("qualitaet")
        if q is not None:
            punkte += PUNKTE_QUALITAET * q
            info["qualitaet"] = q
            if q > 0.4:
                gruende.append(("+", "Hohe Qualität: profitabel, wenig verschuldet (im Vergleich zur Watchlist)"))
            elif q < -0.4:
                gruende.append(("-", "Schwächere Qualität: geringe Profitabilität oder hohe Schulden"))

        # Sektor-relative Stärke (3 Monate)
        sektor = ((e.get("info") or {}).get("sector"))
        etf = SEKTOR_ETF.get(sektor or "")
        if etf and etf in daten and t in daten:
            try:
                a = daten[t]["Close"]
                b = daten[etf]["Close"].reindex(a.index).ffill()
                rs = (a.iloc[-1] / a.iloc[-63] - 1) - (b.iloc[-1] / b.iloc[-63] - 1)
                if np.isfinite(rs):
                    punkte += PUNKTE_SEKTOR * math.tanh(rs / 0.10)
                    info["sektor"] = {"name": sektor, "etf": etf, "rs_3m": round(float(rs), 4)}
                    if rs > 0.05:
                        gruende.append(("+", f"Stärker als der eigene Sektor ({sektor}, 3 Monate {rs:+.0%})"))
                    elif rs < -0.05:
                        gruende.append(("-", f"Schwächer als der eigene Sektor ({sektor}, 3 Monate {rs:+.0%})"))
            except Exception:
                pass

        # Funding (Krypto)
        p, txt = funding_punkte(e.get("funding"))
        if txt:
            punkte += p
            gruende.append(("+" if p > 0 else "-", txt))
        if e.get("funding"):
            info["funding"] = e["funding"]

        score_neu = float(np.clip(basis + punkte, -100, 100))
        signal = E.signal_aus(score_neu)

        # Earnings-Sperre
        ed = (e.get("earnings") or {}).get("datum")
        if ed:
            info["earnings"] = ed
            try:
                tage = (datetime.fromisoformat(ed).date() - heute).days
                if 0 <= tage <= EARNINGS_SPERRE_TAGE:
                    gruende.append(("-", f"Quartalszahlen am {datetime.fromisoformat(ed):%d.%m.} – kein Neukauf davor (Kurssprung-Risiko)"))
                    info["earnings_sperre"] = True
                    if signal in ("STARK KAUFEN", "KAUFEN"):
                        signal = "HALTEN"
            except Exception:
                pass

        r.score_aktuell = score_neu
        r.zusatzpunkte = punkte
        r.signal = signal
        r.extras = info
        r.gruende = list(r.gruende) + gruende
