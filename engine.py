"""
Signal-Radar – Rechen-Engine
============================
Alle Berechnungen sind kausal: Der Wert an Tag t nutzt nur Daten bis einschließlich t.
Die Gewichte sind a priori festgelegt (nicht auf Daten optimiert), um Overfitting zu vermeiden.
"""
from __future__ import annotations

import math
import zlib
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------------------
# EINSTELLUNGEN
# --------------------------------------------------------------------------------------
STANDARD_AKTIEN = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "BRK-B", "JPM",
    "LLY", "V", "MA", "UNH", "XOM", "COST", "NFLX", "AMD", "ORCL", "CRM",
    "ADBE", "PLTR", "WMT", "HD", "PG", "JNJ", "ASML", "TSM", "SAP", "NVO",
]
STANDARD_ETFS = ["SPY", "QQQ", "IWM", "SMH", "XLK", "XLF", "XLE", "XLV", "GLD", "TLT"]
STANDARD_KRYPTO = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD",
    "ADA-USD", "DOGE-USD", "AVAX-USD", "LINK-USD",
]
HILFS_TICKER = ["SPY", "^VIX", "^VIX3M", "BTC-USD",
                "XLK", "XLF", "XLV", "XLE", "XLY", "XLC", "XLI", "XLB", "XLU", "XLRE", "XLP"]  # Sektor-ETFs

FORWARD_TAGE = 20          # Haltedauer für die historische Auswertung (Handelstage/Bars)
KAUF_SCHWELLE = 15         # Score ab dem "KAUFEN"
STARK_SCHWELLE = 40        # Score ab dem "STARK KAUFEN" / ab -40 "STARK VERKAUFEN"
KOSTEN_PRO_WECHSEL = 0.001 # 0,1 % Kosten + Slippage pro Ein-/Ausstieg im Backtest

GEWICHTE = {               # Summe = 1.0
    "trend": 0.25,
    "momentum": 0.20,
    "rel_staerke": 0.15,
    "akkumulation": 0.15,
    "zone": 0.15,
    "volatilitaet": 0.10,
}

SIGNAL_FARBEN = {
    "STARK KAUFEN": "#0f7b3f",
    "KAUFEN": "#3fae5a",
    "HALTEN": "#8a8f98",
    "VERKAUFEN": "#e0782f",
    "STARK VERKAUFEN": "#c62828",
}


def ist_krypto(ticker: str) -> bool:
    return ticker.upper().endswith("-USD")


def bars_pro_jahr(ticker: str) -> int:
    return 365 if ist_krypto(ticker) else 252


# --------------------------------------------------------------------------------------
# DATEN
# --------------------------------------------------------------------------------------
def _normalisiere(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=str.title)
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None) if getattr(df.index, "tz", None) else pd.to_datetime(df.index)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["Close"])
    for c in ["Open", "High", "Low"]:
        if c in df.columns:
            df[c] = df[c].fillna(df["Close"])
    if "Volume" not in df.columns:
        df["Volume"] = 0.0
    df["Volume"] = df["Volume"].fillna(0.0).astype(float)
    return df


def lade_yahoo(tickers: list[str], period: str = "10y") -> dict[str, pd.DataFrame]:
    """Lädt Tagesdaten kostenlos über yfinance (in Blöcken, mit Wiederholung)."""
    import yfinance as yf

    out: dict[str, pd.DataFrame] = {}
    tickers = list(dict.fromkeys(t.strip().upper() for t in tickers if t.strip()))
    for i in range(0, len(tickers), 25):
        block = tickers[i:i + 25]
        raw = None
        for versuch in range(3):
            try:
                raw = yf.download(block, period=period, interval="1d", auto_adjust=True,
                                  group_by="ticker", threads=True, progress=False)
                break
            except Exception:
                time.sleep(2 * (versuch + 1))
        if raw is None or raw.empty:
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            # Ebene mit 'Close' finden (Reihenfolge je nach yfinance-Version unterschiedlich)
            lvl_feld = 1 if "Close" in raw.columns.get_level_values(1) else 0
            lvl_ticker = 1 - lvl_feld
            for t in block:
                if t in raw.columns.get_level_values(lvl_ticker):
                    sub = raw.xs(t, axis=1, level=lvl_ticker)
                    sub = _normalisiere(sub)
                    if len(sub) > 50:
                        out[t] = sub
        else:
            sub = _normalisiere(raw)
            if len(sub) > 50:
                out[block[0]] = sub
    return out


