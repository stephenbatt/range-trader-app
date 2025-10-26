import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime, time as dtime, timedelta
import math

st.set_page_config(page_title="4PM Range Payout Simulator", page_icon="📈", layout="centered")

# -------------------------------------------------
# UTILITIES
# -------------------------------------------------

def get_intraday_history(ticker: str, period="1d", interval="1m"):
    """
    Get today's intraday 1-minute candles from yfinance.
    Returns a DataFrame with DatetimeIndex (timezone-aware sometimes).
    """
    try:
        data = yf.Ticker(ticker).history(period=period, interval=interval)
        if data.empty:
            return None
        # Normalize index to naive datetime (remove tz if present)
        data = data.tz_localize(None) if data.index.tz is not None else data
        return data
    except Exception:
        return None

def get_live_price(ticker: str):
    """
    Get most recent last trade price using 1m data for today.
    """
    data = get_intraday_history(ticker, period="1d", interval="1m")
    if data is None or data.empty:
        return None
    return float(data["Close"].iloc[-1])

def get_recent_daily_history(ticker: str, days=20):
    """
    Pull recent daily candles for ATR calc (1d interval).
    """
    try:
        data = yf.Ticker(ticker).history(period=f"{days}d", interval="1d")
        if data.empty:
            return None
        data = data.tz_localize(None) if data.index.tz is not None else data
        return data
    except Exception:
        return None

def calc_atr(df_daily: pd.DataFrame, lookback=14):
    """
    Average True Range:
    TR for each day = max(high-low, abs(high-prevClose), abs(low-prevClose))
    ATR = average of TR over N days
    """
    if df_daily is None or len(df_daily) < lookback + 1:
        return None

    highs = df_daily["High"].values
    lows = df_daily["Low"].values
    closes = df_daily["Close"].values

    trs = []
    for i in range(1, lookback + 1):
        high = highs[-i]
        low = lows[-i]
        prev_close = closes[-i - 1]
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        trs.append(tr)

    if len(trs) == 0:
        return None
    return sum(trs) / len(trs)

def get_opening_range(df_intraday, start_time=dtime(9,30), end_time=dtime(10,0)):
    """
    Slice the first 30 mins of the session (9:30 - 10:00 ET) and get its high & low.
    We'll assume your system clock is US market time / roughly Eastern feed.
    """
    if df_intraday is None or df_intraday.empty:
        return None, None

    # Filter rows where timestamp is in that window
    rng = df_intraday[(df_intraday.index.time >= start_time) & (df_intraday.index.time <= end_time)]
    if rng.empty:
        return None, None

    hi = float(rng["High"].max())
    lo = float(rng["Low"].min())
    return hi, lo

def suggested_range(ticker: str):
    """
    Build an auto range:
    - step 1: opening high/low from 9:30-10:00
    - step 2: add cushion based on ATR and ticker behavior
    returns (high_level, low_level) or (None, None) if can't compute
    """
    intra = get_intraday_history(ticker, period="1d", interval="1m")
    day_hi, day_lo = get_opening_range(intra)

    daily = get_recent_daily_history(ticker, days=20)
    atr = calc_atr(daily, lookback=14)

    if day_hi is None or day_lo is None:
        return None, None, None, None
    if atr is None:
        return None, None, day_hi, day_lo

    # Vol cushion:
    # SPY-like tickers: cushion ~ 0.25 * ATR
    # TSLA-like monsters: might need more, but we'll keep the same for now.
    cushion = 0.25 * atr

    auto_high = day_hi + cushion
    auto_low = day_lo - cushion

    return auto_high, auto_low, day_hi, day_lo

# -------------------------------------------------
# MULTI-USER ACCOUNT STATE
# -------------------------------------------------

# We keep per-user data in a dict keyed by username inside session_state.
# Each user profile has:
# - cash_total (paper running P/L)
# - log (list of past sessions)
# - active day session details (tracking, symbol, etc.)

if "users" not in st.session_state:
    st.session_state.users = {}

if "current_user" not in st.session_state:
    st.session_state.current_user = None

def init_user(username: str):
    if username not in st.session_state.users:
        st.session_state.users[username] = {
            "cash_total": 0.0,  # running win/loss total
            "log": [],
            "session": {
                "tracking": False,
                "symbol": "SPY",
                "high_level": None,
                "low_level": None,
                "start_price": None,
                "start_time": None,
                "last_price": None,
                "range_broken": False
            }
        }

