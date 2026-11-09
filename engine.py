#!/usr/bin/env python3
"""Scanner engine for the Streamlit dashboard. Research tool, not investment advice. Never places orders."""
import argparse, csv, html as _html, io, json, math, os, random, re, threading, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0"}
NIFTY50 = ("ADANIENT ADANIPORTS APOLLOHOSP ASIANPAINT AXISBANK BAJAJ-AUTO BAJFINANCE BAJAJFINSV BEL BPCL BHARTIARTL "
 "BRITANNIA CIPLA COALINDIA DRREDDY EICHERMOT ETERNAL GRASIM HCLTECH HDFCBANK HDFCLIFE HEROMOTOCO HINDALCO HINDUNILVR "
 "ICICIBANK ITC INDUSINDBK INFY JSWSTEEL KOTAKBANK LT M&M MARUTI NTPC NESTLEIND ONGC POWERGRID RELIANCE SBILIFE "
 "SHRIRAMFIN SBIN SUNPHARMA TCS TATACONSUM TATAMOTORS TATASTEEL TECHM TITAN TRENT ULTRACEMCO WIPRO").split()
SECT = {}
for _p in ("Banks:HDFCBANK ICICIBANK AXISBANK KOTAKBANK SBIN INDUSINDBK|Finance:BAJFINANCE BAJAJFINSV SHRIRAMFIN HDFCLIFE SBILIFE|"
 "IT:TCS INFY HCLTECH WIPRO TECHM|Auto:MARUTI M&M TATAMOTORS BAJAJ-AUTO EICHERMOT HEROMOTOCO|"
 "Energy:RELIANCE ONGC BPCL COALINDIA NTPC POWERGRID ADANIENT ADANIPORTS|Metals:TATASTEEL JSWSTEEL HINDALCO|"
 "Pharma:SUNPHARMA CIPLA DRREDDY APOLLOHOSP|FMCG:HINDUNILVR ITC NESTLEIND BRITANNIA TATACONSUM|"
 "Materials:ULTRACEMCO GRASIM ASIANPAINT|Consumer:TITAN TRENT ETERNAL BHARTIARTL|Industrials:LT BEL").split("|"):
    _k, _v = _p.split(":")
    for _s in _v.split(): SECT[_s] = _k
POS = set("beats surges jumps record upgrade profit growth wins order approval buyback rallies gains soars strong outperform dividend bonus".split())
NEG = set("falls plunges misses downgrade loss probe fraud raid penalty lawsuit ban resigns weak cuts slump default recall crash warns".split())
LOCK = threading.Lock(); A = None; INDEX = "^NSEI"; HEALTH = {"ok": 0, "fail": 0}
CTX = {"mkt": 0.0, "secr": {}, "trend": {}}; DAILY = {}
ST = {"open": [], "closed": [], "watch": {"intraday": [], "swing": []}, "status": {}}

def fetch(sym, interval, rng):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=%s&range=%s" % (urllib.parse.quote(sym), interval, rng)
    for i in range(3):
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15))["chart"]["result"][0]
            q = r["indicators"]["quote"][0]; bars = []
            for t, o, h, l, c, v in zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"], q["volume"]):
                if None not in (o, h, l, c): bars.append((t, o, h, l, c, v or 0))
            m = r["meta"]; HEALTH["ok"] += 1
            return bars, m.get("currentTradingPeriod", {}).get("regular", {}).get("end"), m.get("gmtoffset", 0)
        except Exception: time.sleep(1 + i)
    HEALTH["fail"] += 1
    return None, None, 0

def _score(title):
    w = set(re.sub(r"[^a-z ]", " ", title.lower()).split())
    return len(w & POS) - len(w & NEG)

def news(sym):
    """Headline sentiment from Yahoo Finance news + Google News RSS (last 3 days). Returns (score, top headline)."""
    sc, head = 0, ""
    try:
        u = "https://query2.finance.yahoo.com/v1/finance/search?q=%s&newsCount=8&quotesCount=0" % urllib.parse.quote(sym)
        for n in json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=10)).get("news", []):
            if time.time() - n.get("providerPublishTime", 0) > 3 * 86400: continue
            sc += _score(n.get("title", "")); head = head or n.get("title", "")
    except Exception: pass
    try:
        base = sym.split(".")[0]; q = '"%s" %s when:3d' % (base, "share NSE" if sym.endswith(".NS") else "stock")
        u = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en" % urllib.parse.quote(q)
        root = ET.fromstring(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=10).read())
        for it in root.findall("./channel/item")[:8]:
            t = it.findtext("title") or ""; sc += _score(t); head = head or t
    except Exception: pass
    return sc, head

