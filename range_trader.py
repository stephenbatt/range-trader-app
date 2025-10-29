import streamlit as st
import requests
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime

# ==========================================================
# Redeyebatt Range Trader (rollback stable version)
# - simple login
# - Finnhub data
# - Alpaca paper trading
# - Beginner scan
# - NO video, NO extra CSS, NO sidebar stretching junk
# ==========================================================

st.set_page_config(page_title="Redeyebatt Range Trader", layout="wide")

# -------------------------
# USERS / THEMES
# -------------------------
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

# -------------------------
# SECRETS (Streamlit Cloud)
# -------------------------
FINNHUB_KEY = st.secrets.get("FINNHUB_KEY", None)

ALPACA_KEY = st.secrets.get("ALPACA_KEY", None)
ALPACA_SECRET = st.secrets.get("ALPACA_SECRET", None)
ALPACA_BASE_URL = st.secrets.get(
    "ALPACA_BASE_URL",
    "https://paper-api.alpaca.markets/v2"
)

# ==========================================================
# STATE
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

def log_trade(u, msg, pl=None):
    st.session_state.trade_log[u].append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": msg,
        "p_l": pl if pl is not None else "",
    })

# ==========================================================
# THEME (simple color change per user)
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
        .stMetricValue, .stMetricLabel,
        h1, h2, h3, h4, h5, h6, p, span, div {{
            color: {theme['fg']} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    return theme

# ==========================================================
# FINNHUB DATA (no cache, just straight pull)
# ==========================================================
def get_finnhub_intraday(symbol: str, resolution="5", lookback_minutes=390):
    """
    Pull recent intraday 5m candles from Finnhub.
    Returns (df, err). df has t, Open, High, Low, Close, Volume.
    If FINNHUB_KEY missing or blocked, err explains.
    """
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

    try:
        r = requests.get(url, timeout=10)
    except Exception as e:
        return None, f"Finnhub request failed: {e}"

    if r.status_code == 403:
        # throttled / limit / afterhours free plan
        return None, "Finnhub 403 (rate limit / closed market)"

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

    if df is None or df.empty:
        return None, "Empty data"

    return df, None

def calc_levels(df_5m: pd.DataFrame, atr_lookback=14, cushion_frac=0.25):
    """
    - First ~30 min (first 6 x 5m candles) gives opening range
    - ATR-ish = avg(high-low) over last atr_lookback candles
    - High fence / low fence from that
    """
    if df_5m is None or len(df_5m) < 6:
        return None

    first_slice = df_5m.head(6)  # ~ first 30 mins
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
# ALPACA HELPERS
# ==========================================================
def alpaca_account():
    """
    Check Alpaca and return acct info.
    Returns (acct_json, err_str)
    """
    if not (ALPACA_KEY and ALPACA_SECRET and ALPACA_BASE_URL):
        return None, "Alpaca creds missing"

    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }

    try:
        r = requests.get(f"{ALPACA_BASE_URL}/account", headers=headers, timeout=10)
    except Exception as e:
        return None, f"Alpaca error: {e}"

    if r.status_code == 200:
        return r.json(), None

    return None, f"{r.status_code}: {r.text}"

def alpaca_market_order(symbol, qty, side):
    """
    Send paper market BUY/SELL.
    side is 'buy' or 'sell'.
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
        r = requests.post(
            f"{ALPACA_BASE_URL}/orders",
            headers=headers,
            json=body,
            timeout=10
        )
        return r, None
    except Exception as e:
        return None, f"Order error: {e}"

# ==========================================================
# BEGINNER HELPER
# ==========================================================
def beginner_scan():
    """
    Check common tickers, show who is BREAKOUT/BREAKDOWN/RANGE.
    """
    watchlist = ["SPY", "NVDA", "AAPL", "TSLA", "AMZN", "AMD", "QQQ"]
    out_rows = []

    for sym in watchlist:
        df_sym, err = get_finnhub_intraday(sym)
        if df_sym is None or err:
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

    if len(out_rows) == 0:
        return pd.DataFrame([{
            "Symbol": "N/A",
            "Last": "",
            "ATR": "",
            "Mode": "No data / rate limit",
        }])

    # hot stuff first
    priority = {"BREAKOUT": 0, "BREAKDOWN": 1, "RANGE_HELD": 2}
    out_rows.sort(key=lambda r: priority.get(r["Mode"], 99))

    return pd.DataFrame(out_rows)

# ==========================================================
# LOGIN SCREEN
# ==========================================================
def show_login():
    st.title("🔐 Redeyebatt Trading Terminal Login")

    col1, col2 = st.columns(2)
    with col1:
        username = st.text_input("User (dad / neil / lucas / guest)").strip().lower()
    with col2:
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

    # ---- Sidebar ----
    st.sidebar.header("Session Controls")
    symbol = st.sidebar.text_input("Ticker Symbol", "SPY").upper()
    qty = st.sidebar.number_input("Trade Quantity", min_value=1, value=1)

    auto_flag = st.sidebar.checkbox(
        "Auto Trade (paper) breakout/breakdown",
        value=st.session_state.auto_trade[user],
        help="If ON: BREAKOUT buys, BREAKDOWN sells using Alpaca paper.",
    )
    st.session_state.auto_trade[user] = auto_flag

    st.sidebar.markdown("---")
    buy_btn = st.sidebar.button("BUY Market (Paper)")
    sell_btn = st.sidebar.button("SELL Market (Paper)")

    # ---- Data ----
    df_live, fin_err = get_finnhub_intraday(symbol)
    stats = calc_levels(df_live) if df_live is not None else None

    acct, acct_err = alpaca_account()

    # ---- Account row ----
    colA, colB, colC = st.columns(3)
    if acct and not acct_err:
        try:
            colA.metric("Buying Power", f"${float(acct.get('buying_power',0)):,.2f}")
            colB.metric("Cash", f"${float(acct.get('cash',0)):,.2f}")
            colC.success("Alpaca Connected ✅")
        except Exception:
            colA.write("Alpaca connected")
            colB.write("Account ok")
            colC.write("")
    else:
        colA.write("Alpaca not connected")
        colB.write(acct_err if acct_err else "")
        colC.write("")

    # ---- Range / Mode ----
    if stats:
        mode = classify_mode(
            stats["last_price"],
            stats["high_fence"],
            stats["low_fence"],
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ATR-ish", f"{stats['atr']:.2f}")
        c2.metric("High Fence", f"{stats['high_fence']:.2f}")
        c3.metric("Low Fence", f"{stats['low_fence']:.2f}")
        c4.metric("Last Price", f"{stats['last_price']:.2f}")

        st.write(f"**Market Mode:** {mode}")

        # auto trade logic
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

        # chart
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
        st.error(f"Data feed unavailable: {fin_err if fin_err else 'No data'}")

    # ---- manual trade buttons (sidebar clicks) ----
    if buy_btn:
        r, err = alpaca_market_order(symbol, qty, "buy")
        if r is not None and r.status_code in (200, 201):
            st.success("Buy sent ✅")
            log_trade(user, f"MANUAL BUY {qty} {symbol}")
        else:
            st.error(f"Buy failed: {err if err else r.text if r is not None else 'no response'}")

    if sell_btn:
        r, err = alpaca_market_order(symbol, qty, "sell")
        if r is not None and r.status_code in (200, 201):
            st.success("Sell sent ✅")
            log_trade(user, f"MANUAL SELL {qty} {symbol}")
        else:
            st.error(f"Sell failed: {err if err else r.text if r is not None else 'no response'}")

    # ---- Trade Log ----
    st.subheader("Session Log")
    if len(st.session_state.trade_log[user]) == 0:
        st.write("No trades yet.")
    else:
        st.dataframe(
            pd.DataFrame(st.session_state.trade_log[user]),
            use_container_width=True
        )

    # ---- Beginner Helper ----
    st.subheader("🔍 Beginner Helper: Stocks to Watch")
    st.caption("BREAKOUT = going up fast. BREAKDOWN = flushing down. RANGE_HELD = chop/collect.")
    if st.button("Scan Watchlist"):
        recs = beginner_scan()
        st.dataframe(recs, use_container_width=True)

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

