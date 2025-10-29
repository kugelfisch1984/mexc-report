import os, json, time, datetime as dt
from decimal import Decimal
import ccxt

# --- helper ------------------------------------------------------

def now_iso():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def d(v):
    try:
        return float(v)
    except Exception:
        return None

def safe_total_usdt(balance):
    # Summiert alle USDT-Werte; ccxt liefert total in Währungen
    total = 0.0
    # bevorzugt 'USDT' direkt
    if 'USDT' in balance.get('total', {}):
        total += d(balance['total'].get('USDT', 0.0)) or 0.0
    # versuchsweise 'free'/'used' (falls total leer ist)
    if total == 0.0:
        for part in ('free','used','total'):
            for sym,val in balance.get(part,{}).items():
                if sym.upper()=='USDT':
                    total += d(val) or 0.0
    return round(total, 8)

def trades_to_rows(trades):
    rows = []
    for t in trades:
        rows.append({
            "date": dt.datetime.utcfromtimestamp(t.get('timestamp',0)/1000).strftime("%Y-%m-%d"),
            "symbol": t.get('symbol'),
            "side": t.get('side'),
            "amount": d(t.get('amount')),
            "price": d(t.get('price')),
            "fee": (t.get('fee') or {}).get('cost'),
            "fee_ccy": (t.get('fee') or {}).get('currency'),
            # placeholder für PnL/ROI (Spot-Trade-PnL sauber zu berechnen braucht Positions-/FIFO-Logik)
            "pnl_usdt": None,
            "roi_pct": None
        })
    return rows

def make_empty(reason):
    out = {
        "updated_at": now_iso(),
        "status": reason,
        "equity_usdt": None,
        "equity_eur": None,
        "eur_per_usdt": 0.92,  # Default – wird im Frontend überschrieben, wenn du einen Kurs eingibst
        "pnl_daily": [],       # [{date: "...", pnl_usdt: x}]
        "pnl_cum": [],         # [{date: "...", pnl_usdt: x}]
        "copytrades": []       # wir befüllen das aus normalen Trades; echte Copy-API ist proprietär
    }
    with open("data/latest.json","w",encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out

# --- main --------------------------------------------------------

API_KEY    = os.getenv("MEXC_API_KEY", "").strip()
API_SECRET = os.getenv("MEXC_API_SECRET", "").strip()

if not API_KEY or not API_SECRET:
    make_empty("no_api_keys")
    raise SystemExit(0)

# ccxt broker für MEXC Spot + Swap
spot = ccxt.mexc({"apiKey": API_KEY, "secret": API_SECRET, "enableRateLimit": True})
swap = ccxt.mexc3({"apiKey": API_KEY, "secret": API_SECRET, "enableRateLimit": True})  # futures/swap

since_days = 14
since_ms = int((dt.datetime.utcnow() - dt.timedelta(days=since_days)).timestamp()*1000)

equity_usdt = None
trades_rows = []

try:
    # 1) Equity (USDT) – zuerst Spot
    bal_spot = spot.fetch_balance()
    # versuch zusätzlich Swap (USDT-Margined)
    try:
        bal_swap = swap.fetch_balance()
    except Exception:
        bal_swap = {"total":{}}

    equity_spot = safe_total_usdt(bal_spot)
    equity_swap = safe_total_usdt(bal_swap)
    equity_usdt = round((equity_spot or 0.0) + (equity_swap or 0.0), 8)

    # 2) Trades holen (Spot)
    spot_trades = []
    try:
        spot_markets = spot.load_markets()
        symbols = [s for s in spot_markets.keys() if s.endswith("/USDT")][:30]  # Limit (Zeit)
        for sym in symbols:
            try:
                spot_trades.extend(spot.fetch_my_trades(sym, since=since_ms, limit=200))
            except Exception:
                pass
    except Exception:
        pass

    # 3) Trades holen (Swap) – ccxt liefert häufig unter 'swap' ähnliche Struktur
    swap_trades = []
    try:
        swap_markets = swap.load_markets()
        symbols = [s for s in swap_markets.keys() if "USDT" in s][:30]
        for sym in symbols:
            try:
                swap_trades.extend(swap.fetch_my_trades(sym, since=since_ms, limit=200))
            except Exception:
                pass
    except Exception:
        pass

    trades_rows = trades_to_rows(spot_trades + swap_trades)

    # 4) Grobe PnL-Reihen (ohne FIFO) -> hier nur 0en damit Charts was sehen
    #    Du bekommst täglich 0, damit der Chart funktioniert; echte PnL kannst du später verfeinern.
    days = [ (dt.datetime.utcnow()-dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in reversed(range(7)) ]
    pnl_daily = [{"date":d,"pnl_usdt":0.0} for d in days]
    pnl_cum = []
    run = 0.0
    for r in pnl_daily:
        run += r["pnl_usdt"]
        pnl_cum.append({"date": r["date"], "pnl_usdt": run})

    out = {
        "updated_at": now_iso(),
        "status": "ok",
        "equity_usdt": equity_usdt,
        "equity_eur": None,          # Frontend rechnet mit Eingabekurs um
        "eur_per_usdt": 0.92,
        "pnl_daily": pnl_daily,
        "pnl_cum": pnl_cum,
        "copytrades": trades_rows    # wir nennen sie copytrades für die Tabelle
    }

    with open("data/latest.json","w",encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

except Exception as e:
    # Fehler? -> niemals crashen, immer Datei schreiben
    make_empty(f"error: {type(e).__name__}")
