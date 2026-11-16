import io, json, os, urllib.request, zipfile
import pandas as pd
UA = os.environ.get("SEC_IDENTITY") or "Signal-Radar Bot signal-radar-bot@users.noreply.github.com"
out = {}
for host in ("www.sec.gov", "dcm.sec.gov"):
    url = f"https://{host}/files/structureddata/data/insider-transactions-data-sets/2026q1_form345.zip"
    try:
        roh = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120).read()
        z = zipfile.ZipFile(io.BytesIO(roh))
        out["host"] = host; out["bytes"] = len(roh); out["dateien"] = z.namelist()
        for n in z.namelist():
            if n.upper().endswith(".TSV") and any(k in n.upper() for k in ("SUBMISSION", "REPORTINGOWNER", "NONDERIV_TRANS")):
                df = pd.read_csv(z.open(n), sep="\t", dtype=str, nrows=2000)
                out[n] = {"spalten": list(df.columns), "zeilen": df.head(3).to_dict("records")}
                if "TRANS_CODE" in df.columns: out[n]["codes"] = df["TRANS_CODE"].value_counts().to_dict()
        break
    except Exception as ex:
        out[f"fehler_{host}"] = f"{type(ex).__name__}: {str(ex)[:150]}"
# Tagesindex testen
for url in ["https://www.sec.gov/Archives/edgar/daily-index/2026/QTR4/form.20261113.idx"]:
    try:
        t = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60).read().decode("latin-1")
        z = t.splitlines(); out["idx_zeilen"] = len(z); out["idx_kopf"] = z[:12]
        out["idx_form4"] = [x for x in z if x.startswith("4 ")][:3]; out["idx_n4"] = sum(1 for x in z if x.startswith("4 "))
    except Exception as ex:
        out["fehler_idx"] = f"{type(ex).__name__}: {str(ex)[:150]}"
os.makedirs("insider_neu", exist_ok=True)
json.dump(out, open("insider_neu/probe.json", "w"), indent=1, default=str)
print(json.dumps(out, default=str)[:3000])
