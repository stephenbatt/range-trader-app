import streamlit as st
import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime
import plotly.graph_objects as go

# ==========================================================
# Redeyebatt Range Trader Terminal
# Range + Breakout + Breakdown Hybrid
# Login screen w/ looping video background
# Finnhub (cached) + Polygon fallback
# Alpaca paper trading
# ==========================================================

# ---------- PAGE CONFIG (favicon uses your .ico file name) ----------
st.set_page_config(
    page_title="Redeyebatt Range Trader",
    page_icon="range_trader.ico",
    layout="wide"
)

# ---------- USER / PIN CONFIG ----------
USER_PINS = {
    "dad": "1234",
    "neil": "1234",
    "lucas": "1234",
    "guest": "1234",
}

# visual theme per user
USER_THEMES = {
    "dad":   {"bg": "#0d1b3d", "fg": "#ffffff", "label": "Dad"},
    "neil":  {"bg": "#1a1a1a", "fg": "#ffffff", "label": "Neil"},
    "lucas": {"bg": "#2b2f33", "fg": "#ffffff", "label": "Lucas"},
    "guest": {"bg": "#ffffff", "fg": "#000000", "label": "Guest"},
}

# ---------- READ SECRETS ----------
FINNHUB_KEY   = st.secrets.get("FINNHUB_KEY", None)
POLYGON_KEY   = st.secrets.get("POLYGON_KEY", None)  # optional fallback
ALPACA_KEY    = st.secrets.get("ALPACA_KEY", None)
ALPACA_SECRET = st.secrets.get("ALPACA_SECRET", None)
ALPACA_BASE   = st.secrets.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")

# ==========================================================
# APP STATE (Session)
# ==========================================================
def init_state():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if "user" not in st.session_state:
        st.session_state.user = None
    if "trade_log" not in st.session_state:
        st.session_state.trade_log = {
            "dad": [],
            "neil": [],
            "lucas": [],
            "guest": [],
        }
    if "auto_trade" not in st.session_state:
        st.session_state.auto_trade = {
            "dad": False,
            "neil": False,
            "lucas": False,
            "guest": False,
        }
    # cache bucket for candles so we don't spam Finnhub
    # structure: { "SPY": {"t": <epoch when fetched>, "df": <DataFrame>} }
    if "candle_cache" not in st.session_state:
        st.session_state.candle_cache = {}

def log_trade(u, msg):
    st.session_state.trade_log[u].append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": msg,
    })

