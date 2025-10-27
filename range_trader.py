import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import plotly.graph_objects as go

# ==========================================================
# CONFIG
# ==========================================================
st.set_page_config(page_title="Redeyebatt Range Trader", layout="wide")

# --- USER SYSTEM (no hints on screen) ---
USER_PINS = {
    "dad": "1111",
    "neil": "2222",
    "lucas": "3333",
    "guest": "0000",
}

USER_THEMES = {
    "dad":   {"bg": "#0d1b3d", "fg": "#ffffff", "label": "Dad"},
    "neil":  {"bg": "#1a1a1a", "fg": "#ffffff", "label": "Neil"},
    "lucas": {"bg": "#2b2f33", "fg": "#ffffff", "label": "Lucas"},
    "guest": {"bg": "#ffffff", "fg": "#000000", "label": "Guest"},
}

# secrets (from Streamlit Cloud)
FINNHUB_KEY = st.secrets.get("FINNHUB_KEY", None)
ALPACA_KEY = st.secrets.get("ALPACA_KEY", None)
ALPACA_SECRET = st.secrets.get("ALPACA_SECRET", None)
ALPACA_BASE_URL = st.secrets.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def apply_user_theme(user):
    """Changes background/text color so each login has its own vibe."""
    theme = USER_THEMES.get(user, USER_THEMES["guest"])
    st.markdown(
        f"""
        <style>
        .main {{
            background-color: {theme['bg']} !important;
            color: {theme['fg']} !important;
        }}
        .stMetricValue, .stMetricLabel, h1, h2, h3, h4, h5, h6, p, span {{
            color: {theme['fg']} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True
    )
    return theme

def get_finnhub_candles(symbol: str, resolution="5", lookback_minutes=390):
    """
    Pull recent intraday candles (default ~1 trading day of 5-min bars).
    Returns df with t, Open, High, Low, Close, Volume.
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
        return None, f"Finnhub HTTP {r.status_code}"
    data = r.json()
    if data.get("s") != "ok":
        return None, "Finnhub returned no data"
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
    Take 5-minute bars, estimate:
    - opening range high/low (first ~30 min)
    - ATR-ish volatility
    - high fence / low fence
    - last price
    """
    if df_5m is None or len(df_5m) < 6:
        return None

    first_slice = df_5m.head(6)  # ~ first 30 min of session
    opening_high = first_slice["High"].max()
    opening_low = first_slice["Low"].min()

    recent_slice = df_5m.tail(atr_lookback)
    atr_est = (recent_slice["High"] - recent_slice["Low"]).mean()
    if pd.isna(atr_est) or atr_est == 0:
        atr_est = 1.0  # safety fallback so we don't divide by 0

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

def classify_mode(price, high_fence, low_fence):
    """
    Tells us if market is:
    - BREAKOUT: bullish
    - BREAKDOWN: bearish
    - RANGE_HELD: chop/collect
    """
    if price > high_fence:
        return "BREAKOUT"
    elif price < low_fence:
        return "BREAKDOWN"
    else:
        return "RANGE_HELD"

def get_alpaca_account():
    """Get account info from Alpaca paper trading."""
    if not (ALPACA_KEY and ALPACA_SECRET and ALPACA_BASE_URL):
        return None, "Alpaca creds missing"
    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }
    r = requests.get(f"{ALPACA_BASE_URL}/account", headers=headers)
    if r.status_code != 200:
        return None, f"Alpaca {r.status_code}: {r.text}"
    return r.json(), None

def place_alpaca_order(symbol, qty, side):
    """
    Send market order to Alpaca paper.
    side = 'buy' or 'sell'
    """
    if not (ALPACA_KEY and ALPACA_SECRET and ALPACA_BASE_URL):
        return None, "Missing Alpaca creds"
    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json",
    }
    body = {
        "symbol": symbol.upper(),
        "qty": str(int(qty)),
        "side": side,
        "type": "market",
        "time_in_force": "day",
    }
    r = requests.post(f"{ALPACA_BASE_URL}/orders", headers=headers, json=body)
    return r, None

def beginner_scan():
    """
    Beginner helper:
    We scan high-volume tickers most new traders stare at.
    Goal: tell you where 'the fight' is happening.
    """
    watch = ["SPY", "NVDA", "AAPL", "TSLA", "AMZN", "AMD", "QQQ"]
    out_rows = []
    for sym in watch:
        df, err = get_finnhub_candles(sym)
        if err or df is None or len(df) < 6:
            continue
        stats = calc_range_levels(df)
        if not stats:
            continue
        mode = classify_mode(
            stats["last_price"],
            stats["high_fence"],
            stats["low_fence"],
        )
        out_rows.append({
            "Symbol": sym,
            "Last": round(stats["last_price"], 2),
            "ATR": round(stats["atr"], 2),
            "Mode": mode,
        })
    # sort by danger/opportunity:
    # breakout first, breakdown second, range last
    priority = {"BREAKOUT": 0, "BREAKDOWN": 1, "RANGE_HELD": 2}
    out_rows.sort(key=lambda row: priority.get(row["Mode"], 99))
    return pd.DataFrame(out_rows)

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

def log_trade(u, msg):
    st.session_state.trade_log[u].append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": msg,
    })

# ==========================================================
# LOGIN SCREEN
# ==========================================================
def show_login():
    st.title("🔐 Login")

    col1, col2 = st.columns(2)
    with col1:
        username = st.text_input("Username").strip().lower()
    with col2:
        pin = st.text_input("PIN", type="password")

    login_btn = st.button("Enter")

    if login_btn:
        if username in USER_PINS and pin == USER_PINS[username]:
            st.session_state.logged_in = True
            st.session_state.user = username
            # Instant jump to dashboard
            st.rerun()
        else:
            st.error("Access denied")

# ==========================================================
# DASHBOARD (AFTER LOGIN)
# ==========================================================
def show_dashboard():
    user = st.session_state.user
    theme = apply_user_theme(user)

    st.markdown(f"### Welcome, {theme['label']}")

    # ------------- SIDEBAR CONTROLS -------------
    st.sidebar.header("Session Controls")
    symbol = st.sidebar.text_input("Ticker Symbol", "SPY").upper()
    qty = st.sidebar.number_input("Trade Quantity", min_value=1, value=1)
    auto_flag = st.sidebar.checkbox(
        "Auto Trade (breakout buys / breakdown sells)",
        value=st.session_state.auto_trade[user],
        help="If on: breakout -> BUY, breakdown -> SELL (paper)."
    )
    st.session_state.auto_trade[user] = auto_flag
    st.sidebar.markdown("---")

    # ------------- GET DATA -------------
    df_live, data_err = get_finnhub_candles(symbol)
    stats = calc_range_levels(df_live) if not data_err else None
    acct, acct_err = get_alpaca_account()

    # ------------- SHOW ACCOUNT INFO -------------
    colA, colB, colC = st.columns(3)
    if acct and not acct_err:
        try:
            colA.metric("Buying Power", f"${float(acct['buying_power']):,.2f}")
            colB.metric("Equity", f"${float(acct['equity']):,.2f}")
            colC.metric("Cash", f"${float(acct['cash']):,.2f}")
        except Exception:
            colA.write("Alpaca connected")
            colB.write("Account read ok")
            colC.write("")
    else:
        colA.write("Alpaca not connected")
        colB.write(acct_err if acct_err else "")
        colC.write("")

    # ------------- SHOW RANGE / MODE / CHART -------------
    if stats:
        mode = classify_mode(
            stats["last_price"],
            stats["high_fence"],
            stats["low_fence"],
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ATR", f"{stats['atr']:.2f}")
        c2.metric("High Fence", f"{stats['high_fence']:.2f}")
        c3.metric("Low Fence", f"{stats['low_fence']:.2f}")
        c4.metric("Last Price", f"{stats['last_price']:.2f}")

        st.write(f"**Market Mode:** {mode}")

        # Auto-trade logic
        if auto_flag:
            if mode == "BREAKOUT":
                resp, _ = place_alpaca_order(symbol, qty, "buy")
                if resp is not None and resp.status_code in (200, 201):
                    st.success("AUTO BUY sent ✅")
                    log_trade(user, f"AUTO BUY {qty} {symbol} (BREAKOUT)")
                else:
                    st.error("AUTO BUY failed / Alpaca auth?")
            elif mode == "BREAKDOWN":
                resp, _ = place_alpaca_order(symbol, qty, "sell")
                if resp is not None and resp.status_code in (200, 201):
                    st.warning("AUTO SELL sent ⛔")
                    log_trade(user, f"AUTO SELL {qty} {symbol} (BREAKDOWN)")
                else:
                    st.error("AUTO SELL failed / Alpaca auth?")
            else:
                st.info("AUTO: HOLD / RANGE")

        # Chart
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
        # graceful fallback if data feed broke
        st.error(f"Data feed unavailable: {data_err if data_err else 'No live stats'}")

    # ------------- MANUAL TRADE BUTTONS -------------
    st.subheader("Trade Controls (Paper)")
    b1, b2 = st.columns(2)
    if b1.button("BUY Market (Paper)"):
        resp, _ = place_alpaca_order(symbol, qty, "buy")
        if resp is not None and resp.status_code in (200, 201):
            b1.success("Buy sent ✅")
            log_trade(user, f"MANUAL BUY {qty} {symbol}")
        else:
            b1.error("Buy failed (check Alpaca keys)")
    if b2.button("SELL Market (Paper)"):
        resp, _ = place_alpaca_order(symbol, qty, "sell")
        if resp is not None and resp.status_code in (200, 201):
            b2.success("Sell sent ✅")
            log_trade(user, f"MANUAL SELL {qty} {symbol}")
        else:
            b2.error("Sell failed (check Alpaca keys)")

    # ------------- TRADE LOG -------------
    st.subheader("Session Log")
    if len(st.session_state.trade_log[user]) == 0:
        st.write("No trades yet.")
    else:
        st.dataframe(
            pd.DataFrame(st.session_state.trade_log[user]),
            use_container_width=True
        )

    # ------------- BEGINNER HELPER -------------
    st.subheader("🔍 Beginner Helper: Stocks to Watch")
    st.caption("Focus list for learning: breakout = running long, breakdown = fading short, range = chop/sell premium.")

    if st.button("Scan Watchlist"):
        recs = beginner_scan()
        if recs is None or len(recs) == 0:
            st.write("No symbols to suggest right now.")
        else:
            st.dataframe(recs, use_container_width=True)

    st.caption(
        "Goal: you stop guessing. You only play the stuff showing real action.\n"
        "BREAKOUT = bull run / long bias.\n"
        "BREAKDOWN = flush / short bias.\n"
        "RANGE_HELD = chop / sell premium / scalp small."
    )

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