def nur_fertige_tage(daten: dict) -> dict:
    """Unfertigen Handelstag entfernen: Aktien/ETFs vor 21:15 UTC (nach US-Schluss), Krypto den laufenden UTC-Tag.
    So zeigen App, Push-Nachrichten und Protokoll immer dieselben Signale aus abgeschlossenen Tagen."""
    from datetime import datetime, timezone
    jetzt = datetime.now(timezone.utc)
    heute = jetzt.date()
    aktien_fertig = jetzt.hour * 60 + jetzt.minute >= 21 * 60 + 15
    out = {}
    for t, df in daten.items():
        if len(df) and df.index[-1].date() >= heute and (ist_krypto(t) or not aktien_fertig):
            df = df.iloc[:-1]
        out[t] = df
    return out


def demo_daten(tickers: list[str], jahre: int = 8, seed: int = 7) -> dict[str, pd.DataFrame]:
    """Synthetische, realistische Kursdaten (Regimewechsel, Volumen) – nur zum Ausprobieren."""
    out = {}
    rng_markt = np.random.default_rng(seed)
    n_b = 252 * jahre
    idx_b = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n_b)
    n_k = 365 * jahre
    idx_k = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n_k, freq="D")

    def regime_pfad(n, rng, mu_bull, mu_bear, vol_bull, vol_bear, p_wechsel):
        state = 1
        rets = np.empty(n)
        vols = np.empty(n)
        for i in range(n):
            if rng.random() < p_wechsel:
                state = 1 - state
            mu, vol = (mu_bull, vol_bull) if state else (mu_bear, vol_bear)
            vols[i] = vol
            rets[i] = rng.normal(mu, vol)
        return rets, vols

    markt_b, markt_vol = regime_pfad(n_b, rng_markt, 0.0006, -0.0008, 0.009, 0.02, 0.008)
    markt_k, _ = regime_pfad(n_k, rng_markt, 0.002, -0.002, 0.03, 0.05, 0.006)

    for t in list(dict.fromkeys(tickers + HILFS_TICKER)):
        rng = np.random.default_rng(zlib.crc32(t.encode()))
        krypto = ist_krypto(t)
        idx = idx_k if krypto else idx_b
        markt = markt_k if krypto else markt_b
        n = len(idx)
        if t == "^VIX" or t == "^VIX3M":
            base = pd.Series(markt_vol * math.sqrt(252) * 100, index=idx).rolling(10, min_periods=1).mean()
            lvl = base * (1.0 if t == "^VIX" else 0.95) + (0 if t == "^VIX" else 1.5)
            close = lvl.values + rng.normal(0, 0.5, n)
            df = pd.DataFrame({"Open": close, "High": close * 1.03, "Low": close * 0.97,
                               "Close": close, "Volume": 0.0}, index=idx)
            out[t] = df
            continue
        beta = 1.0 if t in ("SPY", "BTC-USD") else rng.uniform(0.6, 1.6)
        idio_vol = 0.0 if t in ("SPY", "BTC-USD") else rng.uniform(0.008, 0.02) * (2.0 if krypto else 1.0)
        drift = rng.normal(0.0002, 0.0004)
        r = beta * markt + rng.normal(drift, idio_vol, n) if idio_vol else markt
        close = 100 * np.exp(np.cumsum(r))
        spanne = np.abs(rng.normal(0, 1, n)) * (np.abs(r) + 0.006) + 0.004
        high = close * (1 + spanne * rng.uniform(0.3, 0.7, n))
        low = close * (1 - spanne * rng.uniform(0.3, 0.7, n))
        open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.003, n))
        high = np.maximum.reduce([high, open_, close])
        low = np.minimum.reduce([low, open_, close])
        vol = (1e6 * rng.lognormal(0, 0.3, n)) * (1 + 25 * np.abs(r))
        out[t] = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol}, index=idx)
    return out


# --------------------------------------------------------------------------------------
# INDIKATOREN (alle kausal)
# --------------------------------------------------------------------------------------
def atr(df: pd.DataFrame, n: int) -> pd.Series:
    pc = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def _rolling_argext(arr: np.ndarray, w: int, minimum: bool) -> np.ndarray:
    """Absoluter Index des Minimums/Maximums im Fenster [t-w+1, t] (Anfang: expandierend)."""
    n = len(arr)
    res = np.zeros(n, dtype=int)
    f = np.argmin if minimum else np.argmax
    for t in range(min(w - 1, n)):
        res[t] = f(arr[: t + 1])
    if n >= w:
        win = np.lib.stride_tricks.sliding_window_view(arr, w)
        res[w - 1:] = f(win, axis=1) + np.arange(n - w + 1)
    return res