_FUND = {}; _FLOCK = threading.Lock()
def _num(x):
    try: return float(re.sub(r"[,%\s]", "", x))
    except Exception: return None

def _strip(x): return _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x))).strip()

def _table(page, sid, title):
    """First data table after the section id (or heading text). Returns (column headers, {row label: [numbers]})."""
    i = page.find('id="%s"' % sid)
    if i < 0: i = page.find(title)
    m = re.search(r"<table.*?</table>", page[i:], re.S) if i >= 0 else None
    if not m: return [], {}
    heads, rows = [], {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S):
        cells = [_strip(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if not cells: continue
        if "<th" in tr and not heads: heads = cells[1:]
        else: rows[re.sub(r"\s*\+$", "", cells[0])] = [_num(c) for c in cells[1:]]
    return heads, rows

def _yoy(v, lag):
    if v and len(v) > lag and v[-1] is not None and v[-1 - lag] is not None and v[-1 - lag] > 0: return v[-1] / v[-1 - lag] - 1
    return None

def fund_metrics(qh, q, b):
    """Quarterly-results + balance-sheet health: summary text, score in [-1, 1] (None if unreadable), results-due-soon flag."""
    clip = lambda x: max(-1.0, min(1.0, x))
    sales = q.get("Sales") or q.get("Revenue"); gs = _yoy(sales, 4); gp = _yoy(q.get("Net Profit"), 4)
    opm = q.get("OPM %"); dm = opm[-1] - opm[-5] if opm and len(opm) > 4 and opm[-1] is not None and opm[-5] is not None else None
    fin = "Financing Profit" in q or "Interest" in q and not q.get("Sales")
    bor, res, eq = b.get("Borrowings"), b.get("Reserves"), b.get("Equity Capital")
    de = bor[-1] / (eq[-1] + res[-1]) if bor and res and eq and None not in (bor[-1], res[-1], eq[-1]) and eq[-1] + res[-1] > 0 else None
    rg = _yoy(res, 1); comp = []; txt = []
    if gs is not None: comp.append(clip(gs / 0.2)); txt.append("Sales %+.0f%% YoY" % (gs * 100))
    if gp is not None: comp.append(clip(gp / 0.3)); txt.append("Net profit %+.0f%% YoY" % (gp * 100))
    if dm is not None: comp.append(clip(dm / 5)); txt.append("OPM %.0f%% (%+.1fpp YoY)" % (opm[-1], dm))
    if de is not None and not fin: comp.append(1 if de < 0.3 else 0 if de < 1 else max(-1, -(de - 1) / 0.5)); txt.append("Debt/equity %.2f" % de)
    if rg is not None: comp.append(clip(rg / 0.15)); txt.append("Reserves %+.0f%% vs prior period" % (rg * 100))
    soon = False
    try: soon = (time.time() - datetime.strptime(qh[-1], "%b %Y").timestamp()) > 75 * 86400
    except Exception: pass
    return {"fsum": ", ".join(txt), "fscore": round(sum(comp) / len(comp), 2) if comp else None, "results_soon": soon,
            "qlast": qh[-1] if qh else ""}

def screener(sym):
    """screener.in public company page: key ratios, pros/cons, quarterly results + balance sheet. Cached 24h, 1 request per 2s."""
    base = sym.split(".")[0]
    if base in _FUND and time.time() - _FUND[base][0] < 86400: return _FUND[base][1]
    out = {}
    try:
        with _FLOCK:
            time.sleep(2)
            for suf in ("/consolidated/", "/"):
                try:
                    page = urllib.request.urlopen(urllib.request.Request("https://www.screener.in/company/%s%s" % (urllib.parse.quote(base), suf), headers=UA), timeout=15).read().decode("utf-8", "ignore")
                except Exception: page = ""
                if 'id="top-ratios"' in page: break
        blk = re.search(r'id="top-ratios"(.*?)</ul>', page, re.S)
        if blk:
            out["ratios"] = {_strip(a): _strip(b) for a, b in re.findall(r'<span class="name">(.*?)</span>\s*<span class="nowrap value">(.*?)</span>\s*</li>', blk.group(1), re.S)}
        for k in ("pros", "cons"):
            m = re.search(r'<div class="%s">(.*?)</div>' % k, page, re.S)
            out[k] = [_strip(x) for x in re.findall(r"<li[^>]*>(.*?)</li>", m.group(1), re.S)][:5] if m else []
        qh, q = _table(page, "quarters", "Quarterly Results"); _, b = _table(page, "balance-sheet", "Balance Sheet")
        if q or b: out.update(fund_metrics(qh, q, b))
    except Exception: out = {}
    _FUND[base] = (time.time() if out else time.time() - 82800, out)
    return out

def sma(x, n): return sum(x[-n:]) / n
def rsi(c, n=14):
    g = l = 0.0
    for i in range(1, n + 1): d = c[i] - c[i-1]; g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i-1]; g = (g*(n-1) + max(d, 0)) / n; l = (l*(n-1) + max(-d, 0)) / n
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)
def atr(b, n=14):
    tr = [max(b[i][2]-b[i][3], abs(b[i][2]-b[i-1][4]), abs(b[i][3]-b[i-1][4])) for i in range(1, len(b))]
    return sum(tr[-n:]) / len(tr[-n:])

