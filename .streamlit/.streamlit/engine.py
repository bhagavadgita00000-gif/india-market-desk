#!/usr/bin/env python3
"""Scanner engine for the Streamlit dashboard. Research tool, not investment advice. Never places orders."""
import argparse, csv, html as _html, io, json, math, os, random, re, threading, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
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
def screener(sym):
    """Public company page on screener.in: key ratios + its pros/cons. Cached 24h, 1 request per 2s. {} on any failure."""
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
        strip = lambda x: _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x))).strip()
        blk = re.search(r'id="top-ratios"(.*?)</ul>', page, re.S)
        if blk:
            out["ratios"] = {strip(a): strip(b) for a, b in re.findall(r'<span class="name">(.*?)</span>\s*<span class="nowrap value">(.*?)</span>\s*</li>', blk.group(1), re.S)}
        for k in ("pros", "cons"):
            m = re.search(r'<div class="%s">(.*?)</div>' % k, page, re.S)
            out[k] = [strip(x) for x in re.findall(r"<li[^>]*>(.*?)</li>", m.group(1), re.S)][:5] if m else []
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

def simulate(R, e, target, stop, s, steps, N):
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

def evaluate(kind, y, s, e, dist, tgt, R, steps, bt, lev, why, expires=None):
    if len(R) < 30 or dist <= 0: return None
    stop = e - s * dist; target = e * (1 + s * tgt)
    ps, pst = simulate(R, e, target, stop, s, steps, 6000)
    w, n = bt
    p = 0.5 * ps + 0.5 * w / n if n >= 8 else ps
    rr = tgt * e / dist; ev = p * tgt * e - pst * dist
    nsc, head = news(y)
    ok = p >= A.min_prob / 100 and rr >= 1.5 and ev > 0 and s * nsc > -2
    fund = screener(y) if ok and getattr(A, "screener", False) and y.endswith(".NS") else {}
    if fund and len(fund.get("cons", [])) >= len(fund.get("pros", [])) + 3 and s == 1: why += " | Screener lists many more cons than pros"
    return {"fund": fund, "id": "%s-%s-%d" % (kind, y, time.time()), "kind": kind, "ysym": y, "sym": y.split(".")[0],
            "side": "BUY" if s == 1 else "SELL", "entry": round(e, 2), "target": round(target, 2), "stop": round(stop, 2),
            "rr": round(rr, 2), "prob": round(p, 3), "sim": round(ps, 3), "bt": round(w / n, 3) if n else None, "bt_n": n,
            "news": nsc, "headline": head, "why": why, "lev": lev, "created": time.time(), "expires": expires,
            "status": "OPEN", "last": round(e, 2), "pnl": 0.0, "ok": ok}

def ingest(kind, recs):
    recs = sorted([r for r in recs if r], key=lambda r: -r["prob"]); now = time.time()
    with LOCK:
        busy = {x["ysym"] for x in ST["open"]}
        cool = {x["ysym"] for x in ST["closed"] if x["kind"] == kind and now - x["closed"] < (86400 if kind == "intraday" else 3 * 86400)}
        room = A.max_open - sum(1 for x in ST["open"] if x["kind"] == kind)
        for r in recs:
            if r["ysym"] in busy or r["ysym"] in cool or not r["ok"] or room <= 0: continue
            ST["open"].append(r); busy.add(r["ysym"]); room -= 1
        ST["watch"][kind] = [r for r in recs if r["ysym"] not in busy][:8]

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
    ic = comp.get(INDEX)
    return len(comp), ("Bullish (index above 50DMA)" if ic and ic[-1][4] > sma([x[4] for x in ic], 50) else "Bearish (index below 50DMA)")

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
            if sh: out = ("STOP HIT", sig["stop"], x[0]); break
            if th: out = ("TARGET HIT", sig["target"], x[0]); break
        last = bars[-1][4]
        if not out:
            if sig["kind"] == "intraday" and sig["expires"] and now > sig["expires"]: out = ("EXPIRED (EOD)", last, now)
            elif sig["kind"] == "swing":
                cd = (sig["created"] + off) // 86400; da = {(x[0] + off) // 86400 for x in after if (x[0] + off) // 86400 > cd}
                if len(da) >= 3 and (len(da) > 3 or now - bars[-1][0] > 1800): out = ("EXPIRED (3 days)", last, now)
        px = out[1] if out else last
        sig["last"] = round(last, 2); sig["pnl"] = round(s * (px / sig["entry"] - 1) * 100 * sig["lev"], 2)
        if out:
            sig.update(status=out[0], exit=round(out[1], 2), closed=out[2])
            with LOCK:
                ST["open"] = [x for x in ST["open"] if x["id"] != sig["id"]]; ST["closed"] = ([sig] + ST["closed"])[:300]

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

_gist_t = [0.0]
def _gist(method, body=None):
    tok, gid = os.environ.get("GITHUB_TOKEN"), os.environ.get("GIST_ID")
    if not (tok and gid): return None
    req = urllib.request.Request("https://api.github.com/gists/" + gid, data=json.dumps(body).encode() if body else None, method=method,
        headers={"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json", "User-Agent": "desk"})
    return json.load(urllib.request.urlopen(req, timeout=20))

def load_state():
    try:
        g = _gist("GET")
        if g: ST.update(json.loads(g["files"]["state.json"]["content"])); return
    except Exception as ex: print("gist load failed:", repr(ex))
    if os.path.exists("state.json"):
        try: ST.update(json.load(open("state.json")))
        except Exception: pass

def save():
    with LOCK: txt = json.dumps(ST)
    open("state.json.tmp", "w").write(txt); os.replace("state.json.tmp", "state.json")
    if time.time() - _gist_t[0] > 120:
        try:
            if _gist("PATCH", {"files": {"state.json": {"content": txt}}}): _gist_t[0] = time.time()
        except Exception as ex: print("gist save failed:", repr(ex))

def loop(syms):
    pool = ThreadPoolExecutor(A.workers); daily = {}; last_daily = 0
    while True:
        t0 = time.time(); now = t0
        try:
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
                daily = dict(zip(syms + [INDEX], res)); last_daily = now
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
                    "index": INDEX, "fetch_ok": HEALTH["ok"], "fetch_fail": HEALTH["fail"], "cfg": {"swing_target_pct": A.target_swing, "swing_lev": A.lev_swing, "intraday_target_pct": A.target_intraday,
                    "intraday_lev": A.lev_intraday, "min_prob": A.min_prob}}
            save()
        except Exception as ex: print("cycle error:", repr(ex))
        print("scan done in %.0fs | open %d | closed %d" % (time.time() - t0, len(ST["open"]), len(ST["closed"])))
        if A.once: return
        time.sleep(max(5, A.interval - (time.time() - t0)))


_started = False
def start(cfg=None):
    """Start the background scanner once per process; cfg overrides defaults."""
    global A, INDEX, _started
    if _started: return A
    d = dict(market="nse", universe=None, limit=500, interval=300, workers=32, target_swing=6.0, target_intraday=6.0,
             lev_swing=1.0, lev_intraday=5.0, min_prob=25.0, max_open=8, topk=120, min_turnover=2e7, once=False, screener=True)
    d.update(cfg or {}); A = argparse.Namespace(**d)
    load_state()
    if A.market == "us": INDEX = "^GSPC"; A.min_turnover = 5e6
    syms = universe()
    threading.Thread(target=loop, args=(syms,), daemon=True).start(); _started = True
    return A
