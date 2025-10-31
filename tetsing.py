# app.py
import csv, io, json, re, ssl, urllib.request
from urllib.error import URLError, HTTPError
from datetime import date, timedelta

import streamlit as st
import pandas as pd

# ---------- Constants ----------
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
API_CANDIDATES = [
    "https://data-asg.goldprice.org/dbXRates/USD",
    "https://data-asg.goldprice.org/dbXRates/XAU",
]
WIDGET_JS = "http://charts.goldprice.org/gold-price.js"  # fallback
STOOQ_XAUUSD_CSV = "https://stooq.com/q/d/l/?s=xauusd&i=d"
TROY_OUNCE_IN_GRAMS = 31.1034768

# ---------- Network helpers ----------
def _ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    return ctx

def fetch(url, timeout=12, referer=None):
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return r.read().decode("utf-8", "replace"), r.headers.get("Content-Type","")

# ---------- Pricing fetchers ----------
@st.cache_data(show_spinner=False, ttl=300)
def get_spot_usd_per_oz():
    # Try JSON endpoints
    for url in API_CANDIDATES:
        try:
            text, _ = fetch(url, referer="https://www.goldprice.org/")
            obj = json.loads(text)
            if isinstance(obj, dict):
                if "items" in obj and obj["items"]:
                    item = obj["items"][0]
                    for k in ("xauPrice","price","ask","lastPrice"):
                        if k in item and isinstance(item[k], (int,float)):
                            return float(item[k])
                for k in ("xauPrice","price","ask","lastPrice"):
                    if k in obj and isinstance(obj[k], (int,float)):
                        return float(obj[k])
        except Exception:
            continue
    # Fallback: scrape widget JS
    try:
        js_text, _ = fetch(WIDGET_JS, referer="https://www.goldprice.org/")
        for pat in (
            r'(?i)\bUSD[^0-9]*([\d]{1,3}(?:[,]\d{3})*(?:\.\d+)?)\b',
            r'(?i)"?xauPrice"?\s*[:=]\s*"?([\d]{1,3}(?:[,]\d{3})*(?:\.\d+)?)"?',
            r'(?i)"?price"?\s*[:=]\s*"?([\d]{1,3}(?:[,]\d{3})*(?:\.\d+)?)"?',
            r'(?i)"?lastPrice"?\s*[:=]\s*"?([\d]{1,3}(?:[,]\d{3})*(?:\.\d+)?)"?',
            r'(?i)"?ask"?\s*[:=]\s*"?([\d]{1,3}(?:[,]\d{3})*(?:\.\d+)?)"?',
        ):
            m = re.search(pat, js_text)
            if m:
                return float(m.group(1).replace(",", ""))
    except Exception:
        pass
    return None

@st.cache_data(show_spinner=False, ttl=3600)
def get_stooq_series():
    """Return list[(date, closeUSDperOz)] sorted asc."""
    try:
        text, _ = fetch(STOOQ_XAUUSD_CSV, referer="https://stooq.com/")
        out = []
        rdr = csv.reader(io.StringIO(text))
        _ = next(rdr, None)  # header
        for row in rdr:
            if len(row) < 5: continue
            d_s, close_s = row[0].strip(), row[4].strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", d_s): continue
            try:
                y,m,d = map(int, d_s.split("-"))
                out.append((date(y,m,d), float(close_s)))
            except Exception:
                continue
        out.sort(key=lambda t: t[0])
        return out
    except Exception:
        return []

# ---------- Helpers ----------
def years_ago_date(n:int):
    today = date.today()
    y = today.year - n
    try:
        return date(y, today.month, today.day)
    except ValueError:
        # clamp to month end (handles Feb 29)
        if today.month == 12: first_next = date(y+1,1,1)
        else: first_next = date(y, today.month+1, 1)
        return first_next - timedelta(days=1)

def closest_price(series, target):
    if not series: return None
    best, best_delta = None, None
    for d, v in series:
        delta = abs((d - target).days)
        if best is None or delta < best_delta:
            best, best_delta = (d, v), delta
    return best

# ---------- UI ----------
st.set_page_config(page_title="Gold Needed — Then vs Today", page_icon="🪙", layout="centered")
st.title("🪙 Gold Needed — Then vs Today")
st.caption("Visualize the purchase item price in grams of gold at the current market rate, with an option to select and compare prices from previous years .")

col1, col2 = st.columns(2)
with col1:
    purchase_price = st.number_input("Enter Item price in (USD)", value=1000.0, min_value=0.0, step=10.0, format="%.2f")
