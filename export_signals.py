"""
Berechnet die aktuellen Signale (gleiche Logik wie die App) und schreibt sie nach ausgabe/:
  signale.json  – alle Werte maschinenlesbar
  signale.md    – lesbare Zusammenfassung
Läuft automatisch in GitHub Actions (.github/workflows/signale.yml).
"""
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

import engine as E
import extras as X

KONTO, RISIKO = 10000.0, 1.0  # Referenzwerte für Stückzahl in der Übersicht


def z(x, n=4):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, n) if np.isfinite(x) else None


def main(ziel="ausgabe", vorher="vorher"):
    gruppen = {"Aktien": E.STANDARD_AKTIEN, "ETFs": E.STANDARD_ETFS, "Krypto": E.STANDARD_KRYPTO}
    alle = list(dict.fromkeys(sum(gruppen.values(), []) + E.HILFS_TICKER))
    daten = E.lade_yahoo(alle)
    daten = E.nur_fertige_tage(daten)
    if len(daten) < 10:
        print(f"Zu wenige Daten geladen ({len(daten)}). Abbruch.", file=sys.stderr)
        sys.exit(1)
    fred = X.lade_fred()
    ergebnisse, regime = E.analysiere(daten, gruppen, fred)

    # Zusatzdaten mit Cache aus dem letzten Lauf
    cache = None
    try:
        with open(os.path.join(vorher, "extras.json"), encoding="utf-8") as fh:
            cache = json.load(fh)
    except Exception:
        pass
    identitaet = os.environ.get("SEC_IDENTITY") or "Signal-Radar Bot signal-radar-bot@users.noreply.github.com"
    extras = X.sammle(gruppen["Aktien"], gruppen["Krypto"], cache, identitaet)
    extras["diagnose"]["fred_spalten"] = list(fred.columns) if len(fred) else []
    X.anwenden(ergebnisse, extras, daten)

    reg_out = {}
    for name, r in regime.items():
        if r is None or not len(r):
            continue
        last = r.iloc[-1]
        reg_out[name] = {
            "datum": str(r.index[-1].date()),
            "label": E.regime_label(float(last["score"])),
            "score": z(last["score"], 2),
            **{k: z(last.get(k), 3) for k in ["trend", "kreuz", "vix", "vix_struktur", "breite", "vola", "kredit_stufe", "hy_spread", "zinskurve"] if k in r.columns},
        }

    werte = []
    for t, r in ergebnisse.items():
        f = r.merkmale
        kurs = float(f["close"].iloc[-1])
        lv = r.levels
        kauf = r.signal in ("STARK KAUFEN", "KAUFEN")
        stueck, wert = E.positionsgroesse(kurs, lv["stop"], KONTO, RISIKO) if kauf else (0.0, 0.0)
        tab = r.auswertung["tabelle"].set_index("Signal")
        kt = tab.loc[["STARK KAUFEN", "KAUFEN"]]
        n = kt["Anzahl Tage"].sum()
        werte.append({
            "ticker": t, "gruppe": r.gruppe, "signal": r.signal,
            "score": z(r.score_aktuell if r.score_aktuell is not None else r.score.dropna().iloc[-1], 1),
            "basis_score": z(r.score.dropna().iloc[-1], 1), "zusatzpunkte": z(r.zusatzpunkte, 1),
            "extras": r.extras or {},
            "datum": str(f.index[-1].date()), "kurs": z(kurs), "tag_pct": z(r.tag_pct),
            "stop": z(lv["stop"]), "ziel": z(lv["ziel"]), "crv": z(lv["crv"], 2),
            "stueck_10k_1pct": z(stueck, 3), "positionswert_10k_1pct": z(wert, 0),
            "ext_pct": z(f["ext_pct"].iloc[-1], 3),
            "komponenten": {k: z(v, 3) for k, v in r.komponenten.iloc[-1].items()},
            "gruende": [{"richtung": a, "text": b} for a, b in r.gruende],
            "hist": {
                "treffer_kauf": z((kt["Trefferquote"] * kt["Anzahl Tage"]).sum() / n, 3) if n else None,
                "rendite_kauf_20t": z((kt[f"Ø Rendite {E.FORWARD_TAGE}T"] * kt["Anzahl Tage"]).sum() / n, 4) if n else None,
                "treffer_alle_tage": z(r.auswertung["basis_treffer"], 3),
                "rendite_alle_tage_20t": z(r.auswertung["basis_rendite"], 4),
                "cagr_strategie": z(r.auswertung["cagr_strategie"]), "cagr_halten": z(r.auswertung["cagr_halten"]),
                "mdd_strategie": z(r.auswertung["mdd_strategie"]), "mdd_halten": z(r.auswertung["mdd_halten"]),
            },
        })
    werte.sort(key=lambda w: -(w["score"] or -999))

    jetzt = datetime.now(timezone.utc)
    os.makedirs(ziel, exist_ok=True)
    out = {"erstellt_utc": jetzt.isoformat(timespec="seconds"), "regime": reg_out,
           "referenz": {"konto": KONTO, "risiko_pct": RISIKO}, "werte": werte}
    with open(os.path.join(ziel, "signale.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    def k(x):
        return "–" if x is None else (f"{x:,.4f}" if abs(x) < 1 else f"{x:,.2f}")

    md = [f"# Signal-Radar – Stand {jetzt:%Y-%m-%d %H:%M} UTC", ""]
    for name, r in reg_out.items():
        md.append(f"- **{name}: {r['label']}** (Daten bis {r['datum']})")
    md += ["", "| Ticker | Gruppe | Signal | Score | Kurs | Stop | Ziel | CRV | Hist. Treffer Kauf |",
           "|---|---|---|---|---|---|---|---|---|"]
    for w in werte:
        tq = w["hist"]["treffer_kauf"]
        md.append(f"| {w['ticker']} | {w['gruppe']} | {w['signal']} | {w['score']:+.0f} | {k(w['kurs'])} | "
                  f"{k(w['stop']) if w['stueck_10k_1pct'] else '–'} | {k(w['ziel']) if w['stueck_10k_1pct'] else '–'} | "
                  f"{w['crv'] if w['stueck_10k_1pct'] and w['crv'] is not None else '–'} | "
                  f"{'–' if tq is None else f'{tq:.0%}'} |")
    with open(os.path.join(ziel, "extras.json"), "w", encoding="utf-8") as fh:
        json.dump(extras, fh, ensure_ascii=False, indent=1, default=str)
    with open(os.path.join(ziel, "signale.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    print(f"OK: {len(werte)} Werte, {jetzt:%Y-%m-%d %H:%M} UTC")
    try:
        import swing
        sw = swing.live(ergebnisse, werte, daten, reg_out, vorher, ziel)
        print("Swing:", len(sw["offen"]), "offen/geplant,", len(sw["geschlossen"]), "abgeschlossen")
    except Exception as ex:
        import traceback
        print("Swing fehlgeschlagen:", traceback.format_exc()[-800:])
    try:
        import protokoll
        pr = protokoll.aktualisieren(werte, daten, vorher, ziel)
        print("Protokoll:", len(pr["eintraege"]), "Einträge seit", pr["start"])
    except Exception as ex:
        print("Protokoll fehlgeschlagen:", repr(ex)[:300])
    try:
        import krypto
        krypto.main(ziel, vorher)
    except Exception as ex:
        print("Krypto-Strategie fehlgeschlagen:", repr(ex)[:300])
    print("Diagnose Zusatzdaten:", json.dumps(extras["diagnose"], ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main(*sys.argv[1:])
