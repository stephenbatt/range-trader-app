# ==============================================================
# Redeyebatt Range + Breakout + Breakdown Hybrid Strategy
# ==============================================================
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, time

st.set_page_config(page_title="Range Trader Hybrid", layout="wide")
st.title("📈 Redeyebatt Range + Breakout + Breakdown Hybrid")

# --- Sidebar Controls ---------------------------------------------------------
symbol = st.sidebar.text_input("Ticker Symbol", "SPY").upper()
days_back = st.sidebar.slider("Look-back days", 5, 60, 20)
dollars_per_point = st.sidebar.number_input("Dollars per $1 move", 10.0)
payout_size = st.sidebar.number_input("Fixed Range Payout", 100.0)
st.sidebar.markdown("---")

# --- Pull market data ---------------------------------------------------------
data = yf.download(symbol, period=f"{days_back}d", interval="1d")
if data.empty:
    st.error("No data found for that ticker.")
    st.stop()

data["ATR"] = (
    data["High"] - data["Low"]
).rolling(window=14).mean()  # simple ATR approximation

latest = data.iloc[-1]
opening_range_high = latest["High"]
opening_range_low = latest["Low"]
atr = latest["ATR"]

cushion = 0.25 * atr
high_level = opening_range_high + cushion
low_level = opening_range_low - cushion

st.subheader(f"{symbol} Range Setup (Last {days_back} Days)")
col1, col2, col3 = st.columns(3)
col1.metric("ATR", f"{atr:.2f}")
col2.metric("High Level", f"{high_level:.2f}")
col3.metric("Low Level", f"{low_level:.2f}")

# --- Session State ------------------------------------------------------------
if "tracking" not in st.session_state:
    st.session_state.update(
        {
            "tracking": False,
            "breakout_triggered": False,
            "breakout_price": None,
            "breakdown_triggered": False,
            "breakdown_price": None,
            "mode_result": None,
            "payout_today": 0.0,
        }
    )

# --- Start / Stop Buttons -----------------------------------------------------
colA, colB = st.columns(2)
if colA.button("▶ Start Tracking Today"):
    st.session_state["tracking"] = True
    st.session_state["start_time"] = datetime.now()

if colB.button("⏹ Reset / Stop"):
    for key in [
        "tracking",
        "breakout_triggered",
        "breakout_price",
        "breakdown_triggered",
        "breakdown_price",
        "mode_result",
        "payout_today",
    ]:
        st.session_state[key] = False if key == "tracking" else None
    st.success("Session reset.")

# --- Live Simulation / Manual Refresh ----------------------------------------
if st.session_state["tracking"]:
    live = yf.download(symbol, period="1d", interval="5m")
    live_price = live["Close"].iloc[-1]
    st.metric("Live Price", f"{live_price:.2f}")

    # Detect breakouts / breakdowns
    if live_price > high_level:
        if not st.session_state["breakout_triggered"]:
            st.session_state["breakout_triggered"] = True
            st.session_state["breakout_price"] = live_price
    elif live_price < low_level:
        if not st.session_state["breakdown_triggered"]:
            st.session_state["breakdown_triggered"] = True
            st.session_state["breakdown_price"] = live_price

    st.write(
        f"Breakout Triggered ➡️ {st.session_state['breakout_triggered']} @ {st.session_state['breakout_price']}"
    )
    st.write(
        f"Breakdown Triggered ⬇️ {st.session_state['breakdown_triggered']} @ {st.session_state['breakdown_price']}"
    )

    # Plot
    import plotly.graph_objects as go

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=live.index,
        open=live["Open"], high=live["High"], low=live["Low"], close=live["Close"],
        name=symbol))
    fig.add_hline(y=high_level, line_color="green", line_dash="dash", annotation_text="High Fence")
    fig.add_hline(y=low_level, line_color="red", line_dash="dash", annotation_text="Low Fence")
    st.plotly_chart(fig, use_container_width=True)

# --- Settlement Button --------------------------------------------------------
if st.button("🏁 Settle Day / Calculate P&L"):
    live = yf.download(symbol, period="1d", interval="5m")
    final_price = live["Close"].iloc[-1]

    if not st.session_state["breakout_triggered"] and not st.session_state["breakdown_triggered"]:
        st.session_state["mode_result"] = "RANGE_HELD"
        pl = payout_size
    elif st.session_state["breakout_triggered"]:
        st.session_state["mode_result"] = "BREAKOUT"
        move = final_price - st.session_state["breakout_price"]
        pl = move * dollars_per_point
    elif st.session_state["breakdown_triggered"]:
        st.session_state["mode_result"] = "BREAKDOWN"
        move = st.session_state["breakdown_price"] - final_price
        pl = move * dollars_per_point
    else:
        pl = 0

    st.session_state["payout_today"] = pl

    st.success(f"**Mode:** {st.session_state['mode_result']}  |  P/L = ${pl:,.2f}")

