import os, io, base64, json, math, time, datetime as dt
import requests
import pandas as pd
import plotly.graph_objects as go
import ccxt

# ========= Einstellungen =========
DAYS = int(os.getenv("DAYS", "14"))        # Zeitraum für PnL
OUTDIR = os.getenv("OUTDIR", "site")       # Ausgabeordner (für GitHub Pages)
os.makedirs(OUTDIR, exist_ok=True)

# Secrets aus GitHub Actions
API_KEY    = os.getenv("MEXC_KEY", "")
API_SECRET = os.getenv("MEXC_SECRET", "")

def now_utc():
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)

def ts_ms(d: dt.datetime) -> int:
    return int(d.timestamp() * 1000)

def make_ex(default_type="spot"):
    if not API_KEY or not API_SECRET:
        raise RuntimeError("MEXC_KEY/MEXC_SECRET fehlen (als Repository Secrets setzen).")
    e = ccxt.mexc({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "enableRateLimit": True,
        "options": {"defaultType": default_type},
        "timeout": 20000,
    })
    e.load_markets()
    return e

def fetch_all_trades_spot(ex, since_ms):
    """Spot-Trades (USDT-Quote) seit 'since_ms' paginiert über alle Symbole."""
    out = []
    symbols = [m["symbol"] for m in ex.markets.values()
               if m.get("spot") and m.get("quote") == "USDT"]
    for sym in symbols:
        cursor = since_ms
        while True:
            try:
                batch = ex.fetch_my_trades(sym, since=cursor, limit=200)
            except Exception:
                break
            if not batch:
                break
            out += batch
            last = batch[-1].get("timestamp", cursor)
            if last <= cursor:
                break
            cursor = last + 1
            if len(out) > 10000:
                break
        # optional: kurze Pause gegen Rate Limits
        if len(out) > 0:
            time.sleep(ex.rateLimit/1000)
    return out

def fetch_all_trades_swap(ex, since_ms):
    """USDT-M Perp/SWAP (linear) – über alle linearen USDT-Kontrakte."""
    out = []
    symbols = [m["symbol"] for m in ex.markets.values()
               if m.get("swap") and m.get("linear") and m.get("quote") == "USDT"]
    for sym in symbols:
        cursor = since_ms
        while True:
            try:
                batch = ex.fetch_my_trades(sym, since=cursor, limit=200)
            except Exception:
                break
            if not batch:
                break
            out += batch
            last = batch[-1].get("timestamp", cursor)
            if last <= cursor:
                break
            cursor = last + 1
            if len(out) > 10000:
                break
        if len(out) > 0:
            time.sleep(ex.rateLimit/1000)
    return out

def df_from_trades(trades):
    """Normiert ccxt-Trades in ein DataFrame + versucht Copytrade-Metadaten zu erkennen."""
    if not trades:
        return pd.DataFrame(columns=[
            "date","symbol","side","price","amount","cost","fee_cost","fee_ccy",
            "is_copy","copy_trader"
        ])
    rows=[]
    for t in trades:
        ts  = t.get("timestamp")
        dat = dt.datetime.utcfromtimestamp(ts/1000).date().isoformat() if ts else None
        fee = t.get("fee") or {}
        info = t.get("info") or {}

        # Heuristik: MEXC liefert für Copytrades je nach Segment Felder im 'info'
        # Wir scannen nach offensichtlichen Hinweisen.
        is_copy = False
        copy_trader = None
        for k in ("copy", "isCopy", "copyFlag", "strategyId", "traderId", "leaderId", "followerId", "followId"):
            if k in info:
                is_copy = True
        # Häufige Felder für Trader/Strategy IDs (wenn vorhanden)
        for k in ("traderId","leaderId","strategyId","strategyName","leaderName","traderName"):
            if k in info:
                copy_trader = str(info.get(k))
                break

        rows.append({
            "date": dat,
            "symbol": t.get("symbol"),
            "side": (t.get("side") or "").lower(),
            "price": float(t.get("price") or 0.0),
            "amount": float(t.get("amount") or 0.0),
            "cost": float(t.get("cost") or 0.0),
            "fee_cost": float((fee.get("cost") or 0.0)),
            "fee_ccy": (fee.get("currency") or "").upper(),
            "is_copy": is_copy,
            "copy_trader": copy_trader,
        })
    return pd.DataFrame(rows)