def walk(bars, s, target, stop):  # 1 win, -1 loss, 0 neither; stop wins ties
    for x in bars:
        if s == 1:
            if x[3] <= stop: return -1
            if x[2] >= target: return 1
        else:
            if x[2] >= stop: return -1
            if x[3] <= target: return 1
    return 0

def swing_signal(b, s):
    if len(b) < 60: return None
    c = [x[4] for x in b]; v = [x[5] for x in b]; p = c[-1]
    if s * (p - sma(c, 50)) <= 0 or s * (p - sma(c, 20)) <= 0: return None
    prior = c[-21:-1]
    if (s == 1 and p < max(prior)) or (s == -1 and p > min(prior)): return None
    r = rsi(c) if s == 1 else 100 - rsi(c)
    if not 52 <= r <= 75: return None
    av = sum(v[-21:-1]) / 20
    if av <= 0 or v[-1] < 1.2 * av: return None
    d = min(max(1.5 * atr(b), 0.01 * p), 0.04 * p)
    return {"p": p, "dist": d, "rsi": round(r), "vol": round(v[-1] / av, 1)}

def intra_signal(S, s):
    if len(S) < 5: return None
    H = max(x[2] for x in S[:3]); L = min(x[3] for x in S[:3]); p = S[-1][4]
    lvl = H if s == 1 else L
    if s * (p - lvl) <= 0 or s * (p - lvl) / lvl > 0.006: return None
    tv = sum(x[5] for x in S)
    vw = sum((x[2]+x[3]+x[4]) / 3 * x[5] for x in S) / tv if tv else p
    if s * (p - vw) <= 0: return None
    av = sum(x[5] for x in S[:-1]) / (len(S) - 1)
    if av > 0 and S[-1][5] < av: return None
    mid = (H + L) / 2; a = atr(S, min(14, len(S) - 1))
    d = max(0.003 * p, min(s * (p - mid), 3 * a))
    return {"entry": p, "dist": d}

def ratios(b, day=None, n=500):
    out = []
    for i in range(max(1, len(b) - n), len(b)):
        if day and day(b[i]) != day(b[i-1]): continue
        pc = b[i-1][4]; out.append((b[i][2]/pc, b[i][3]/pc, b[i][4]/pc))
    if not out: return out
    g = math.exp(sum(math.log(r[2]) for r in out) / len(out))  # remove historical drift: neutral odds
    return [(h/g, l/g, c/g) for h, l, c in out]

SCEN = (("Context-tilted base case", 1.0, 1.0, 0.50), ("Neutral: no drift", 0.0, 1.0, 0.20),
        ("Adverse: tilt reversed, volatility +30%", -1.0, 1.3, 0.15), ("Favourable: tilt doubled, volatility -10%", 2.0, 0.9, 0.15))

def simulate(R, e, target, stop, s, steps, N, d=0.0, vm=1.0):
    """Monte Carlo from the stock's own past moves. d = extra drift per step, vm = volatility multiplier."""
    if d or vm != 1.0:
        R = [(math.exp(math.log(h) * vm + d), math.exp(math.log(l) * vm + d), math.exp(math.log(c) * vm + d)) for h, l, c in R]
    win = st = 0
    for _ in range(N):
        p = e
        for _ in range(steps):
            h, l, c = random.choice(R); hi, lo = p*h, p*l; p *= c
            if s == 1: sh, th = lo <= stop, hi >= target
            else: sh, th = hi >= stop, lo <= target
            if sh: st += 1; break
            if th: win += 1; break
    return win / N, st / N

