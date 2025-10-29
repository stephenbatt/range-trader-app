import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import plotly.graph_objects as go

# ==========================================================
# Redeyebatt Range + Breakout + Breakdown Hybrid
# Full integrated terminal w/ login, scanner, paper trade hooks
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
# Secrets from Streamlit Cloud
# -------------------------
FINNHUB_KEY = st.secrets.get("FINNHUB_KEY", None)
ALPACA_KEY = st.secrets.get("ALPACA_KEY", None)
ALPACA_SECRET = st.secrets.get("ALPACA_SECRET", None)
ALPACA_BASE_URL = st.secrets.get(
    "ALPACA_BASE_URL",
    "https://paper-api.alpaca.markets"
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
# Data helpers
# ==========================================================
def get_finnhub_intraday(symbol: str, resolution="5", lookback_minutes=390):
    if not FINNHUB_KEY:
        return None, "No FINNHUB_KEY in secrets"
    now = int(datetime.now().timestamp())
    frm = now - (lookback_minutes * 60)
    url = (
        "https://finnhub.io/api/v1/stock/candle"
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

def calc_levels(df_5m: pd.DataFrame, atr_lookback=14, cushion_frac=0.25):
    if df_5m is None or len(df_5m) < 6:
        return None
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
# Alpaca helpers
# ==========================================================
def alpaca_status():
    if not (ALPACA_KEY and ALPACA_SECRET and ALPACA_BASE_URL):
        return None, "Alpaca creds missing"
    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }
    r = requests.get(f"{ALPACA_BASE_URL}/v2/account", headers=headers)
    if r.status_code == 200:
        return r.json(), None
    return None, f"{r.status_code}: {r.text}"

def alpaca_market_order(symbol, qty, side):
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
    r = requests.post(f"{ALPACA_BASE_URL}/v2/orders", headers=headers, json=body)
    return r, None

# ==========================================================
# Beginner Helper
# ==========================================================
def beginner_scan():
    watchlist = ["SPY", "NVDA", "AAPL", "TSLA", "AMZN"]
    out_rows = []
    for sym in watchlist:
        df_sym, err = get_finnhub_intraday(sym)
        if err or df_sym is None or len(df_sym) < 6:
            continue
        levels = calc_levels(df_sym)
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
            "ATR": round(levels["atr"], 2),
            "Mode": mode,
        })
    priority = {"BREAKOUT": 0, "BREAKDOWN": 1, "RANGE_HELD": 2}
    out_rows.sort(key=lambda row: priority.get(row["Mode"], 99))
    if len(out_rows) == 0:
        return pd.DataFrame([{
            "Symbol": "N/A",
            "Last": "",
            "ATR": "",
            "Mode": "No data / key?"
        }])
    return pd.DataFrame(out_rows)

# ==========================================================
# LOGIN SCREEN
# ==========================================================
def show_login():
    st.title("🔐 Login")
    c1, c2 = st.columns(2)
    with c1:
        username = st.text_input("Username").strip().lower()
    with c2:
        pin = st.text_input("PIN", type="password")
    if st.button("Enter"):
        if username in USER_PINS and pin == USER_PINS[username]:
            st.session_state.logged_in = True
            st.session_state.user = username
            st.rerun()
        else:
            st.error("Access denied")

# ==========================================================
# DASHBOARD
# ==========================================================
def show_dashboard():
    user = st.session_state.user
    theme = apply_user_theme(user)
    st.markdown(f"### Welcome, {theme['label']}")
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

    df_live, fin_err = get_finnhub_intraday(symbol)
    levels = calc_levels(df_live) if df_live is not None else None
    acct, acct_err = alpaca_status()

    colA, colB, colC = st.columns(3)
    if acct and not acct_err:
        colA.metric("Buying Power", f"${float(acct.get('buying_power',0)):,.2f}")
        colB.metric("Cash", f"${float(acct.get('cash',0)):,.2f}")
        colC.success("Alpaca Connected ✅")
    else:
        colA.write("Alpaca not connected")
        colB.write(acct_err if acct_err else "")
        colC.write("")

    if levels:
        mode = classify_mode(
            levels["last_price"],
            levels["high_fence"],
            levels["low_fence"],
        )
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("ATR", f"{levels['atr']:.2f}")
        m2.metric("High Fence", f"{levels['high_fence']:.2f}")
        m3.metric("Low Fence", f"{levels['low_fence']:.2f}")
        m4.metric("Last Price", f"{levels['last_price']:.2f}")
        st.write(f"**Market Mode:** {mode}")

        if auto_flag and acct and not acct_err:
            if mode == "BREAKOUT":
                r, _ = alpaca_market_order(symbol, qty, "buy")
                if r is not None and r.status_code in (200, 201):
                    st.success("AUTO BUY sent ✅")
                    log_trade(user, f"AUTO BUY {qty} {symbol} (BREAKOUT)")
            elif mode == "BREAKDOWN":
                r, _ = alpaca_market_order(symbol, qty, "sell")
                if r is not None and r.status_code in (200, 201):
                    st.warning("AUTO SELL sent ⛔")
                    log_trade(user, f"AUTO SELL {qty} {symbol} (BREAKDOWN)")
            else:
                st.info("AUTO: HOLD / RANGE")

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
                y=levels["high_fence"], line_color="green", line_dash="dash", annotation_text="High Fence"
            )
            fig.add_hline(
                y=levels["low_fence"], line_color="red", line_dash="dash", annotation_text="Low Fence"
            )
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.error(f"Data feed unavailable: {fin_err if fin_err else 'No data'}")

    st.subheader("Trade Controls (Paper)")
    col_buy, col_sell = st.columns(2)
    if col_buy.button("BUY Market (Paper)"):
        r, _ = alpaca_market_order(symbol, qty, "buy")
        if r is not None and r.status_code in (200, 201):
            col_buy.success("Buy sent ✅")
            log_trade(user, f"MANUAL BUY {qty} {symbol}")
    if col_sell.button("SELL Market (Paper)"):
        r, _ = alpaca_market_order(symbol, qty, "sell")
        if r is not None and r.status_code in (200, 201):
            col_sell.success("Sell sent ✅")
            log_trade(user, f"MANUAL SELL {qty} {symbol}")

    st.subheader("Session Log / P&L")
    if len(st.session_state.trade_log[user]) == 0:
        st.write("No trades yet.")
    else:
        st.dataframe(pd.DataFrame(st.session_state.trade_log[user]), use_container_width=True)

    st.subheader("🔍 Beginner Helper: Stocks to Watch")
    st.caption("BREAKOUT = bullish run. BREAKDOWN = short bias. RANGE_HELD = chop.")
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