def get_user_session():
    if st.session_state.current_user is None:
        return None
    return st.session_state.users[st.session_state.current_user]["session"]

def get_user_log():
    if st.session_state.current_user is None:
        return []
    return st.session_state.users[st.session_state.current_user]["log"]

def get_user_cash():
    if st.session_state.current_user is None:
        return 0.0
    return st.session_state.users[st.session_state.current_user]["cash_total"]

def set_user_cash(val: float):
    if st.session_state.current_user is not None:
        st.session_state.users[st.session_state.current_user]["cash_total"] = val

# -------------------------------------------------
# LOGIN / USER PICK
# -------------------------------------------------

st.sidebar.title("👤 User / Profile")

entered_user = st.sidebar.text_input("Enter your name (ex: DAD, SON):", value=st.session_state.current_user or "")
login_btn = st.sidebar.button("👈 Use This Profile")

if login_btn and entered_user.strip() != "":
    init_user(entered_user.strip())
    st.session_state.current_user = entered_user.strip()

if st.session_state.current_user is None:
    st.warning("Choose a user/profile on the left to begin (ex: type DAD, click Use This Profile).")
    st.stop()

# from here on we assume user active
session = get_user_session()

st.sidebar.markdown(f"**Active User:** {st.session_state.current_user}")
st.sidebar.metric("Running Total P/L ($)", f"{get_user_cash():.2f}")

# -------------------------------------------------
# MAIN UI
# -------------------------------------------------

st.title("📈 4PM Range Payout Simulator (Auto-Track All Day)")
st.write("Sell a high and a low, let price stay inside that fence until 4PM. If it stays inside → you get paid.")

st.markdown("---")

# If we have active session, show current symbol/high/low; else fall back defaults
current_symbol = session["symbol"] if session["symbol"] else "SPY"
current_high_level = session["high_level"] if session["high_level"] is not None else 0.0
current_low_level = session["low_level"] if session["low_level"] is not None else 0.0

colTop, colInfo = st.columns([2,1])
with colTop:
    symbol_input = st.text_input("Ticker (index or ETF):", value=current_symbol).upper()
with colInfo:
    st.write("SPX = main index\nSPY = ETF version\nQQQ = Nasdaq\nUse liquid stuff.")

colHL1, colHL2 = st.columns(2)
with colHL1:
    manual_high = st.number_input(
        "TOP of range (your call strike / ceiling)",
        min_value=0.0,
        step=0.5,
        value=float(current_high_level)
    )
with colHL2:
    manual_low = st.number_input(
        "BOTTOM of range (your put strike / floor)",
        min_value=0.0,
        step=0.5,
        value=float(current_low_level)
    )

st.caption("This fence is where you say: 'Market, stay in here, and I get paid.'")

# -------------------------------------------------
# AUTO-SET RANGE BUTTON
# -------------------------------------------------

st.subheader("🤖 Auto-Set Range (Opening Range + ATR Cushion)")
st.write("This grabs 9:30–10:00 high/low and adds cushion based on recent volatility, like a market maker.")

if st.button("🧠 Auto-Set Range For Me"):
    auto_hi, auto_lo, open_hi, open_lo = suggested_range(symbol_input)

    if auto_hi is None or auto_lo is None:
        st.error("Couldn't compute auto-range (market may be closed or data not ready).")
    else:
        session["symbol"] = symbol_input
        session["high_level"] = round(auto_hi, 2)
        session["low_level"] = round(auto_lo, 2)
        current_high_level = session["high_level"]
        current_low_level = session["low_level"]
        st.success(
            f"Auto range set for {symbol_input}.\n"
            f"OpeningRangeHigh={round(open_hi,2)}, OpeningRangeLow={round(open_lo,2)}\n"
            f"Your Fence: High={current_high_level}, Low={current_low_level}"
        )

st.markdown("---")

# -------------------------------------------------
# START DAY
# -------------------------------------------------

