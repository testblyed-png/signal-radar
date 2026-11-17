"""
Signal-Radar – Oberfläche
Start:  streamlit run app.py   (oder Doppelklick auf start.bat)
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import engine as E
import extras as X

EXTRAS_URL = "https://raw.githubusercontent.com/testblyed-png/signal-radar/signale/extras.json"
KAND_URL = "https://raw.githubusercontent.com/testblyed-png/signal-radar/kandidaten/kandidaten.json"
INSIDER_URL = "https://raw.githubusercontent.com/testblyed-png/signal-radar/insider/"
KRYPTO_URL = "https://raw.githubusercontent.com/testblyed-png/signal-radar/signale/krypto.json"
PROTOKOLL_URL = "https://raw.githubusercontent.com/testblyed-png/signal-radar/signale/protokoll.json"
SWING_LIVE_URL = "https://raw.githubusercontent.com/testblyed-png/signal-radar/signale/swing_live.json"
SWING_TEST_URL = "https://raw.githubusercontent.com/testblyed-png/signal-radar/swing/swing_test.json"

st.set_page_config(page_title="Signal-Radar", layout="wide")

SIGNAL_FARBE = E.SIGNAL_FARBEN
REGIME_FARBE = {"RISK-ON": "#0f7b3f", "NEUTRAL": "#b58900", "RISK-OFF": "#c62828", "UNBEKANNT": "#8a8f98"}
KAUF_SIGNALE = ("STARK KAUFEN", "KAUFEN")


def badge(text: str, farbe: str, gross: bool = False) -> str:
    size = "1.6rem" if gross else "0.95rem"
    pad = "10px 22px" if gross else "3px 10px"
    return (f"<span style='background:{farbe};color:white;padding:{pad};border-radius:8px;"
            f"font-weight:700;font-size:{size};white-space:nowrap'>{text}</span>")


def fmt_kurs(x):
    if x is None or not np.isfinite(x):
        return "–"
    return f"{x:,.4f}" if x < 1 else f"{x:,.2f}"


def fmt_pct(x, plus=True):
    if x is None or not np.isfinite(x):
        return "–"
    return f"{x:+.1%}" if plus else f"{x:.0%}"


# ------------------------------------------------------------------ Seitenleiste
with st.sidebar:
    st.header("Einstellungen")
    quelle = st.radio("Datenquelle", ["Live (Yahoo Finance, kostenlos)", "Demo (Zufallsdaten zum Testen)"])
    demo = quelle.startswith("Demo")
    konto = st.number_input("Kontogröße (in Kurswährung, meist USD)", min_value=100.0, value=10000.0, step=500.0)
    risiko = st.slider("Max. Risiko pro Trade (% vom Konto)", 0.25, 3.0, 1.0, 0.25,
                       help="So viel verlierst du, wenn der Stop greift. Profis: 0,5–1 %.")
    with st.expander("Watchlists bearbeiten"):
        akt_txt = st.text_area("Aktien", ", ".join(E.STANDARD_AKTIEN), height=120)
        etf_txt = st.text_area("ETFs", ", ".join(E.STANDARD_ETFS), height=70)
        kry_txt = st.text_area("Krypto (Format BTC-USD)", ", ".join(E.STANDARD_KRYPTO), height=70)
        st.caption("Ticker wie auf finance.yahoo.com, z. B. SAP.DE für Xetra, VOW3.DE, BTC-USD.")
    if st.button("Daten neu laden", width="stretch"):
        st.cache_data.clear()
    st.caption("Signale werden einmal pro Stunde neu berechnet. Basis: Tagesschlusskurse.")


def liste(txt):
    return [t.strip().upper() for t in txt.replace("\n", ",").split(",") if t.strip()]


gruppen = {"Aktien": liste(akt_txt), "ETFs": liste(etf_txt), "Krypto": liste(kry_txt)}
alle = list(dict.fromkeys(sum(gruppen.values(), []) + E.HILFS_TICKER))


@st.cache_data(ttl=3600, show_spinner=False)
def lade(tickers: tuple, demo: bool):
    return E.demo_daten(list(tickers)) if demo else E.nur_fertige_tage(E.lade_yahoo(list(tickers)))


@st.cache_data(ttl=3600, show_spinner=False)
def lade_extras():
    try:
        import json, urllib.request
        with urllib.request.urlopen(EXTRAS_URL, timeout=15) as fh:
            return json.loads(fh.read().decode("utf-8"))
    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def lade_kandidaten():
    try:
        import json, urllib.request
        with urllib.request.urlopen(KAND_URL, timeout=20) as fh:
            return json.loads(fh.read().decode("utf-8"))
    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def lade_insider(datei: str):
    try:
        import json, urllib.request
        with urllib.request.urlopen(INSIDER_URL + datei, timeout=20) as fh:
            return json.loads(fh.read().decode("utf-8"))
    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def lade_json(url: str):
    try:
        import json, urllib.request
        with urllib.request.urlopen(url, timeout=20) as fh:
            return json.loads(fh.read().decode("utf-8"))
    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def lade_protokoll():
    try:
        import json, urllib.request
        with urllib.request.urlopen(PROTOKOLL_URL, timeout=20) as fh:
            return json.loads(fh.read().decode("utf-8"))
    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def lade_krypto():
    try:
        import json, urllib.request
        with urllib.request.urlopen(KRYPTO_URL, timeout=20) as fh:
            return json.loads(fh.read().decode("utf-8"))
    except Exception:
        return None


_TEST_ZAEHLER = [0]


def test_anzeige(test: dict, hauptreihe: str):
    """Kurven + Kennzahlen eines Portfolio-Tests."""
    fig = go.Figure()
    for name, k in test["kurven"].items():
        fig.add_trace(go.Scatter(x=k["monat"], y=k["wert"], name=name,
                                 line=dict(width=3 if name == hauptreihe else 1.5)))
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), yaxis_type="log",
                      yaxis_title="Wert von 1 € (log)", legend=dict(orientation="h", y=-0.2))
    _TEST_ZAEHLER[0] += 1
    st.plotly_chart(fig, width="stretch", key=f"test_anzeige_{_TEST_ZAEHLER[0]}")
    kz = pd.DataFrame([{"Strategie": n, "Rendite p.a.": v.get("cagr"), "nach KESt p.a. (grob)": v.get("cagr_nach_steuer"),
                        "Größter Rückgang": v.get("mdd"), "Sharpe": v.get("sharpe"),
                        "1. Hälfte p.a.": v.get("cagr_haelfte1"), "2. Hälfte p.a.": v.get("cagr_haelfte2")}
                       for n, v in test["kennzahlen"].items() if v])
    st.dataframe(kz, hide_index=True, width="stretch", column_config={
        **{c: st.column_config.NumberColumn(format="percent") for c in kz.columns if c not in ("Strategie", "Sharpe")},
        "Sharpe": st.column_config.NumberColumn(format="%.2f", help="Rendite pro Risiko; über 1 ist sehr gut")})
    st.caption(f"Zeitraum {test['von']} bis {test['bis']} · monatliche Umschichtung · 0,1 % Kosten je Kauf/Verkauf · "
               "Kauf einen Tag nach dem Signal · KESt 27,5 %: bei aktiven Strategien jährlich auf Gewinne, bei Halten erst am Ende (Näherung).")


@st.cache_data(ttl=3600, show_spinner=False)
def rechne(tickers: tuple, demo: bool, gruppen_key: tuple):
    daten = lade(tickers, demo)
    gr = {g: list(l) for g, l in gruppen_key}
    fred = None if demo else X.lade_fred()
    erg, reg = E.analysiere(daten, gr, fred)
    ex = None if demo else lade_extras()
    X.anwenden(erg, ex, daten)
    return erg, reg, (ex or {}).get("erstellt_utc")


with st.spinner("Lade Kurse und berechne Signale … (beim ersten Start ca. 30–60 Sekunden)"):
    ergebnisse, regime, extras_stand = rechne(tuple(alle), demo, tuple((g, tuple(l)) for g, l in gruppen.items()))

if not ergebnisse:
    st.error("Keine Daten geladen. Internetverbindung prüfen, kurz warten und auf „Daten neu laden“ klicken. "
             "Yahoo begrenzt manchmal kurzzeitig die Abrufe.")
    st.stop()

# ------------------------------------------------------------------ Kopf + Marktampel
st.title("Signal-Radar")
stand = max(r.merkmale.index[-1] for r in ergebnisse.values())
st.caption(f"Stand: abgeschlossene Handelstage bis {stand:%d.%m.%Y} (wie Push-Nachrichten)" + ("  ·  DEMO-DATEN – keine echten Kurse!" if demo else "")
           + (f"  ·  Zusatzdaten vom {pd.Timestamp(extras_stand).tz_convert('Europe/Vienna'):%d.%m. %H:%M}" if extras_stand else ""))

c1, c2 = st.columns(2)
for spalte, name, key in [(c1, "Aktienmarkt (S&P 500)", "Aktien"), (c2, "Kryptomarkt (Bitcoin)", "Krypto")]:
    r = regime.get(key)
    with spalte:
        if r is None or not len(r):
            st.info(f"{name}: keine Daten")
            continue
        last = r.iloc[-1]
        lab = E.regime_label(float(last["score"]))
        txt = []
        txt.append("Trend über 200-Tage-Linie" if last["trend"] > 0 else "Trend unter 200-Tage-Linie")
        if "vix" in r.columns and np.isfinite(last.get("vix", np.nan)):
            txt.append(f"VIX {last['vix']:.1f}")
        if "vix_struktur" in r.columns and np.isfinite(last.get("vix_struktur", np.nan)):
            txt.append("Angst kurzfristig niedrig" if last["vix_struktur"] < 1 else "Angst kurzfristig erhöht")
        if "breite" in r.columns and np.isfinite(last.get("breite", np.nan)):
            txt.append(f"{last['breite']:.0%} der Aktien im Aufwärtstrend")
        if "hy_spread" in r.columns and np.isfinite(last.get("hy_spread", np.nan)):
            ks = last.get("kredit_stufe", 0)
            txt.append(f"Kreditaufschlag {last['hy_spread']:.2f} % ({'entspannt' if ks > 0 else 'Stress' if ks < 0 else 'normal'})")
        if "zinskurve" in r.columns and np.isfinite(last.get("zinskurve", np.nan)):
            txt.append(f"Zinskurve 10J–2J {last['zinskurve']:+.2f} %")
        if key == "Krypto" and "BTC-USD" in ergebnisse and (ergebnisse["BTC-USD"].extras or {}).get("funding"):
            fr = ergebnisse["BTC-USD"].extras["funding"]["rate_8h_avg"] * 3 * 365
            txt.append(f"BTC-Funding {fr:+.0%} p.a.")
        if "vola" in r.columns:
            txt.append("Schwankung normal" if last["vola"] >= 0 else "Schwankung extrem")
        st.markdown(f"**{name}**&nbsp;&nbsp;{badge(lab, REGIME_FARBE[lab], gross=True)}", unsafe_allow_html=True)
        st.caption(" · ".join(txt))

with st.expander("Was bedeutet die Marktampel?"):
    st.markdown(
        "- **RISK-ON**: Umfeld ist günstig – Kaufsignale haben historisch besser funktioniert.\n"
        "- **NEUTRAL**: gemischtes Umfeld – kleinere Positionen.\n"
        "- **RISK-OFF**: Markt im Stress – Kaufsignale werden automatisch abgeschwächt, Vorsicht."
    )

def insider_text(i):
    if not i:
        return ""
    if not i.get("kaeufe"):
        return "keine Käufe"
    k, s = i.get("kaeufer", 0), i.get("summe_usd", 0)
    betrag = f"{s / 1e6:.1f} Mio $" if s >= 1e6 else f"{s / 1e3:.0f} Tsd $"
    return f"{k} Käufer · {betrag}"


# ------------------------------------------------------------------ Tabelle bauen
zeilen = []
for t, r in ergebnisse.items():
    last_score = float(r.score_aktuell if r.score_aktuell is not None else r.score.dropna().iloc[-1])
    kurs = float(r.merkmale["close"].iloc[-1])
    lv = r.levels
    kauf = r.signal in KAUF_SIGNALE
    stueck, wert = E.positionsgroesse(kurs, lv["stop"], konto, risiko) if kauf else (0.0, 0.0)
    tab = r.auswertung["tabelle"].set_index("Signal")
    treffer_kauf = tab.loc[list(KAUF_SIGNALE), "Trefferquote"]
    n_kauf = tab.loc[list(KAUF_SIGNALE), "Anzahl Tage"]
    tq = float((treffer_kauf * n_kauf).sum() / n_kauf.sum()) if n_kauf.sum() > 0 else np.nan
    hinweis = ""
    ep = r.merkmale["ext_pct"].iloc[-1]
    ex = r.extras or {}
    if ex.get("earnings_sperre"):
        hinweis = f"Quartalszahlen {pd.Timestamp(ex['earnings']):%d.%m.} – abwarten"
    elif np.isfinite(ep) and ep > 0.95:
        hinweis = "überdehnt – Gewinne sichern"
    elif kauf and np.isfinite(lv["crv"]) and lv["crv"] < 1.5:
        hinweis = "Chance/Risiko knapp – Rücksetzer abwarten"
    elif r.merkmale["sweep_kuerzlich"].iloc[-1] > 0 and r.komponenten["trend"].iloc[-1] > 0:
        hinweis = "Sweep zurückerobert"
    zeilen.append({
        "Ticker": t, "Gruppe": r.gruppe, "Signal": r.signal, "Score": round(last_score),
        "Insider 90T": insider_text(ex.get("insider")),
        "Kurs": kurs, "Heute": r.tag_pct,
        "Stop": lv["stop"] if kauf else np.nan, "Ziel": lv["ziel"] if kauf else np.nan,
        "CRV": lv["crv"] if kauf else np.nan,
        "Stück": stueck if kauf else np.nan, "Positionswert": wert if kauf else np.nan,
        "Hist. Treffer Kauf": tq, "Nächste Zahlen": ex.get("earnings") or "", "Hinweis": hinweis,
    })
tabelle = pd.DataFrame(zeilen).sort_values("Score", ascending=False).reset_index(drop=True)

tab_ueber, tab_swing, tab_detail, tab_kry, tab_ins, tab_kand, tab_rot, tab_guete, tab_hilfe = st.tabs(
    ["Übersicht", "Swing-Trades", "Detailanalyse", "Krypto-Strategie", "Insider-Radar", "Potenzial-Kandidaten", "ETF-Strategien",
     "Wie gut sind die Signale?", "Anleitung"])

# ------------------------------------------------------------------ Übersicht
with tab_ueber:
    k1, k2 = st.columns(2)
    with k1:
        st.subheader("Beste Kaufkandidaten")
        top = tabelle[tabelle["Signal"].isin(KAUF_SIGNALE)].head(6)
        if top.empty:
            st.write("Derzeit kein Kaufsignal.")
        for _, z in top.iterrows():
            st.markdown(f"{badge(z['Signal'], SIGNAL_FARBE[z['Signal']])} &nbsp; **{z['Ticker']}** · Score {z['Score']} · "
                        f"Kurs {fmt_kurs(z['Kurs'])} · Stop {fmt_kurs(z['Stop'])} · Ziel {fmt_kurs(z['Ziel'])}",
                        unsafe_allow_html=True)
    with k2:
        st.subheader("Eher meiden / verkaufen")
        flop = tabelle[tabelle["Signal"].isin(["STARK VERKAUFEN", "VERKAUFEN"])].sort_values("Score").head(6)
        if flop.empty:
            st.write("Derzeit kein Verkaufssignal.")
        for _, z in flop.iterrows():
            st.markdown(f"{badge(z['Signal'], SIGNAL_FARBE[z['Signal']])} &nbsp; **{z['Ticker']}** · Score {z['Score']} · "
                        f"Kurs {fmt_kurs(z['Kurs'])}", unsafe_allow_html=True)

    st.divider()
    f1, f2 = st.columns([2, 3])
    gr_filter = f1.multiselect("Gruppe", list(gruppen), default=list(gruppen))
    sig_filter = f2.multiselect("Signal", list(SIGNAL_FARBE), default=list(SIGNAL_FARBE))
    ansicht = tabelle[tabelle["Gruppe"].isin(gr_filter) & tabelle["Signal"].isin(sig_filter)]

    def farbe_signal(v):
        return f"background-color:{SIGNAL_FARBE.get(v, '#888')};color:white;font-weight:700"

    styler = ansicht.style.map(farbe_signal, subset=["Signal"]) if hasattr(ansicht.style, "map") \
        else ansicht.style.applymap(farbe_signal, subset=["Signal"])
    st.dataframe(
        styler, width="stretch", hide_index=True, height=min(38 * (len(ansicht) + 1), 900),
        column_config={
            "Score": st.column_config.ProgressColumn("Score", min_value=-100, max_value=100, format="%d"),
            "Kurs": st.column_config.NumberColumn(format="%.2f"),
            "Heute": st.column_config.NumberColumn(format="percent"),
            "Stop": st.column_config.NumberColumn(format="%.2f", help="Verkaufen, wenn der Kurs darunter schließt"),
            "Ziel": st.column_config.NumberColumn(format="%.2f", help="Erste Zone für Gewinnmitnahme"),
            "CRV": st.column_config.NumberColumn(format="%.1f", help="Chance/Risiko: möglicher Gewinn ÷ möglicher Verlust"),
            "Stück": st.column_config.NumberColumn(format="%.2f", help="Bei deinem Risiko pro Trade"),
            "Positionswert": st.column_config.NumberColumn(format="%.0f"),
            "Hist. Treffer Kauf": st.column_config.NumberColumn(
                format="percent", help=f"Wie oft lag der Kurs {E.FORWARD_TAGE} Tage nach einem Kaufsignal höher (dieser Wert, Vergangenheit)"),
        },
    )
    st.caption("Stop, Ziel und Stückzahl nur bei Kaufsignalen. Score: −100 (sehr schwach) bis +100 (sehr stark).")

# ------------------------------------------------------------------ Detail
with tab_detail:
    wahl = st.selectbox("Wert auswählen", tabelle["Ticker"].tolist(), index=0)
    r = ergebnisse[wahl]
    f = r.merkmale
    zeile = tabelle[tabelle["Ticker"] == wahl].iloc[0]
    st.markdown(f"## {wahl} &nbsp; {badge(r.signal, SIGNAL_FARBE[r.signal], gross=True)}", unsafe_allow_html=True)
    m = st.columns(6)
    m[0].metric("Kurs", fmt_kurs(zeile["Kurs"]), fmt_pct(zeile["Heute"]))
    m[1].metric("Score", f"{zeile['Score']:+d}", f"Zusatzfaktoren {r.zusatzpunkte:+.0f}" if r.zusatzpunkte else None, delta_color="off")
    m[2].metric("Stop", fmt_kurs(r.levels["stop"]))
    m[3].metric("Ziel", fmt_kurs(r.levels["ziel"]))
    m[4].metric("Chance/Risiko", f"{r.levels['crv']:.1f}" if np.isfinite(r.levels["crv"]) else "–")
    st_k, wert_k = E.positionsgroesse(zeile["Kurs"], r.levels["stop"], konto, risiko)
    m[5].metric("Stück bei deinem Risiko", f"{st_k:.2f}", f"Wert {wert_k:,.0f}", delta_color="off")
    if r.signal not in KAUF_SIGNALE:
        st.caption("Stop/Ziel/Stück gelten nur, falls du trotzdem kaufen würdest – das System empfiehlt hier keinen Kauf.")
    elif np.isfinite(r.levels["crv"]) and r.levels["crv"] < 1.5:
        st.warning("Kaufsignal, aber das Chance/Risiko-Verhältnis ist unter 1,5: Das nächste Ziel liegt nah. "
                   "Besser einen Rücksetzer Richtung Stop-Zone abwarten oder kleiner einsteigen.")

    links, rechts = st.columns([3, 2])
    with links:
        zeitraum = st.radio("Zeitraum", ["6 Monate", "1 Jahr", "2 Jahre"], index=1, horizontal=True)
        tage = {"6 Monate": 182, "1 Jahr": 365, "2 Jahre": 730}[zeitraum]
        d = r.daten.loc[r.daten.index >= r.daten.index[-1] - pd.Timedelta(days=tage)]
        fz = f.loc[d.index]
        fig = go.Figure()
        fig.add_trace(go.Candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name="Kurs"))
        fig.add_trace(go.Scatter(x=fz.index, y=fz["sma50"], name="50-Tage-Linie", line=dict(width=1.2, color="#1f77b4")))
        fig.add_trace(go.Scatter(x=fz.index, y=fz["sma200"], name="200-Tage-Linie", line=dict(width=1.6, color="#7f3fbf")))
        fig.add_trace(go.Scatter(x=fz.index, y=fz["avwap_tief"], name="VWAP ab 52W-Tief", line=dict(width=1.2, dash="dot", color="#2ca02c")))
        fig.add_trace(go.Scatter(x=fz.index, y=fz["avwap_tief"] + 2 * fz["avwap_tief_sd"], name="+2σ (überdehnt)",
                                 line=dict(width=1, dash="dot", color="#d62728")))
        last = f.iloc[-1]
        for y, name, col in [(last["poc"], "POC", "#555"), (last["val"], "VAL", "#888"), (last["vah"], "VAH", "#888")]:
            if np.isfinite(y):
                fig.add_hline(y=y, line=dict(color=col, width=1, dash="dash"), annotation_text=name, annotation_position="right", annotation_xshift=8)
        if r.signal in KAUF_SIGNALE:
            fig.add_hline(y=r.levels["stop"], line=dict(color="#c62828", width=2), annotation_text="Stop", annotation_position="left")
            fig.add_hline(y=r.levels["ziel"], line=dict(color="#0f7b3f", width=2), annotation_text="Ziel", annotation_position="left")
        fig.update_layout(height=520, xaxis_rangeslider_visible=False, margin=dict(l=10, r=70, t=10, b=10),
                          legend=dict(orientation="h", y=1.02, x=0))
        st.plotly_chart(fig, width="stretch")

        sc = r.score.loc[d.index]
        fig2 = go.Figure(go.Scatter(x=sc.index, y=sc, fill="tozeroy", line=dict(color="#1f77b4"), name="Score"))
        for yv, col in [(E.KAUF_SCHWELLE, "#3fae5a"), (E.STARK_SCHWELLE, "#0f7b3f"), (-E.KAUF_SCHWELLE, "#e0782f"), (-E.STARK_SCHWELLE, "#c62828")]:
            fig2.add_hline(y=yv, line=dict(color=col, width=1, dash="dot"))
        fig2.update_layout(height=200, margin=dict(l=10, r=10, t=30, b=10), title="Score-Verlauf", yaxis_range=[-100, 100])
        st.plotly_chart(fig2, width="stretch")

    with rechts:
        st.subheader("Warum dieses Signal?")
        for richtung, txt in r.gruende:
            sym, col = {"+": ("▲", "#0f7b3f"), "-": ("▼", "#c62828"), "0": ("●", "#8a8f98")}[richtung]
            st.markdown(f"<span style='color:{col};font-weight:700'>{sym}</span> {txt}", unsafe_allow_html=True)

        st.subheader("Insiderkäufe (90 Tage)")
        ins = (r.extras or {}).get("insider")
        if r.gruppe != "Aktien" or wahl in ("ASML", "TSM", "SAP", "NVO") or "." in wahl:
            st.caption("Nur für US-Aktien verfügbar (SEC Form 4).")
        elif not ins:
            st.caption("Noch keine Daten – werden einmal täglich von der SEC geladen.")
        else:
            if ins.get("kaeufe"):
                st.markdown(f"**{ins['kaeufer']} Manager** haben für **${ins['summe_usd']:,.0f}** eigene Aktien "
                            f"am offenen Markt gekauft ({ins['kaeufe']} Meldungen).")
            else:
                st.markdown(f"Keine Käufe am offenen Markt ({ins.get('filings_geprueft', 0)} Meldungen geprüft). "
                            "Verkäufe sind meist geplant (Vergütung, 10b5-1) und zählen nicht.")
        if r.gruppe == "Aktien" and "." not in wahl:
            st.markdown(f"[Alle Insider-Meldungen bei OpenInsider](http://openinsider.com/screener?s={wahl.replace('-', '.')}) · "
                        f"[SEC Form 4](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={wahl}&type=4&owner=include&count=40)")

        st.subheader("Bausteine des Scores")
        k_last = r.komponenten.iloc[-1]
        namen = {"trend": "Trend", "momentum": "Momentum", "rel_staerke": "Relative Stärke",
                 "akkumulation": "Akkumulation", "zone": "Kauf-/Verkaufszone", "volatilitaet": "Volatilität"}
        werte = [k_last[n] * E.GEWICHTE[n] * 100 for n in namen] + [r.zusatzpunkte]
        fig3 = go.Figure(go.Bar(x=werte, y=list(namen.values()) + ["Zusatzfaktoren*"], orientation="h",
                                marker_color=["#0f7b3f" if w >= 0 else "#c62828" for w in werte]))
        fig3.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="Beitrag zum Score")
        st.plotly_chart(fig3, width="stretch")
        st.caption("*Insider, Qualität, Sektorstärke, Funding – nur aktuell verfügbar, daher nicht im Backtest.")

    st.subheader(f"So haben die Signale bei {wahl} früher funktioniert")
    a = r.auswertung
    t = a["tabelle"].copy()
    st.dataframe(t, hide_index=True, width="stretch", column_config={
        "Trefferquote": st.column_config.NumberColumn(format="percent"),
        f"Ø Rendite {E.FORWARD_TAGE}T": st.column_config.NumberColumn(format="percent"),
    })
    st.caption(f"Zum Vergleich – an allen Tagen: Trefferquote {fmt_pct(a['basis_treffer'], False)}, "
               f"Ø Rendite {fmt_pct(a['basis_rendite'])} nach {E.FORWARD_TAGE} Tagen. "
               "Ein Signal ist nur dann etwas wert, wenn es besser ist als dieser Durchschnitt.")
    e1, e2 = st.columns([3, 2])
    with e1:
        eq = a["equity"]
        fig4 = go.Figure()
        for col, farbe in [("Signal-Strategie", "#1f77b4"), ("Kaufen & Halten", "#999")]:
            fig4.add_trace(go.Scatter(x=eq.index, y=eq[col], name=col, line=dict(color=farbe)))
        fig4.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), yaxis_type="log",
                           legend=dict(orientation="h", y=1.05))
        st.plotly_chart(fig4, width="stretch")
    with e2:
        st.markdown(
            f"| | Signal-Strategie | Kaufen & Halten |\n|---|---|---|\n"
            f"| Rendite pro Jahr | {fmt_pct(a['cagr_strategie'])} | {fmt_pct(a['cagr_halten'])} |\n"
            f"| Größter Rückgang | {fmt_pct(a['mdd_strategie'])} | {fmt_pct(a['mdd_halten'])} |\n"
            f"| Zeit investiert | {fmt_pct(a['zeit_im_markt'], False)} | 100% |\n"
            f"| Zeitraum | {a['jahre']:.1f} Jahre | |")
        st.caption("Strategie: kaufen ab Signal KAUFEN, verkaufen wenn Score unter −5 fällt, "
                   "Ausführung am nächsten Tag, 0,1 % Kosten je Wechsel.")

# ------------------------------------------------------------------ Swing-Trades
with tab_swing:
    sl, stt = lade_json(SWING_LIVE_URL), lade_json(SWING_TEST_URL)
    st.subheader("Swing-Trades: Modell-Depot mit festen Regeln")
    st.markdown(
        "- **Einstieg:** STARK KAUFEN, Chance/Risiko mindestens 1,5, nicht überdehnt, Markt nicht RISK-OFF, keine Quartalszahlen in "
        "7 Tagen → **Kauf zur nächsten Eröffnung**\n"
        "- **Stop** sofort beim Broker setzen. Er wird mit dem Kurs **nur nach oben** nachgezogen (höchster Schluss minus 3 × ATR)\n"
        "- **Ziel:** dort **halbe Position** verkaufen, Rest-Stop mindestens auf Einstand\n"
        "- **Ausstieg** über den Stop, wenn der Score unter −5 fällt oder nach 60 Handelstagen\n"
        "- Höchstens 10 Positionen. Jede Aktion kommt als Push-Nachricht")
    sth = stt.get("watchlist") if stt else None
    stu = stt.get("universum") if stt else None
    if stu and stu["statistik"].get("trades"):
        x = stu["statistik"]
        k_d, k_u = stu["kennzahlen"].get("Swing-Depot (Regeln)", {}), stu["kennzahlen"].get("Alle Werte gleichgewichtet halten", {})
        besser = k_d.get("cagr", -1) > k_u.get("cagr", 0)
        (st.success if besser else st.error)(
            f"**Testergebnis dieser Regeln über {stu['werte']} US-Aktien ({stu['von'][:4]}–{stu['bis'][:4]}):** {x['trades']} Trades, "
            f"Trefferquote {x['trefferquote']:.0%}, Ø Gewinn {x['gewinn_mittel']:+.1%}, Ø Verlust {x['verlust_mittel']:+.1%}, "
            f"Ø pro Trade {x['erwartung']:+.2%}. Als Depot: {k_d.get('cagr', 0):+.1%} pro Jahr gegenüber {k_u.get('cagr', 0):+.1%} "
            "für alle Aktien gleichgewichtet. " + ("" if besser else "Die Regeln haben im Rückblick **keinen Vorteil** gebracht – "
                                                   "Modell-Depot nur als Disziplin- und Lernwerkzeug verwenden."))
    if not sl:
        st.info("Das Modell-Depot startet mit der nächsten Berechnung nach US-Börsenschluss.")
    else:
        st.caption(f"Modell-Depot seit {pd.Timestamp(sl['start']):%d.%m.%Y} · zuletzt verarbeitet: Handelstag {pd.Timestamp(sl['letzter_tag']):%d.%m.%Y}")
        zeilen = []
        for t in sl.get("offen", []):
            entry = t.get("entry")
            akt = t.get("kurs_aktuell")
            basis = entry or t["kurs_signal"]
            stueck, wert = E.positionsgroesse(basis, t["stop_start"] if not entry else t["stop"], konto, risiko) \
                if t["status"] == "geplant" else (None, None)
            zeilen.append({"Ticker": t["ticker"] + (" ⭐" if t.get("top") else ""),
                           "Status": {"geplant": "KAUFEN zur Eröffnung", "offen": "offen"}.get(t["status"], t["status"]) +
                                     (" · Verkauf geplant" if t.get("ausstieg_geplant") else ""),
                           "Signal vom": t["signal_tag"], "Einstieg": entry, "Kurs": akt,
                           "Ergebnis": (t.get("wert") - 1) if entry and t.get("wert") else None,
                           "Stop jetzt": t["stop"], "Ziel (½)": None if t["halb"] else t["ziel"], "½ verkauft": "ja" if t["halb"] else "",
                           "Tage": t["tage"], "Stück (neu)": stueck})
        if zeilen:
            sdf = pd.DataFrame(zeilen)
            st.dataframe(sdf, hide_index=True, width="stretch", column_config={
                **{c_: st.column_config.NumberColumn(format="%.2f") for c_ in ["Einstieg", "Kurs", "Stop jetzt", "Ziel (½)", "Stück (neu)"]},
                "Ergebnis": st.column_config.NumberColumn(format="percent")})
            st.caption("„Stück (neu)“ gilt für neue Käufe und ist mit deiner Kontogröße und deinem Risiko pro Trade aus der Seitenleiste berechnet.")
        else:
            st.write("Aktuell keine offenen oder geplanten Trades.")
        g = [t for t in sl.get("geschlossen", []) if t.get("status") == "geschlossen"]
        b_ = sl.get("bilanz", {})
        if b_.get("trades"):
            st.markdown(f"**Live-Bilanz:** {b_['trades']} abgeschlossene Trades · Trefferquote {b_['trefferquote']:.0%} · "
                        f"Ø pro Trade {b_['erwartung']:+.2%}")
        if g:
            with st.expander(f"Abgeschlossene Trades ({len(g)})"):
                gdf = pd.DataFrame([{"Ticker": t["ticker"], "Einstieg": t["einstieg_tag"], "Ausstieg": t["ausstieg_tag"],
                                     "Kauf": t["entry"], "Verkauf": t["ausstieg_preis"], "Ergebnis": t["ergebnis"],
                                     "Grund": t["grund"], "Tage": t["tage"]} for t in reversed(g)])
                st.dataframe(gdf, hide_index=True, width="stretch", column_config={
                    "Kauf": st.column_config.NumberColumn(format="%.2f"), "Verkauf": st.column_config.NumberColumn(format="%.2f"),
                    "Ergebnis": st.column_config.NumberColumn(format="percent")})
    if stt:
        st.divider()
        st.subheader("Backtest der Regeln")
        for teil, titel in ((stu, "Breiter Test: S&P 1500 (aussagekräftiger)"), (sth, "Nur Watchlist (geschönt: heutige Gewinner)")):
            if not teil or not teil["statistik"].get("trades"):
                continue
            x = teil["statistik"]
            st.markdown(f"**{titel}** – {teil['werte']} Werte, {teil['von']} bis {teil['bis']}")
            st.dataframe(pd.DataFrame([{
                "Trades": x["trades"], "Trefferquote": x["trefferquote"], "Ø Gewinn": x["gewinn_mittel"], "Ø Verlust": x["verlust_mittel"],
                "Ø pro Trade": x["erwartung"], "Profit-Faktor": x["profit_faktor"], "Ø in R": x["r_mittel"], "Haltedauer (Median, Tage)": x["tage_median"],
                "Ø pro Trade 1. Hälfte": x["erwartung_haelfte1"], "Ø pro Trade 2. Hälfte": x["erwartung_haelfte2"]}]), hide_index=True, width="stretch",
                column_config={**{c_: st.column_config.NumberColumn(format="percent") for c_ in
                                  ["Trefferquote", "Ø Gewinn", "Ø Verlust", "Ø pro Trade", "Ø pro Trade 1. Hälfte", "Ø pro Trade 2. Hälfte"]},
                               "Profit-Faktor": st.column_config.NumberColumn(format="%.2f", help="Summe Gewinne ÷ Summe Verluste; über 1 = profitabel"),
                               "Ø in R": st.column_config.NumberColumn(format="%.2f", help="Ergebnis in Vielfachen des anfänglichen Risikos")})
            st.caption("Ausstiegsgründe: " + ", ".join(f"{k} {v}×" for k, v in x["gruende"].items()))
            test_anzeige(teil, "Swing-Depot (Regeln)")
        st.caption("Depot-Simulation: 1 % Risiko je Trade, höchstens 20 % je Position und 10 Positionen, Kauf zur Eröffnung nach dem Signal, "
                   "0,1 % Kosten je Kauf und Verkauf. Quartalszahlen-Sperre und Zusatzfaktoren (Insider usw.) lassen sich rückwirkend nicht "
                   "prüfen. Keine Anlageberatung.")

# ------------------------------------------------------------------ Krypto-Strategie
with tab_kry:
    kr = lade_krypto()
    st.subheader("Krypto-Strategie für Bitcoin und Ethereum")
    if not kr or not kr.get("coins"):
        st.info("Die Krypto-Strategie wird alle 3 Stunden berechnet und ist noch nicht verfügbar.")
    else:
        st.markdown("**Trendfolge mit fünf Regeln:** Jede Regel, die „an“ ist, steht für 20 % Anteil. Sind alle fünf an, bist du "
                    "voll investiert, ist keine an, hältst du Cash. Geprüft wird nur der Tagesschlusskurs, einmal täglich reicht.")
        spalten = st.columns(len(kr["coins"]))
        for sp, (t, c) in zip(spalten, kr["coins"].items()):
            a_ = c["aktuell"]
            farbe = "#0f7b3f" if a_["anteil"] >= 0.6 else "#b58900" if a_["anteil"] >= 0.4 else "#c62828"
            with sp:
                anteil_txt = "{:.0%} investiert".format(a_["anteil"])
                st.markdown(f"**{c['name']}** · Kurs {c['kurs']:,.0f} $&nbsp;&nbsp;" + badge(anteil_txt, farbe, gross=True),
                            unsafe_allow_html=True)
                st.caption(f"Unverändert seit {pd.Timestamp(a_['seit']):%d.%m.%Y} · Stand Tagesschluss {pd.Timestamp(c['bis']):%d.%m.%Y}")
                for e in a_["einzel"]:
                    sym, col = ("▲", "#0f7b3f") if e["an"] else ("▼", "#c62828")
                    was = "Durchschnitt" if e["regel"].startswith("SMA") else "Kurs damals"
                    st.markdown(f"<span style='color:{col};font-weight:700'>{sym}</span> {e['regel']}: {'an' if e['an'] else 'aus'} "
                                f"· {was} {e['schwelle']:,.0f} $ ({e['abstand']:+.0%})", unsafe_allow_html=True)
        st.caption("Die Prozentzahl in Klammern zeigt, wie weit der Kurs über (+) oder unter (−) der Schwelle liegt. "
                   "Kreuzt er sie, schaltet die Regel um, und du bekommst eine Push-Nachricht.")

        st.divider()
        wahl_k = st.radio("Test anzeigen für", [c["name"] for c in kr["coins"].values()], horizontal=True, key="kr_wahl")
        t, c = next((t, c) for t, c in kr["coins"].items() if c["name"] == wahl_k)
        u, kz = c["urteil"], c["kennzahlen"]
        h_, e_ = kz["Kaufen und Halten"], kz["Ensemble (5 Regeln)"]
        st.success(f"**Testergebnis {c['name']} ({pd.Timestamp(c['von']):%m/%Y} bis heute):** Die Strategie brachte {e_['cagr']:+.0%} pro Jahr "
                   f"bei einem größten Rückgang von {e_['mdd']:.0%}. Halten brachte {h_['cagr']:+.0%} bei {h_['mdd']:.0%}. "
                   f"Rendite pro Risiko war in beiden Zeithälften besser, und {u['sma_laengen_besser_als_halten']} von {u['sma_laengen']} "
                   "getesteten Durchschnittslängen schlugen das Halten. Das Ergebnis hängt also nicht an einer Glückszahl.")
        zk = pd.DataFrame([{"Regel": n, "Rendite p.a.": v["cagr"], "nach KESt p.a. (grob)": v["cagr_nach_steuer"],
                            "Größter Rückgang": v["mdd"], "Sharpe": v["sharpe"], "Schlechtestes Jahr": v["schlechtestes_jahr"],
                            "2. Hälfte p.a.": v["cagr_haelfte2"], "Zeit investiert": v.get("zeit_investiert"),
                            "Wechsel pro Jahr": v.get("umschichtungen_pro_jahr")} for n, v in kz.items()])
        st.dataframe(zk, hide_index=True, width="stretch", column_config={
            **{c_: st.column_config.NumberColumn(format="percent") for c_ in ["Rendite p.a.", "nach KESt p.a. (grob)", "Größter Rückgang",
                                                                             "Schlechtestes Jahr", "2. Hälfte p.a.", "Zeit investiert"]},
            "Sharpe": st.column_config.NumberColumn(format="%.2f", help="Rendite pro Risiko"),
            "Wechsel pro Jahr": st.column_config.NumberColumn(format="%.0f", help="Summe der Umschichtungen in vollen Positionen")})
        figk = go.Figure()
        for n, k in c["kurven"].items():
            figk.add_trace(go.Scatter(x=k["monat"], y=k["wert"], name=n, line=dict(width=3 if n.startswith("Ensemble") else 1.5)))
        figk.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=10), yaxis_type="log", yaxis_title="Wert von 1 $ (log)",
                           legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(figk, width="stretch")
        mv = c.get("mvrv")
        if mv:
            hilft = [n for n, ok in mv.get("verbessert", {}).items() if ok]
            st.caption(f"MVRV (Marktwert geteilt durch den Einstandspreis aller Coins) steht bei {mv['aktuell']:.2f}. Über {mv['hoch']} gilt als "
                       f"überhitzt, unter {mv['tief']} als günstig. Die MVRV-Zeilen in der Tabelle zeigen, was diese Zusatzregeln gebracht hätten: "
                       + ("bei diesem Coin eine kleine Verbesserung. " if hilft else "bei diesem Coin keine Verbesserung. ")
                       + "Weil sie nicht bei beiden Coins helfen, sind sie nicht Teil der Strategie.")
        tr = pd.DataFrame([{"Regel": n, "Trades": v["trades"]["trades"], "Trefferquote": v["trades"]["trefferquote"],
                            "Ø Gewinn": v["trades"]["gewinn_mittel"], "Ø Verlust": v["trades"]["verlust_mittel"],
                            "Größter Gewinn": v["trades"]["groesster_gewinn"]} for n, v in kz.items() if v.get("trades")])
        if len(tr):
            with st.expander("Trefferquote der einzelnen Regeln: warum sie niedrig ist und trotzdem funktioniert"):
                st.dataframe(tr, hide_index=True, width="stretch", column_config={
                    c_: st.column_config.NumberColumn(format="percent") for c_ in ["Trefferquote", "Ø Gewinn", "Ø Verlust", "Größter Gewinn"]})
                st.caption("Trendfolge liegt nur bei etwa jedem dritten bis zweiten Trade richtig. Sie verdient, weil ein Gewinn im Schnitt "
                           "ein Vielfaches eines Verlusts bringt: Verluste werden früh begrenzt, Gewinne laufen gelassen.")
        jr = pd.DataFrame({"Halten": h_["jahresrenditen"], "Strategie": e_["jahresrenditen"]})
        jr.index.name = "Jahr"
        with st.expander("Rendite je Kalenderjahr"):
            st.dataframe(jr.reset_index(), hide_index=True, width="stretch",
                         column_config={c_: st.column_config.NumberColumn(format="percent") for c_ in jr.columns})
        st.caption(f"Signal aus dem Tagesschluss, Handel am Folgetag, {kr['kosten']:.2%} Kosten je Umschichtung. "
                   "KESt-Näherung: Gewinne werden jedes Jahr versteuert, beim Halten erst am Ende.")

        fg = kr.get("fear_greed")
        if fg:
            st.divider()
            st.subheader(f"Fear & Greed Index: aktuell {fg['aktuell']:.0f}")
            st.markdown("Oft heißt es: „Kaufen, wenn alle Angst haben.“ Die Daten seit 2018 stützen das **nicht**:")
            fdf = pd.DataFrame([{"Stimmung": x["stufe"], "Tage": x["tage"], "Ø Rendite 30 Tage": x["mittel_30"], "Median 30 Tage": x["median_30"],
                                 "Ø Rendite 90 Tage": x["mittel_90"], "Median 90 Tage": x["median_90"], "Gewinn nach 90 Tagen in": x["treffer_90"]}
                                for x in fg["stufen"]])
            st.dataframe(fdf, hide_index=True, width="stretch",
                         column_config={c_: st.column_config.NumberColumn(format="percent") for c_ in fdf.columns[2:]})
            st.caption("Bitcoin-Rendite nach Tagen mit der jeweiligen Stimmung. Nach extremer Gier lief es im Schnitt besser als nach "
                       "extremer Angst. Das passt zur Trendfolge. Der Index fließt deshalb nicht in die Strategie ein.")
        st.warning("Grenzen: Krypto hat erst wenige vollständige Zyklen hinter sich, die Vergangenheit ist keine Garantie. "
                   "Auch die Strategie hatte Verlustjahre und Rückgänge von über 50 %. Nach Steuern lag sie bei Bitcoin hinter dem "
                   "Halten, weil jeder Verkauf mit Gewinn Steuer auslöst. Nur Geld einsetzen, dessen Verlust du verkraftest. "
                   "Keine Anlageberatung.")

# ------------------------------------------------------------------ Insider-Radar
with tab_ins:
    ins, stu = lade_insider("insider.json"), lade_insider("studie.json")
    st.subheader("Insider-Radar: Wo kaufen Manager gerade ihre eigene Aktie?")
    st.markdown("Der Scanner liest **alle** Insider-Meldungen des US-Marktes bei der SEC, nicht nur die Watchlist. "
                "**Cluster** = mindestens 3 verschiedene Manager oder Aufsichtsräte kaufen innerhalb von 30 Tagen am offenen Markt, "
                "zusammen für mindestens 100.000 $. **Chef-Großkauf** = CEO, CFO, President oder Chairman kauft für mindestens 250.000 $.")
    h6_ = ((stu or {}).get("ergebnis", {}).get("A", {}).get("horizonte", {}) or {}).get("126")
    if h6_ and h6_["t_monate"] < 2:
        st.error(f"**Ehrliches Testergebnis:** In {h6_['n']} auswertbaren Cluster-Fällen seit 2016 lag der Vorsprung gegenüber dem "
                 f"Small-Cap-Index nach 6 Monaten im Schnitt bei {h6_['mittel']:+.1%} (Median {h6_['median']:+.1%}, "
                 f"Trefferquote {h6_['treffer']:.0%}). Das ist **kein belegbarer Vorteil**. Die Liste zeigt, wo Manager kaufen – "
                 "sie ist eine Ideensammlung, kein Kaufsignal.")
    if not ins:
        st.info("Der Scanner läuft alle 3 Stunden und hat noch kein Ergebnis geliefert.")
    else:
        if not ins.get("vollstaendig"):
            st.warning(f"Der Scanner füllt die letzten 30 Tage noch auf (bisher lückenlos: {ins.get('abgedeckte_tage', 0)} Tage). "
                       "Bis dahin können Cluster fehlen oder zu klein erscheinen.")
        st.caption(f"Stand {pd.Timestamp(ins['erstellt_utc']).tz_convert('Europe/Vienna'):%d.%m.%Y %H:%M} · "
                   f"{ins.get('kaeufe_im_speicher', 0)} Insiderkäufe der letzten Wochen im Speicher")
        zeilen = []
        for c in ins.get("cluster", []):
            stueck, wert = E.positionsgroesse(c.get("kurs") or float("nan"), c.get("stop") or float("nan"), konto, risiko)
            hinw = []
            if c.get("handelbar") is False:
                hinw.append("zu wenig Handel")
            if c.get("handelbar") is None:
                hinw.append("keine Kursdaten")
            if c.get("ueber_200") is False:
                hinw.append("im Abwärtstrend")
            if c.get("zum_kaufpreis") is not None and abs(c["zum_kaufpreis"]) > 0.5:
                hinw.append("Preis passt nicht zum Kurs (andere Aktiengattung oder Auslandsbörse)")
            elif c.get("zum_kaufpreis") is not None and c["zum_kaufpreis"] > 0.15:
                hinw.append("schon deutlich über Insider-Preis")
            zeilen.append({"Art": "Cluster" if c["typ"] == "A" else "Chef-Großkauf", "Ticker": c["ticker"], "Firma": c["firma"],
                           "Datum": c["datum"], "Käufer": c["kaeufer_anzahl"], "Summe $": c["summe"],
                           "Insider-Preis": c.get("kaufpreis_mittel"), "Kurs": c.get("kurs"), "Kurs vs. Insider": c.get("zum_kaufpreis"),
                           "Stop": c.get("stop"), "Stück": stueck or None,
                           "Tagesumsatz Mio $": (c.get("umsatz_usd") or 0) / 1e6 or None, "Hinweis": " · ".join(hinw)})
        if zeilen:
            idf = pd.DataFrame(zeilen)
            st.dataframe(idf, hide_index=True, width="stretch", height=min(38 * (len(idf) + 1), 560), column_config={
                "Summe $": st.column_config.NumberColumn(format="%.0f"), "Insider-Preis": st.column_config.NumberColumn(format="%.2f"),
                "Kurs": st.column_config.NumberColumn(format="%.2f"), "Stop": st.column_config.NumberColumn(format="%.2f"),
                "Kurs vs. Insider": st.column_config.NumberColumn(format="percent", help="Aktueller Kurs gegenüber dem Durchschnittspreis der Insider"),
                "Stück": st.column_config.NumberColumn(format="%.1f"), "Tagesumsatz Mio $": st.column_config.NumberColumn(format="%.1f")})
            wahl_i = st.selectbox("Wer hat gekauft?", [f"{c['ticker']} · {c['datum']}" for c in ins["cluster"]], key="ins_wahl")
            c = ins["cluster"][[f"{x['ticker']} · {x['datum']}" for x in ins["cluster"]].index(wahl_i)]
            for kf in c.get("kaeufer", []):
                st.markdown(f"- **{kf['name']}** ({kf['titel']}): ${kf['wert']:,.0f} zu {kf['preis']:.2f} $, gemeldet am {pd.Timestamp(kf['gemeldet']):%d.%m.}")
            st.markdown(f"[Alle Meldungen bei OpenInsider](http://openinsider.com/screener?s={c['ticker'].replace('-', '.')}) · "
                        f"[Chart bei Yahoo](https://finance.yahoo.com/quote/{c['ticker']})")
        else:
            st.write("Aktuell kein Cluster und kein Chef-Großkauf in den letzten 30 Tagen.")

    st.divider()
    st.subheader("Hat das früher funktioniert? Studie über alle Insiderkäufe")
    if not stu:
        st.info("Die Studie ist noch nicht berechnet.")
    else:
        st.caption(f"Alle Form-4-Käufe der SEC, Kurse {stu['zeitraum'][0]} bis {stu['zeitraum'][1]}. Kauf am Tag nach der Meldung, "
                   f"Vergleich mit dem Small-Cap-Index (IWM), {stu['regeln']['KOSTEN_RUNDE']:.1%} Kosten je Trade abgezogen.")
        zs = []
        for typ, z in stu["ergebnis"].items():
            for h, e in z["horizonte"].items():
                zs.append({"Ereignis": z["name"], "Haltedauer": {"21": "1 Monat", "63": "3 Monate", "126": "6 Monate"}.get(h, h),
                           "Fälle": e["n"], "Ø Vorsprung": e["mittel"], "Median": e["median"], "Trefferquote": e["treffer"],
                           "t-Wert": e["t_monate"], "1. Hälfte": e["haelfte1"], "2. Hälfte": e["haelfte2"]})
        if zs:
            sdf = pd.DataFrame(zs)
            st.dataframe(sdf, hide_index=True, width="stretch", column_config={
                **{c_: st.column_config.NumberColumn(format="percent") for c_ in ["Ø Vorsprung", "Median", "Trefferquote", "1. Hälfte", "2. Hälfte"]},
                "t-Wert": st.column_config.NumberColumn(format="%.1f", help="Über 2: wahrscheinlich kein Zufall (nach Monaten gebündelt)")})
            st.caption("Vorsprung = Rendite der Aktie minus Rendite des Small-Cap-Index im selben Zeitraum, nach Kosten. "
                       "Trefferquote = Anteil der Fälle, die besser liefen als der Index.")
        fig = go.Figure()
        for typ, z in stu["ergebnis"].items():
            if z.get("pfad"):
                fig.add_trace(go.Scatter(x=z["pfad"]["tag"], y=[v * 100 for v in z["pfad"]["ueberrendite"]], name=z["name"]))
        if fig.data:
            fig.add_hline(y=0, line=dict(color="#888", width=1))
            fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), title="Durchschnittlicher Vorsprung nach der Meldung (vor Kosten)",
                              xaxis_title="Handelstage nach der Meldung", yaxis_title="Vorsprung in %", legend=dict(orientation="h", y=-0.3))
            st.plotly_chart(fig, width="stretch")
        for typ, pf in (stu.get("portfolio") or {}).items():
            st.markdown(f"**Portfolio-Test „{stu['typen'][typ]}“** – jede Aktie 6 Monate halten, alle gleich gewichtet "
                        f"(im Schnitt {pf['positionen_mittel']:.0f} Positionen)")
            test_anzeige(pf, next(iter(pf["kurven"])))
        a_ = stu["ergebnis"].get("A", {})
        h6 = a_.get("horizonte", {}).get("126", {})
        if a_:
            st.warning(f"Grenzen der Studie: Von {a_['ereignisse_gesamt']} Cluster-Fällen ließen sich {a_['mit_kursen_handelbar']} auswerten. "
                       f"Für {a_['ohne_kurse']} fehlen Kurse, meist weil die Firma nicht mehr existiert (übernommen oder pleite). "
                       + (f"Hätten alle fehlenden Fälle 30 % schlechter abgeschnitten als der Index, läge der Vorsprung nach 6 Monaten bei "
                          f"{h6['pessimistisch']:+.1%} statt {h6['mittel']:+.1%}. " if h6.get("pessimistisch") is not None else "")
                       + "Kleine Aktien schwanken stark und sind teurer zu handeln. Keine Anlageberatung.")

# ------------------------------------------------------------------ Potenzial-Kandidaten
kand = lade_kandidaten()
with tab_kand:
    if not kand:
        st.info("Die Kandidaten-Liste wird einmal täglich nach US-Börsenschluss berechnet und ist noch nicht verfügbar.")
    else:
        st.subheader("Potenzial-Kandidaten: mittelgroße US-Firmen")
        st.caption(f"Stand: Kurse bis {pd.Timestamp(kand['daten_bis']):%d.%m.%Y} · {kand['universum_anzahl']} Aktien geprüft "
                   f"(S&P 1500) · Kandidaten nur aus Mid und Small Caps · Marktampel {kand['regime']} · höchstens 3 Werte pro Sektor")
        fc_roh = kand.get("faktor_check", [])
        belegt = [r["name"] for r in fc_roh if r["urteil"].startswith("wirkt (") and r.get("gewicht")]
        pt = (kand.get("portfolio_test") or {}).get("kennzahlen", {})
        s_top, s_uni = pt.get("Top 15 nach Score", {}), pt.get("Alle Aktien des Universums (gleichgewichtet)", {})
        if s_top and s_uni and s_top["cagr"] < s_uni["cagr"]:
            st.error(f"**Ehrliches Testergebnis:** Im Rückblick hat der Kurs-Teil dieses Scores **keinen Vorteil** gebracht "
                     f"({s_top['cagr']:+.1%} pro Jahr gegenüber {s_uni['cagr']:+.1%} für alle Aktien des Universums). "
                     + ("Kein einzelner Kurs-Faktor zeigte einen belegten Zusammenhang mit späteren Gewinnen. " if not belegt else "")
                     + "Nutze die Liste als **Ideensammlung zum Selbst-Prüfen**, nicht als Kaufsignal. Details unten.")
        st.markdown("Hier sucht das Radar **abseits der bekannten Riesen**. Punkte (0–100): bis 60 aus Kurs und Volumen "
                    "(Momentum, Nähe zum Hoch, Kurssprünge nach Nachrichten, ruhiger Verlauf, Kaufdruck), dazu Zu- und Abschläge für "
                    "Gewinnüberraschung, Analysten-Revisionen, Insiderkäufe, Wachstum und Qualität.")
        kz = []
        for z in kand["kandidaten"]:
            stueck, wert = E.positionsgroesse(z.get("kurs") or float("nan"), z.get("stop") or float("nan"), konto, risiko)
            ins = z.get("insider") or {}
            kz.append({
                "Ticker": z["ticker"], "Firma": z["name"], "Sektor": z["sektor"], "Größe": z.get("segment") or "", "Punkte": round(z["punkte"]),
                "Kurs": z.get("kurs"), "Stop": z.get("stop"), "Ziel": z.get("ziel"), "CRV": z.get("crv"),
                "Stück": stueck, "Positionswert": wert,
                "Börsenwert Mrd $": z.get("marktwert_mrd"),
                "Gewinn vs. Erwartung %": z.get("ueberraschung_pct"),
                "Schätzungen ↑/↓": (f"{z['rev_hoch']}/{z['rev_runter']}" if z.get("rev_hoch") is not None else ""),
                "Insider 90T": insider_text(ins) if ins else "",
                "Nächste Zahlen": z.get("naechste_zahlen") or "",
                "Hinweis": " · ".join(z.get("hinweise") or []),
            })
        kdf = pd.DataFrame(kz)
        st.dataframe(kdf, hide_index=True, width="stretch", height=min(38 * (len(kdf) + 1), 640), column_config={
            "Punkte": st.column_config.ProgressColumn("Punkte", min_value=0, max_value=100, format="%d"),
            "Kurs": st.column_config.NumberColumn(format="%.2f"), "Stop": st.column_config.NumberColumn(format="%.2f"),
            "Ziel": st.column_config.NumberColumn(format="%.2f"), "CRV": st.column_config.NumberColumn(format="%.1f"),
            "Stück": st.column_config.NumberColumn(format="%.1f"), "Positionswert": st.column_config.NumberColumn(format="%.0f"),
            "Börsenwert Mrd $": st.column_config.NumberColumn(format="%.1f"),
            "Gewinn vs. Erwartung %": st.column_config.NumberColumn(format="%+.0f", help="Letzte Quartalszahlen: Gewinn je Aktie gegenüber der Analystenschätzung"),
            "Schätzungen ↑/↓": st.column_config.TextColumn(help="Analysten, die ihre Gewinnschätzung in 30 Tagen angehoben / gesenkt haben"),
        })
        if len(kdf):
            kw = st.selectbox("Begründung für", kdf["Ticker"].tolist(), key="kand_wahl")
            z = next(x for x in kand["kandidaten"] if x["ticker"] == kw)
            extra = []
            if z.get("branche"):
                extra.append(z["branche"])
            if z.get("boerse_seit_jahren"):
                extra.append(f"an der Börse seit ca. {z['boerse_seit_jahren']:.0f} Jahren")
            st.markdown(f"**{z['name']} ({kw})** – {z['punkte']:.0f} Punkte = {z['preis_punkte']:.0f} aus Kurs/Volumen "
                        f"{z['zusatz_punkte']:+.0f} aus Fundamentaldaten" + (" · " + " · ".join(extra) if extra else ""))
            for g in z["gruende"]:
                sym, col = {"+": ("▲", "#0f7b3f"), "-": ("▼", "#c62828")}.get(g["richtung"], ("●", "#8a8f98"))
                st.markdown(f"<span style='color:{col};font-weight:700'>{sym}</span> {g['text']}", unsafe_allow_html=True)
            for h in z.get("hinweise") or []:
                st.warning(h)
            st.markdown(f"[Chart bei Yahoo](https://finance.yahoo.com/quote/{kw}) · "
                        f"[Insider bei OpenInsider](http://openinsider.com/screener?s={kw.replace('-', '.')})")

        st.divider()
        st.subheader("Welche Faktoren hingen wirklich mit Gewinnen zusammen?")
        st.markdown("Für jeden Monat der letzten Jahre wurden alle Aktien nach dem Faktor sortiert und mit der Rendite des "
                    "**Folgemonats** verglichen. **Vorsprung** = bestes Fünftel minus schlechtestes Fünftel. "
                    "**t-Wert** über 2 heißt: wahrscheinlich kein Zufall.")
        fc = pd.DataFrame([{"Faktor": r["name"], "Gewicht im Score": r.get("gewicht"), "Vorsprung pro Monat": r["spread_monat"],
                            "Zusammenhang (IC)": r["ic"], "t-Wert": r["t"], "IC 1. Hälfte": r["ic_haelfte1"],
                            "IC 2. Hälfte": r["ic_haelfte2"], "Urteil": r["urteil"]} for r in kand.get("faktor_check", [])])
        if len(fc):
            st.dataframe(fc, hide_index=True, width="stretch", column_config={
                "Gewicht im Score": st.column_config.NumberColumn(format="percent"),
                "Vorsprung pro Monat": st.column_config.NumberColumn(format="percent"),
                "Zusammenhang (IC)": st.column_config.NumberColumn(format="%.3f"), "t-Wert": st.column_config.NumberColumn(format="%.1f"),
                "IC 1. Hälfte": st.column_config.NumberColumn(format="%.3f"), "IC 2. Hälfte": st.column_config.NumberColumn(format="%.3f")})
            st.caption("Die Gewichte wurden vorab aus der Forschung übernommen und nicht an diese Daten angepasst. "
                       "Faktoren ohne Gewicht dienen nur dem Vergleich.")

        lb = kand.get("live_bilanz") or {}
        st.subheader("Echtzeit-Protokoll: Was bringt die Liste wirklich?")
        if lb.get("fertig"):
            c1, c2, c3 = st.columns(3)
            c1.metric("Ø Liste nach 1 Monat", f"{lb['mittel_liste']:+.1%}")
            c2.metric("Ø alle Aktien", f"{lb['mittel_universum']:+.1%}")
            c3.metric("Ø S&P 500", f"{lb['mittel_spy']:+.1%}")
            st.caption(f"{lb['fertig']} abgeschlossene Tageslisten seit {lb['seit']}. Aussagekräftig erst ab etwa 6–12 Monaten.")
        else:
            st.info(f"Seit {lb.get('seit') or 'heute'} wird jede Tagesliste gespeichert und nach einem Monat mit dem Markt verglichen. "
                    "Das ist der einzige saubere Test für den Gesamt-Score (inklusive Gewinnüberraschung, Analysten und Insidern), "
                    "weil er nicht in die Vergangenheit schauen kann. Erste Ergebnisse in etwa einem Monat.")
        if kand.get("portfolio_test", {}).get("kennzahlen"):
            st.subheader("Portfolio-Test: jeden Monat die besten 15 kaufen")
            test_anzeige(kand["portfolio_test"], "Top 15 nach Score")
            st.warning("Der faire Vergleich ist **„Alle Aktien des Universums“**, nicht der ETF: Das Universum enthält nur Firmen, die "
                       "heute im S&P 1500 sind. Pleiten und Rauswürfe fehlen. Das hebt den Durchschnitt aller Aktien künstlich an und "
                       "benachteiligt Strategien, die auf Stärke setzen – das wahre Ergebnis liegt vermutlich weniger weit auseinander. "
                       "Der Test nutzt nur Kurs und Volumen; Gewinnüberraschung, Revisionen und Insider lassen sich mit "
                       "kostenlosen Daten nicht rückwirkend prüfen. Keine Anlageberatung.")

# ------------------------------------------------------------------ ETF-Strategien
with tab_rot:
    rot = (kand or {}).get("etf_rotation") or {}
    if not rot.get("kennzahlen"):
        st.info("Der ETF-Strategie-Test wird einmal täglich berechnet und ist noch nicht verfügbar.")
    else:
        st.subheader("Einfache ETF-Strategien: eine Entscheidung pro Monat")
        st.markdown("Zwei veröffentlichte Regeln, getestet mit ganzen Anlageklassen statt Einzelaktien. Hier gibt es keine "
                    "verschwundenen Pleitefirmen – dieser Rückblick ist der **sauberste** im ganzen Radar.\n"
                    "- **Trendfolge (Meb Faber, 2007):** fünf Anlageklassen zu je 20 %. Jede nur halten, solange sie über ihrem "
                    "10-Monats-Durchschnitt liegt, sonst Cash.\n"
                    "- **Dual Momentum (Gary Antonacci, 2014):** Waren US-Aktien in 12 Monaten besser als Cash? Dann die stärkere "
                    "von US- und Auslandsaktien halten, sonst Anleihen.")
        akt, namen = rot["aktuell"], rot.get("namen", {})
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Trendfolge – aktuelle Aufteilung** (Signal vom {pd.Timestamp(akt['signal_vom']):%d.%m.%Y})")
            for t, g in sorted(akt["gtaa"].items(), key=lambda x: -x[1]):
                st.markdown(f"- {g:.0%} {namen.get(t, t)}")
        with c2:
            st.markdown("**Dual Momentum – aktuelle Position**")
            for t, g in akt["gem"].items():
                st.markdown(f"- {g:.0%} {namen.get(t, t)}")
            st.caption(f"Unverändert seit {pd.Timestamp(akt['gem_seit']):%d.%m.%Y} · {akt['gem_wechsel_gesamt']} Wechsel im ganzen Testzeitraum")
        test_anzeige(rot, "Dual Momentum (Antonacci)")
        jr = pd.DataFrame(rot.get("jahresrenditen", {}))
        if len(jr):
            with st.expander("Rendite je Kalenderjahr"):
                jr.index.name = "Jahr"
                st.dataframe(jr.reset_index(), hide_index=True, width="stretch",
                             column_config={c: st.column_config.NumberColumn(format="percent") for c in jr.columns})
        st.warning("Einordnung: Solche Regeln haben historisch vor allem **Crashs abgefedert**, nicht die Rendite erhöht. "
                   "In Österreich fällt bei jedem Wechsel mit Gewinn 27,5 % KESt an – beim Sparplan erst beim Verkauf. "
                   "Getestet mit US-ETFs in Dollar; in der EU bräuchtest du vergleichbare UCITS-ETFs, und der Euro-Dollar-Kurs "
                   "verändert das Ergebnis. Keine Anlageberatung.")

# ------------------------------------------------------------------ Güte gesamt
with tab_guete:
    pr = lade_protokoll()
    st.subheader("Echtzeit-Protokoll: STARK KAUFEN und STARK VERKAUFEN")
    if not pr:
        st.info("Das Protokoll startet mit der nächsten Berechnung.")
    else:
        st.markdown(f"Seit **{pd.Timestamp(pr['start']):%d.%m.%Y}** wird jeder Wechsel auf STARK KAUFEN oder STARK VERKAUFEN mit Datum und "
                    "Schlusskurs festgehalten und nach 1 und 3 Monaten mit dem S&P 500 (Krypto: Bitcoin) verglichen. "
                    "Dieser Test kann nicht in die Zukunft schauen. **Aussagekräftig wird er nach etwa 6 Monaten und 30+ Signalen.**")
        k1, k2 = st.columns(2)
        for sp, sig in ((k1, "STARK KAUFEN"), (k2, "STARK VERKAUFEN")):
            b_ = pr["bilanz"].get(sig, {})
            with sp:
                st.markdown(f"{badge(sig, SIGNAL_FARBE[sig])} &nbsp; {b_.get('anzahl_inkl_start', 0)} Signale protokolliert", unsafe_allow_html=True)
                for k, txt in (("1M", "nach 1 Monat"), ("3M", "nach 3 Monaten")):
                    z = b_.get(k)
                    if z:
                        st.markdown(f"**{txt}** ({z['fertig']} ausgewertet): Ø {z['rendite']:+.1%} gegenüber Index {z['vergleich']:+.1%} · "
                                    f"Vorsprung {z['vorsprung']:+.1%} · richtig in {z['treffer']:.0%}")
                    else:
                        st.caption(f"{txt}: noch keine Signale alt genug")
        ein = pd.DataFrame([{"Datum": e["datum"], "Ticker": e["ticker"], "Signal": e["signal"], "Kurs damals": e["kurs"],
                             "seitdem": e.get("seit"), "Index seitdem": e.get("seit_bench"), "nach 1 Monat": e.get("r_1M"),
                             "nach 3 Monaten": e.get("r_3M"), "Start-Eintrag": "ja" if e.get("erster_lauf") else ""}
                            for e in reversed(pr.get("eintraege", []))])
        if len(ein):
            st.dataframe(ein, hide_index=True, width="stretch", height=min(38 * (len(ein) + 1), 420), column_config={
                "Kurs damals": st.column_config.NumberColumn(format="%.2f"),
                **{c_: st.column_config.NumberColumn(format="percent") for c_ in ["seitdem", "Index seitdem", "nach 1 Monat", "nach 3 Monaten"]},
                "Start-Eintrag": st.column_config.TextColumn(help="Stand beim Start des Protokolls, kein frischer Wechsel")})
            st.caption("Verkaufssignal gilt als richtig, wenn der Wert danach schlechter lief als der Index.")
    st.divider()
    if kand and kand.get("radar_test", {}).get("kennzahlen"):
        st.subheader("Portfolio-Test: jeden Monat die 5 Werte mit dem höchsten Radar-Score")
        test_anzeige(kand["radar_test"], "Radar Top 5 (monatlich)")
        st.warning("Vorsicht bei diesem Ergebnis: Die Watchlist wurde aus den **heutigen Gewinnern** zusammengestellt (Nvidia, AMD, "
                   "Palantir …). Wer unter lauter späteren Gewinnern die jeweils stärksten kauft, sieht im Rückblick zwangsläufig gut aus. "
                   "Das ist **kein Beleg**, dass das Radar künftige Gewinner findet. Fairer Vergleich: „Watchlist gleichgewichtet“.")
        st.divider()
    st.subheader("Alle Werte zusammen: Was haben die Signale früher gebracht?")
    teile = []
    for r in ergebnisse.values():
        tt = r.auswertung["tabelle"].copy()
        tt["Gruppe"] = r.gruppe
        teile.append(tt)
    gesamt = pd.concat(teile)
    rk = f"Ø Rendite {E.FORWARD_TAGE}T"
    gesamt["_t"] = gesamt["Trefferquote"] * gesamt["Anzahl Tage"]
    gesamt["_r"] = gesamt[rk] * gesamt["Anzahl Tage"]
    agg = gesamt.groupby(["Gruppe", "Signal"], sort=False)[["Anzahl Tage", "_t", "_r"]].sum()
    agg["Trefferquote"] = agg["_t"] / agg["Anzahl Tage"]
    agg[rk] = agg["_r"] / agg["Anzahl Tage"]
    agg = agg.drop(columns=["_t", "_r"]).reset_index()
    st.dataframe(agg, hide_index=True, width="stretch", column_config={
        "Trefferquote": st.column_config.NumberColumn(format="percent"),
        rk: st.column_config.NumberColumn(format="percent"),
    })
    vergleich = pd.DataFrame([{
        "Ticker": t, "Gruppe": r.gruppe,
        "Strategie p.a.": r.auswertung["cagr_strategie"], "Halten p.a.": r.auswertung["cagr_halten"],
        "Max. Rückgang Strategie": r.auswertung["mdd_strategie"], "Max. Rückgang Halten": r.auswertung["mdd_halten"],
    } for t, r in ergebnisse.items()])
    besser = (vergleich["Strategie p.a."] > vergleich["Halten p.a."]).mean()
    weniger_dd = (vergleich["Max. Rückgang Strategie"] > vergleich["Max. Rückgang Halten"]).mean()
    st.markdown(f"**Signal-Strategie schlägt Kaufen & Halten bei {besser:.0%} der Werte (Rendite) "
                f"und hat bei {weniger_dd:.0%} kleinere Rückgänge.**")
    st.dataframe(vergleich.sort_values("Strategie p.a.", ascending=False), hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="percent") for c in vergleich.columns[2:]})
    st.warning(
        "Ehrliche Einordnung: Die Watchlist enthält die heutigen Gewinner – das schönt die Vergangenheit (Survivorship-Bias). "
        "Überlappende 20-Tage-Fenster sind nicht unabhängig. Vergangene Trefferquoten garantieren nichts. "
        "Typisch für Trend-/Momentum-Systeme: ähnliche oder etwas geringere Rendite als Halten, aber deutlich kleinere Crashs."
    )

# ------------------------------------------------------------------ Anleitung
with tab_hilfe:
    st.markdown(f"""
