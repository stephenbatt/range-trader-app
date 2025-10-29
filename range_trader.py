import streamlit as st
import requests
import pandas as pd
from datetime import datetime
import plotly.graph_objects as go
import time

# ==========================================================
# CONFIG
# ==========================================================
st.set_page_config(page_title="Redeyebatt Range Trader", layout="wide")

USER_PINS = {"dad": "1111", "neil": "2222", "lucas": "3333", "guest": "0000"}
USER_THEMES = {
    "dad": {"bg": "#0d1b3d", "fg": "#ffffff", "label": "Dad"},
    "neil": {"bg": "#1a1a1a", "fg": "#ffffff", "label": "Neil"},
    "lucas": {"bg": "#2b2f33", "fg": "#ffffff", "label": "Lucas"},
    "guest": {"bg": "#ffffff", "fg": "#000000", "label": "Guest"},
}

FINNHUB_KEY = st.secrets.get("FINNHUB_KEY", None)
ALPACA_KEY = st.secrets.get("ALPACA_KEY", None)
ALPACA_SECRET = st.secrets.get("ALPACA_SECRET", None)
ALPACA_BASE_URL = st.secrets.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")

# ==========================================================
# SMART FINNHUB CACHE  (30s reuse window)
# ==========================================================
_finnhub_cache = {}

def get_finnhub_intraday(symbol: str, resolution="5", lookback_minutes=390):
    """Pull intraday 5m candles from Finnhub with 30s cache + graceful fallback."""
    if not symbol:
        return None, "No symbol"
    if not FINNHUB_KEY:
        return None, "No FINNHUB_KEY"

    now = time.time()
    # Use cache if <30s old
    if symbol in _finnhub_cache:
        last_ts, cached_df = _finnhub_cache[symbol]
        if now - last_ts < 30:
            return cached_df, None

    frm = int(now) - (lookback_minutes * 60)
    url = (
        f"https://finnhub.io/api/v1/stock/candle"
        f"?symbol={symbol.upper()}&resolution={resolution}"
        f"&from={frm}&to={int(now)}&token={FINNHUB_KEY}"
    )

    try:
        with st.spinner(f"Fetching {symbol} data..."):
            r = requests.get(url, timeout=10)
    except Exception as e:
        st.warning(f"⚠️ Finnhub request failed: {e}")
        return None, "Network error"

    if r.status_code == 403:
        st.warning("⏳ Finnhub 403 (throttled) — pausing 10 seconds before next try.")
        time.sleep(10)
        return None, "Throttled (403)"
    if r.status_code != 200:
        st.warning(f"⚠️ Finnhub HTTP {r.status_code}")
        return None, f"HTTP {r.status_code}"

    data = r.json()
    if data.get("s") != "ok":
        st.warning("⚠️ Finnhub returned no data (maybe closed market)")
        return None, "No data"

    df = pd.DataFrame({
        "t": pd.to_datetime(data["t"], unit="s"),
        "Open": data["o"],
        "High": data["h"],
        "Low": data["l"],
        "Close": data["c"],
        "Volume": data["v"],
    })
    if df.empty:
        st.warning("⚠️ Finnhub returned empty dataframe")
        return None, "Empty"

    _finnhub_cache[symbol] = (now, df)
    return df, None

# ==========================================================
# RANGE CALC + HELPERS
# ==========================================================
def calc_range_levels(df_5m: pd.DataFrame, atr_lookback=14, cushion_frac=0.25):
    if df_5m is None or len(df_5m) < 6:
        return None
    first = df_5m.head(6)
    opening_high = first["High"].max()
    opening_low = first["Low"].min()
    atr_est = (df_5m.tail(atr_lookback)["High"] - df_5m.tail(atr_lookback)["Low"]).mean()
    atr_est = 1.0 if pd.isna(atr_est) or atr_est == 0 else float(atr_est)
    cushion = atr_est * cushion_frac
    return {
        "atr": atr_est,
        "high_fence": opening_high + cushion,
        "low_fence": opening_low - cushion,
        "last_price": df_5m["Close"].iloc[-1],
    }

def classify_mode(p, hi, lo):
    return "BREAKOUT" if p > hi else "BREAKDOWN" if p < lo else "RANGE_HELD"

# ==========================================================
# VIDEO LOGIN SCREEN
# ==========================================================
def show_login():
    video_path = "login_bg.mp4"
    video_html = f"""
    <style>
    [data-testid="stAppViewContainer"] {{background: none;}}
    video.bg-video {{
        position: fixed; right: 0; bottom: 0;
        min-width: 100%; min-height: 100%;
        z-index: 1; object-fit: cover;
        filter: brightness(0.8);
    }}
    .login-box {{
        position: relative; z-index: 2; width: 340px;
        margin: auto; text-align: center;
        padding: 2rem; background-color: rgba(0,0,0,0.55);
        border-radius: 20px; box-shadow: 0 0 30px rgba(0,0,0,0.5);
        backdrop-filter: blur(3px);
    }}
    label, input, button, h1, p {{color:#fff !important;}}
    .login-box button {{
        background: linear-gradient(45deg,#00c6ff,#0072ff);
        border:none; border-radius:10px; padding:0.5rem 1rem;
        color:#fff; font-weight:bold; cursor:pointer;
    }}
    </style>
    <video autoplay muted loop playsinline class="bg-video">
        <source src="{video_path}" type="video/mp4">
    </video>
    """
    st.markdown(video_html, unsafe_allow_html=True)
    st.markdown("<div class='login-box'>", unsafe_allow_html=True)
    st.title("🔐 Redeyebatt Trader Login")
    user = st.text_input("Username").strip().lower()
    pin = st.text_input("PIN", type="password")
    if st.button("Log In"):
        if user in USER_PINS and pin == USER_PINS[user]:
            st.session_state.logged_in = True
            st.session_state.user = user
            st.rerun()
        else:
            st.error("Invalid login")
    st.markdown("</div>", unsafe_allow_html=True)

# ==========================================================
# DASHBOARD
# ==========================================================
def show_dashboard():
    user = st.session_state.user
    st.markdown(f"### Welcome, {USER_THEMES[user]['label']}")
    st.sidebar.markdown("### 🔧 Controls")

    symbol = st.sidebar.text_input("Ticker", "SPY").upper()
    qty = st.sidebar.number_input("Qty", min_value=1, value=1)
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.rerun()

    df, err = get_finnhub_intraday(symbol)
    stats = calc_range_levels(df) if df is not None else None

    if stats:
        mode = classify_mode(stats["last_price"], stats["high_fence"], stats["low_fence"])
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ATR", f"{stats['atr']:.2f}")
        c2.metric("High Fence", f"{stats['high_fence']:.2f}")
        c3.metric("Low Fence", f"{stats['low_fence']:.2f}")
        c4.metric("Last Price", f"{stats['last_price']:.2f}")
        st.write(f"**Mode:** {mode}")

        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=df["t"],
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            increasing_line_color='green',
            decreasing_line_color='red',
            name=symbol
        ))
        fig.add_hline(y=stats["high_fence"], line_color="lime", line_dash="dash")
        fig.add_hline(y=stats["low_fence"], line_color="red", line_dash="dash")
        st.plotly_chart(fig, use_container_width=True)

        # Last updated time
        st.caption(f"🕒 Last updated: {datetime.now().strftime('%I:%M:%S %p')}")
    else:
        st.error(f"Data feed unavailable: {err if err else 'No data'}")

# ==========================================================
# MAIN
# ==========================================================
def main():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if not st.session_state.logged_in:
        show_login()
    else:
        show_dashboard()

if __name__ == "__main__":
    main()