with col2:
    years_ago = st.number_input("Years ago for Comparision", value=2, min_value=0, step=1, help="Compare to ~this many years back")

if st.button("Calculate", type="primary"):
    spot = get_spot_usd_per_oz()
    if spot is None or spot <= 0:
        st.error("Could not load current spot price. Try again or check network.")
        st.stop()

    # Today
    spot_per_g = spot / TROY_OUNCE_IN_GRAMS
    ounces_now = purchase_price / spot
    grams_now  = purchase_price / spot_per_g

    # Historical
    target = years_ago_date(int(years_ago))
    series = get_stooq_series()
    hist = closest_price(series, target)
    if not hist:
        st.warning("Historical price unavailable.")
        st.stop()

    hist_date, hist_price = hist
    hist_per_g = hist_price / TROY_OUNCE_IN_GRAMS
    ounces_then = purchase_price / hist_price
    grams_then  = purchase_price / hist_per_g

    # --- Metrics ---
    m1, m2, m3 = st.columns(3)
    m1.metric("Spot today (USD/oz)", f"{spot:,.2f}")
    m2.metric("Closest historical date", hist_date.isoformat(), f"~{int(years_ago)}y")
    change_pct = (spot - hist_price) / hist_price * 100 if hist_price else float("nan")
    m3.metric("Hist. price (USD/oz)", f"{hist_price:,.2f}", f"{change_pct:+.1f}%")

    st.divider()

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Today")
        st.write(f"**Ounces needed:** {ounces_now:.6f}")
        st.write(f"**Grams needed:**  {grams_now:.2f}")
    with c2:
        st.subheader(f"~{int(years_ago)} years ago")
        st.write(f"**Ounces needed:** {ounces_then:.6f}")
        st.write(f"**Grams needed:**  {grams_then:.2f}")

    st.divider()
    st.subheader("Comparison chart")

    # --- Altair grouped bar chart (preferred) ---
    try:
        import altair as alt

        df = pd.DataFrame({
            "Measure": [ "Grams", "Grams"],
            "When":    ["Then", "Today"],
            "Value":   [grams_then, grams_now],
        })

        base = alt.Chart(df).encode(
            x=alt.X("Measure:N", axis=alt.Axis(title=None)),
            y=alt.Y("Value:Q", axis=alt.Axis(title="Amount")),
            color=alt.Color("When:N", scale=alt.Scale(range=["#6aa9ff", "#f7a35c"]))
        )

        bars = base.mark_bar(size=35).encode(xOffset="When:N")
        labels = base.mark_text(dy=-5, color="#222").encode(
            xOffset="When:N",
            text=alt.Text("Value:Q", format=".3f")
        )

        chart = (bars + labels).properties(title="Gold Needed: Then vs Today", height=260)
        st.altair_chart(chart, use_container_width=True)

    except Exception as e:
        # --- Matplotlib fallback ---
        st.info("Altair rendering failed or not installed; falling back to matplotlib.")
        try:
            import matplotlib.pyplot as plt
            import numpy as np
            labels = ["Grams"]
            then_vals = [ grams_then]
            now_vals  = [grams_now]
            x = np.arange(len(labels)); w = 0.35
            fig, ax = plt.subplots(figsize=(7, 4))
            b1 = ax.bar(x - w/2, then_vals, w, label="Then", color="#6aa9ff")
            b2 = ax.bar(x + w/2, now_vals,  w, label="Today", color="#f7a35c")
            for bars in (b1, b2):
                for bar in bars:
                    h = bar.get_height()
                    ax.text(bar.get_x() + bar.get_width()/2, h, f"{h:.3f}",
                            ha="center", va="bottom", fontsize=9)
            ax.set_title("Gold Needed: Then vs Today")
            ax.set_ylabel("Amount")
            ax.set_xticks(x, labels)
            ax.legend()
            ax.grid(axis="y", linestyle="--", alpha=0.3)
            st.pyplot(fig)
        except Exception as e2:
            st.error(f"Chart failed to render: {e2}")

    # --- Download results ---
    st.download_button(
        "Download results (CSV)",
        data=f"""field,value
spot_usd_per_oz,{spot:.2f}
hist_date,{hist_date.isoformat()}
hist_usd_per_oz,{hist_price:.2f}
ounces_then,{ounces_then:.6f}
grams_then,{grams_then:.2f}
ounces_now,{ounces_now:.6f}
grams_now,{grams_now:.2f}
""".strip(),
        file_name="gold_results.csv",
        mime="text/csv"
    )
