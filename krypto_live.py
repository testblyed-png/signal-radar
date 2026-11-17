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


# ------------------------------------------------------------------ Live
VERSION = 1
REGEL_TEXT = {"SMA 50": "50-Tage-Linie", "SMA 100": "100-Tage-Linie", "SMA 200": "200-Tage-Linie",
              "Momentum 30 Tage": "Kurs vor 30 Tagen", "Momentum 90 Tage": "Kurs vor 90 Tagen"}


def lies(pfad: str) -> dict:
    try:
        with open(pfad, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def preis(paar: str) -> float:
    for host in HOSTS:
        try:
            return float(hole(f"{host}/api/v3/ticker/price?symbol={paar}")["price"])
        except Exception:
            continue
    raise RuntimeError(f"kein Preis für {paar}")


def push_coins(signale_dir: str, intraday_dir: str) -> tuple[set, dict]:
    """Push nur für Coins, die den Langzeit-Test (Tagesschluss, Yahoo) bestanden haben und bei denen die
    laufende Prüfung im Stunden-Test nicht schlechter war (Rendite pro Risiko höchstens 0,1 niedriger)."""
    kr = lies(os.path.join(signale_dir, "krypto.json")).get("coins", {})
    it = lies(os.path.join(intraday_dir, "krypto_intraday.json")).get("coins", {})
    ok, grund = set(), {}
    for t in PAARE:
        if not (kr.get(t) or {}).get("test_bestanden"):
            grund[t] = "Langzeit-Test nicht bestanden" if t in kr else "noch nicht getestet"
            continue
        k = (it.get(t) or {}).get("kennzahlen")
        if k:
            d, l = k["Prüfung nur am Tagesschluss"], k["Laufende Prüfung (stündlich)"]
            if l["sharpe"] < d["sharpe"] - 0.1:
                grund[t] = "laufende Prüfung im Test schwächer"
                continue
        ok.add(t)
        grund[t] = "ok"
    return ok, grund


def live_main(ziel: str, vorher: str, signale_dir: str, intraday_dir: str):
    jetzt = datetime.now(timezone.utc)
    alt = lies(os.path.join(vorher, "krypto_live.json"))
    if alt.get("version") != VERSION:
        alt = {}
    alt_coins = alt.get("coins", {})
    erlaubt, grund = push_coins(signale_dir, intraday_dir)
    out = {"erstellt_utc": jetzt.isoformat(timespec="seconds"), "version": VERSION, "puffer": PUFFER, "coins": {}, "fehler": []}
    meldungen = []
    for t, paar in PAARE.items():
        try:
            tg = kerzen(paar, "1d", int((jetzt - pd.Timedelta(days=260)).timestamp() * 1000), 86_400_000)
            tag_close = tg.loc[tg["fertig"], "close"]
            if len(tag_close) < 201:
                out["fehler"].append(f"{t}: zu wenig Tage")
                continue
            p = preis(paar)
            schw = {f"SMA {n}": (tag_close.iloc[-(n - 1):].sum() + p) / n for n in (50, 100, 200)}
            schw.update({f"Momentum {n} Tage": float(tag_close.iloc[-n]) for n in (30, 90)})
            vorher_an = {e["regel"]: e["an"] for e in (alt_coins.get(t) or {}).get("einzel", [])}
            if len(vorher_an) < len(REGELN):                  # erster Lauf: Zustand aus den letzten Stunden rekonstruieren
                hs = kerzen(paar, "1h", int((jetzt - pd.Timedelta(days=60)).timestamp() * 1000), H)
                ph = hs.loc[hs["fertig"], "close"]
                _, teile = ensemble(ph, tag_close, False)
                vorher_an = {k: bool(v.iloc[-1] > 0) for k, v in teile.items()}
            einzel = []
            for r in REGELN:
                s_ = float(schw[r])
                an = True if p > s_ * (1 + PUFFER) else False if p < s_ * (1 - PUFFER) else bool(vorher_an.get(r, False))
                einzel.append({"regel": r, "an": an, "schwelle": s_, "abstand": p / s_ - 1, "vorher": bool(vorher_an.get(r, False))})
            anteil = sum(e["an"] for e in einzel) / len(einzel)
            a_alt = (alt_coins.get(t) or {}).get("anteil")
            wechsel = list((alt_coins.get(t) or {}).get("wechsel", []))
            seit = (alt_coins.get(t) or {}).get("seit") or jetzt.isoformat(timespec="minutes")
            if a_alt is not None and abs(a_alt - anteil) > 1e-9:
                seit = jetzt.isoformat(timespec="minutes")
                wechsel.insert(0, {"zeit": seit, "von": a_alt, "auf": anteil, "kurs": p})
                if t in erlaubt:
                    rauf = anteil > a_alt
                    gekippt = [f"Kurs {'über' if e['an'] else 'unter'} {REGEL_TEXT[e['regel']]} ({e['schwelle']:,.4g} $)"
                               for e in einzel if e["an"] != e["vorher"]]
                    meldungen.append((rauf, f"{'🟢 KAUFEN' if rauf else '🔴 VERKAUFEN'} {NAMEN[t]}: {stufe(a_alt)} → {stufe(anteil)}. "
                                            f"Ziel-Anteil {a_alt:.0%} → {anteil:.0%}, Kurs {p:,.4g} $. Auslöser: {', '.join(gekippt)}. "
                                            f"Bei 1.000 € Budget für {NAMEN[t]}: {abs(anteil - a_alt) * 1000:.0f} € {'kaufen' if rauf else 'verkaufen'}."))
            out["coins"][t] = {"name": NAMEN[t], "anteil": anteil, "signal": stufe(anteil), "kurs": p, "einzel": einzel,
                               "seit": seit, "wechsel": wechsel[:30], "push": t in erlaubt, "push_grund": grund.get(t)}
        except Exception as ex:
            out["fehler"].append(f"{t}: {type(ex).__name__}: {str(ex)[:120]}")
            if t in alt_coins:
                out["coins"][t] = alt_coins[t]                 # letzten Stand behalten
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if meldungen and topic:
        try:
            import benachrichtigung as B
            rauf = [m for r, m in meldungen if r]
            titel = "Krypto: " + ("KAUFEN" if len(rauf) == len(meldungen) else "VERKAUFEN" if not rauf else "Trendwechsel")
            B.senden(topic, titel, "\n".join(m for _, m in meldungen) + "\n\nKeine Anlageberatung.", 4)
            out["push"] = len(meldungen)
        except Exception as ex:
            out["fehler"].append(f"Push: {type(ex).__name__}: {str(ex)[:80]}")
    os.makedirs(ziel, exist_ok=True)
    with open(os.path.join(ziel, "krypto_live.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(json.dumps({t: (c["signal"], c["anteil"]) for t, c in out["coins"].items()}), out["fehler"], "Pushes:", len(meldungen))


if __name__ == "__main__":
    modus = sys.argv[1]
    if modus == "test":
        test_main(sys.argv[2])
    elif modus == "live":
        live_main(*sys.argv[2:6])
