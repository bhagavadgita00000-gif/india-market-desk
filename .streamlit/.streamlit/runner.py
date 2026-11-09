"""Entry point for the GitHub Actions scanner. Runs the scanner until just after the NSE close (15:40 IST)."""
import sys, time
import engine as E

if "--test" in sys.argv:
    E.notify("TradeDesk test", "If you can read this on your phone, trade alerts are working.", 4, "white_check_mark")
    print("test notification sent (if NTFY_TOPIC is set)")
    sys.exit(0)

now = time.time()
ist = time.gmtime(now + 19800)
secs = ist.tm_hour * 3600 + ist.tm_min * 60 + ist.tm_sec
close = now + max(0, 15 * 3600 + 40 * 60 - secs)          # 15:40 IST today (or now, if already past)
until = min(now + 5.8 * 3600, close) if close > now else now  # GitHub jobs are capped at 6 hours
E.run(until)
