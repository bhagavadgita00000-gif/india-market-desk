import os
import time
import pandas as pd
import streamlit as st
import engine as E

st.set_page_config(page_title="Indian Market Live Desk", page_icon="📈", layout="wide")
for k in ("GIST_TOKEN", "GITHUB_TOKEN", "GIST_ID"):          # read-only viewer: the scanner itself runs in GitHub Actions
    try:
        if k in st.secrets: os.environ[k] = str(st.secrets[k])
    except Exception: pass
C = {**E.DEFAULTS, **E.read_config()}
IST = 19800
def ist(ts, f="%d %b %H:%M"): return time.strftime(f, time.gmtime(ts + IST))
def month_of(x): return x.get("month") or ist(x.get("closed", 0), "%Y-%m")

@st.cache_data(ttl=20)
def get_state():
    try: return E.remote_state() or {"_error": "No saved state yet. The scanner has not run, or GIST_ID / GIST_TOKEN are missing in Streamlit Secrets."}
    except Exception as ex: return {"_error": "Could not read the saved state: " + repr(ex)}

cap, lv, lvs = C["capital"], C["lev_intraday"], C["lev_swing"]
with st.sidebar:
    st.header("Your setup")
    st.markdown(f"""- Capital **₹{cap:,.0f}**, intraday leverage **{lv:g}x** (exposure ₹{cap*lv:,.0f})
- Intraday target **₹{cap*C['target_intraday']/100:,.0f}** ({C['target_intraday']:g}% of capital) = price move **{C['target_intraday']/lv:.2f}%**
- Intraday stretch **₹{cap*C['stretch_intraday']/100:,.0f}** ({C['stretch_intraday']:g}%) = price move **{C['stretch_intraday']/lv:.2f}%**
- Swing target **{C['target_swing']:g}%** price move (stretch {C['stretch_swing']:g}%), 3 sessions
- Minimum chance to list: **{C['min_prob']:g}%**
- Closed early if the chance left drops below **{C['invalidate_prob']:g}%**""")
    st.markdown("""**How a trade is chosen:** 10,000 simulated paths from the stock's own past moves under 4 scenarios, tilted by news, quarterly results, balance sheet, market, sector and trend, blended with how often the same setup worked in that stock's past.""")
    st.caption("Research tool, not investment advice. Estimates can be wrong. Always place a stop-loss order with your broker. Brokerage and taxes are not included in ₹ figures.")

st.title("📈 Indian Market Live Desk")

def open_table(rows):
    if not rows:
        st.info("Nothing here right now."); return
    st.dataframe(pd.DataFrame([{
        "Stock": x["sym"], "Action": x["side"], "Qty": x.get("qty"), "Entry": x["entry"],
        "Target": x["target"], "Profit at target ₹": x.get("cap_profit"), "Stretch target": x.get("target2"), "Profit at stretch ₹": x.get("cap_profit2"),
        "Stop-loss": x["stop"], "Loss at stop ₹": x.get("cap_loss"), "R:R": x["rr"],
        "Chance of target %": round(x["prob"] * 100), "Chance of stretch %": None if x.get("p2") is None else round(x["p2"] * 100),
        "Live": x["last"], "P/L now ₹": x.get("pnl_rs", 0), "Opened (IST)": ist(x["created"]),
        "Why": x["why"] + (" | News: " + x["headline"] if x.get("headline") else "")} for x in rows]),
        hide_index=True, width="stretch", column_config={
        "Chance of target %": st.column_config.ProgressColumn("Chance of target %", min_value=0, max_value=100, format="%d%%"),
        "Why": st.column_config.TextColumn(width="large")})

def closed_table(rows):
    if not rows:
        st.info("No closed trades in this period."); return None
    d = pd.DataFrame([{"Closed (IST)": ist(x.get("closed", 0)), "Stock": x["sym"], "Type": x["kind"], "Action": x["side"], "Qty": x.get("qty"),
                       "Entry": x["entry"], "Exit": x.get("exit"), "Result": x["status"], "Reason": x.get("reason", ""),
                       "P/L ₹": x.get("pnl_rs"), "P/L % on capital": x["pnl"]} for x in sorted(rows, key=lambda r: -r.get("closed", 0))])
    st.dataframe(d, hide_index=True, width="stretch", column_config={"Reason": st.column_config.TextColumn(width="large")})
    return d