# ==========================================================
# THEME / STYLING
# ==========================================================
def apply_theme(user):
    theme = USER_THEMES.get(user, USER_THEMES["guest"])
    st.markdown(
        f"""
        <style>
        .main {{
            background-color: {theme['bg']} !important;
            color: {theme['fg']} !important;
        }}
        .stMetricValue, .stMetricLabel,
        h1, h2, h3, h4, h5, h6, p, span, div, label {{
            color: {theme['fg']} !important;
        }}
        /* center login card */
        .login-card {{
            background: rgba(0,0,0,0.55);
            border-radius: 16px;
            padding: 1.5rem 2rem;
            width: 320px;
            max-width: 90%;
            color: #fff;
            margin-left: auto;
            margin-right: auto;
            box-shadow: 0 20px 60px rgba(0,0,0,0.9);
            border: 1px solid rgba(255,255,255,0.15);
        }}

        .login-title {{
            text-align: center;
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
            color: #fff !important;
        }}

        .small-caption {{
            font-size: 0.7rem;
            text-align: center;
            color: #ccc !important;
            margin-top: 0.25rem;
            margin-bottom: 1rem;
        }}

        /* put inputs tighter / smaller text */
        .login-input label p {{
            font-size: 0.8rem !important;
            color: #ddd !important;
        }}

        .login-btn div.stButton > button:first-child {{
            width: 100%;
            background: #00d26a;
            color: #000;
            border-radius: 10px;
            font-weight: 600;
            border: 0;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    return theme

# ==========================================================
# DATA: FINNHUB + OPTIONAL POLYGON FALLBACK
# ==========================================================

def fetch_finnhub_5m(symbol: str, lookback_minutes=390):
    """
    Ask Finnhub for last ~1 day's worth of 5m candles.
    Returns (df, err). We DO NOT spam Finnhub. We respect cache.
    """
    # 1. CACHE CHECK
    now_epoch = time.time()
    cache_entry = st.session_state.candle_cache.get(symbol)

    if cache_entry:
        age = now_epoch - cache_entry["t"]
        # if younger than 60 seconds, reuse
        if age < 60:
            return cache_entry["df"], None

    # 2. FINNHUB REQUEST
    if not FINNHUB_KEY:
        return None, "No FINNHUB_KEY in secrets"

    end_ts = int(now_epoch)
    start_ts = end_ts - (lookback_minutes * 60)

    url = (
        "https://finnhub.io/api/v1/stock/candle"
        f"?symbol={symbol.upper()}"
        f"&resolution=5"
        f"&from={start_ts}"
        f"&to={end_ts}"
        f"&token={FINNHUB_KEY}"
    )

    try:
        r = requests.get(url, timeout=10)
    except Exception as e:
        return None, f"Finnhub network error: {e}"

    # handle throttle / weekend
    if r.status_code == 403:
        return None, "Finnhub 403 (throttle/closed)"

    if r.status_code != 200:
        return None, f"Finnhub HTTP {r.status_code}"

    data = r.json()
    if data.get("s") != "ok":
        return None, "Finnhub no data"

    # build df
    df = pd.DataFrame({
        "t": pd.to_datetime(data["t"], unit="s"),
        "Open": data["o"],
        "High": data["h"],
        "Low": data["l"],
        "Close": data["c"],
        "Volume": data["v"],
    })

    if df.empty:
        return None, "Finnhub empty"

    # save in cache
    st.session_state.candle_cache[symbol] = {
        "t": now_epoch,
        "df": df,
    }

    return df, None

def fetch_polygon_5m(symbol: str, lookback_minutes=390):
    """
    Backup data fetch using Polygon.io free key (if provided).
    We'll pull last 1 day of 5m bars with /aggs API.
    Returns (df, err). If no POLYGON_KEY or bad, returns (None, err).
    """
    if not POLYGON_KEY:
        return None, "No POLYGON_KEY"

    # Polygon wants a date, not from/to timestamps for aggregates by minute.
    # We'll just grab today's date in YYYY-MM-DD and ask for that day.
    today = datetime.utcnow().strftime("%Y-%m-%d")
    url = (
        "https://api.polygon.io/v2/aggs/ticker/"
        f"{symbol.upper()}/range/5/minute/{today}/{today}"
        f"?adjusted=true&sort=asc&limit=5000&apiKey={POLYGON_KEY}"
    )

    try:
        r = requests.get(url, timeout=10)
    except Exception as e:
        return None, f"Polygon network error: {e}"

    if r.status_code != 200:
        return None, f"Polygon HTTP {r.status_code}"

    data = r.json()
    if data.get("status") != "OK" or "results" not in data:
        return None, "Polygon no data"

    rows = data["results"]
    if len(rows) == 0:
        return None, "Polygon empty"

    df = pd.DataFrame({
        "t": pd.to_datetime([row["t"] for row in rows], unit="ms"),
        "Open": [row["o"] for row in rows],
        "High": [row["h"] for row in rows],
        "Low":  [row["l"] for row in rows],
        "Close":[row["c"] for row in rows],
        "Volume":[row["v"] for row in rows],
    })

    if df.empty:
        return None, "Polygon empty"

    # also stick Polygon result in cache so Finnhub isn't hammered later
    st.session_state.candle_cache[symbol] = {
        "t": time.time(),
        "df": df,
    }

    return df, None

def get_intraday_5m(symbol: str):
    """
    Master fetch:
    1. Try Finnhub (cached / throttled)
    2. If Finnhub says '403 / no data', try Polygon as backup
    3. Return (df, source_name, err)
    """
    df, err = fetch_finnhub_5m(symbol)
    if df is not None:
        return df, "finnhub", None

    # Only try polygon if Finnhub failed AND we have polygon
    if err is not None and POLYGON_KEY:
        df2, err2 = fetch_polygon_5m(symbol)
        if df2 is not None:
            return df2, "polygon", None
        else:
            return None, None, f"Finnhub:{err} / Polygon:{err2}"

    return None, None, err

# ==========================================================
# RANGE / FENCE LOGIC
# ==========================================================
def calc_levels(df_5m: pd.DataFrame, atr_lookback=14, cushion_frac=0.25):
    """
    Use first ~30min of day to define opening range.
    Use last N candles to estimate ATR-ish range.
    Build high_fence / low_fence.
    """
    if df_5m is None or len(df_5m) < 6:
        return None

    first_slice = df_5m.head(6)  # ~30 minutes (6x5m)
    opening_high = first_slice["High"].max()
    opening_low = first_slice["Low"].min()

    recent_slice = df_5m.tail(atr_lookback)
    atr_est = (recent_slice["High"] - recent_slice["Low"]).mean()

    if pd.isna(atr_est) or atr_est == 0:
        atr_est = 1.0

    cushion = atr_est * cushion_frac

    high_fence = opening_high + cushion
    low_fence = opening_low - cushion

    last_price = df_5m["Close"].iloc[-1]

    return {
        "atr": float(atr_est),
        "opening_high": float(opening_high),
        "opening_low": float(opening_low),
        "high_fence": float(high_fence),
        "low_fence": float(low_fence),
        "last_price": float(last_price),
    }

def classify_mode(last_price, high_fence, low_fence):
    if last_price > high_fence:
        return "BREAKOUT"
    elif last_price < low_fence:
        return "BREAKDOWN"
    else:
        return "RANGE_HELD"

# ==========================================================
# ALPACA HELPERS (PAPER)
# ==========================================================
def alpaca_account():
    if not (ALPACA_KEY and ALPACA_SECRET and ALPACA_BASE):
        return None, "Missing Alpaca creds"
    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }
    try:
        r = requests.get(f"{ALPACA_BASE}/account", headers=headers, timeout=10)
    except Exception as e:
        return None, f"Alpaca net error: {e}"

    if r.status_code != 200:
        return None, f"{r.status_code}: {r.text}"
    return r.json(), None

def alpaca_market_order(symbol, qty, side):
    if not (ALPACA_KEY and ALPACA_SECRET and ALPACA_BASE):
        return None, "Missing Alpaca creds"
    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json",
    }
    body = {
        "symbol": symbol.upper(),
        "qty": str(int(qty)),
        "side": side,  # "buy" or "sell"
        "type": "market",
        "time_in_force": "day",
    }
    try:
        r = requests.post(f"{ALPACA_BASE}/orders", headers=headers, json=body, timeout=10)
    except Exception as e:
        return None, f"Order net error: {e}"
    return r, None

# ==========================================================
# BEGINNER HELPER SCAN
# ==========================================================
def beginner_scan():
    watchlist = ["SPY", "AAPL", "NVDA", "TSLA", "AMZN"]
    rows = []

    for sym in watchlist:
        df_sym, _, err = get_intraday_5m(sym)
        if df_sym is None or err:
            continue
        stats = calc_levels(df_sym)
        if not stats:
            continue

        mode = classify_mode(
            stats["last_price"],
            stats["high_fence"],
            stats["low_fence"],
        )

        rows.append({
            "Symbol": sym,
            "Last": round(stats["last_price"], 2),
            "ATR-ish": round(stats["atr"], 2),
            "Mode": mode,
        })

    priority = {"BREAKOUT": 0, "BREAKDOWN": 1, "RANGE_HELD": 2}
    rows.sort(key=lambda r: priority.get(r["Mode"], 99))

    if len(rows) == 0:
        return pd.DataFrame([{
            "Symbol": "N/A",
            "Last": "",
            "ATR-ish": "",
            "Mode": "No data",
        }])

    return pd.DataFrame(rows)

# ==========================================================
# LOGIN SCREEN (VIDEO BG)
# ==========================================================
def show_login():
    # Custom CSS background (gradient, animated pulse style),
    # and a glass card for the login box.
    st.markdown(
        """
        <style>
        body, .stApp {
            background: radial-gradient(circle at 20% 20%, #001428 0%, #000000 70%);
            background-size: 200% 200%;
            animation: bgmove 6s ease-in-out infinite alternate;
        }
        @keyframes bgmove {
            0%   { background-position: 0% 0%; }
            100% { background-position: 100% 100%; }
        }
        .login-wrapper {
            display: flex;
            width: 100%;
            min-height: 100vh;
            align-items: center;
            justify-content: center;
        }
        .login-card {
            width: 320px;
            border-radius: 20px;
            padding: 1.5rem 1.25rem 1rem 1.25rem;
            background: rgba(15, 25, 45, 0.6);
            backdrop-filter: blur(12px);
            box-shadow: 0 24px 60px rgba(0,0,0,0.8);
            border: 1px solid rgba(255,255,255,0.08);
            color: #fff;
            text-align: center;
        }
        .login-logo {
            width: 72px;
            height: 72px;
            border-radius: 12px;
            border: 2px solid rgba(255,255,255,0.25);
            box-shadow: 0 10px 30px rgba(0,0,0,0.8);
            object-fit: contain;
            margin-bottom: 0.5rem;
            background-color: #0f1a2d;
        }
        .login-title {
            color: #fff;
            font-size: 0.95rem;
            font-weight: 600;
            line-height: 1.4;
            margin-bottom: 1rem;
        }
        .small-hint {
            color: #6d7a9c;
            font-size: 0.7rem;
            line-height: 1.2;
            margin-top: 0.25rem;
            margin-bottom: 0.5rem;
        }
        .stTextInput input {
            text-align: center;
        }
        </style>

        <div class="login-wrapper">
            <div class="login-card">
                <img class="login-logo" src="data:image/x-icon;base64,REPLACE_BASE64_LOGO_HERE" />
                <div class="login-title">
                    🔐 Redeyebatt Range Trader<br/>
                    <span style="color:#6d7a9c; font-weight:400;">Breakout · Breakdown · Range Defense</span>
                </div>
        """,
        unsafe_allow_html=True,
    )

    username = st.text_input("Username", key="user_input").strip().lower()
    st.markdown('<div class="small-hint">dad / neil / lucas / guest</div>', unsafe_allow_html=True)
    pin = st.text_input("PIN", type="password", key="pin_input")
    st.markdown('<div class="small-hint">Your private code</div>', unsafe_allow_html=True)

    login_btn = st.button("Log In", use_container_width=True)

    st.markdown("</div></div>", unsafe_allow_html=True)

    if login_btn:
        if username in USER_PINS and pin == USER_PINS[username]:
            st.session_state.logged_in = True
            st.session_state.user = username
            st.rerun()
        else:
            st.error("Invalid login")

# ==========================================================
# DASHBOARD (AFTER LOGIN)
# ==========================================================
def show_dashboard():
    user = st.session_state.user
    theme = apply_user_theme(user)

    st.markdown(f"### Welcome, {theme['label']}")

    # 🧩 Layout Fix: center dashboard and tighten metrics/tables
    st.markdown("""
    <style>
    /* shrink and center the main container */
    section[data-testid="stSidebar"] {width: 280px !important;}
    div.block-container {
        max-width: 900px;  /* keep content from stretching */
        margin: auto;
        padding-top: 1.5rem;
        padding-bottom: 1.5rem;
    }

    /* tighten metric boxes */
    [data-testid="stMetricValue"] {
        font-size: 1.2rem;
        font-weight: 600;
    }

    /* keep tables compact */
    [data-testid="stDataFrame"] {
        font-size: 0.9rem !important;
    }
    </style>
    """, unsafe_allow_html=True)

    # SIDEBAR CONTROLS
    st.sidebar.header("Session Controls")

    symbol = st.sidebar.text_input("Ticker Symbol", "SPY").upper()
    qty = st.sidebar.number_input("Trade Quantity", min_value=1, value=1)

    auto_flag = st.sidebar.checkbox(
        "Auto Trade (paper) breakout/breakdown",
        value=st.session_state.auto_trade[user],
        help="If ON: breakout will BUY, breakdown will SELL using Alpaca paper.",
    )
    st.session_state.auto_trade[user] = auto_flag

    st.sidebar.markdown("---")

    # LIVE DATA
    df_live, source_name, data_err = get_intraday_5m(symbol)
    stats = calc_levels(df_live) if df_live is not None else None

    acct, acct_err = alpaca_account()

    # ACCOUNT / CONNECTION
    colA, colB, colC = st.columns(3)
    if acct and not acct_err:
        colA.metric("Buying Power", f"${float(acct.get('buying_power', 0)):,.2f}")
        colB.metric("Cash", f"${float(acct.get('cash', 0)):,.2f}")
        colC.success("Alpaca Connected ✅")
    else:
        colA.write("Alpaca not connected")
        colB.write(acct_err if acct_err else "")
        colC.write("")

    # RANGE / MODE
    if stats:
        mode = classify_mode(
            stats["last_price"],
            stats["high_fence"],
            stats["low_fence"],
        )

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("ATR-ish", f"{stats['atr']:.2f}")
        m2.metric("High Fence", f"{stats['high_fence']:.2f}")
        m3.metric("Low Fence", f"{stats['low_fence']:.2f}")
        m4.metric("Last Price", f"{stats['last_price']:.2f}")
        m5.metric("Data Source", source_name if source_name else "—")

        st.write(f"**Market Mode:** {mode}")

        # AUTO TRADE
        if auto_flag and acct and not acct_err:
            if mode == "BREAKOUT":
                r, err = alpaca_market_order(symbol, qty, "buy")
                if r is not None and r.status_code in (200, 201):
                    st.success("AUTO BUY sent ✅")
                    log_trade(user, f"AUTO BUY {qty} {symbol} (BREAKOUT)")
                else:
                    st.error(f"AUTO BUY failed: {err if err else r.text if r is not None else 'no response'}")

            elif mode == "BREAKDOWN":
                r, err = alpaca_market_order(symbol, qty, "sell")
                if r is not None and r.status_code in (200, 201):
                    st.warning("AUTO SELL sent ⛔")
                    log_trade(user, f"AUTO SELL {qty} {symbol} (BREAKDOWN)")
                else:
                    st.error(f"AUTO SELL failed: {err if err else r.text if r is not None else 'no response'}")

            else:
                st.info("AUTO: HOLD / RANGE")

        # CHART
        if df_live is not None and len(df_live) > 0:
            fig = go.Figure()
            fig.add_trace(
                go.Candlestick(
                    x=df_live["t"],
                    open=df_live["Open"],
                    high=df_live["High"],
                    low=df_live["Low"],
                    close=df_live["Close"],
                    name=symbol,
                )
            )
            fig.add_hline(
                y=stats["high_fence"],
                line_color="green",
                line_dash="dash",
                annotation_text="High Fence",
            )
            fig.add_hline(
                y=stats["low_fence"],
                line_color="red",
                line_dash="dash",
                annotation_text="Low Fence",
            )
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.error(f"Data feed unavailable: {data_err if data_err else 'No data'}")

    # MANUAL TRADING
    st.subheader("Trade Controls (Paper)")
    col_buy, col_sell = st.columns(2)
    if col_buy.button("BUY Market (Paper)"):
        r, err = alpaca_market_order(symbol, qty, "buy")
        if r is not None and r.status_code in (200, 201):
            col_buy.success("Buy sent ✅")
            log_trade(user, f"MANUAL BUY {qty} {symbol}")
        else:
            col_buy.error(f"Buy failed: {err if err else r.text if r is not None else 'no response'}")

    if col_sell.button("SELL Market (Paper)"):
        r, err = alpaca_market_order(symbol, qty, "sell")
        if r is not None and r.status_code in (200, 201):
            col_sell.success("Sell sent ✅")
            log_trade(user, f"MANUAL SELL {qty} {symbol}")
        else:
            col_sell.error(f"Sell failed: {err if err else r.text if r is not None else 'no response'}")

    # TRADE LOG
    st.subheader("Session Log")
    if len(st.session_state.trade_log[user]) == 0:
        st.write("No trades yet.")
    else:
        st.dataframe(pd.DataFrame(st.session_state.trade_log[user]), use_container_width=True)

    # BEGINNER HELPER
    st.subheader("🔍 Beginner Helper: Stocks to Watch")
    st.caption("BREAKOUT = chasing up. BREAKDOWN = short bias. RANGE_HELD = chop/collect.")
    if st.button("Scan Watchlist"):
        recs = beginner_scan()
        st.dataframe(recs, use_container_width=True)
    st.caption("Goal: stop gambling. Only touch tickers that are actually moving.")
# ==========================================================
# MAIN
# ==========================================================
def main():
    init_state()
    if not st.session_state.logged_in:
        show_login()
    else:
        show_dashboard()

if __name__ == "__main__":
    main()






