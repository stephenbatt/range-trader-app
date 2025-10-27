# ==============================================================
# Redeyebatt Range + Breakout + Breakdown Hybrid (YFinance Only)
# ==============================================================
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import plotly.graph_objects as go

st.set_page_config(page_title="Redeyebatt Range Trader", layout="wide")
st.title("📈 Redeyebatt Range + Breakout + Breakdown Hybrid")

# ----------------- Sidebar Controls -----------------
symbol = st.sidebar.text_input("Ticker Symbol", "SPY").upper()
days_back = st.sidebar.slider("Look-back days", 5, 60, 20)
dollars_per_point = st.sidebar.number_input("Dollars per $1 move", 10.0)
payout_size = st.sidebar.number_input("Fixed Range Payout", 100.0)
st.sidebar.markdown("---")

# ----------------- Get intraday data -----------------
def get_yahoo_intraday(symbol: str):
    """Always returns 5-minute intraday data from 9:30 → now."""
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
        if df is None or df.empty:
            return None, "No intraday data from Yahoo"
        df = df.reset_index()       # make sure index is a column
        df = df.rename(
            columns={
                "Datetime": "t",
                "Open": "Open",
                "High": "High",
                "Low": "Low",
                "Close": "Close",
                "Volume": "Volume",
            }
        )
        return df, None
    except Exception as e:
        return None, str(e)

# ----------------- Compute levels -----------------
def calc_levels(df):
    """Build ATR-ish cushion and breakout fences."""
    if df is None or df.empty:
        return None
    first_slice = df.head(6)                     # ~first 30 minutes
    opening_high = first_slice["High"].max()
    opening_low = first_slice["Low"].min()
    recent_slice = df.tail(14)
    atr_est = (recent_slice["High"] - recent_slice["Low"]).mean()
    if pd.isna(atr_est) or atr_est == 0:
        atr_est = 1.0
    cushion = atr_est * 0.25
    high_fence = opening_high + cushion
    low_fence = opening_low - cushion
    last_price = df["Close"].iloc[-1]
    return {
        "atr": float(atr_est),
        "opening_high": float(opening_high),
        "opening_low": float(opening_low),
        "high_fence": float(high_fence),
        "low_fence": float(low_fence),
        "last_price": float(last_price),
    }

def classify_mode(p, hi, lo):
    if p > hi:
        return "BREAKOUT"
    elif p < lo:
        return "BREAKDOWN"
    else:
        return "RANGE_HELD"

# ----------------- Main body -----------------
df_live, err = get_yahoo_intraday(symbol)
if err:
    st.error(err)
    st.stop()

stats = calc_levels(df_live)
if not stats:
    st.error("Could not calculate range levels.")
    st.stop()

# display metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric("ATR", f"{stats['atr']:.2f}")
col2.metric("High Fence", f"{stats['high_fence']:.2f}")
col3.metric("Low Fence", f"{stats['low_fence']:.2f}")
col4.metric("Last", f"{stats['last_price']:.2f}")

mode = classify_mode(stats["last_price"], stats["high_fence"], stats["low_fence"])
st.markdown(f"### Market Mode → **{mode}**")

# ----------------- Chart -----------------
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
fig.add_hline(y=stats["high_fence"], line_color="green", line_dash="dash", annotation_text="High Fence")
fig.add_hline(y=stats["low_fence"], line_color="red", line_dash="dash", annotation_text="Low Fence")
st.plotly_chart(fig, use_container_width=True)

