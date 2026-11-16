"""
Push-Benachrichtigungen über ntfy (kostenlos, App für Android/iPhone, kein Konto nötig).
Vergleicht den neuen Stand (ausgabe/signale.json) mit dem vorherigen (vorher/signale.json)
und meldet nur ÄNDERUNGEN – keine Wiederholungen bei jedem Lauf.

Aufruf:  python benachrichtigung.py <vorher-ordner> <ausgabe-ordner>
Umgebung: NTFY_TOPIC (GitHub-Secret), optional TEST_PUSH=true
Bestand:  bestand.txt (ein Ticker pro Zeile) – für diese Werte gelten strengere Warnungen.
"""
import json
import os
import sys
import urllib.request

APP_URL = "https://testblyed-png-signal-radar-app-2f1kx1.streamlit.app/"
KAUF = ("STARK KAUFEN", "KAUFEN")
VERKAUF = ("VERKAUFEN", "STARK VERKAUFEN")
CRASH_TAG = -0.08
VIX_ALARM = 30


def lade(pfad):
    try:
        with open(pfad, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def bestand():
    try:
        with open("bestand.txt", encoding="utf-8") as fh:
            return {z.strip().upper() for z in fh if z.strip() and not z.startswith("#")}
    except Exception:
        return set()


def kurs(x):
    return "–" if x is None else (f"{x:,.4f}" if abs(x) < 1 else f"{x:,.2f}")


TOP_SCORE = 55
TOP_CRV = 1.5


def ist_top(w, regime):
    """Sehr gute Kaufchance: alles muss passen."""
    if not w or w.get("signal") != "STARK KAUFEN":
        return False
    markt = "Krypto" if w.get("gruppe") == "Krypto" else "Aktien"
    if (regime.get(markt) or {}).get("label") == "RISK-OFF":
        return False
    ex = w.get("extras") or {}
    h = w.get("hist") or {}
    tk, ta = h.get("treffer_kauf"), h.get("treffer_alle_tage")
    return ((w.get("score") or 0) >= TOP_SCORE
            and (w.get("crv") or 0) >= TOP_CRV
            and not ex.get("earnings_sperre")
            and (w.get("ext_pct") is None or w["ext_pct"] < 0.9)
            and tk is not None and ta is not None and tk >= ta + 0.05)


def top_text(w):
    h = w["hist"]
    ins = (w.get("extras") or {}).get("insider") or {}
    zusatz = f" · Insiderkäufe {ins['kaeufer']}×" if ins.get("kaeufe") else ""
    return (f"⭐ TOP-CHANCE {w['ticker']}: Score {w['score']:+.0f} · Kurs {kurs(w['kurs'])} · Stop {kurs(w['stop'])} · "
            f"Ziel {kurs(w['ziel'])} · CRV {w['crv']:.1f} · früher {h['treffer_kauf']:.0%} Treffer nach Kaufsignal "
            f"(sonst {h['treffer_alle_tage']:.0%}){zusatz}")


def ereignisse(alt, neu, mein):
    """Liste aus (Priorität 1-5, Text)."""
    ev = []
    ra, rn = (alt or {}).get("regime", {}), neu.get("regime", {})
    for markt in ("Aktien", "Krypto"):
        a, n = ra.get(markt, {}), rn.get(markt, {})
        if a.get("label") and n.get("label") and a["label"] != n["label"]:
            prio = 5 if n["label"] == "RISK-OFF" else 3
            ev.append((prio, f"Marktampel {markt}: {a['label']} → {n['label']}"
                       + (" – keine neuen Käufe, Stops prüfen!" if n["label"] == "RISK-OFF" else "")))
    a, n = ra.get("Aktien", {}), rn.get("Aktien", {})
    if (n.get("vix") or 0) > VIX_ALARM >= (a.get("vix") or 0):
        ev.append((5, f"VIX über {VIX_ALARM} ({n['vix']:.1f}) – Panik am Aktienmarkt"))
    if n.get("kredit_stufe") == -1 and a.get("kredit_stufe") not in (None, -1):
        ev.append((4, f"Kreditmarkt im Stress: Hochzins-Aufschlag {n.get('hy_spread', 0):.2f} % steigt deutlich"))

    alt_w = {w["ticker"]: w for w in (alt or {}).get("werte", [])}
    for w in neu.get("werte", []):
        t, s = w["ticker"], w["signal"]
        p = alt_w.get(t)
        if not p:
            continue
        ps = p.get("signal")
        im_bestand = t in mein
        # Top-Chance (strengste Stufe, höchste Priorität)
        if ist_top(w, rn) and not ist_top(p, ra):
            ev.append((5, top_text(w)))
            continue
        # Neue Kaufchance
        if s == "STARK KAUFEN" and ps != "STARK KAUFEN" and not w.get("extras", {}).get("earnings_sperre"):
            crv = w.get("crv")
            if crv is not None and crv >= 1.5:
                ev.append((3, f"Neue Kaufchance {t}: STARK KAUFEN (Score {w['score']:+.0f}) · Kurs {kurs(w['kurs'])} · "
                              f"Stop {kurs(w['stop'])} · Ziel {kurs(w['ziel'])} · CRV {crv:.1f}"))
        # Verschlechterung
        if im_bestand and ps in KAUF and s not in KAUF:
            ev.append((4, f"Dein Bestand {t}: {ps} → {s} (Score {w['score']:+.0f}) – Stop nachziehen oder Teilverkauf prüfen"))
        elif s in VERKAUF and ps not in VERKAUF and (ps in KAUF or im_bestand):
            ev.append((4 if im_bestand else 3, f"{'Dein Bestand ' if im_bestand else ''}{t}: {ps} → {s} (Score {w['score']:+.0f})"))
        # Insider-Cluster
        ki = (w.get("extras", {}).get("insider") or {}).get("kaeufer", 0)
        kp = (p.get("extras", {}).get("insider") or {}).get("kaeufer", 0)
        if ki >= 2 > kp:
            summe = w["extras"]["insider"].get("summe_usd", 0)
            ev.append((3, f"Insider-Cluster {t}: {ki} Manager kauften für ${summe:,.0f} – Signal aktuell {s}"))
        # Absturz an einem Tag
        tp = w.get("tag_pct")
        if tp is not None and tp <= CRASH_TAG and (p.get("datum") != w.get("datum") or (p.get("tag_pct") or 0) > CRASH_TAG):
            ev.append((5 if im_bestand else 4, f"{'Dein Bestand ' if im_bestand else ''}{t} stürzt ab: {tp:+.1%} heute (Kurs {kurs(w['kurs'])})"))
    return ev


def senden(topic, titel, text, prio):
    body = json.dumps({"topic": topic, "title": titel, "message": text, "priority": int(prio),
                       "tags": ["chart_with_upwards_trend"], "click": APP_URL}).encode("utf-8")
    req = urllib.request.Request("https://ntfy.sh/", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def main(vorher="vorher", ausgabe="ausgabe"):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("Kein NTFY_TOPIC gesetzt – keine Benachrichtigung.")
        return
    neu = lade(os.path.join(ausgabe, "signale.json"))
    alt = lade(os.path.join(vorher, "signale.json"))
    if os.environ.get("TEST_PUSH", "").lower() == "true":
        senden(topic, "Signal-Radar: Test", "Push-Benachrichtigungen funktionieren. Du bekommst ab jetzt nur Meldungen bei wichtigen Änderungen.", 3)
        print("Test-Push gesendet.")
    if not neu or not alt:
        print("Kein Vergleichsstand – keine Benachrichtigung.")
        return
    ev = sorted(ereignisse(alt, neu, bestand()), key=lambda e: -e[0])
    if not ev:
        print("Keine relevanten Änderungen.")
        return
    prio = max(p for p, _ in ev)
    titel = "Signal-Radar: " + (ev[0][1][:60] if len(ev) == 1 else f"{len(ev)} wichtige Änderungen")
    text = "\n".join(f"• {t}" for _, t in ev[:15]) + "\n\nKeine Anlageberatung."
    senden(topic, titel, text, prio)
    print(f"Push gesendet: {len(ev)} Ereignisse")


if __name__ == "__main__":
    main(*sys.argv[1:])
