import streamlit as st
import yfinance as yf
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import plotly.graph_objects as go

# ==========================================================
# Redeyebatt Range + Breakout + Breakdown Hybrid
# Login, scanner, paper trade hooks
# NOW USING YAHOO FOR PRICE DATA (no Finnhub needed)
# ==========================================================

st.set_page_config(page_title="Redeyebatt Range Trader", layout="wide")

# -------------------------
# PINs (everyone 1234 now)
# -------------------------
USER_PINS = {
    "dad": "1234",
    "neil": "1234",
    "lucas": "1234",
    "guest": "1234",
}

# readable theme for each login (light bg, dark text)
USER_THEMES = {
    "dad":   {"bg": "#f0f2f6", "fg": "#000000", "label": "Dad"},
    "neil":  {"bg": "#f9fafc", "fg": "#000000", "label": "Neil"},
    "lucas": {"bg": "#ffffff", "fg": "#000000", "label": "Lucas"},
    "guest": {"bg": "#ffffff", "fg": "#000000", "label": "Guest"},
}

# -------------------------
# Secrets from Streamlit Cloud (Alpaca ONLY now)
# -------------------------
ALPACA_KEY = st.secrets.get("ALPACA_KEY", None)
ALPACA_SECRET = st.secrets.get("ALPACA_SECRET", None)
ALPACA_BASE_URL = st.secrets.get(
    "ALPACA_BASE_URL",
    "https://paper-api.alpaca.markets/v2"
)

# ==========================================================
# State init
# ==========================================================
def init_state():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if "user" not in st.session_state:
        st.session_state.user = None
    if "auto_trade" not in st.session_state:
        st.session_state.auto_trade = {
            "dad": False,
            "neil": False,
            "lucas": False,
            "guest": False,
        }
    if "trade_log" not in st.session_state:
        st.session_state.trade_log = {
            "dad": [],
            "neil": [],
            "lucas": [],
            "guest": [],
        }

def log_trade(u, msg, pl=None):
    st.session_state.trade_log[u].append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": msg,
        "p_l": pl if pl is not None else "",
    })

