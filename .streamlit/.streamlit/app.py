import os
import time
import pandas as pd
import streamlit as st
import engine as E

st.set_page_config(page_title="Indian Market Live Desk", page_icon="📈", layout="wide")

@st.cache_resource
def boot():
    # One background scanner per server process, shared by every visitor. Settings come from Streamlit "Secrets" (owner only).
    cfg = {"market": "nse"}
    try: cfg.update(dict(st.secrets.get("config", {})))
    except Exception: pass
    for k in ("GITHUB_TOKEN", "GIST_ID"):
        try:
            if k in st.secrets: os.environ[k] = str(st.secrets[k])
        except Exception: pass
    return E.start(cfg)
A = boot()

with st.sidebar:
    st.header("How this works")
    st.markdown(f"""Scans the Nifty 500 in the background and lists only setups that pass the filters.

- Intraday target: **{A.target_intraday}%** on capital at **{A.lev_intraday:g}x**
- Swing target: **{A.target_swing}%** price move, 3 sessions
- Minimum probability: **{A.min_prob:g}%**

Probability = 50% scenario simulation + 50% how often this exact setup worked in the stock's own past (when it has at least 8 past cases).

The simulation replays the stock's own past price moves (10,000 paths) under 4 scenarios, tilted by news, quarterly results and balance sheet, market trend, sector and the stock's own 6-month trend.""")
    st.caption("Research tool, not investment advice. Estimates can be wrong. It never places orders.")

st.title("📈 Indian Market Live Desk")

def table(rows, closed=False):
    if not rows:
        st.info("Nothing here right now."); return
    d = pd.DataFrame([{
        "Stock": x["sym"], "Action": x["side"], "Entry": x["entry"], "Target": x["target"], "Stop-loss": x["stop"], "R:R": x["rr"],
        "Probability %": round(x["prob"] * 100), "Simulation %": round(x["sim"] * 100),
        "History %": None if x["bt"] is None else round(x["bt"] * 100), "Past setups": x["bt_n"],
        "Result" if closed else "Live": x["status"] if closed else x["last"], "P/L % on capital": x["pnl"],
        "Opened": time.strftime("%d %b %H:%M", time.localtime(x["created"])),
        "Results & balance sheet": (x.get("fund") or {}).get("fsum") or "–",
        "Context tilt": x.get("tilt"),
        "Why": x["why"] + (" | News: " + x["headline"] if x.get("headline") else "")} for x in rows])
    st.dataframe(d, hide_index=True, width="stretch", column_config={
        "Probability %": st.column_config.ProgressColumn("Probability %", min_value=0, max_value=100, format="%d%%"),
        "Why": st.column_config.TextColumn(width="large")})

@st.fragment(run_every=15)
def live():
    S = E.ST; s = S.get("status", {})
    cols = st.columns(max(1, len(S.get("indices", [])) or 1))
    for c, i in zip(cols, S.get("indices", [])):
        c.metric(i["name"], f"{i['price']:,.2f}", f"{i['chg']:+.2f}%")
    if s.get("last_scan"):
        st.caption(f"Last scan {time.strftime('%d %b %H:%M:%S', time.localtime(s['last_scan']))} · {s['scanned']} of {s['universe']} stocks analysed · "
                   f"market {'OPEN' if s['market_open'] else 'closed'} · {s['regime']}")
        tot = s.get("fetch_ok", 0) + s.get("fetch_fail", 0)
        if tot > 20 and s.get("fetch_fail", 0) > 0.5 * tot:
            st.error("Yahoo Finance is rejecting most requests from this server, so data may be stale. The dashboard keeps showing the last good state.")
    else:
        st.warning("First scan in progress (it analyses the whole universe; this can take a few minutes)…")
    cl = S["closed"]; w = sum(x["status"] == "TARGET HIT" for x in cl); l = sum(x["status"] == "STOP HIT" for x in cl)
    m = st.columns(4)
    m[0].metric("Open signals", len(S["open"])); m[1].metric("Targets hit", w); m[2].metric("Stops hit", l)
    m[3].metric("Hit rate", f"{100*w/(w+l):.0f}%" if w + l else "–")
    t1, t2, t3, t5, t4 = st.tabs(["Intraday", "Swing (2–3 days)", "Market movers", "News & insights", "Closed trades"])
    for tab, kind in ((t1, "intraday"), (t2, "swing")):
        with tab:
            op = sorted([x for x in S["open"] if x["kind"] == kind], key=lambda x: -x["prob"])
            st.subheader("BUY list"); table([x for x in op if x["side"] == "BUY"])
            st.subheader("SELL list"); table([x for x in op if x["side"] == "SELL"])
            with st.expander("Watchlist: setups that failed the probability / reward filters"):
                table(S["watch"].get(kind, []))
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
            with st.expander(f"{x['sym']} · {x['side']} · {x['kind']} · {round(x['prob']*100)}% probability"):
                st.write("**Latest headline:** " + (x.get("headline") or "none in the last 3 days") + f"  (sentiment score {x['news']:+d})")
                st.write("**Past-trend context:** " + x["why"])
                if x.get("scen"):
                    st.write("**Simulation under different conditions** (context tilt %+.2f; + = bullish for price)" % x.get("tilt", 0))
                    st.dataframe(pd.DataFrame([{"Scenario": n, "Chance of hitting target %": round(p * 100, 1), "Weight %": round(w * 100)} for n, p, w in x["scen"]]), hide_index=True, width="stretch")
                    st.caption("Inputs (-1 bearish to +1 bullish): " + ", ".join("%s %+.2f" % (k, v) for k, v in x.get("ctx", {}).items()))
                if f.get("fsum"): st.write("**Latest quarterly results & balance sheet (Screener, quarter %s):** %s" % (f.get("qlast", "?"), f["fsum"]))
                if f.get("results_soon"): st.warning("A new quarterly result may be announced during this trade. Results days can gap the price either way.")
                if f.get("ratios"): st.write("**Key ratios (Screener):** " + " · ".join(f"{k}: {v}" for k, v in f["ratios"].items()))
                c1, c2 = st.columns(2)
                c1.write("**Pros (Screener)**"); [c1.write("• " + p) for p in f.get("pros", [])] or c1.write("–")
                c2.write("**Cons (Screener)**"); [c2.write("• " + p) for p in f.get("cons", [])] or c2.write("–")
    with t4:
        table(cl[:100], closed=True)
live()