def pnl_daily(df):
    """Cashflow-PnL: Sell=+cost, Buy=-cost, USDT-Fees abziehen."""
    if df.empty:
        return pd.DataFrame(columns=["date","pnl_usdt"])
    cf=[]
    for _,r in df.iterrows():
        cash = r["cost"] if r["side"]=="sell" else -r["cost"]
        if r["fee_ccy"] in ("USDT","USD"):
            cash -= r["fee_cost"]
        cf.append({"date": r["date"], "pnl_usdt": cash})
    g = pd.DataFrame(cf).groupby("date", as_index=False).sum().sort_values("date")
    return g

def current_equity_usdt():
    """Gesamte Equity (Spot + Swap) in USDT – mit Market-Preisen bewertet."""
    total = 0.0
    details = []
    for typ in ("spot","swap"):
        try:
            ex = make_ex(typ)
            bal = ex.fetch_balance()
            totals = bal.get("total") or {}
            for ccy, qty in (totals.items()):
                q = float(qty or 0.0)
                if q == 0:
                    continue
                if ccy.upper() in ("USDT","USD","BUSD","USDC"):
                    pu = 1.0
                else:
                    pu = 0.0
                    # Versuche ccy/USDT-Preis
                    sym = f"{ccy}/USDT"
                    if sym in ex.markets:
                        try:
                            pu = float(ex.fetch_ticker(sym).get("last") or 0.0)
                        except Exception:
                            pass
                val = q * pu
                details.append({"type": typ, "asset": ccy, "qty": q, "price_usdt": pu, "value_usdt": val})
                total += val
        except Exception:
            pass
    return total, pd.DataFrame(details)

def eur_rate():
    """EUR/USDT (bzw. EUR/USD) – wir nutzen exchangerate.host als kostenlosen Dienst."""
    try:
        r = requests.get("https://api.exchangerate.host/latest?base=USD&symbols=EUR", timeout=10)
        eur = float(r.json()["rates"]["EUR"])
        return eur
    except Exception:
        return 0.92  # Fallback grob

def equity_curve(pnl_df, eq_now):
    """Equity(t) = StartEquity + CumSum(PnL); Start = eq_now - Sum(PnL)."""
    if pnl_df.empty:
        return pd.DataFrame(columns=["date","equity_usdt"])
    s = pnl_df["pnl_usdt"].sum()
    start_equity = eq_now - s if not math.isnan(eq_now) else 0.0
    out = pnl_df.copy()
    out["equity_usdt"] = start_equity + out["pnl_usdt"].cumsum()
    return out[["date","equity_usdt"]]

def roi(pnl_df, eq_now):
    """Einfacher ROI: Sum(PnL) / StartEquity."""
    if pnl_df.empty:
        return float("nan")
    total_pnl = pnl_df["pnl_usdt"].sum()
    start_equity = eq_now - total_pnl
    if start_equity <= 0:
        return float("nan")
    return 100.0 * (total_pnl / start_equity)

def write_dashboard(df_pnl, df_eq, eq_now_usdt, eurusd, copy_df):
    # Texte oben – beide Währungen vorbereiten
    eq_now_eur = eq_now_usdt * eurusd
    total_pnl = float(df_pnl["pnl_usdt"].sum()) if not df_pnl.empty else 0.0
    roi_pct = roi(df_pnl, eq_now_usdt)
    roi_txt = f"{roi_pct:.2f} %" if not (math.isnan(roi_pct)) else "–"

    # Charts (Plotly) – wir geben Daten + Layout in JS weiter (Dropdown steuert Währung)
    data = {
        "pnl": df_pnl.to_dict(orient="list"),
        "equity": df_eq.to_dict(orient="list"),
        "eq_now_usdt": eq_now_usdt,
        "eurusd": eurusd,
        "copy": copy_df.to_dict(orient="records"),
        "summary": {
            "eq_now_usdt": eq_now_usdt,
            "eq_now_eur": eq_now_eur,
            "total_pnl_usdt": total_pnl,
            "total_pnl_eur": total_pnl * eurusd,
            "roi_pct": roi_txt,
        }
    }
    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>MEXC Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,"Helvetica Neue",Arial,sans-serif;margin:24px;}}