# ==========================================================
# Styling per user
# ==========================================================
def apply_user_theme(user):
    theme = USER_THEMES.get(user, USER_THEMES["guest"])
    st.markdown(
        f"""
        <style>
        .main {{
            background-color: {theme['bg']} !important;
            color: {theme['fg']} !important;
        }}
        .stMetricValue, .stMetricLabel, h1, h2, h3, h4, h5, h6, p, span, div {{
            color: {theme['fg']} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True
    )
    return theme

# ==========================================================
# Data helpers (YAHOO VERSION)
# ==========================================================

def get_yahoo_intraday(symbol: str):
    """
    Get intraday 5-minute candles for today using yfinance.
    Force timestamps so Yahoo always returns data during market hours.
    """
    try:
        now = datetime.now()
        start = now.replace(hour=9, minute=30, second=0, microsecond=0)
        end = now
        df = yf.download(
            symbol,
            start=start,
            end=end,
            interval="5m",
            progress=False,
            prepost=False,
        )
    except Exception as e:
        return None, f"Yahoo error: {e}"

    if df is None or df.empty:
        return None, f"No intraday data from Yahoo for {symbol} between {start} and {end}"

    out = pd.DataFrame({
        "t": df.index,
        "Open": df["Open"],
        "High": df["High"],
        "Low": df["Low"],
        "Close": df["Close"],
        "Volume": df["Volume"],
    })
    return out, None
    
    if df is None or df.empty:
        return None, f"No intraday data from Yahoo for {symbol} between {start} and {end}"

    out = pd.DataFrame({
        "t": df.index,
        "Open": df["Open"],
        "High": df["High"],
        "Low": df["Low"],
        "Close": df["Close"],
        "Volume": df["Volume"],
    })
    return out, None

def calc_levels(df_5m: pd.DataFrame, atr_lookback=14, cushion_frac=0.25):
    """
    - Opening range high/low = first ~30 minutes (first 6 candles of 5m)
    - ATR-ish = avg(high-low) of last atr_lookback candles
    - high_fence / low_fence = opening range +/- cushion
    """
    if df_5m is None or len(df_5m) < 6:
        return None

    # first ~30 minutes of the session
    first_slice = df_5m.head(6)
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
# Alpaca helpers (paper trading)
# ==========================================================

def alpaca_status():
    """
    Check if Alpaca creds are good and account is reachable.
    Returns (acct_json, err_str).
    If not connected, we don't crash the app.
    """
    if not (ALPACA_KEY and ALPACA_SECRET and ALPACA_BASE_URL):
        return None, "Alpaca creds missing"

    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }

    try:
        r = requests.get(f"{ALPACA_BASE_URL}/account", headers=headers)
    except Exception as e:
        return None, f"Alpaca error: {e}"

    if r.status_code == 200:
        return r.json(), None

    return None, f"{r.status_code}: {r.text}"

def alpaca_market_order(symbol, qty, side):
    """
    Send a simple market BUY/SELL to paper trading.
    side is 'buy' or 'sell'.
    We will only call this if connected AND user turned Auto Trade on.
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

    try:
        r = requests.post(f"{ALPACA_BASE_URL}/orders", headers=headers, json=body)
        return r, None
    except Exception as e:
        return None, f"Alpaca order error: {e}"

# ==========================================================
# Beginner Helper (now uses Yahoo instead of Finnhub)
# ==========================================================
def beginner_scan():
    """
    Scan 5 names and tell user where the action is.
    We rank by hottest move: BREAKOUT first, BREAKDOWN second, RANGE last.
    """
    watchlist = ["SPY", "NVDA", "AAPL", "TSLA", "AMZN"]
    out_rows = []

    for sym in watchlist:
        candles, err = get_yahoo_intraday(sym)
        if err or candles is None or len(candles) < 6:
            continue

        levels = calc_levels(candles)
        if not levels:
            continue

        mode = classify_mode(
            levels["last_price"],
            levels["high_fence"],
            levels["low_fence"],
        )

        out_rows.append({
            "Symbol": sym,
            "Last": round(levels["last_price"], 2),
            "ATR-ish": round(levels["atr"], 2),
            "Mode": mode,
        })

    priority = {"BREAKOUT": 0, "BREAKDOWN": 1, "RANGE_HELD": 2}
    out_rows.sort(key=lambda row: priority.get(row["Mode"], 99))

    if len(out_rows) == 0:
        return pd.DataFrame([{
            "Symbol": "N/A",
            "Last": "",
            "ATR-ish": "",
            "Mode": "No data",
        }])

    return pd.DataFrame(out_rows)

# ==========================================================
# LOGIN SCREEN
# ==========================================================
def show_login():
    st.title("🔐 Redeyebatt Trading Terminal Login")

    c1, c2 = st.columns(2)
    with c1:
        username = st.text_input("User (dad / neil / lucas / guest)").strip().lower()
    with c2:
        pin = st.text_input("PIN", type="password")

    if st.button("Log In"):
        if username in USER_PINS and pin == USER_PINS[username]:
            st.session_state.logged_in = True
            st.session_state.user = username
            st.success(f"Welcome, {USER_THEMES[username]['label']} ✅")
            st.rerun()
        else:
            st.error("Invalid login")

# ==========================================================
# DASHBOARD
# ==========================================================
def show_dashboard():
    user = st.session_state.user
    theme = apply_user_theme(user)

    st.markdown(f"### Welcome, {theme['label']}")

    # SIDEBAR CONTROLS
    st.sidebar.header("Session Controls")

    symbol = st.sidebar.text_input("Ticker Symbol", "SPY").upper()
    qty = st.sidebar.number_input("Trade Quantity", min_value=1, value=1)

    auto_flag = st.sidebar.checkbox(
        "Auto Trade (paper) breakout/breakdown",
        value=st.session_state.auto_trade[user],
        help="If ON: breakout will BUY, breakdown will SELL using paper account",
    )
    st.session_state.auto_trade[user] = auto_flag

    st.sidebar.markdown("---")

    # LIVE DATA via Yahoo
    df_live, data_err = get_yahoo_intraday(symbol)
    levels = calc_levels(df_live) if df_live is not None else None

    acct, acct_err = alpaca_status()

    # ACCOUNT / CONNECTION STATUS
    colA, colB, colC = st.columns(3)
    if acct and not acct_err:
        colA.metric("Buying Power", f"${float(acct.get('buying_power',0)):,.2f}")
        colB.metric("Cash", f"${float(acct.get('cash',0)):,.2f}")
        colC.success("Alpaca Connected ✅")
    else:
        colA.write("Alpaca not connected")
        colB.write(acct_err if acct_err else "")
        colC.write("")

    # RANGE / MODE DISPLAY
    if levels:
        mode = classify_mode(
            levels["last_price"],
            levels["high_fence"],
            levels["low_fence"],
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("ATR-ish", f"{levels['atr']:.2f}")
        m2.metric("High Fence", f"{levels['high_fence']:.2f}")
        m3.metric("Low Fence", f"{levels['low_fence']:.2f}")
        m4.metric("Last Price", f"{levels['last_price']:.2f}")

        st.write(f"**Market Mode:** {mode}")

        # AUTO TRADE LOGIC (only fires if Alpaca is connected AND auto_flag true)
        if auto_flag and acct and not acct_err:
            if mode == "BREAKOUT":
                r, err = alpaca_market_order(symbol, qty, "buy")
                if r is not None and r.status_code in (200, 201):
                    st.success("AUTO BUY sent ✅")
                    log_trade(user, f"AUTO BUY {qty} {symbol} (BREAKOUT)")
                else:
                    st.error(f"AUTO BUY failed: {r.text if r is not None else err}")

            elif mode == "BREAKDOWN":
                r, err = alpaca_market_order(symbol, qty, "sell")
                if r is not None and r.status_code in (200, 201):
                    st.warning("AUTO SELL sent ⛔")
                    log_trade(user, f"AUTO SELL {qty} {symbol} (BREAKDOWN)")
                else:
                    st.error(f"AUTO SELL failed: {r.text if r is not None else err}")

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
                y=levels["high_fence"],
                line_color="green",
                line_dash="dash",
                annotation_text="High Fence",
            )
            fig.add_hline(
                y=levels["low_fence"],
                line_color="red",
                line_dash="dash",
                annotation_text="Low Fence",
            )
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.error(f"Data feed unavailable: {data_err if data_err else 'No data'}")

    # MANUAL PAPER TRADE CONTROLS
    st.subheader("Trade Controls (Paper)")
    col_buy, col_sell = st.columns(2)
    if col_buy.button("BUY Market (Paper)"):
        r, err = alpaca_market_order(symbol, qty, "buy")
        if r is not None and r.status_code in (200, 201):
            col_buy.success("Buy sent ✅")
            log_trade(user, f"MANUAL BUY {qty} {symbol}")
        else:
            col_buy.error(f"Buy failed: {r.text if r is not None else err}")

    if col_sell.button("SELL Market (Paper)"):
        r, err = alpaca_market_order(symbol, qty, "sell")
        if r is not None and r.status_code in (200, 201):
            col_sell.success("Sell sent ✅")
            log_trade(user, f"MANUAL SELL {qty} {symbol}")
        else:
            col_sell.error(f"Sell failed: {r.text if r is not None else err}")

    # TRADE LOG
    st.subheader("Session Log / P&L")
    if len(st.session_state.trade_log[user]) == 0:
        st.write("No trades yet.")
    else:
        st.dataframe(pd.DataFrame(st.session_state.trade_log[user]), use_container_width=True)

    # BEGINNER HELPER
    st.subheader("🔍 Beginner Helper: Stocks to Watch")
    st.caption("Training wheels. BREAKOUT = momentum long. BREAKDOWN = short/hedge. RANGE_HELD = chop/collect.")

    if st.button("Scan Watchlist"):
        recs = beginner_scan()
        st.dataframe(recs, use_container_width=True)

    st.caption(
        "Goal: stop gambling. Only touch tickers that are actually moving.\n"
        "We rank them so you see momentum and danger first."
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