def _clip(x): return max(-1.0, min(1.0, x))

def evaluate(kind, y, s, e, dist, tgt, R, steps, bt, lev, why, expires=None):
    if len(R) < 30 or dist <= 0: return None
    qty = int(A.capital * lev / e)                                   # shares you can buy with your capital x leverage
    if qty < 1: return None
    stop = e - s * dist; target = e * (1 + s * tgt); N = 2500
    p0, _ = simulate(R, e, target, stop, s, steps, N)            # quick neutral pass: skip hopeless setups before the slow lookups
    if p0 < 0.4 * A.min_prob / 100: return None
    nsc, head = news(y)
    fund = screener(y) if getattr(A, "screener", False) and y.endswith(".NS") else {}
    fs = fund.get("fscore"); k = SECT.get(y.split(".")[0], "-")
    wts = {"news": .25, "fund": .25, "mkt": .20, "sec": .15, "trend": .15} if kind == "swing" else {"news": .30, "fund": .10, "mkt": .25, "sec": .20, "trend": .15}
    vals = {"news": _clip(nsc / 3), "fund": fs, "mkt": CTX["mkt"], "sec": _clip(CTX["secr"].get(k, 0) / 0.05), "trend": CTX["trend"].get(y, 0)}
    use = {kk: v for kk, v in vals.items() if v is not None}
    tilt = sum(wts[kk] * v for kk, v in use.items()) / sum(wts[kk] for kk in use)   # + = bullish for price, - = bearish
    D = (0.002 if kind == "swing" else 0.00004) * tilt                                 # judgment-based drift per day / per 5-min bar
    scen = [(nm, *simulate(R, e, target, stop, s, steps, N, D * dm, vm), wt) for nm, dm, vm, wt in SCEN]
    ps = sum(w * p for _, p, _, w in scen); pst = sum(w * q for _, _, q, w in scen)
    w, n = bt
    p = 0.5 * ps + 0.5 * w / n if n >= 8 else ps
    rr = tgt * e / dist; ev = p * tgt * e - pst * dist
    tgt2 = (A.stretch_intraday / A.lev_intraday if kind == "intraday" else A.stretch_swing / A.lev_swing) / 100
    target2 = e * (1 + s * tgt2); p2, _ = simulate(R, e, target2, stop, s, steps, 1500, D, 1.0)   # chance of the bigger (stretch) target
    veto = s * nsc <= -2 or (kind == "swing" and fs is not None and s * fs < -0.6)
    ok = p >= A.min_prob / 100 and rr >= 1.5 and ev > 0 and not veto
    if fund.get("fsum"): why += " | Results/balance sheet: " + fund["fsum"]
    if fund.get("results_soon"): why += " | WARNING: new quarterly results may land inside this trade window"
    if fund and len(fund.get("cons", [])) >= len(fund.get("pros", [])) + 3 and s == 1: why += " | Screener lists many more cons than pros"
    return {"fund": fund, "id": "%s-%s-%d" % (kind, y, time.time()), "kind": kind, "ysym": y, "sym": y.split(".")[0],
            "side": "BUY" if s == 1 else "SELL", "entry": round(e, 2), "target": round(target, 2), "stop": round(stop, 2),
            "target2": round(target2, 2), "p2": round(p2, 3), "qty": qty, "cap_profit": round(qty * abs(target - e)),
            "cap_profit2": round(qty * abs(target2 - e)), "cap_loss": round(qty * dist),
            "rr": round(rr, 2), "prob": round(p, 3), "sim": round(ps, 3), "bt": round(w / n, 3) if n else None, "bt_n": n,
            "news": nsc, "headline": head, "why": why, "lev": lev, "created": time.time(), "expires": expires,
            "scen": [[nm, round(pw, 3), wt] for nm, pw, _, wt in scen], "tilt": round(tilt, 2),
            "ctx": {kk: round(v, 2) for kk, v in use.items()}, "status": "OPEN", "last": round(e, 2), "pnl": 0.0, "pnl_rs": 0, "ok": ok}