def anchored_vwap(df: pd.DataFrame, anker: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """AVWAP und volumengewichtete Standardabweichung ab Ankerindex je Tag."""
    tp = ((df["High"] + df["Low"] + df["Close"]) / 3).values
    v = df["Volume"].values.astype(float)
    if v.sum() == 0:
        v = np.ones_like(v)
    pv = np.r_[0, np.cumsum(tp * v)]
    p2v = np.r_[0, np.cumsum(tp * tp * v)]
    cv = np.r_[0, np.cumsum(v)]
    t = np.arange(len(tp))
    sv = cv[t + 1] - cv[anker]
    sv = np.where(sv <= 0, np.nan, sv)
    vw = (pv[t + 1] - pv[anker]) / sv
    var = (p2v[t + 1] - p2v[anker]) / sv - vw ** 2
    return vw, np.sqrt(np.clip(var, 0, None))


def volume_profile(h: np.ndarray, l: np.ndarray, v: np.ndarray, bins: int = 40, va: float = 0.70):
    """Volumenprofil aus Tagesbars: Volumen wird gleichmäßig über High-Low verteilt.
    Rückgabe: POC, VAL, VAH."""
    lo, hi = float(np.min(l)), float(np.max(h))
    if not np.isfinite(lo) or hi <= lo:
        return np.nan, np.nan, np.nan
    edges = np.linspace(lo, hi, bins + 1)
    if v.sum() == 0:
        v = np.ones_like(v)
    rng = np.maximum(h - l, (hi - lo) * 1e-6)
    overlap = np.clip(np.minimum(h[:, None], edges[1:]) - np.maximum(l[:, None], edges[:-1]), 0, None)
    vol = (overlap / rng[:, None] * v[:, None]).sum(axis=0)
    mitte = (edges[:-1] + edges[1:]) / 2
    poc_i = int(np.argmax(vol))
    ziel = vol.sum() * va
    lo_i = hi_i = poc_i
    summe = vol[poc_i]
    while summe < ziel and (lo_i > 0 or hi_i < bins - 1):
        unten = vol[lo_i - 1] if lo_i > 0 else -1
        oben = vol[hi_i + 1] if hi_i < bins - 1 else -1
        if oben >= unten:
            hi_i += 1
            summe += vol[hi_i]
        else:
            lo_i -= 1
            summe += vol[lo_i]
    return mitte[poc_i], edges[lo_i], edges[hi_i + 1]


def rolling_profile(df: pd.DataFrame, fenster: int = 120, schritt: int = 5) -> pd.DataFrame:
    h, l, v = df["High"].values, df["Low"].values, df["Volume"].values.astype(float)
    n = len(df)
    poc = np.full(n, np.nan)
    val = np.full(n, np.nan)
    vah = np.full(n, np.nan)
    punkte = list(range(fenster - 1, n, schritt))
    if n - 1 not in punkte and n >= fenster:
        punkte.append(n - 1)
    for t in punkte:
        s = slice(t - fenster + 1, t + 1)
        poc[t], val[t], vah[t] = volume_profile(h[s], l[s], v[s])
    out = pd.DataFrame({"poc": poc, "val": val, "vah": vah}, index=df.index).ffill()
    return out


def merkmale(df: pd.DataFrame, ticker: str, bench: pd.DataFrame | None) -> pd.DataFrame:
    bpy = bars_pro_jahr(ticker)
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    f = pd.DataFrame(index=df.index)
    f["close"] = c
    f["ret1"] = c.pct_change()
    f["atr14"] = atr(df, 14)
    f["atr5"] = atr(df, 5)
    f["atr50"] = atr(df, 50)
    f["sma20"] = c.rolling(20).mean()
    f["sma50"] = c.rolling(50).mean()
    f["sma200"] = c.rolling(200).mean()
    f["slope200"] = f["sma200"] / f["sma200"].shift(20) - 1
    f["vol_ann"] = f["ret1"].rolling(bpy, min_periods=120).std() * math.sqrt(bpy)
    f["vol20"] = f["ret1"].rolling(20).std() * math.sqrt(bpy)
    f["vol_pct"] = f["vol20"].rolling(bpy, min_periods=120).rank(pct=True)

    # Momentum 12-1 (letzter Monat ausgelassen), risikoadjustiert
    m1 = 21 if bpy == 252 else 30
    f["mom_12_1"] = c.shift(m1) / c.shift(bpy) - 1
    f["mom_6m"] = c / c.shift(bpy // 2) - 1

    # Relative Stärke zum Benchmark (SPY bzw. BTC)
    if bench is not None and ticker not in ("SPY", "BTC-USD"):
        b = bench["Close"].reindex(df.index).ffill()
        f["rs_6m"] = f["mom_6m"] - (b / b.shift(bpy // 2) - 1)
    else:
        f["rs_6m"] = 0.0

    # Akkumulation / Distribution (Close Location Value × Volumen)
    spanne = (h - l).replace(0, np.nan)
    clv = (((c - l) - (h - c)) / spanne).fillna(0)
    vsum = v.rolling(20).sum().replace(0, np.nan)
    f["ad_flow"] = ((clv * v).rolling(20).sum() / vsum).fillna(0)
    up_v = v.where(c > c.shift(1), 0).rolling(20).sum()
    dn_v = v.where(c < c.shift(1), 0).rolling(20).sum()
    f["updown"] = (up_v + 1) / (dn_v + 1)
    f["rvol"] = v / v.rolling(50).median().replace(0, np.nan)

    # Überdehnung: Abstand zur SMA50 in ATR, als Perzentil der letzten ~3 Jahre
    f["ext"] = (c - f["sma50"]) / f["atr14"]
    f["ext_pct"] = f["ext"].rolling(3 * bpy, min_periods=bpy).rank(pct=True)

    # Anchored VWAPs: ab 52-Wochen-Tief und ab 52-Wochen-Hoch
    anker_tief = _rolling_argext(l.values, bpy, minimum=True)
    anker_hoch = _rolling_argext(h.values, bpy, minimum=False)
    f["avwap_tief"], f["avwap_tief_sd"] = anchored_vwap(df, anker_tief)
    f["avwap_hoch"], f["avwap_hoch_sd"] = anchored_vwap(df, anker_hoch)
    f["hoch_52w"] = h.rolling(bpy, min_periods=50).max()
    f["tief_52w"] = l.rolling(bpy, min_periods=50).min()
    f["drawdown"] = c / f["hoch_52w"] - 1

    # Volumenprofil (120 Bars)
    prof = rolling_profile(df)
    f = f.join(prof)

    # Liquiditäts-Sweep: unter Swing-Tief der Bars t-20..t-3 gestochen, Schluss wieder darüber
    swing_tief = l.shift(3).rolling(18).min()
    sweep = (l < swing_tief - 0.1 * f["atr14"]) & (c > swing_tief)
    f["swing_tief"] = swing_tief
    f["sweep"] = sweep.astype(float)
    f["sweep_kuerzlich"] = sweep.astype(float).rolling(5, min_periods=1).max()
    swing_hoch = h.shift(3).rolling(18).max()
    f["swing_hoch"] = swing_hoch
    f["sweep_oben"] = ((h > swing_hoch + 0.1 * f["atr14"]) & (c < swing_hoch)).astype(float).rolling(5, min_periods=1).max()
    return f


# --------------------------------------------------------------------------------------
# MARKTREGIME
# --------------------------------------------------------------------------------------
def regime_aktien(daten: dict[str, pd.DataFrame], aktien: list[str], fred: pd.DataFrame | None = None) -> pd.DataFrame:
    spy = daten.get("SPY")
    if spy is None:
        return pd.DataFrame()
    c = spy["Close"]
    sma200, sma50 = c.rolling(200).mean(), c.rolling(50).mean()
    r = pd.DataFrame(index=spy.index)
    r["trend"] = np.where(c > sma200, 1.0, -1.0)
    r["kreuz"] = np.where(sma50 > sma200, 1.0, -1.0)
    vix = daten.get("^VIX")
    vix3m = daten.get("^VIX3M")
    if vix is not None:
        vx = vix["Close"].reindex(r.index).ffill()
        r["vix"] = vx
        r["vix_stufe"] = np.select([vx < 18, vx > 28], [1.0, -1.0], 0.0)
        if vix3m is not None:
            v3 = vix3m["Close"].reindex(r.index).ffill()
            ts = vx / v3
            r["vix_struktur"] = ts
            r["struktur"] = np.select([ts < 0.95, ts > 1.03], [1.0, -1.0], 0.0)
        else:
            r["struktur"] = 0.0
    else:
        vol = c.pct_change().rolling(20).std()
        vp = vol.rolling(252, min_periods=120).rank(pct=True)
        r["vix_stufe"] = np.select([vp < 0.5, vp > 0.9], [1.0, -1.0], 0.0)
        r["struktur"] = 0.0
    # Marktbreite: Anteil der Aktien über SMA200
    ueber = []
    for t in aktien:
        d = daten.get(t)
        if d is None:
            continue
        s = (d["Close"] > d["Close"].rolling(200).mean()).astype(float)
        s[d["Close"].rolling(200).mean().isna()] = np.nan
        ueber.append(s.reindex(r.index).ffill())
    if ueber:
        breite = pd.concat(ueber, axis=1).mean(axis=1)
        r["breite"] = breite
        r["breite_stufe"] = np.select([breite > 0.6, breite < 0.4], [1.0, -1.0], 0.0)
    else:
        r["breite_stufe"] = 0.0
    # Kreditmarkt (FRED, Hochzins-Aufschlag): steigt er deutlich, ist das ein frühes Stresssignal
    r["kredit_stufe"] = 0.0
    if fred is not None and len(fred) and "BAMLH0A0HYM2" in fred.columns:
        oas = fred["BAMLH0A0HYM2"].dropna().reindex(r.index, method="ffill")
        med = oas.rolling(252, min_periods=60).median()
        chg = oas - oas.shift(20)
        r["hy_spread"] = oas
        r["kredit_stufe"] = np.select([(oas > med + 1.0) | (chg > 0.75), (oas < med) & (chg <= 0)], [-1.0, 1.0], 0.0)
    if fred is not None and len(fred) and "T10Y2Y" in fred.columns:
        r["zinskurve"] = fred["T10Y2Y"].dropna().reindex(r.index, method="ffill")
    r["score"] = (0.30 * r["trend"] + 0.10 * r["kreuz"] + 0.15 * r["vix_stufe"]
                  + 0.10 * r["struktur"] + 0.20 * r["breite_stufe"] + 0.15 * r["kredit_stufe"])
    return r


def regime_krypto(daten: dict[str, pd.DataFrame]) -> pd.DataFrame:
    btc = daten.get("BTC-USD")
    if btc is None:
        return pd.DataFrame()
    c = btc["Close"]
    sma200, sma50 = c.rolling(200).mean(), c.rolling(50).mean()
    vol = c.pct_change().rolling(20).std()
    vp = vol.rolling(365, min_periods=120).rank(pct=True)
    r = pd.DataFrame(index=btc.index)
    r["trend"] = np.where(c > sma200, 1.0, -1.0)
    r["kreuz"] = np.where(sma50 > sma200, 1.0, -1.0)
    r["vola"] = np.select([vp < 0.5, vp > 0.9], [1.0, -1.0], 0.0)
    r["score"] = 0.5 * r["trend"] + 0.25 * r["kreuz"] + 0.25 * r["vola"]
    return r


def regime_label(score: float) -> str:
    if not np.isfinite(score):
        return "UNBEKANNT"
    if score >= 0.4:
        return "RISK-ON"
    if score <= -0.2:
        return "RISK-OFF"
    return "NEUTRAL"


# --------------------------------------------------------------------------------------
# SCORE
# --------------------------------------------------------------------------------------
def komponenten(f: pd.DataFrame) -> pd.DataFrame:
    k = pd.DataFrame(index=f.index)
    c = f["close"]
    d200 = c / f["sma200"] - 1
    k["trend"] = (0.4 * np.tanh(d200 / 0.08) + 0.3 * np.tanh(f["slope200"] / 0.03)
                  + 0.3 * np.sign(f["sma50"] - f["sma200"]))
    vol = f["vol_ann"].clip(lower=0.05)
    k["momentum"] = np.tanh((f["mom_12_1"] / vol) / 0.8)
    k["rel_staerke"] = np.tanh((f["rs_6m"] / (vol * math.sqrt(0.5))) / 0.8)
    k["akkumulation"] = 0.6 * np.tanh(f["ad_flow"] / 0.15) + 0.4 * np.tanh(np.log(f["updown"]) / 0.5)

    aufwaerts = (k["trend"] > 0).astype(float)
    nahe_avwap = ((c - f["avwap_tief"]).abs() <= np.maximum(f["avwap_tief_sd"], f["atr14"])).astype(float)
    in_value = ((c >= f["val"] - 0.5 * f["atr14"]) & (c <= f["poc"] + 0.25 * f["atr14"])).astype(float)
    ruecksetzer = (f["ext_pct"] < 0.3).astype(float)
    bonus = (nahe_avwap + in_value + f["sweep_kuerzlich"] + ruecksetzer) / 3.0 * aufwaerts
    ueberdehnt = (f["ext_pct"] - 0.8).clip(lower=0) / 0.2
    ueber_band = (c > f["avwap_tief"] + 2 * f["avwap_tief_sd"]).astype(float) * 0.3
    k["zone"] = (bonus - ueberdehnt - ueber_band - 0.5 * f["sweep_oben"]).clip(-1, 1)

    ratio = f["atr5"] / f["atr50"]
    kompression = ((0.75 - ratio) / 0.25).clip(-1, 1) * aufwaerts
    stress = (f["vol_pct"] - 0.8).clip(lower=0) / 0.2
    k["volatilitaet"] = (0.5 * kompression - stress).clip(-1, 1)
    return k.astype(float)


def score_aus(k: pd.DataFrame, regime: pd.Series | None) -> pd.Series:
    roh = sum(GEWICHTE[n] * k[n] for n in GEWICHTE) * 100
    if regime is not None and len(regime):
        reg = regime.reindex(k.index).ffill().fillna(0.0)
        daempfung = np.where((reg <= -0.2) & (roh > 0), 0.6, 1.0)
        s = roh * daempfung + 10 * reg
    else:
        s = roh
    return pd.Series(np.clip(s, -100, 100), index=k.index)


def signal_aus(score: float) -> str:
    if not np.isfinite(score):
        return "HALTEN"
    if score >= STARK_SCHWELLE:
        return "STARK KAUFEN"
    if score >= KAUF_SCHWELLE:
        return "KAUFEN"
    if score > -KAUF_SCHWELLE:
        return "HALTEN"
    if score > -STARK_SCHWELLE:
        return "VERKAUFEN"
    return "STARK VERKAUFEN"


def signal_serie(score: pd.Series) -> pd.Series:
    return score.apply(signal_aus)


# --------------------------------------------------------------------------------------
# LEVELS: Stop, Ziel, Positionsgröße
# --------------------------------------------------------------------------------------
def levels(f_last: pd.Series) -> dict:
    c, a = f_last["close"], f_last["atr14"]
    if not (np.isfinite(c) and np.isfinite(a) and a > 0):
        return {"stop": np.nan, "ziel": np.nan, "crv": np.nan}
    stuetzen = [f_last.get(n) for n in ["val", "poc", "avwap_tief", "sma50", "swing_tief"]]
    stuetzen = [s for s in stuetzen if s is not None and np.isfinite(s) and s < c]
    stop = (max(stuetzen) - 0.5 * a) if stuetzen else c - 2.5 * a
    stop = float(np.clip(stop, c - 4 * a, c - 1.5 * a))
    widerst = [f_last.get("vah"), f_last.get("avwap_tief", np.nan) + 2 * f_last.get("avwap_tief_sd", np.nan),
               f_last.get("avwap_hoch"), f_last.get("hoch_52w"), f_last.get("swing_hoch")]
    widerst = [w for w in widerst if w is not None and np.isfinite(w) and w > c + 0.5 * a]
    ziel = min(widerst) if widerst else c + 3 * a
    ziel = float(max(ziel, c + 1.5 * a))
    crv = (ziel - c) / (c - stop) if c > stop else np.nan
    return {"stop": stop, "ziel": ziel, "crv": crv}


def positionsgroesse(kurs: float, stop: float, konto: float, risiko_pct: float, max_anteil: float = 0.20) -> tuple[float, float]:
    if not (np.isfinite(kurs) and np.isfinite(stop)) or kurs <= stop:
        return 0.0, 0.0
    risiko = konto * risiko_pct / 100
    stueck = risiko / (kurs - stop)
    stueck = min(stueck, konto * max_anteil / kurs)
    return stueck, stueck * kurs


# --------------------------------------------------------------------------------------
# BEGRÜNDUNG
# --------------------------------------------------------------------------------------
def begruendung(f_last: pd.Series, k_last: pd.Series, reg_label: str) -> list[tuple[str, str]]:
    """Liste aus (Richtung, Text). Richtung: '+', '-', '0'."""
    g = []
    c = f_last["close"]

    def add(cond_pos, cond_neg, txt_pos, txt_neg, txt_neu=None):
        if cond_pos:
            g.append(("+", txt_pos))
        elif cond_neg:
            g.append(("-", txt_neg))
        elif txt_neu:
            g.append(("0", txt_neu))

    add(k_last["trend"] > 0.3, k_last["trend"] < -0.3,
        "Intakter Aufwärtstrend (über 200-Tage-Linie, Linie steigt)",
        "Abwärtstrend (unter 200-Tage-Linie oder Linie fällt)",
        "Kein klarer Trend")
    m = f_last["mom_12_1"]
    add(k_last["momentum"] > 0.3, k_last["momentum"] < -0.3,
        f"Starkes 12-Monats-Momentum ({m:+.0%})",
        f"Schwaches 12-Monats-Momentum ({m:+.0%})")
    rs = f_last["rs_6m"]
    add(k_last["rel_staerke"] > 0.3, k_last["rel_staerke"] < -0.3,
        f"Stärker als der Gesamtmarkt (6 Monate {rs:+.0%} relativ)",
        f"Schwächer als der Gesamtmarkt (6 Monate {rs:+.0%} relativ)")
    add(k_last["akkumulation"] > 0.3, k_last["akkumulation"] < -0.3,
        "Volumen deutet auf Akkumulation (Käufe überwiegen)",
        "Volumen deutet auf Distribution (Verkäufe überwiegen)")
    if f_last.get("sweep_kuerzlich", 0) > 0 and k_last["trend"] > 0:
        g.append(("+", "Liquiditäts-Sweep: Tief unterschritten und sofort zurückerobert"))
    if f_last.get("sweep_oben", 0) > 0:
        g.append(("-", "Fehlausbruch nach oben: Hoch überschritten, aber darunter geschlossen"))
    if np.isfinite(f_last.get("val", np.nan)) and f_last["val"] - 0.5 * f_last["atr14"] <= c <= f_last["poc"] + 0.25 * f_last["atr14"]:
        g.append(("+" if k_last["trend"] > 0 else "0", "Kurs in der Value Area (Zone mit viel gehandeltem Volumen)"))
    if np.isfinite(f_last.get("avwap_tief", np.nan)) and abs(c - f_last["avwap_tief"]) <= max(f_last["avwap_tief_sd"], f_last["atr14"]):
        g.append(("+" if k_last["trend"] > 0 else "0", "Kurs nahe dem Durchschnittspreis seit dem 52-Wochen-Tief (Anchored VWAP)"))
    ep = f_last.get("ext_pct", np.nan)
    if np.isfinite(ep):
        if ep > 0.95:
            g.append(("-", f"Stark überdehnt (Abstand zur 50-Tage-Linie im {ep:.0%}-Perzentil) – Gewinne sichern"))
        elif ep > 0.8:
            g.append(("-", f"Schon weit gelaufen ({ep:.0%}-Perzentil) – Einstieg besser bei Rücksetzer"))
        elif ep < 0.3 and k_last["trend"] > 0:
            g.append(("+", "Rücksetzer im Aufwärtstrend"))
    if np.isfinite(f_last.get("avwap_tief", np.nan)) and c > f_last["avwap_tief"] + 2 * f_last["avwap_tief_sd"]:
        g.append(("-", "Über dem +2σ-Band des Anchored VWAP – statistisch überdehnt"))
    vp = f_last.get("vol_pct", np.nan)
    if np.isfinite(vp) and vp > 0.9:
        g.append(("-", "Ungewöhnlich hohe Schwankung – erhöhtes Risiko"))
    elif k_last["volatilitaet"] > 0.3:
        g.append(("+", "Volatilität zieht sich zusammen (oft vor einem Ausbruch)"))
    if reg_label == "RISK-OFF":
        g.append(("-", "Gesamtmarkt im Risk-Off – Kaufsignale werden abgeschwächt"))
    elif reg_label == "RISK-ON":
        g.append(("+", "Gesamtmarkt im Risk-On"))
    return g


# --------------------------------------------------------------------------------------
# BACKTEST (historische Auswertung der Signale)
# --------------------------------------------------------------------------------------
def auswertung(f: pd.DataFrame, score: pd.Series, h: int = FORWARD_TAGE) -> dict:
    c = f["close"]
    fwd = c.shift(-h) / c - 1
    sig = signal_serie(score)
    gueltig = score.notna() & fwd.notna() & f["sma200"].notna()
    tab = []
    basis = fwd[gueltig]
    for s in SIGNAL_FARBEN:
        m = gueltig & (sig == s)
        r = fwd[m]
        tab.append({
            "Signal": s,
            "Anzahl Tage": int(m.sum()),
            "Trefferquote": float((r > 0).mean()) if len(r) else np.nan,
            f"Ø Rendite {h}T": float(r.mean()) if len(r) else np.nan,
        })
    tab = pd.DataFrame(tab)

    # Einfache Strategie: Einstieg ab KAUFEN, Ausstieg unter -5 (Hysterese), Ausführung am Folgetag
    zustand = pd.Series(np.where(score >= KAUF_SCHWELLE, 1.0, np.where(score < -5, 0.0, np.nan)), index=score.index)
    pos = zustand.ffill().fillna(0.0).shift(1).fillna(0.0)
    ret = c.pct_change().fillna(0.0)
    start = f["sma200"].first_valid_index()
    pos, ret = pos.loc[start:], ret.loc[start:]
    strat = pos * ret - KOSTEN_PRO_WECHSEL * pos.diff().abs().fillna(0.0)
    eq_s = (1 + strat).cumprod()
    eq_b = (1 + ret).cumprod()
    bpy = 365 if len(ret) and (ret.index.to_series().diff().dt.days == 1).mean() > 0.9 else 252
    jahre = max(len(ret) / bpy, 1e-9)

    def kennz(eq):
        if len(eq) < 2:
            return np.nan, np.nan
        cagr = eq.iloc[-1] ** (1 / jahre) - 1
        mdd = (eq / eq.cummax() - 1).min()
        return cagr, mdd

    cagr_s, mdd_s = kennz(eq_s)
    cagr_b, mdd_b = kennz(eq_b)
    return {
        "tabelle": tab,
        "basis_treffer": float((basis > 0).mean()) if len(basis) else np.nan,
        "basis_rendite": float(basis.mean()) if len(basis) else np.nan,
        "equity": pd.DataFrame({"Signal-Strategie": eq_s, "Kaufen & Halten": eq_b}),
        "cagr_strategie": cagr_s, "mdd_strategie": mdd_s,
        "cagr_halten": cagr_b, "mdd_halten": mdd_b,
        "zeit_im_markt": float(pos.mean()) if len(pos) else np.nan,
        "jahre": jahre,
    }


# --------------------------------------------------------------------------------------
# GESAMTANALYSE
# --------------------------------------------------------------------------------------
@dataclass
class Ergebnis:
    ticker: str
    gruppe: str
    merkmale: pd.DataFrame
    komponenten: pd.DataFrame
    score: pd.Series
    signal: str
    levels: dict
    gruende: list
    auswertung: dict
    regime_label: str
    tag_pct: float
    daten: pd.DataFrame = field(repr=False, default=None)
    score_aktuell: float | None = None
    zusatzpunkte: float = 0.0
    extras: dict | None = None


def analysiere(daten: dict[str, pd.DataFrame], gruppen: dict[str, list[str]], fred: pd.DataFrame | None = None) -> tuple[dict[str, Ergebnis], dict]:
    aktien_liste = gruppen.get("Aktien", [])
    reg_a = regime_aktien(daten, aktien_liste, fred)
    reg_k = regime_krypto(daten)
    ergebnisse: dict[str, Ergebnis] = {}
    for gruppe, liste in gruppen.items():
        for t in liste:
            df = daten.get(t)
            if df is None or len(df) < 260:
                continue
            krypto = ist_krypto(t)
            bench = daten.get("BTC-USD") if krypto else daten.get("SPY")
            reg = reg_k if krypto else reg_a
            reg_s = reg["score"] if len(reg) else None
            try:
                f = merkmale(df, t, bench)
                k = komponenten(f)
                s = score_aus(k, reg_s)
            except Exception:
                continue
            letzter = s.last_valid_index()
            if letzter is None:
                continue
            f_last, k_last = f.loc[letzter], k.loc[letzter]
            reg_wert = float(reg_s.reindex([letzter], method="ffill").iloc[0]) if reg_s is not None else np.nan
            rl = regime_label(reg_wert)
            ergebnisse[t] = Ergebnis(
                ticker=t, gruppe=gruppe, merkmale=f, komponenten=k, score=s,
                signal=signal_aus(float(s.loc[letzter])), levels=levels(f_last),
                gruende=begruendung(f_last, k_last, rl), auswertung=auswertung(f, s),
                regime_label=rl, tag_pct=float(f["ret1"].iloc[-1]), daten=df,
            )
    regime_info = {"Aktien": reg_a, "Krypto": reg_k}
    return ergebnisse, regime_info
