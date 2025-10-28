# ==============================================================
# 📈 Redeyebatt Range + Breakout + Breakdown Hybrid (FINNHUB)
# Clean working version - No Yahoo, fully integrated
# ==============================================================

import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import plotly.graph_objects as go

# ==============================================================
# Keys and Config
# ==============================================================
FINNHUB_KEY = st.secrets.get("FINNHUB_KEY", None)
ALPACA_KEY = st.secrets.get("ALPACA_KEY", None)
ALPACA_SECRET = st.secrets.get("ALPACA_SECRET", None)
ALPACA_BASE_URL = st.secrets.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")

# ==============================================================
# App Setup
# ==============================================================
st.set_page_config(page_title="📈 Redeyebatt Range Trader", layout="wide")

# ------------------------------
# Simple login (temporary)
# ------------------------------
USER_PINS = {"dad": "1234", "neil": "1234", "lucas": "1234", "guest": "1234"}
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("🔐 Login")
    u = st.text_input("Username").strip().lower()
    p = st.text_input("PIN", type="password")
    if st.button("Enter"):
        if u in USER_PINS and p == USER_PINS[u]:
            st.session_state.logged_in = True
            st.session_state.user = u
            st.rerun()
        else:
            st.error("Access denied")
    st.stop()

user = st.session_state.user
st.title(f"📊 Welcome, {user.capitalize()}")

# ==============================================================
# Finnhub intraday fetcher
# ==============================================================
def get_finnhub_intraday(symbol="SPY", resolution="5", minutes_back=390):
    if not FINNHUB_KEY:
        return None, "No FINNHUB_KEY in secrets"

    now = int(datetime.now().timestamp())
    frm = now - minutes_back * 60

    url = (
        f"https://finnhub.io/api/v1/stock/candle?"
        f"symbol={symbol.upper()}&resolution={resolution}&from={frm}&to={now}&token={FINNHUB_KEY}"
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
        "Volume": data["v"]
    })
    return df, None

# ==============================================================
# Range logic
# ==============================================================
def calc_levels(df):
    if df is None or len(df) < 6:
        return None

    open_slice = df.head(6)
    high = open_slice["High"].max()
    low = open_slice["Low"].min()

    atr_est = (df["High"].tail(14) - df["Low"].tail(14)).mean()
    if pd.isna(atr_est) or atr_est == 0:
        atr_est = 1.0

    cushion = atr_est * 0.25
    return {
        "atr": float(atr_est),
        "high_fence": float(high + cushion),
        "low_fence": float(low - cushion),
        "last_price": float(df["Close"].iloc[-1])
    }

def classify_mode(last_price, high_fence, low_fence):
    if last_price > high_fence:
        return "BREAKOUT"
    elif last_price < low_fence:
        return "BREAKDOWN"
    else:
        return "RANGE_HELD"

# ==============================================================
# Alpaca helpers
# ==============================================================
def alpaca_status():
    if not (ALPACA_KEY and ALPACA_SECRET):
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
    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json",
    }
    body = {"symbol": symbol, "qty": str(qty), "side": side, "type": "market", "time_in_force": "day"}
    return requests.post(f"{ALPACA_BASE_URL}/v2/orders", headers=headers, json=body)

# ==============================================================
# Dashboard
# ==============================================================
symbol = st.sidebar.text_input("Ticker Symbol", "SPY").upper()
qty = st.sidebar.number_input("Qty", 1, 100, 1)
auto_trade = st.sidebar.checkbox("Auto Trade (Paper)", value=False)

df_live, err = get_finnhub_intraday(symbol)
acct, acct_err = alpaca_status()

colA, colB, colC = st.columns(3)
if acct:
    colA.metric("Buying Power", f"${float(acct.get('buying_power',0)):,.2f}")
    colB.metric("Cash", f"${float(acct.get('cash',0)):,.2f}")
    colC.success("Alpaca Connected ✅")
else:
    colA.write("Alpaca not connected")
    colB.write(acct_err)
    colC.write("")

if df_live is None:
    st.error(f"Data feed unavailable: {err}")
    st.stop()

levels = calc_levels(df_live)
if not levels:
    st.error("Could not calculate range levels.")
    st.stop()

mode = classify_mode(levels["last_price"], levels["high_fence"], levels["low_fence"])

m1, m2, m3, m4 = st.columns(4)
m1.metric("ATR", f"{levels['atr']:.2f}")
m2.metric("High Fence", f"{levels['high_fence']:.2f}")
m3.metric("Low Fence", f"{levels['low_fence']:.2f}")
m4.metric("Last", f"{levels['last_price']:.2f}")
st.write(f"**Market Mode:** {mode}")

# --- Chart
fig = go.Figure()
fig.add_trace(go.Candlestick(
    x=df_live["t"],
    open=df_live["Open"],
    high=df_live["High"],
    low=df_live["Low"],
    close=df_live["Close"],
    name=symbol,
))
fig.add_hline(y=levels["high_fence"], line_color="green", line_dash="dash")
fig.add_hline(y=levels["low_fence"], line_color="red", line_dash="dash")
st.plotly_chart(fig, use_container_width=True)