st.subheader("1️⃣ Start Tracking the Day (~10:00 AM ET)")
if not session["tracking"]:
    if st.button("🚀 Start Day (Lock Entry)"):
        # lock in fields from UI in case user didn't auto-set
        session["symbol"] = symbol_input
        session["high_level"] = float(manual_high)
        session["low_level"] = float(manual_low)

        live_now = get_live_price(session["symbol"])
        session["start_price"] = live_now
        session["last_price"] = live_now
        session["start_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        session["range_broken"] = False
        session["tracking"] = True

        st.success(
            f"Tracking started for {session['symbol']} at price {session['start_price']}.\n"
            f"High fence={session['high_level']} Low fence={session['low_level']}"
        )
else:
    st.info("Tracking is already running for today.")

# -------------------------------------------------
# LIVE MONITOR
# -------------------------------------------------

st.subheader("2️⃣ Live Range Monitor (Refresh During Day)")

if session["tracking"]:
    live_price = get_live_price(session["symbol"])
    if live_price is not None:
        session["last_price"] = live_price

        # check rules: did we ever break fence
        hi = session["high_level"]
        lo = session["low_level"]
        if hi and live_price > hi:
            session["range_broken"] = True
        if lo and live_price < lo:
            session["range_broken"] = True

    colp1, colp2, colp3 = st.columns(3)
    with colp1:
        st.metric("Current Price", f"${session['last_price']:.2f}" if session['last_price'] else "N/A")
    with colp2:
        st.metric("Range High", f"${session['high_level']:.2f}" if session['high_level'] else "N/A")
    with colp3:
        st.metric("Range Low", f"${session['low_level']:.2f}" if session['low_level'] else "N/A")

    if session["range_broken"]:
        st.error("❌ RANGE VIOLATED at some point today (would lose premium).")
    else:
        st.success("✅ Still INSIDE range so far (would keep premium).")

    st.caption("Click '🔄 Refresh Now' below to pull current price again. We can wire auto-refresh later.")

    if st.button("🔄 Refresh Now"):
        pass  # re-run updates session again when script reruns
else:
    st.warning("Not tracking right now. Hit 'Start Day' above first.")

# -------------------------------------------------
# SETTLE DAY (4PM)
# -------------------------------------------------

st.subheader("3️⃣ Settle Day at 4PM Close")

payout_size = st.number_input(
    "Target payout if fence holds ($ you 'collected' today)",
    min_value=0.0,
    step=25.0,
    value=250.0
)

if st.button("🏁 Settle Day (4PM Cash Out)"):
    if not session["tracking"]:
        st.error("You haven't started tracking today.")
    else:
        final_price = get_live_price(session["symbol"])
        broke = session["range_broken"]

        if broke:
            outcome = "LOSS"
            pl = 0.0 - payout_size
        else:
            outcome = "WIN"
            pl = payout_size

        # update running cash total for this user
        new_total = get_user_cash() + pl
        set_user_cash(new_total)

        # log the result for this user
        st.session_state.users[st.session_state.current_user]["log"].append({
            "Date": datetime.now().strftime("%Y-%m-%d"),
            "User": st.session_state.current_user,
            "Symbol": session["symbol"],
            "StartTime": session["start_time"],
            "StartPrice": session["start_price"],
            "EndPrice": final_price,
            "HighFence": session["high_level"],
            "LowFence": session["low_level"],
            "StayedInsideAllDay": (not broke),
            "Outcome": outcome,
            "DayPayout($)": pl,
            "RunningTotalAfter($)": new_total
        })

        # reset today's session
        session["tracking"] = False
        session["range_broken"] = False
        session["start_price"] = None
        session["start_time"] = None
        session["last_price"] = None

        st.success(f"Day settled: {outcome}. P/L = ${pl:.2f}. Running total for {st.session_state.current_user}: ${new_total:.2f}")

# -------------------------------------------------
# HISTORY / SCOREBOARD
# -------------------------------------------------

st.markdown("---")
st.subheader("📜 Scoreboard / History")

user_log = get_user_log()
if len(user_log) == 0:
    st.write("No settled days yet for this user.")
else:
    df = pd.DataFrame(user_log)
    st.dataframe(df)

    st.metric(
        f"Total P/L for {st.session_state.current_user}",
        f"${get_user_cash():,.2f}"
    )

st.caption(
    "This is all PAPER. You're simulating what market makers do with same-day SPX / XSP 'stay in the range until close and you get paid' income selling."
)
