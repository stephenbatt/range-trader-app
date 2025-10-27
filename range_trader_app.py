import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import plotly.graph_objects as go

# =========================
# CONFIG / PAGE
# =========================
st.set_page_config(page_title="Redeyebatt Range Trader Terminal", layout="wide")

# Themes per user
USER_THEMES = {
    "dad": {"bg": "#0d1b3d", "fg": "#ffffff", "name": "Dad"},
    "neil": {"bg": "#1a1a1a", "fg": "#ffffff", "name": "Neil"},
    "lucas": {"bg": "#2b2f33", "fg": "#ffffff", "name": "Lucas"},
    "guest": {"bg": "#ffffff", "fg": "#000000", "name": "Guest"},
}

# Default PINs (you can change these any time)
USER_PINS = {
    "dad": "1111",
    "neil": "2222",
    "lucas": "3333",
    "guest": "0000",
}

# read secrets
FINNHUB_KEY = st.secrets.get("API_KEY", None)
ALPACA_KEY = st.secrets.get("ALPACA_KEY", None)
ALPACA_SECRET = st.secrets.get("ALPACA_SECRET", None)
ALPACA_BASE_URL = st.secrets.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# =========================
# HELPERS
# =========================

def set_style(user):
    """Apply background/text color based on who is logged in."""
    theme = USER_THEMES.get(user, USER_THEMES["guest"])
    st.markdown(
        f"""
        <style>
        .main {{
            background-color: {theme['bg']} !important;
            color: {theme['fg']} !important;
        }}
        div[data-testid="stMarkdown"] h1, 
        div[data-testid="stMarkdown"] h2, 
        div[data-testid="stMarkdown"] h3,
        div[data-testid="stMetricValue"] {{
            color: {theme['fg']} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    return theme

def get_finnhub_candles(symbol: str, resolution="5", lookback_minutes=390):
    """
    Pull recent candles from Finnhub.
    resolution "5" = 5 min bars
    lookback ~390 min = 1 trading day
    Returns dataframe with columns: t, o, h, l, c, v
    """
    if not FINNHUB_KEY:
        return None, "No FINNHUB_KEY in secrets"
    now = int(datetime.now().timestamp())
    frm = now - (lookback_minutes * 60)

    url = (
        f"https://finnhub.io/api/v1/stock/candle"
        f"?symbol={symbol.upper()}"
        f"&resolution={resolution}"
        f"&from={frm}"
        f"&to={now}"
        f"&token={FINNHUB_KEY}"
    )
    r = requests.get(url)
    if r.status_code != 200:
        return None, f"Finnhub error {r.status_code}"
    data = r.json()
    if data.get("s") != "ok":
        return None, "No candle data"
    df = pd.DataFrame({
        "t": pd.to_datetime(data["t"], unit="s"),
        "Open": data["o"],
        "High": data["h"],
        "Low": data["l"],
        "Close": data["c"],
        "Volume": data["v"],
    })
    return df, None

def calc_range_levels(df_5m: pd.DataFrame, atr_lookback=14, cushion_frac=0.25):
    """
    Calculate ATR-like volatility, top fence, bottom fence.
    We'll approximate ATR using High-Low average over N bars.
    """
    if df_5m is None or len(df_5m) == 0:
        return None

    # We'll group last ~X bars to simulate "opening range"
    # Use first ~6 bars (~30 min if 5min candles)
    first_slice = df_5m.head(6)
    opening_high = first_slice["High"].max()
    opening_low = first_slice["Low"].min()

    # ATR-style estimate: mean(high-low) over last atr_lookback bars
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

def breakout_status(price, high_fence, low_fence):
    """
    Figure out if we are in range, breakout, or breakdown.
    """
    if price > high_fence:
        return "BREAKOUT"
    elif price < low_fence:
        return "BREAKDOWN"
    else:
        return "RANGE_HELD"

def get_alpaca_account():
    """Get Alpaca account info (paper trading)."""
    if not (ALPACA_KEY and ALPACA_SECRET and ALPACA_BASE_URL):
        return None, "Missing Alpaca creds in secrets"
    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }
    r = requests.get(f"{ALPACA_BASE_URL}/v2/account", headers=headers)
    if r.status_code != 200:
        return None, f"Alpaca account error {r.status_code}: {r.text}"
    return r.json(), None

def place_alpaca_order(symbol, qty, side):
    """Market order through Alpaca paper account."""
    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json",
    }
    body = {
        "symbol": symbol.upper(),
        "qty": str(int(qty)),
        "side": side,              # "buy" or "sell"
        "type": "market",
        "time_in_force": "day"
    }
    r = requests.post(f"{ALPACA_BASE_URL}/v2/orders", headers=headers, json=body)
    return r

def suggest_stocks_watchlist():
    """
    Beginner helper:
    We scan a small watchlist of common movers
    and rank them by how 'interesting' they are
    using same breakout/breakdown logic.
    """
    watchlist = ["SPY", "TSLA", "NVDA", "AAPL", "AMD", "QQQ", "IWM"]
    recs = []
    for sym in watchlist:
        df, err = get_finnhub_candles(sym)
        if err or df is None or len(df) < 10:
            continue
        stats = calc_range_levels(df)
        if not stats:
            continue
        mode = breakout_status(stats["last_price"], stats["high_fence"], stats["low_fence"])
        recs.append({
            "symbol": sym,
            "price": stats["last_price"],
            "atr": stats["atr"],
            "mode": mode,
        })
    # sort: show most aggressive plays first (breakout, breakdown, then range)
    priority = {"BREAKOUT": 0, "BREAKDOWN": 1, "RANGE_HELD": 2}
    recs.sort(key=lambda r: priority.get(r["mode"], 99))
    return recs

def init_user_state():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if "user" not in st.session_state:
        st.session_state.user = None
    if "trade_log" not in st.session_state:
        # per-user logs:
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

def log_trade(user, message, pl=None):
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": message,
    }
    if pl is not None:
        entry["pl"] = pl
    st.session_state.trade_log[user].append(entry)

# =========================
# LOGIN SCREEN
# =========================
def show_login():
    st.title("🔐 Redeyebatt Trading Terminal Login")

    col1, col2 = st.columns(2)
    with col1:
        username = st.text_input("User (dad / neil / lucas / guest)").strip().lower()
    with col2:
        pin = st.text_input("PIN", type="password")

    login_btn = st.button("Log In")

    if login_btn:
        if username in USER_PINS and pin == USER_PINS[username]:
            st.session_state.logged_in = True
            st.session_state.user = username
            st.success(f"Welcome, {USER_THEMES[username]['name']} ✅")
            st.rerun() 
        else:
            st.error("Invalid login")

# =========================
# DASHBOARD
# =========================
def show_dashboard():
    user = st.session_state.user
    theme = set_style(user)

    st.markdown(f"### Welcome, {theme['name']}")

    # ==== Sidebar controls ====
    st.sidebar.header("Session Controls")
    symbol = st.sidebar.text_input("Ticker Symbol", "SPY").upper()
    share_qty = st.sidebar.number_input("Trade Quantity", min_value=1, value=1)
    st.sidebar.markdown("---")

    # Auto-trade toggle
    auto = st.sidebar.checkbox(
        "Auto-Trade Breakout/Breakdown",
        value=st.session_state.auto_trade[user],
        help="If ON, will auto-send BUY on breakout and SELL on breakdown (paper)."
    )
    st.session_state.auto_trade[user] = auto

    # Place manual trades
    buy_btn = st.sidebar.button("BUY Market (Paper)")
    sell_btn = st.sidebar.button("SELL Market (Paper)")

    # ==== Pull data ====
    df_live, fin_err = get_finnhub_candles(symbol)
    stats = calc_range_levels(df_live) if not fin_err else None
    acct, acct_err = get_alpaca_account()

    # ==== Account row ====
    colA, colB, colC = st.columns(3)
    if acct and not acct_err:
        colA.metric("Buying Power", f"${float(acct['buying_power']):,.2f}")
        colB.metric("Equity", f"${float(acct['equity']):,.2f}")
        colC.metric("Cash", f"${float(acct['cash']):,.2f}")
    else:
        colA.write("Alpaca not connected")
        colB.write(acct_err if acct_err else "")
        colC.write("")

    # ==== Range/Breakout row ====
    if stats:
        mode = breakout_status(stats["last_price"], stats["high_fence"], stats["low_fence"])

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("ATR", f"{stats['atr']:.2f}")
        col2.metric("High Fence", f"{stats['high_fence']:.2f}")
        col3.metric("Low Fence", f"{stats['low_fence']:.2f}")
        col4.metric("Last Price", f"{stats['last_price']:.2f}")

        st.markdown(f"**Market Mode:** {mode}")

        if mode == "BREAKOUT":
            st.info("Suggested Action: LONG / BUY")
            if auto:
                # auto-buy on breakout
                r = place_alpaca_order(symbol, share_qty, "buy")
                if r.status_code == 200 or r.status_code == 201:
                    log_trade(user, f"AUTO BUY {share_qty} {symbol} (BREAKOUT)")
                    st.success("Auto BUY sent")
                else:
                    st.error(f"Auto BUY failed: {r.text}")
        elif mode == "BREAKDOWN":
            st.warning("Suggested Action: SHORT / SELL")
            if auto:
                # auto-sell on breakdown
                r = place_alpaca_order(symbol, share_qty, "sell")
                if r.status_code == 200 or r.status_code == 201:
                    log_trade(user, f"AUTO SELL {share_qty} {symbol} (BREAKDOWN)")
                    st.success("Auto SELL sent")
                else:
                    st.error(f"Auto SELL failed: {r.text}")
        else:
            st.write("Suggested Action: HOLD / COLLECT RANGE")

        # ==== Chart ====
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
        st.error(f"Data error: {fin_err if fin_err else 'No stats'}")

    # ==== Manual trade buttons (paper) ====
    st.subheader("Trade Controls (Paper)")
    col_buy, col_sell = st.columns(2)
    if buy_btn:
        r = place_alpaca_order(symbol, share_qty, "buy")
        if r.status_code == 200 or r.status_code == 201:
            log_trade(user, f"MANUAL BUY {share_qty} {symbol}")
            col_buy.success("Buy order sent ✅")
        else:
            col_buy.error(f"Buy failed: {r.text}")
    if sell_btn:
        r = place_alpaca_order(symbol, share_qty, "sell")
        if r.status_code == 200 or r.status_code == 201:
            log_trade(user, f"MANUAL SELL {share_qty} {symbol}")
            col_sell.success("Sell order sent ✅")
        else:
            col_sell.error(f"Sell failed: {r.text}")

    # ==== Trade log for this user ====
    st.subheader("Session Log")
    if len(st.session_state.trade_log[user]) == 0:
        st.write("No trades yet.")
    else:
        log_df = pd.DataFrame(st.session_state.trade_log[user])
        st.dataframe(log_df, use_container_width=True)

    # ==== Beginner Helper ====
    st.subheader("🔍 Beginner Helper: Stocks to Watch")
    st.caption("These are names showing action (breakout, breakdown, or building a range).")
    if st.button("Scan Watchlist"):
        recs = suggest_stocks_watchlist()
        if not recs:
            st.write("No symbols to suggest right now.")
        else:
            rec_df = pd.DataFrame(recs)
            # nicer order
            rec_df = rec_df[["symbol", "price", "mode", "atr"]]
            st.dataframe(rec_df, use_container_width=True)

    st.caption("Mode meanings: BREAKOUT = bullish run, BREAKDOWN = bearish flush, RANGE_HELD = chop/collect premium.")

# =========================
# MAIN
# =========================
def main():
    init_user_state()
    if not st.session_state.logged_in:
        show_login()
    else:
        show_dashboard()

if __name__ == "__main__":
    main()


