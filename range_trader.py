# ==============================================================
# Redeyebatt Range + Breakout + Breakdown Hybrid Strategy
# ==============================================================
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime

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

# basic ATR-ish estimate
data["ATR_est"] = (data["High"] - data["Low"]).rolling(window=14).mean()

latest = data.iloc[-1]
opening_range_high = latest["High"]
opening_range_low = latest["Low"]

# --- Compute and sanitize ATR -------------------------------------------------
atr_val = latest["ATR_est"]

# Try to coerce ATR to a clean float
try:
    atr = float(atr_val)
except Exception:
    atr = np.nan

# fallback ATR calculation if weird
if np.isnan(atr) or atr == 0:
    fallback_atr = (data["High"].iloc[-14:] - data["Low"].iloc[-14:]).mean()
    try:
        atr = float(fallback_atr)
    except Exception:
        atr = np.nan

if np.isnan(atr) or atr == 0:
    atr = 1.0  # absolute last fallback so app never crashes

# build cushion / fences
cushion = 0.25 * atr
high_level_val = opening_range_high + cushion
low_level_val = opening_range_low - cushion

# force high/low levels to printable strings
def safe_fmt(x):
    try:
        return f"{float(x):.2f}"
    except Exception:
        return "N/A"

atr_display = safe_fmt(atr)
high_display = safe_fmt(high_level_val)
low_display = safe_fmt(low_level_val)

st.subheader(f"{symbol} Range Setup (Last {days_back} Days)")
col1, col2, col3 = st.columns(3)
col1.metric("ATR", atr_display)
col2.metric("High Level", high_display)
col3.metric("Low Level", low_display)

# keep numeric versions too for logic later
high_level = None
low_level = None
try:
    high_level = float(high_level_val)
    low_level = float(low_level_val)
except Exception:
    pass  # if they end up None, we just won't evaluate breakout logic

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

    # pull intraday candles
    live = yf.download(symbol, period="1d", interval="5m")
    if live.empty:
        st.warning("No intraday data available.")
    else:
        live_price = live["Close"].iloc[-1]
        st.metric("Live Price", f"{live_price:.2f}")

        # only run breakout logic if fences are valid floats
        if high_level is not None and low_level is not None:
            # Detect upside breakout
            if live_price > high_level:
                if not st.session_state["breakout_triggered"]:
                    st.session_state["breakout_triggered"] = True
                    st.session_state["breakout_price"] = live_price

            # Detect downside breakdown
            if live_price < low_level:
                if not st.session_state["breakdown_triggered"]:
                    st.session_state["breakdown_triggered"] = True
                    st.session_state["breakdown_price"] = live_price

        st.write(
            f"Breakout Triggered ➡️ {st.session_state['breakout_triggered']} @ {st.session_state['breakout_price']}"
        )
        st.write(
            f"Breakdown Triggered ⬇️ {st.session_state['breakdown_triggered']} @ {st.session_state['breakdown_price']}"
        )

        # Plot candles + fences
        import plotly.graph_objects as go

        fig = go.Figure()
        fig.add_trace(
            go.Candlestick(
                x=live.index,
                open=live["Open"],
                high=live["High"],
                low=live["Low"],
                close=live["Close"],
                name=symbol,
            )
        )

        if high_level is not None:
            fig.add_hline(
                y=high_level,
                line_color="green",
                line_dash="dash",
                annotation_text="High Fence",
            )
        if low_level is not None:
            fig.add_hline(
                y=low_level,
                line_color="red",
                line_dash="dash",
                annotation_text="Low Fence",
            )

        st.plotly_chart(fig, use_container_width=True)

# --- Settlement Button --------------------------------------------------------
if st.button("🏁 Settle Day / Calculate P&L"):

    live = yf.download(symbol, period="1d", interval="5m")
    if live.empty:
        st.error("No intraday data, can't settle.")
    else:
        final_price = live["Close"].iloc[-1]

        breakout = st.session_state["breakout_triggered"]
        breakdown = st.session_state["breakdown_triggered"]

        # 3 cases:
        # 1. stayed in range (no breakout OR breakdown)
        if not breakout and not breakdown:
            st.session_state["mode_result"] = "RANGE_HELD"
            pl = payout_size

        # 2. upside breakout -> ride the rocket
        elif breakout and st.session_state["breakout_price"] is not None:
            st.session_state["mode_result"] = "BREAKOUT"
            move = final_price - st.session_state["breakout_price"]
            pl = move * dollars_per_point

        # 3. downside breakdown -> hedge/short
        elif breakdown and st.session_state["breakdown_price"] is not None:
            st.session_state["mode_result"] = "BREAKDOWN"
            move = st.session_state["breakdown_price"] - final_price
            pl = move * dollars_per_point

        else:
            st.session_state["mode_result"] = "UNKNOWN"
            pl = 0.0

        st.session_state["payout_today"] = pl

        st.success(
            f"**Mode:** {st.session_state['mode_result']}  |  P/L = ${pl:,.2f}"
        )
