import streamlit as st
import requests

st.set_page_config(page_title="🔑 API Connection Test", layout="centered")

FINNHUB_KEY = st.secrets.get("FINNHUB_KEY")
POLYGON_KEY = st.secrets.get("POLYGON_KEY")
ALPACA_KEY = st.secrets.get("ALPACA_KEY")
ALPACA_SECRET = st.secrets.get("ALPACA_SECRET")

st.title("🔍 Testing API Connections")

# --- FINNHUB ---
try:
    r = requests.get(f"https://finnhub.io/api/v1/quote?symbol=SPY&token={FINNHUB_KEY}")
    if r.status_code == 200 and "c" in r.json():
        st.success("✅ Finnhub connected")
    else:
        st.error(f"❌ Finnhub failed: {r.text}")
except Exception as e:
    st.error(f"Finnhub error: {e}")

# --- POLYGON ---
try:
    r = requests.get(f"https://api.polygon.io/v2/aggs/ticker/SPY/prev?apiKey={POLYGON_KEY}")
    if r.status_code == 200 and "results" in r.json():
        st.success("✅ Polygon.io connected")
    else:
        st.error(f"❌ Polygon.io failed: {r.text}")
except Exception as e:
    st.error(f"Polygon.io error: {e}")

# --- ALPACA ---
try:
    headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    r = requests.get("https://paper-api.alpaca.markets/v2/account", headers=headers)
    if r.status_code == 200:
        st.success("✅ Alpaca connected")
    else:
        st.error(f"❌ Alpaca failed: {r.text}")
except Exception as e:
    st.error(f"Alpaca error: {e}")
