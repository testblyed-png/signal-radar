"""Gemeinsame, vorab festgelegte Regeln für Insider-Studie und Insider-Scanner (nicht auf Daten optimiert)."""
import re

MIN_PREIS = 2.0            # keine Pennystocks
MIN_KAUF_USD = 5_000       # Kleinstkäufe ignorieren
FENSTER_TAGE = 30          # Cluster: Käufe innerhalb von 30 Kalendertagen (nach Meldedatum)
CLUSTER_KAEUFER = 3        # mindestens 3 verschiedene Manager/Aufsichtsräte
CLUSTER_SUMME = 100_000    # zusammen mindestens 100.000 $
CHEF_SUMME = 250_000       # Einzelkauf von CEO/CFO/President/Chairman ab 250.000 $
EINZEL_SUMME = 25_000      # Vergleichsgruppe: irgendein Einzelkauf ab 25.000 $
PAUSE_TAGE = 90            # pro Firma höchstens ein Ereignis je Typ in 90 Tagen
MIN_UMSATZ_USD = 200_000   # handelbar: mittlerer Tagesumsatz mindestens 200.000 $
KOSTEN_RUNDE = 0.006       # 0,6 % für Kauf + Verkauf (kleine Aktien, breitere Spannen)
HORIZONTE = (21, 63, 126)  # Handelstage: 1, 3, 6 Monate

CHEF_TITEL = re.compile(r"\b(ceo|chief executive|cfo|chief financial|president|chairman|chair of the board|executive chair)\b", re.I)
ROLLE_OK = re.compile(r"officer|director", re.I)
TICKER_OK = re.compile(r"^[A-Z]{1,5}(-[A-Z])?$")


def ticker_norm(t) -> str | None:
    t = str(t or "").strip().upper().replace(".", "-").replace("/", "-")
    return t if TICKER_OK.match(t) else None
