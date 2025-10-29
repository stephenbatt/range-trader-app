import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import plotly.graph_objects as go

# ==========================================================
# Redeyebatt Range Trader - FINNHUB + ALPACA VERSION
# Clean rebuild with working login, layout fixes, caching, and trading
# ==========================================================

st.set_page_config(page_title="Redeyebatt Range Trader", layout="wide")

# =========================
# USER ACCOUNTS / THEMES
# =========================

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

# =========================
# SECRETS (Streamlit Cloud)
# =========================

FINNHUB_KEY = st.secrets.get("FINNHUB_KEY", None)

ALPACA_KEY = st.secrets.get("ALPACA_KEY", None)
ALPACA_SECRET = st.secrets.get("ALPACA_SECRET", None)
ALPACA_BASE_URL = st.secrets.get(
    "ALPACA_BASE_URL",
    "https://paper-api.alpaca.markets/v2"
)

# ==========================================================
# SESSION STATE INIT
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

# ==========================================================
# STYLE / THEME
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
        unsafe_allow_html=True
    )
    return theme

# dashboard layout tightening (center content, not full 100% stretch)
def inject_dashboard_css():
    st.markdown(
        """
        <style>
        section[data-testid="stSidebar"] {width: 280px !important;}
        div.block-container {
            max-width: 900px;
            margin: auto;
            padding-top: 1.5rem;
            padding-bottom: 1.5rem;
        }
        [data-testid="stMetricValue"] {
            font-size: 1.2rem;
            font-weight: 600;
        }
        [data-testid="stDataFrame"] {
            font-size: 0.9rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

# ==========================================================
# FINNHUB DATA (with cache + throttle guard)
# ==========================================================

@st.cache_data(ttl=60, show_spinner=False)
def _cached_finnhub(symbol: str, resolution="5", lookback_minutes=390, key_for_cache=""):
    """
    Internal helper that actually calls Finnhub.
    We wrap this in @st.cache_data with ttl=60 seconds.
    key_for_cache is just FINNHUB_KEY to bust cache if key changes.
    """
    if not FINNHUB_KEY:
        return None, "No FINNHUB_KEY in secrets"

    end_ts = int(datetime.now().timestamp())
    start_ts = end_ts - (lookback_minutes * 60)

    url = (
        "https://finnhub.io/api/v1/stock/candle"
        f"?symbol={symbol.upper()}"
        f"&resolution={resolution}"
        f"&from={start_ts}"
        f"&to={end_ts}"
        f"&token={FINNHUB_KEY}"
    )

    try:
        r = requests.get(url, timeout=10)
    except Exception as e:
        return None, f"Request error: {e}"

    # 403 = throttled or market closed weirdness on free tier
    if r.status_code == 403:
        return None, "Finnhub 403 throttle"

    if r.status_code != 200:
        return None, f"Finnhub HTTP {r.status_code}"

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

    if df is None or df.empty:
        return None, "Empty data"

    return df, None


def get_intraday_5m(symbol: str):
    """
    Public function used by dashboard.
    Returns (df, source_name, err_msg)

    Tries Finnhub first.
    If Finnhub is throttled (403), we just say "throttled"
    and return None for df so UI can show a friendly message.
    """
    df, err = _cached_finnhub(
        symbol,
        resolution="5",
        lookback_minutes=390,
        key_for_cache=FINNHUB_KEY or "nokey"
    )

    if df is not None and err is None:
        return df, "Finnhub", None

    # Finnhub failed / throttled
    return None, "Finnhub (throttled/off)", err

# ==========================================================
# RANGE LOGIC
# ==========================================================

def calc_levels(df_5m: pd.DataFrame, atr_lookback=14, cushion_frac=0.25):
    """
    - Opening high/low from first ~30 mins (first 6x 5m candles)
    - ATR-ish estimate from last atr_lookback candles
    - high_fence / low_fence based on cushion from ATR
    """
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
    """
    BREAKOUT   = last_price >  high_fence
    BREAKDOWN  = last_price <  low_fence
    RANGE_HELD = in between
    """
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
    Get Alpaca account info (paper).
    Returns (acct_json, err)
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
        return None, f"Alpaca req error: {e}"

    if r.status_code == 200:
        return r.json(), None

    return None, f"{r.status_code}: {r.text}"

def alpaca_market_order(symbol, qty, side):
    """
    Place market BUY/SELL in Alpaca paper.
    side should be 'buy' or 'sell'.
    """
    if not (ALPACA_KEY and ALPACA_SECRET and ALPACA_BASE_URL):
        return None, "Missing Alpaca creds"

    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json",
    }

    payload = {
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
            json=payload,
            timeout=10
        )
        return r, None
    except Exception as e:
        return None, f"Order error: {e}"

# ==========================================================
# BEGINNER HELPER (watchlist scanner)
# ==========================================================

def beginner_scan():
    """
    Scan common tickers and show who's doing what.
    Uses same fences logic.
    """
    watchlist = ["SPY", "NVDA", "AAPL", "TSLA", "AMZN", "AMD", "QQQ"]

    rows = []
    for sym in watchlist:
        df_sym, _, err_sym = get_intraday_5m(sym)
        if df_sym is None or err_sym:
            continue

        levels = calc_levels(df_sym)
        if not levels:
            continue

        mode = classify_mode(
            levels["last_price"],
            levels["high_fence"],
            levels["low_fence"],
        )

        rows.append({
            "Symbol": sym,
            "Last": round(levels["last_price"], 2),
            "ATR": round(levels["atr"], 2),
            "Mode": mode,
        })

    if len(rows) == 0:
        return pd.DataFrame([{
            "Symbol": "N/A",
            "Last": "",
            "ATR": "",
            "Mode": "No data / throttled?"
        }])

    priority = {"BREAKOUT": 0, "BREAKDOWN": 1, "RANGE_HELD": 2}
    rows.sort(key=lambda r: priority.get(r["Mode"], 99))

    return pd.DataFrame(rows)

# ==========================================================
# TRADE LOGGING
# ==========================================================

def log_trade(user, message, pl=None):
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": message,
    }
    if pl is not None:
        entry["pl"] = pl
    st.session_state.trade_log[user].append(entry)

# ==========================================================
# LOGIN SCREEN
# ==========================================================

def show_login():
    st.title("🔐 Redeyebatt Range Trader Login")

    # username / pin inputs (we'll pretty this later)
    col1, col2 = st.columns(2)
    with col1:
        username = st.text_input("Username").strip().lower()
    with col2:
        pin = st.text_input("PIN", type="password")

    login_btn = st.button("Log In")

    if login_btn:
        if username in USER_PINS and pin == USER_PINS[username]:
            st.session_state.logged_in = True
            st.session_state.user = username
            st.success(f"Welcome, {USER_THEMES[username]['label']} ✅")
            st.rerun()
        else:
            st.error("Invalid login")

# ==========================================================
# DASHBOARD (AFTER LOGIN)
# ==========================================================

def show_dashboard():
    user = st.session_state.user

    # theme colors for background/text
    theme = apply_user_theme(user)

    # tighten layout so it doesn't stretch edge-to-edge
    inject_dashboard_css()

    st.markdown(f"### Welcome, {theme['label']}")

    # SIDEBAR CONTROLS
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

    # LIVE DATA
    df_live, source_name, data_err = get_intraday_5m(symbol)
    stats = calc_levels(df_live) if df_live is not None else None

    acct, acct_err = alpaca_account()

    # ACCOUNT ROW
    colA, colB, colC = st.columns(3)
    if acct and not acct_err:
        # show buying power / cash
        try:
            colA.metric("Buying Power", f"${float(acct.get('buying_power',0)):,.2f}")
            colB.metric("Cash", f"${float(acct.get('cash',0)):,.2f}")
            colC.success("Alpaca Connected ✅")
        except Exception:
            colA.write("Alpaca connected")
            colB.write("Account read ok")
            colC.write("")
    else:
        colA.write("Alpaca not connected")
        colB.write(acct_err if acct_err else "")
        colC.write("")

    # RANGE / MODE DISPLAY
    if stats:
        mode = classify_mode(
            stats["last_price"],
            stats["high_fence"],
            stats["low_fence"],
        )

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("ATR-ish", f"{stats['atr']:.2f}")
        m2.metric("High Fence", f"{stats['high_fence']:.2f}")
        m3.metric("Low Fence", f"{stats['low_fence']:.2f}")
        m4.metric("Last Price", f"{stats['last_price']:.2f}")
        m5.metric("Data Source", source_name if source_name else "—")

        st.write(f"**Market Mode:** {mode}")

        # AUTO TRADE EXECUTION
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
        # friendly message instead of blowing up
        st.error(f"Data feed unavailable: {data_err if data_err else 'No data returned'}")

    # MANUAL PAPER TRADE CONTROLS
    st.subheader("Trade Controls (Paper)")
    col_buy, col_sell = st.columns(2)

    if col_buy.button("BUY Market (Paper)"):
        r, err = alpaca_market_order(symbol, qty, "buy")
        if r is not None and r.status_code in (200, 201):
            col_buy.success("Buy sent ✅")
            log_trade(user, f"MANUAL BUY {qty} {symbol}")
        else:
            col_buy.error(f"Buy failed: {err if err else r.text if r is not None else 'no response'}")

    if col_sell.button("SELL Market (Paper)"):
        r, err = alpaca_market_order(symbol, qty, "sell")
        if r is not None and r.status_code in (200, 201):
            col_sell.success("Sell sent ✅")
            log_trade(user, f"MANUAL SELL {qty} {symbol}")
        else:
            col_sell.error(f"Sell failed: {err if err else r.text if r is not None else 'no response'}")

    # TRADE LOG
    st.subheader("Session Log")
    if len(st.session_state.trade_log[user]) == 0:
        st.write("No trades yet.")
    else:
        st.dataframe(
            pd.DataFrame(st.session_state.trade_log[user]),
            use_container_width=True
        )

    # BEGINNER HELPER
    st.subheader("🔍 Beginner Helper: Stocks to Watch")
    st.caption("BREAKOUT = screaming up. BREAKDOWN = flushing. RANGE_HELD = chop / sell premium.")
    if st.button("Scan Watchlist"):
        recs = beginner_scan()
        st.dataframe(recs, use_container_width=True)
    st.caption("Goal: stop gambling. Only touch tickers actually moving.")

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