def ingest(kind, recs):
    recs = sorted([r for r in recs if r], key=lambda r: -r["prob"]); now = time.time(); new = []
    with LOCK:
        busy = {x["ysym"] for x in ST["open"]}
        cool = {x["ysym"] for x in ST["closed"] if x["kind"] == kind and now - x["closed"] < (86400 if kind == "intraday" else 3 * 86400)}
        room = A.max_open - sum(1 for x in ST["open"] if x["kind"] == kind)
        for r in recs:
            if r["ysym"] in busy or r["ysym"] in cool or not r["ok"] or room <= 0: continue
            ST["open"].append(r); busy.add(r["ysym"]); room -= 1; new.append(r)
        ST["watch"][kind] = [r for r in recs if r["ysym"] not in busy][:8]
    for r in new:                                                    # phone alert for every new signal
        buy = r["side"] == "BUY"
        notify("%s %s (%s)" % (r["side"], r["sym"], r["kind"]),
               "Entry %.2f | Target %.2f (+₹%s) | Stop-loss %.2f (-₹%s) | Qty %d | Chance %d%% | Stretch %.2f (+₹%s)%s" % (
                   r["entry"], r["target"], _inr(r["cap_profit"]), r["stop"], _inr(r["cap_loss"]), r["qty"], round(r["prob"] * 100),
                   r["target2"], _inr(r["cap_profit2"]), "" if buy else " | SELL = intraday short only"), 4, "chart_with_upwards_trend" if buy else "chart_with_downwards_trend")
        r["notified"] = True