h1,h2,h3{{margin:0 0 8px 0;}}
.card{{border:1px solid #e5e7eb;border-radius:12px;padding:16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,0.03)}}
.row{{display:flex;gap:12px;flex-wrap:wrap}}
.col{{flex:1 1 320px}}
.badge{{display:inline-block;padding:4px 8px;border-radius:999px;background:#eef2ff;color:#1e40af;font-weight:600}}
table{{border-collapse:collapse;width:100%}}
th,td{{border-bottom:1px solid #eee;padding:8px;text-align:left;font-size:14px}}
select{{padding:6px 10px;border:1px solid #ddd;border-radius:8px}}
.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono","Courier New",monospace}}
</style>
</head>
<body>
<h1>📊 MEXC Dashboard</h1>
<div class="row">
  <div class="card col">
    <div class="badge">Zusammenfassung</div>
    <div id="summary"></div>
    <div style="margin-top:8px">
      💱 Währung:
      <select id="currency">
        <option value="USDT">USDT</option>
        <option value="EUR">EUR</option>
      </select>
    </div>
  </div>
</div>

<div class="row">
  <div class="card col">
    <h3>Equity-Kurve</h3>
    <div id="equity"></div>
  </div>
  <div class="card col">
    <h3>Täglicher PnL</h3>
    <div id="pnl"></div>
  </div>
</div>

<div class="card">
  <h3>Copytrades</h3>
  <div id="copytable"></div>
  <div style="font-size:12px;color:#6b7280;margin-top:6px">
    Hinweis: Copytrades werden aus Trade-Metadaten erkannt (z. B. traderId/strategyId in der API-Antwort).
    Falls MEXC diese Felder im jeweiligen Segment nicht liefert, bleibt die Tabelle leer.
  </div>
</div>

<script>
const DATA = {json.dumps(data)};

function fmt(n, cur) {{
  if (cur==="EUR") return new Intl.NumberFormat('de-DE', {{ style:'currency', currency:'EUR' }}).format(n);
  return new Intl.NumberFormat('en-US', {{ maximumFractionDigits: 2 }}).format(n) + " USDT";
}}

function renderSummary(cur) {{
  const s = DATA.summary;
  const eq = (cur==="EUR") ? s.eq_now_eur : s.eq_now_usdt;
  const pnl = (cur==="EUR") ? s.total_pnl_eur : s.total_pnl_usdt;
  const html = `
    <div>Kontostand: <b>${{fmt(eq, cur)}}</b></div>
    <div>Summe PnL (${DAYS} Tage): <b>${{fmt(pnl, cur)}}</b></div>
    <div>ROI (einfach): <b>${{s.roi_pct}}</b></div>
  `;
  document.getElementById("summary").innerHTML = html;
}}

function renderEquity(cur) {{
  const eq = DATA.equity;
  if (!eq.date || eq.date.length===0) {{
    document.getElementById("equity").innerHTML = "<i>Keine Daten</i>"; return;
  }}
  const y = (cur==="EUR") ? eq.equity_usdt.map(v => v * DATA.eurusd) : eq.equity_usdt;
  const fig = {{
    data: [{{ x: eq.date, y: y, mode:'lines', name:'Equity' }}],
    layout: {{ margin:{{l:40,r:20,t:10,b:60}}, xaxis:{{tickangle:45}}, yaxis:{{title:cur}} }}
  }};
  Plotly.newPlot('equity', fig.data, fig.layout, {{displayModeBar:false}});
}}

function renderPnL(cur) {{
  const p = DATA.pnl;
  if (!p.date || p.date.length===0) {{
    document.getElementById("pnl").innerHTML = "<i>Keine Daten</i>"; return;
  }}
  const y = (cur==="EUR") ? p.pnl_usdt.map(v => v * DATA.eurusd) : p.pnl_usdt;
  const fig = {{
    data: [{{ x: p.date, y: y, type:'bar', name:'PnL' }}],
    layout: {{ margin:{{l:40,r:20,t:10,b:60}}, xaxis:{{tickangle:45}}, yaxis:{{title:cur}} }}
  }};
  Plotly.newPlot('pnl', fig.data, fig.layout, {{displayModeBar:false}});
}}

function renderCopy() {{
  const rows = DATA.copy || [];
  if (rows.length===0) {{
    document.getElementById("copytable").innerHTML = "<i>Keine Copytrade-Metadaten gefunden.</i>";
    return;
  }}
  // Aggregation: pro copy_trader
  const by = {{}};
  rows.forEach(r => {{
    const k = r.copy_trader || "(unbekannt)";
    if (!by[k]) by[k] = {{ count:0, pnl:0 }};
    const sign = (r.side==='sell') ? +1 : -1;
    let cash = (r.cost||0) * sign;
    if ((r.fee_ccy||'').toUpperCase()==='USDT') cash -= (r.fee_cost||0);
    by[k].count++;
    by[k].pnl += cash;
  }});
  const keys = Object.keys(by).sort((a,b)=>by[b].pnl - by[a].pnl);
  let html = "<table><thead><tr><th>Trader</th><th>Trades</th><th>PnL (USDT)</th></tr></thead><tbody>";
  keys.forEach(k => {{
    html += `<tr><td>${{k}}</td><td>${{by[k].count}}</td><td class="mono">${{by[k].pnl.toFixed(2)}}</td></tr>`;
  }});
  html += "</tbody></table>";
  document.getElementById("copytable").innerHTML = html;
}}

function renderAll() {{
  const cur = document.getElementById("currency").value;
  renderSummary(cur);
  renderEquity(cur);
  renderPnL(cur);
  renderCopy();
}}

document.getElementById("currency").addEventListener("change", renderAll);
renderAll();
</script>

</body>
</html>"""
    out_path = os.path.join(OUTDIR, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path

def main():
    since = now_utc() - dt.timedelta(days=DAYS)

    # Daten holen
    spot = make_ex("spot")
    swap = make_ex("swap")

    spot_tr = fetch_all_trades_spot(spot, ts_ms(since))
    swap_tr = fetch_all_trades_swap(swap, ts_ms(since))
    all_trades = spot_tr + swap_tr

    df = df_from_trades(all_trades)
    df_pnl = pnl_daily(df)

    eq_now, pos = current_equity_usdt()
    rate_eur = eur_rate()
    df_eq = equity_curve(df_pnl, eq_now)

    # Copy-only Ansicht vorbereiten
    df_copy = df[df["is_copy"]] if not df.empty else pd.DataFrame(columns=df.columns)

    # CSVs (optional, zum Download in Actions)
    pos.to_csv(os.path.join(OUTDIR, "positions_now.csv"), index=False)
    df.to_csv(os.path.join(OUTDIR, "trades_all.csv"), index=False)
    df_pnl.to_csv(os.path.join(OUTDIR, "daily_pnl.csv"), index=False)
    df_eq.to_csv(os.path.join(OUTDIR, "equity_curve.csv"), index=False)
    if not df_copy.empty:
        df_copy.to_csv(os.path.join(OUTDIR, "copytrades.csv"), index=False)

    # Dashboard
    page = write_dashboard(df_pnl, df_eq, eq_now, rate_eur, df_copy)
    print("OK:", page)

if __name__ == "__main__":
    main()
