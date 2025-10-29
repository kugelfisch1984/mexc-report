
import os, time, json, datetime as dt
import pandas as pd
import ccxt

OUT = 'docs/data'
os.makedirs(OUT, exist_ok=True)

api_key    = os.environ.get('MEXC_API_KEY')
api_secret = os.environ.get('MEXC_API_SECRET')

ex = ccxt.mexc({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
})

# Zeitraum
days = 7
since_ms = int((dt.datetime.utcnow() - dt.timedelta(days=days)).timestamp()*1000)

# Balance
bal = ex.fetch_balance()
usdt_total = float(bal['total'].get('USDT', 0.0))
usdt_free  = float(bal['free'].get('USDT', 0.0))

# Trades (Spot + Swap wenn verfügbar)
trades = []
for market_type in ['spot', 'swap']:
    try:
        ex.options['defaultType'] = market_type
        # hole paar beliebte Symbole; ccxt hat kein „alle“ Shortcut
        symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
        for sym in symbols:
            if sym in ex.load_markets():
                ts = since_ms
                while True:
                    t = ex.fetch_my_trades(sym, since=ts, limit=200)
                    if not t:
                        break
                    trades.extend(t)
                    ts = t[-1]['timestamp'] + 1
                    if len(t) < 200:
                        break
    except Exception:
        pass

# PnL grob: Summe (sell - buy - fees) in USDT je Tag
rows = []
for t in trades:
    side = (t.get('side') or '').lower()
    cost = float(t.get('cost') or 0.0)
    fee  = 0.0
    f = t.get('fee') or {}
    try:
        fee = float(f.get('cost') or 0.0) if (f.get('currency','').upper() in ['USDT','USD','']) else 0.0
    except Exception:
        pass
    sign = +1 if side == 'sell' else -1
    d = dt.datetime.utcfromtimestamp(t['timestamp']/1000).date().isoformat()
    rows.append({'date': d, 'pnl_usdt': sign*cost - fee})

df = pd.DataFrame(rows)
pnl_daily = (
    df.groupby('date')['pnl_usdt'].sum().reset_index().sort_values('date')
    if not df.empty else pd.DataFrame(columns=['date','pnl_usdt'])
)

# ROI (sehr grob) = Summe PnL / (aktuelles USDT als Proxy)
roi = float(pnl_daily['pnl_usdt'].sum()) / usdt_total if usdt_total > 0 else 0.0

# Copytrades – falls du echte Copytrading-API hast, hier ersetzen:
copytrades_tbl = []
for t in trades:
    copytrades_tbl.append({
        'date': dt.datetime.utcfromtimestamp(t['timestamp']/1000).isoformat(),
        'symbol': t.get('symbol'),
        'side': t.get('side'),
        'amount': t.get('amount'),
        'price': t.get('price'),
        'fee': (t.get('fee') or {}).get('cost', 0),
        'pnl_usdt': 0  # optional verfeinern
    })

out = {
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "equity_usdt": float(equity_usdt) if 'equity_usdt' in locals() else 0.0,
    "equity_eur": float(equity_usdt)*0.92 if 'equity_usdt' in locals() else 0.0,  # Beispielkurs
    "pnl_daily": pnl_daily if 'pnl_daily' in locals() else [],
    "pnl_cum": pnl_cum if 'pnl_cum' in locals() else [],
    "copytrades": copytrades if 'copytrades' in locals() else []
}

os.makedirs("docs/data", exist_ok=True)
with open("docs/data/latest.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
print("Wrote docs/data/latest.json")