def intra_job(y, tgt, lev, now):
    bars, end, off = fetch(y, "5m", "60d")
    if not bars or len(bars) < 200: return []
    day = lambda x: (x[0] + off) // 86400
    days = {}
    for x in bars: days.setdefault(day(x), []).append(x)
    keys = sorted(days); S = days[keys[-1]]
    if now - S[-1][0] > 900: return []
    steps = max(1, int(((end or S[-1][0] + 4500) - S[-1][0]) // 300)); R = ratios(bars, day); out = []
    for s in (1, -1):
        g = intra_signal(S, s)
        if not g: continue
        w = n = 0
        for k in keys[:-1]:
            D = days[k]
            for j in range(5, len(D)):
                gg = intra_signal(D[:j], s)
                if gg:
                    n += 1; w += walk(D[j:], s, gg["entry"] * (1 + s * tgt), gg["entry"] - s * gg["dist"]) == 1; break
        why = "Opening-range %s, above VWAP, volume pickup" % ("breakout" if s == 1 else "breakdown")
        out.append(evaluate("intraday", y, s, g["entry"], g["dist"], tgt, R, steps, (w, n), lev, why, end))
    return out

def scan_swing(data, now, pool):
    comp, live = {}, {}
    for y, (b, end, off) in data.items():
        if not b or len(b) < 80: continue
        part = end and (b[-1][0] + off) // 86400 == (now + off) // 86400 and now < end
        comp[y] = b[:-1] if part else b; live[y] = b[-1][4]
    ret = {y: c[-1][4] / c[-21][4] - 1 for y, c in comp.items()}
    sec = {}
    for y, r in ret.items(): sec.setdefault(SECT.get(y.split(".")[0], "-"), []).append(r)
    secr = {k: sum(v) / len(v) for k, v in sec.items() if k != "-"}
    ir = ret.get(INDEX, 0.0); tgt = A.target_swing / A.lev_swing / 100
    ic = comp.get(INDEX)
    CTX["mkt"] = 0.0 if not ic else (1.0 if ic[-1][4] > sma([x[4] for x in ic], 50) else -1.0)
    CTX["secr"] = secr; CTX["trend"] = {y: _clip((c[-1][4] / c[-127][4] - 1) / 0.3) for y, c in comp.items() if len(c) > 127}
    jobs = [(y, s, g) for y, c in comp.items() if y != INDEX for s in (1, -1)
            for g in [swing_signal(c, s)] if g and abs(live[y] / g["p"] - 1) < 0.015]
    def job(j):
        y, s, g = j; c = comp[y]; w = n = 0
        for i in range(60, len(c) - 3):
            gg = swing_signal(c[:i+1], s)
            if gg: n += 1; w += walk(c[i+1:i+4], s, gg["p"] * (1 + s * tgt), gg["p"] - s * gg["dist"]) == 1
        k = SECT.get(y.split(".")[0], "-"); cc = [x[4] for x in c]; yr = cc[-252:]
        trend = "6m %+.0f%%, at %d%% of 52w range" % ((cc[-1] / cc[-126] - 1) * 100, 100 * (cc[-1] - min(yr)) / max(1e-9, max(yr) - min(yr)))
        why = trend + " | RSI %d, volume %.1fx avg, 20-day %s; sector %s %s; vs index %+.1f%%" % (g["rsi"], g["vol"],
              "high" if s == 1 else "low", k, ("%+.1f%% (20d)" % (secr[k] * 100)) if k in secr else "n/a", (ret[y] - ir) * 100)
        return evaluate("swing", y, s, live[y], g["dist"], tgt, ratios(c), 3, (w, n), A.lev_swing, why)
    ingest("swing", list(pool.map(job, jobs)))
    return len(comp), ("Bullish (index above 50DMA)" if CTX["mkt"] > 0 else "Bearish (index below 50DMA)")

def _close(sig, status, px, ts, reason, s):
    sig.update(status=status, exit=round(px, 2), closed=ts, reason=reason, month=time.strftime("%Y-%m", time.gmtime(ts + A.tz)))
    sig["pnl"] = round(s * (px / sig["entry"] - 1) * 100 * sig["lev"], 2)
    sig["pnl_rs"] = round(sig.get("qty", 0) * s * (px - sig["entry"]))
    with LOCK:
        ST["open"] = [x for x in ST["open"] if x["id"] != sig["id"]]
        ST["closed"] = ([slim(sig)] + ST["closed"])[:3000]
    hit = status in ("TARGET HIT", "STOP HIT")
    notify("%s: %s %s₹%s" % (status, sig["sym"], "+" if sig["pnl_rs"] >= 0 else "-", _inr(abs(sig["pnl_rs"]))),
           "%s %s closed at %.2f (entry %.2f). %s%s" % (sig["side"], sig["sym"], px, sig["entry"], reason,
                                                       " Exit now if you are still holding." if status != "TARGET HIT" else ""),
           4 if hit else 3, "white_check_mark" if status == "TARGET HIT" else "x")

def update_open(now, pool):
    with LOCK: cur = list(ST["open"])
    if not cur: return
    for sig, (bars, end, off) in zip(cur, pool.map(lambda x: fetch(x["ysym"], "5m", "5d"), cur)):
        if not bars: continue
        s = 1 if sig["side"] == "BUY" else -1; out = None
        after = [x for x in bars if x[0] > sig["created"]]
        for x in after:
            sh = x[3] <= sig["stop"] if s == 1 else x[2] >= sig["stop"]
            th = x[2] >= sig["target"] if s == 1 else x[3] <= sig["target"]
            if sh: out = ("STOP HIT", sig["stop"], x[0], "Stop-loss price was touched."); break
            if th: out = ("TARGET HIT", sig["target"], x[0], "Target price was reached."); break
        last = bars[-1][4]
        cd = (sig["created"] + off) // 86400; da = {(x[0] + off) // 86400 for x in after if (x[0] + off) // 86400 > cd}
        if not out:
            if sig["kind"] == "intraday" and sig.get("expires") and now > sig["expires"]:
                out = ("EXPIRED (EOD)", last, now, "Market closed before the target or stop-loss was hit.")
            elif sig["kind"] == "swing" and len(da) >= 3 and (len(da) > 3 or now - bars[-1][0] > 1800):
                out = ("EXPIRED (3 days)", last, now, "The 3-session window ended before the target or stop-loss was hit.")
        if not out and A.invalidate_prob > 0:                          # close early if the target has become unrealistic
            R, left = None, 0
            if sig["kind"] == "intraday" and sig.get("expires") and now - sig["created"] > 1800:
                left = int((sig["expires"] - bars[-1][0]) // 300); R = ratios(bars, lambda x: (x[0] + off) // 86400)
            elif sig["kind"] == "swing" and sig["ysym"] in DAILY and DAILY[sig["ysym"]][0] and len(da) >= 1:
                left = 3 - len(da); R = ratios(DAILY[sig["ysym"]][0])
            if R and len(R) >= 30 and left >= 1:
                p, _ = simulate(R, last, sig["target"], sig["stop"], s, left, 1500)
                if p < A.invalidate_prob / 100:
                    out = ("INVALIDATED", last, now, "Target now unlikely: only about %.0f%% chance left in the remaining time." % (p * 100))
        sig["last"] = round(last, 2)
        if out: _close(sig, out[0], out[1], out[2], out[3], s)
        else:
            sig["pnl"] = round(s * (last / sig["entry"] - 1) * 100 * sig["lev"], 2); sig["pnl_rs"] = round(sig.get("qty", 0) * s * (last - sig["entry"]))

def universe():
    if A.universe:
        return [l.strip() for l in open(A.universe) if l.strip()]
    if A.market == "us":
        t = urllib.request.urlopen(urllib.request.Request("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", headers=UA), timeout=30).read().decode()
        rows = [r.split("|") for r in t.splitlines()[1:] if "|" in r and not r.startswith("File")]
        return [r[0] for r in rows if r[3] == "N" and r[6] == "N" and r[0].isalpha()][:A.limit]
    try:
        t = urllib.request.urlopen(urllib.request.Request("https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv", headers=UA), timeout=30).read().decode()
        return [r["Symbol"] + ".NS" for r in csv.DictReader(io.StringIO(t))][:A.limit]
    except Exception:
        print("Nifty 500 list unavailable, using Nifty 50"); return [s + ".NS" for s in NIFTY50]

KEEP = ("id", "kind", "ysym", "sym", "side", "entry", "target", "target2", "stop", "exit", "status", "reason", "pnl", "pnl_rs",
        "qty", "lev", "prob", "created", "closed", "month")
def slim(sig): return {k: sig[k] for k in KEEP if k in sig}

DEFAULTS = dict(market="nse", universe=None, limit=500, interval=300, workers=32, capital=30000.0, target_swing=6.0, stretch_swing=10.0,
                target_intraday=6.0, stretch_intraday=10.0, lev_swing=1.0, lev_intraday=5.0, min_prob=25.0, max_open=8, topk=120,
                min_turnover=2e7, invalidate_prob=5.0, tz=19800, once=False, screener=True)

def _inr(x): return "{:,.0f}".format(x)

def notify(title, msg, prio=3, tag="bell"):
    """Phone push via ntfy.sh. Needs the NTFY_TOPIC environment variable; silently does nothing without it."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic: return
    try:
        body = json.dumps({"topic": topic, "title": title, "message": msg, "priority": prio, "tags": [tag]}).encode()
        urllib.request.urlopen(urllib.request.Request("https://ntfy.sh/", data=body, headers={"Content-Type": "application/json"}), timeout=10)
    except Exception as ex: print("notify failed:", repr(ex))

def read_config():
    """config.json: from GitHub (live edits, when running in GitHub Actions) or the local copy."""
    d = {}
    try: d = json.load(open("config.json"))
    except Exception: pass
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        try: d = json.load(urllib.request.urlopen(urllib.request.Request("https://raw.githubusercontent.com/%s/main/config.json" % repo, headers=UA), timeout=15))
        except Exception: pass
    return d

def apply_config(d):
    for k, v in d.items():
        if k in DEFAULTS and k not in ("market", "universe") and v is not None:
            try: setattr(A, k, type(DEFAULTS[k])(v))
            except Exception: pass

_gist_t = [0.0]
def _gist(method, body=None):
    tok = os.environ.get("GIST_TOKEN") or os.environ.get("GITHUB_TOKEN"); gid = os.environ.get("GIST_ID")
    if not (tok and gid): return None
    req = urllib.request.Request("https://api.github.com/gists/" + gid, data=json.dumps(body).encode() if body else None, method=method,
        headers={"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json", "User-Agent": "desk"})
    return json.load(urllib.request.urlopen(req, timeout=20))

def remote_state():
    """The scanner's saved state from the secret gist (used by the dashboard and when a scan job starts)."""
    g = _gist("GET")
    if not g: return None
    f = g["files"]["state.json"]; c = f.get("content", "")
    if f.get("truncated"): c = urllib.request.urlopen(urllib.request.Request(f["raw_url"], headers={"User-Agent": "desk"}), timeout=30).read().decode()
    return json.loads(c)

def load_state():
    try:
        d = remote_state()
        if d: ST.update(d); return
    except Exception as ex: print("gist load failed:", repr(ex))
    if os.path.exists("state.json"):
        try: ST.update(json.load(open("state.json")))
        except Exception: pass

def save(force=False):
    with LOCK:
        ST["closed"] = [c for c in ST["closed"] if time.time() - c.get("closed", 0) < 400 * 86400][:3000]
        txt = json.dumps(ST)
    open("state.json.tmp", "w").write(txt); os.replace("state.json.tmp", "state.json")
    if force or time.time() - _gist_t[0] > 120:
        try:
            if _gist("PATCH", {"files": {"state.json": {"content": txt}}}): _gist_t[0] = time.time()
        except Exception as ex: print("gist save failed:", repr(ex))

def loop(syms, until=None):
    pool = ThreadPoolExecutor(A.workers); daily = {}; last_daily = 0
    while True:
        t0 = time.time(); now = t0
        try:
            apply_config(read_config())
            ib, ie, _ = fetch(INDEX, "5m", "5d")
            mkt = bool(ib) and now - ib[-1][0] < 900 and (not ie or now < ie)
            update_open(now, pool)
            ix = []
            for nm, ys in (("NIFTY 50", "^NSEI"), ("BANK NIFTY", "^NSEBANK"), ("SENSEX", "^BSESN"), ("INDIA VIX", "^INDIAVIX")):
                b, _, _ = fetch(ys, "1d", "5d")
                if b and len(b) > 1: ix.append({"name": nm, "price": round(b[-1][4], 2), "chg": round((b[-1][4] / b[-2][4] - 1) * 100, 2)})
            if ix: ST["indices"] = ix
            regime, n = ST["status"].get("regime", "-"), ST["status"].get("scanned", 0)
            if now - last_daily > 1200:
                res = list(pool.map(lambda y: fetch(y, "1d", "2y"), syms + [INDEX]))
                daily = dict(zip(syms + [INDEX], res)); last_daily = now; DAILY.clear(); DAILY.update(daily)
                n, regime = scan_swing(daily, now, pool)
                mv = [{"sym": y.split(".")[0], "price": round(b[-1][4], 2), "chg": round((b[-1][4] / b[-2][4] - 1) * 100, 2)}
                      for y, (b, _, _) in daily.items() if b and len(b) > 2 and y != INDEX]
                mv.sort(key=lambda m: -m["chg"]); ST["movers"] = {"up": mv[:10], "down": mv[-10:][::-1]}
            if mkt:
                act = []
                for y in syms:
                    b = (daily.get(y) or (None,))[0]
                    if b and len(b) > 25 and b[-1][4] * sum(x[5] for x in b[-21:-1]) / 20 > A.min_turnover:
                        av = sum(x[5] for x in b[-21:-1]) / 20
                        act.append((abs(b[-1][4] / b[-2][4] - 1) * (b[-1][5] / av if av else 0), y))
                top = [y for _, y in sorted(act, reverse=True)[:A.topk]]
                tgt = A.target_intraday / A.lev_intraday / 100
                ingest("intraday", [r for rs in pool.map(lambda y: intra_job(y, tgt, A.lev_intraday, time.time()), top) for r in rs])
            with LOCK:
                ST["status"] = {"last_scan": time.time(), "universe": len(syms), "scanned": n, "market_open": mkt, "regime": regime,
                    "index": INDEX, "fetch_ok": HEALTH["ok"], "fetch_fail": HEALTH["fail"],
                    "cfg": {k: getattr(A, k) for k in ("capital", "target_intraday", "stretch_intraday", "lev_intraday", "target_swing", "stretch_swing", "lev_swing", "min_prob")}}
            save()
        except Exception as ex: print("cycle error:", repr(ex))
        print("scan done in %.0fs | open %d | closed %d" % (time.time() - t0, len(ST["open"]), len(ST["closed"])))
        if A.once or (until is not None and time.time() >= until): break
        nap = max(5, A.interval - (time.time() - t0))
        if until is not None: nap = min(nap, max(1, until - time.time()))
        time.sleep(nap)
    save(True)

def init(cfg=None):
    global A, INDEX
    A = argparse.Namespace(**DEFAULTS); cfg = cfg or {}
    A.market = cfg.get("market", "nse"); A.universe = cfg.get("universe"); apply_config(cfg)
    if A.market == "us": INDEX = "^GSPC"; A.min_turnover = 5e6; A.tz = -14400
    load_state()
    return A

def run(until=None):
    """Run the scanner loop in this process until `until` (epoch seconds), or for a single cycle if until is now/past."""
    init(read_config()); loop(universe(), until)