### So nutzt du das Signal-Radar
1. **Marktampel ansehen.** Bei RISK-OFF eher keine neuen Käufe oder nur kleine Positionen.
2. **Übersicht → Beste Kaufkandidaten.** Grün = Kaufsignal. Je höher der Score, desto mehr Faktoren sprechen dafür.
3. **Detailanalyse öffnen.** Unter „Warum dieses Signal?“ steht, was dafür (▲) und dagegen (▼) spricht.
4. **Stop sofort beim Broker setzen.** Die Stückzahl ist so berechnet, dass du beim Stop höchstens {risiko:.2f} % deines Kontos verlierst.
5. **Ziel:** dort einen Teil verkaufen. Steht „überdehnt – Gewinne sichern“, Gewinne absichern.
6. **Verkaufssignal** bei einem Wert, den du hast: Position verkaufen oder Stop eng nachziehen.
7. **Einmal am Tag reicht** – am besten abends nach US-Börsenschluss (22:00 Uhr MEZ). Signale basieren auf Tagesschlusskursen.

### Was das System bewertet
| Baustein | Gewicht | Was er misst |
|---|---|---|
| Trend | 25 % | Lage zur 200-/50-Tage-Linie, Steigung |
| Momentum | 20 % | 12-Monats-Rendite ohne letzten Monat, schwankungsbereinigt |
| Relative Stärke | 15 % | 6 Monate besser/schlechter als S&P 500 bzw. Bitcoin |
| Akkumulation | 15 % | Wo im Tagesbereich schließt der Kurs bei hohem Volumen |
| Kauf-/Verkaufszone | 15 % | Anchored VWAP, Volumenprofil (POC/VAL/VAH), Liquiditäts-Sweeps, Überdehnung |
| Volatilität | 10 % | Kompression (gut) oder Stress (schlecht) |
| Marktampel | ±10 Punkte | S&P-500-Trend, VIX, Marktbreite, Kreditaufschläge (FRED); bei RISK-OFF werden Kaufsignale um 40 % abgeschwächt |
| Insiderkäufe | bis +8 | Manager kaufen eigene Aktien am offenen Markt (SEC Form 4, 90 Tage, nur US-Aktien) |
| Qualität | ±6 | Eigenkapitalrendite, Marge, Verschuldung im Vergleich zur Watchlist |
| Sektorstärke | ±6 | 3 Monate besser/schlechter als der eigene Sektor-ETF |
| Funding (Krypto) | ±8 | Überhitzte Futures (viele gehebelte Käufer) = Abzug |
| Earnings-Sperre | – | 7 Tage vor Quartalszahlen wird aus KAUFEN ein HALTEN |

Die Gewichte sind **bewusst nicht** auf historische Daten optimiert – sonst sähe der Backtest toll aus und würde live versagen.

### Grenzen
- Kostenlose Yahoo-Daten: gelegentlich Lücken oder Verzögerungen. Kein Intraday, kein echter Orderflow.
- Nachrichten und Politik fließen nicht direkt ein – nur über Kurs, Volumen, Kreditmarkt und Marktampel.
- Zusatzfaktoren werden alle 3 Stunden über GitHub Actions aktualisiert (SEC, Yahoo, Krypto-Börsen).
- **Keine Anlageberatung.** Nutze das Radar als Filter und Entscheidungshilfe, nicht als Autopilot.
""")

st.caption("Signal-Radar · Keine Anlageberatung · Vergangene Ergebnisse sind keine Garantie für die Zukunft.")