@st.fragment(run_every=30)
def live():
    S = get_state()
    if "_error" in S:
        st.error(S["_error"]); return
    s = S.get("status", {}); age = time.time() - s.get("last_scan", 0)
    cols = st.columns(max(1, len(S.get("indices", [])) or 1))
    for c, i in zip(cols, S.get("indices", [])):
        c.metric(i["name"], f"{i['price']:,.2f}", f"{i['chg']:+.2f}%")
    if s.get("last_scan"):
        st.caption(f"Last scan {ist(s['last_scan'], '%d %b %H:%M:%S')} IST ({age/60:.0f} min ago) · {s['scanned']} of {s['universe']} stocks analysed · "
                   f"market {'OPEN' if s['market_open'] else 'closed'} · {s['regime']}")
        t = time.gmtime(time.time() + IST); mins = t.tm_hour * 60 + t.tm_min
        if t.tm_wday < 5 and 9 * 60 + 20 <= mins <= 15 * 60 + 30 and age > 1500:
            st.warning(f"The scanner has not reported for {age/60:.0f} minutes during market hours. GitHub may be delaying it; check the Actions tab on GitHub.")
        tot = s.get("fetch_ok", 0) + s.get("fetch_fail", 0)
        if tot > 20 and s.get("fetch_fail", 0) > 0.5 * tot:
            st.error("Yahoo Finance is rejecting most requests from the scanner, so data may be stale.")
    cl = S.get("closed", []); cur = ist(time.time(), "%Y-%m"); mc = [x for x in cl if month_of(x) == cur]
    w = sum(x["status"] == "TARGET HIT" for x in mc); l = sum(x["status"] == "STOP HIT" for x in mc)
    m = st.columns(4)
    m[0].metric("Open signals", len(S["open"])); m[1].metric("This month: targets / stops", f"{w} / {l}")
    m[2].metric("This month: closed trades", len(mc)); m[3].metric("This month: net P/L", f"₹{sum(x.get('pnl_rs') or 0 for x in mc):,.0f}")
    t1, t2, t3, t5, t4 = st.tabs(["Intraday", "Swing (2–3 days)", "Market movers", "News & insights", "Closed trades (monthly)"])
    for tab, kind in ((t1, "intraday"), (t2, "swing")):
        with tab:
            op = sorted([x for x in S["open"] if x["kind"] == kind], key=lambda x: -x["prob"])
            st.subheader("BUY list"); open_table([x for x in op if x["side"] == "BUY"])
            st.subheader("SELL list"); open_table([x for x in op if x["side"] == "SELL"])
            with st.expander("Watchlist: setups that failed the probability / reward filters"):
                open_table(S.get("watch", {}).get(kind, []))
    with t3:
        mv = S.get("movers")
        if mv:
            a, b = st.columns(2)
            a.subheader("Top gainers"); a.dataframe(pd.DataFrame(mv["up"]), hide_index=True, width="stretch")
            b.subheader("Top losers"); b.dataframe(pd.DataFrame(mv["down"]), hide_index=True, width="stretch")
    with t5:
        if not S["open"]: st.info("Insights appear for stocks on the BUY/SELL lists.")
        for x in sorted(S["open"], key=lambda x: -x["prob"]):
            f = x.get("fund") or {}
            with st.expander(f"{x['sym']} · {x['side']} · {x['kind']} · {round(x['prob']*100)}% chance"):
                st.write("**Latest headline:** " + (x.get("headline") or "none in the last 3 days") + f"  (sentiment score {x.get('news', 0):+d})")
                st.write("**Past-trend context:** " + x["why"])
                if x.get("scen"):
                    st.write("**Simulation under different conditions** (context tilt %+.2f; + = bullish for price)" % x.get("tilt", 0))
                    st.dataframe(pd.DataFrame([{"Scenario": n, "Chance of hitting target %": round(p * 100, 1), "Weight %": round(w_ * 100)} for n, p, w_ in x["scen"]]), hide_index=True, width="stretch")
                    st.caption("Inputs (-1 bearish to +1 bullish): " + ", ".join("%s %+.2f" % (k, v) for k, v in x.get("ctx", {}).items()))
                if f.get("fsum"): st.write("**Latest quarterly results & balance sheet (Screener, quarter %s):** %s" % (f.get("qlast", "?"), f["fsum"]))
                if f.get("results_soon"): st.warning("A new quarterly result may be announced during this trade. Results days can gap the price either way.")
                if f.get("ratios"): st.write("**Key ratios (Screener):** " + " · ".join(f"{k}: {v}" for k, v in f["ratios"].items()))
                c1, c2 = st.columns(2)
                c1.write("**Pros (Screener)**"); [c1.write("• " + p) for p in f.get("pros", [])] or c1.write("–")
                c2.write("**Cons (Screener)**"); [c2.write("• " + p) for p in f.get("cons", [])] or c2.write("–")
    with t4:
        months = sorted({month_of(x) for x in cl} | {cur}, reverse=True)
        sel = st.selectbox("Month", months, index=0)
        rows = [x for x in cl if month_of(x) == sel]
        a1, a2, a3, a4, a5 = st.columns(5)
        a1.metric("Closed trades", len(rows)); a2.metric("Targets hit", sum(x["status"] == "TARGET HIT" for x in rows))
        a3.metric("Stops hit", sum(x["status"] == "STOP HIT" for x in rows))
        a4.metric("Expired / invalidated", sum(x["status"] not in ("TARGET HIT", "STOP HIT") for x in rows))
        a5.metric("Net P/L", f"₹{sum(x.get('pnl_rs') or 0 for x in rows):,.0f}")
        d = closed_table(rows)
        if d is not None:
            st.download_button("Download this month as CSV", d.to_csv(index=False).encode(), file_name=f"closed_trades_{sel}.csv", mime="text/csv")
live()
