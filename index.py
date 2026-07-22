"""
STOCKex — The Ultimate Trading Challenge
=================================================
Flask app, deployable to Vercel as a serverless function.

Local run:  python api/index.py   (or `flask --app api/index run`)
Deployed:   see README.md for `vercel deploy` instructions.

Note on state: Vercel functions are stateless/serverless — this app keeps
game state in a process-global dict for simplicity (same approach as the
original), with a "catch-up" price tick computed from elapsed time instead
of a background thread (which cannot run reliably in serverless). See the
README for the implications of this and options for making it fully
persistent with an external store (Vercel KV / Redis / Postgres).
"""

import os
import json
import threading
import random
import time
from datetime import datetime

from flask import Flask, request, redirect as flask_redirect, make_response, jsonify

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 260" role="img">
  <rect x="240" y="20" width="200" height="220" rx="16" fill="#0f1117"/>
  <rect x="240" y="50" width="200" height="190" rx="16" fill="none" stroke="#3b82f6" stroke-width="1.5"/>
  <rect x="268" y="158" width="28" height="66" rx="3" fill="#22c55e"/>
  <rect x="306" y="128" width="28" height="96" rx="3" fill="#3b82f6"/>
  <rect x="344" y="106" width="28" height="118" rx="3" fill="#3b82f6"/>
  <rect x="382" y="138" width="28" height="86" rx="3" fill="#ef4444"/>
  <polyline points="268,158 306,128 344,106 382,138" fill="none" stroke="#f59e0b" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="268" cy="158" r="4" fill="#f59e0b"/>
  <circle cx="306" cy="128" r="4" fill="#f59e0b"/>
  <circle cx="344" cy="106" r="4" fill="#f59e0b"/>
  <circle cx="382" cy="138" r="4" fill="#f59e0b"/>
</svg>"""

PORT = int(os.environ.get("PORT", 8080))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# ── Price volatility config ───────────────────────────────────────────────────
# Each ticker gets its own volatility (% max drift per tick) and mean-reversion strength
VOLATILITY = {
    "ARSR": 0.004, "SGIN": 0.003, "UNSA": 0.006,
    "GLDC": 0.005, "PRAD": 0.004, "SNXA": 0.009,
    "CCBK": 0.002, "SPFN": 0.007, "VTCS": 0.008,
    "FTSG": 0.005, "GHBK": 0.004, "BRSK": 0.006,
}
MEAN_REVERSION = 0.015   # pull back toward base per tick
TICK_INTERVAL  = 2       # seconds between price ticks

# ── Shared game state ─────────────────────────────────────────────────────────
state = {
    "players":       {},       # token → {name, team, balance, portfolio, trades, joined_at}
    "admin_sessions": set(),
    "game_active":   False,
    "news_feed":     [],
    "price_history": {},       # ticker → list of (timestamp_str, price)  (last 30 ticks)
    "prices": {
        "ARSR": {"name": "Alpine ReSecure", "base": 1800, "current": 1800, "color": "#3b82f6"},
        "SGIN": {"name": "SummitGuard Insurance", "base": 700, "current": 700, "color": "#22c55e"},
        "UNSA": {"name": "UnityShield Assurance", "base": 950, "current": 950, "color": "#f97316"},
        "GLDC": {"name": "Golden Crest Capital", "base": 2400, "current": 2400, "color": "#a855f7"},
        "PRAD": {"name": "Pinnacle Risk Advisor", "base": 900, "current": 900, "color": "#eab308"},
        "SNXA": {"name": "Strategic Nexus Advisors", "base": 1100, "current": 1100, "color": "#ef4444"},
        "CCBK": {"name": "Capital Crest Bank", "base": 1250, "current": 1250, "color": "#42f9f9"},
        "SPFN": {"name": "Sterling Peak Financial", "base": 1900, "current": 1900, "color": "#ef2864"},
        "VTCS": {"name": "Vertex Consulting Solutions", "base": 1450, "current": 1450, "color": "#a30542"},
        "FTSG": {"name": "FortiSure Insurance Group", "base": 550, "current": 550, "color": "#bee610"},
        "GHBK": {"name": "Global Horizon Bank", "base": 850, "current": 850, "color": "#70b0e0"},
        "BRSK": {"name": "Bavaria Risk ReSecure", "base": 1600, "current": 1600, "color": "#d15536"},
    },
    "news_pool": [
        {"id":"N01","title":"Climate Catastrophe Alert","category":"Natural Disaster",
         "description":"Global reinsurers warn that insured losses from natural catastrophes could exceed $150 billion this year as hurricanes and floods become more frequent.",
         "impacts":{"ARSR":-8,"BRSK":-8,"SGIN":-5,"FTSG":-6,"UNSA":-3}},
        {"id":"N02","title":"Underwriting Excellence","category":"Earnings Report",
         "description":"A leading insurer reports a sharp rise in quarterly profits after maintaining one of the industry's best combined ratios and disciplined underwriting standards.",
         "impacts":{"SGIN":+10,"UNSA":+3,"FTSG":+2,"ARSR":+1,"BRSK":+1}},
        {"id":"N03","title":"Strategic Divestment Pays Off","category":"Corporate Action",
         "description":"A European financial services giant records a significant profit boost after divesting a stake in one of its Asian insurance ventures.",
         "impacts":{"UNSA":+9,"GHBK":+2,"VTCS":+1}},
        {"id":"N04","title":"Dealmaking Revival","category":"Banking",
         "description":"Central banks signal interest rates may remain elevated for longer, while mergers and acquisitions activity rebounds globally.",
         "impacts":{"GLDC":+8,"CCBK":+7,"SPFN":+7,"GHBK":+5,"VTCS":+2}},
        {"id":"N05","title":"Cyber Threat Escalation","category":"Cybersecurity",
         "description":"A wave of sophisticated cyberattacks on multinational corporations leads businesses to dramatically increase spending on cyber risk advisory and insurance services.",
         "impacts":{"PRAD":+10,"VTCS":+6,"SNXA":+4,"SGIN":3,"UNSA":+2}},
        {"id":"N06","title":"Regulatory Crackdown","category":"Regulation",
         "description":"Regulators launch an investigation into audit quality and consulting independence across several major professional services firms.",
         "impacts":{"SNXA":-10,"VTCS":-3,"PRAD":-2}},
        {"id":"N07","title":"Trading Boom","category":"Financial Markets",
         "description":"One of the world's largest banks reports record trading revenues as market volatility drives increased client activity.",
         "impacts":{"CCBK":+9,"GLDC":+8,"SPFN":+7,"GHBK":+3}},
        {"id":"N08","title":"Wealth Surge","category":"Wealth Management",
         "description":"Global wealth creation reaches an all-time high, pushing assets under management at major wealth-management firms to record levels.",
         "impacts":{"SPFN":+10,"CCBK":5,"GHBK":4,"GLDC":3}},
        {"id":"N09","title":"AI Spending Frenzy","category":"Technology",
         "description":"Governments and Fortune 500 companies announce billions of dollars in spending on artificial intelligence transformation and cloud modernization projects.",
         "impacts":{"VTCS":+10,"SNXA":5,"PRAD":2,"CCBK":1}},
        {"id":"N10","title":"Wildfire Crisis","category":"Natural Disaster",
         "description":"An unusually severe wildfire season causes insured losses across several provinces, leading analysts to revise claim estimates upward.",
         "impacts":{"FTSG":-10,"SGIN":-6,"UNSA":-4,"ARSR":-3,"BRSK":-3}},
        {"id":"N11","title":"Capital Strength Recorded","category":"Corporate Finance",
         "description":"A major international bank unveils a multi-billion-dollar share buyback after exceeding capital adequacy requirements.",
         "impacts":{"GHBK":+9,"CCBK":+3,"SPFN":+2,"GLDC":+2}},
        {"id":"N12","title":"Reinsurance Price Surge","category":"Insurance Market",
         "description":"Property and casualty reinsurance rates rise sharply during annual renewals as insurers seek protection against increasing catastrophe losses.",
         "impacts":{"BRSK":+10,"ARSR":+10,"SGIN":-3,"FTSG":-4,"UNSA":-2}},
        {"id":"N13","title":"Asian Growth Opportunity","category":"Emerging Markets",
         "description":"A rapidly growing middle class in Asia drives strong demand for life insurance and retirement planning products.",
         "impacts":{"UNSA":+8,"GHBK":+7,"PRAD":+2}},
        {"id":"N14","title":"Mega Brokerage Deal","category":"Brokerage",
         "description":"Several large corporations consolidate their insurance brokerage relationships under a single global risk advisory provider.",
         "impacts":{"PRAD":+10,"SGIN":2,"UNSA":+2}},
        {"id":"N15","title":"Banking Rules Relaxed","category":"Regulation",
         "description":"Financial regulators ease restrictions on investment banking activities to stimulate economic growth.",
         "impacts":{"GLDC":+10,"SPFN":+8,"CCBK":+7,"GHBK":+3}},
        {"id":"N16","title":"IPO Freeze","category":"Economic Slowdown",
         "description":"Economic uncertainty causes corporations to delay acquisitions and public listings worldwide.",
         "impacts":{"GLDC":-10,"SPFN":-8,"CCBK":-6,"GHBK":-2}},
    ],
    "lock": threading.Lock(),
    "last_tick_time": time.time(),
}

# Initialise price history
for _t in state["prices"]:
    state["price_history"][_t] = []

# ── Helpers ───────────────────────────────────────────────────────────────────

def generate_token():
    return "%016x" % random.getrandbits(64)

def get_player(token):
    return state["players"].get(token)

def portfolio_value(player):
    total = player["balance"]
    for ticker, qty in player["portfolio"].items():
        total += state["prices"].get(ticker, {}).get("current", 0) * qty
    return round(total, 2)

def apply_news_impact(news_item):
    for ticker, pct in news_item["impacts"].items():
        if ticker in state["prices"]:
            state["prices"][ticker]["current"] = round(
                state["prices"][ticker]["current"] * (1 + pct / 100), 2)

def do_full_reset():
    """Reset everything except admin sessions and news pool."""
    with state["lock"]:
        state["players"].clear()
        state["news_feed"].clear()
        state["game_active"] = False
        for ticker, p in state["prices"].items():
            p["current"] = p["base"]
        for ticker in state["price_history"]:
            state["price_history"][ticker].clear()

# ── Price fluctuation (catch-up model) ────────────────────────────────────────
# Serverless functions can't run a persistent background thread, so instead of
# ticking on a timer, we compute how many TICK_INTERVAL windows have elapsed
# since the last recorded tick and apply that many price updates whenever a
# request comes in (see run_price_ticks() below, wired up via
# @app.before_request). This gives the same mean-reversion Brownian-motion
# behaviour as the original as long as the process stays warm between
# requests; see README.md for the caveats this introduces.

def _apply_one_tick():
    ts = datetime.now().strftime("%H:%M:%S")
    for ticker, p in state["prices"].items():
        vol  = VOLATILITY.get(ticker, 0.005)
        base = p["base"]
        cur  = p["current"]
        shock = random.gauss(0, vol)
        reversion = MEAN_REVERSION * (base - cur) / base
        new_price = cur * (1 + shock + reversion)
        new_price = max(base * 0.40, min(base * 2.50, new_price))
        p["current"] = round(new_price, 2)
        hist = state["price_history"][ticker]
        hist.append((ts, p["current"]))
        if len(hist) > 40:
            hist.pop(0)

def run_price_ticks(max_ticks=20):
    """Catch up on any ticks that should have happened since the last request."""
    now = time.time()
    with state["lock"]:
        if not state["game_active"]:
            state["last_tick_time"] = now
            return
        elapsed = now - state["last_tick_time"]
        ticks_due = int(elapsed // TICK_INTERVAL)
        if ticks_due <= 0:
            return
        # Cap how many we replay at once (e.g. after a long idle period)
        # so a single request can't be stuck doing minutes of computation.
        for _ in range(min(ticks_due, max_ticks)):
            _apply_one_tick()
        state["last_tick_time"] += ticks_due * TICK_INTERVAL

# ── CSS ───────────────────────────────────────────────────────────────────────

COMMON_CSS = """
:root{
  --bg:#0f1117;--bg2:#161a24;--bg3:#1e2330;
  --card:#1a1f2e;--card2:#222840;
  --border:rgba(255,255,255,0.07);--border2:rgba(255,255,255,0.16);
  --text:#e8eaed;--text2:#8c94a8;--text3:#555f78;
  --blue:#3b82f6;--blue2:#1d4ed8;--blue-glow:rgba(59,130,246,0.15);
  --green:#22c55e;--green-dim:#14532d;
  --red:#ef4444;--red-dim:#7f1d1d;
  --orange:#f97316;--gold:#f59e0b;
  --radius:8px;--radius-lg:14px;
  --font:'Inter',system-ui,-apple-system,sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font);background:var(--bg);color:var(--text);line-height:1.6;min-height:100vh}
a{color:var(--blue);text-decoration:none}
input,select{font-family:inherit}
.btn{display:inline-flex;align-items:center;gap:6px;padding:10px 20px;border-radius:var(--radius);border:none;font-size:14px;font-weight:500;cursor:pointer;transition:all .15s}
.btn-primary{background:var(--blue);color:#fff}.btn-primary:hover{opacity:.88}
.btn-success{background:var(--green);color:#fff}.btn-success:hover{opacity:.88}
.btn-danger{background:var(--red);color:#fff}.btn-danger:hover{opacity:.88}
.btn-ghost{background:transparent;border:1px solid var(--border2);color:var(--text)}.btn-ghost:hover{background:var(--bg3)}
.btn-warn{background:var(--gold);color:#000}.btn-warn:hover{opacity:.88}
.btn-sm{padding:6px 14px;font-size:13px}
.btn:disabled{opacity:.35;cursor:not-allowed}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-lg);padding:24px}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:500}
.badge-up{background:var(--green-dim);color:var(--green)}
.badge-dn{background:var(--red-dim);color:var(--red)}
.badge-neu{background:var(--bg3);color:var(--text2)}
.badge-blue{background:var(--blue-glow);color:var(--blue)}
.up{color:var(--green)}.dn{color:var(--red)}
.label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--text3);font-weight:500}
.flash{padding:12px 16px;border-radius:var(--radius);font-size:14px;margin-bottom:16px}
.flash-err{background:var(--red-dim);border:1px solid var(--red);color:#fca5a5}
.flash-ok{background:var(--green-dim);border:1px solid var(--green);color:#86efac}
.flash-warn{background:#451a03;border:1px solid var(--gold);color:#fde68a}
"""

def page(title, body):
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title} — STOCK EX</title>
<style>{COMMON_CSS}</style>
</head><body>{body}</body></html>"""

# ── Register page ─────────────────────────────────────────────────────────────

def render_register(flash="", flash_type="err"):
    fh = f'<div class="flash flash-{flash_type}">{flash}</div>' if flash else ""
    badge = ('<span class="badge badge-up">🟢 Game Live — join now!</span>'
             if state["game_active"] else
             '<span class="badge badge-neu">⏳ Waiting for admin to start</span>')
    return page("Register", f"""
<style>
body{{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
.wrap{{width:100%;max-width:420px}}
.logo{{font-size:28px;font-weight:700;letter-spacing:-.03em;text-align:center;margin-bottom:3px}}
.logo em{{font-style:normal;color:var(--blue)}}
.logo .ex{{font-size:20px}}
.sub{{text-align:center;color:var(--text2);font-size:14px;margin-bottom:14px}}
.field{{margin-bottom:16px}}
.field label{{display:block;font-size:13px;color:var(--text2);margin-bottom:6px;font-weight:500}}
.field input{{width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:var(--radius);padding:10px 14px;color:var(--text);font-size:15px;outline:none}}
.field input:focus{{border-color:var(--blue)}}
</style>
<div class="wrap">
  <div style="text-align:center">{LOGO_SVG}</div>
  <div class="logo"><em>STOCK</em><span class="ex">EX</span></div>
  <div class="sub">The Ultimate Trading Challenge</div>
  <div style="text-align:center;margin-bottom:24px">{badge}</div>
  {fh}
  <div class="card">
    <form method="POST" action="/register">
      <div class="field"><label>Your name</label><input name="name" placeholder="e.g. Arjun Sharma" required autocomplete="off"/></div>
      <div class="field"><label>Team name</label><input name="team" placeholder="e.g. RiskRaptors" required autocomplete="off"/></div>
      <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center;padding:12px">Join the game →</button>
    </form>
  </div>
  <p style="text-align:center;font-size:13px;color:var(--text3);margin-top:20px">Already registered? <a href="/trade">Go to trading floor</a></p>
</div>""")

# ── Trading Floor ─────────────────────────────────────────────────────────────

def render_trade(player, flash="", flash_type="err"):
    fh = f'<div class="flash flash-{flash_type}">{flash}</div>' if flash else ""
    pv   = portfolio_value(player)
    gain = pv - 100000
    gc   = "up" if gain >= 0 else "dn"
    gs   = "+" if gain >= 0 else ""

    price_cards = ""
    for ticker, p in state["prices"].items():
        chg  = round((p["current"] - p["base"]) / p["base"] * 100, 2)
        cc   = "up" if chg >= 0 else "dn"
        arrow = "▲" if chg >= 0 else "▼"
        held  = player["portfolio"].get(ticker, 0)
        sell_attr = '' if held > 0 else 'disabled'
        price_cards += f"""
<div class="p-card" id="pc-{ticker}" data-base="{p['base']}" data-color="{p['color']}">
  <div style="display:flex;justify-content:space-between;align-items:start">
    <div>
      <div class="label" style="color:{p['color']}">{ticker}</div>
      <div style="font-size:14px;font-weight:500;margin-top:3px;color:var(--text2)">{p['name']}</div>
    </div>
    <div style="text-align:right">
      <div class="pc-price" style="font-size:22px;font-weight:700;font-variant-numeric:tabular-nums">₹{p['current']:,.2f}</div>
      <div class="pc-chg {cc}" style="font-size:13px">{arrow} {abs(chg)}%</div>
    </div>
  </div>
  <canvas class="pc-spark" width="220" height="36" style="width:100%;margin:10px 0 2px;display:block"></canvas>
  <div style="padding-top:10px;border-top:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;gap:8px">
    <span style="font-size:13px;color:var(--text2)">Held: <strong class="pc-held" style="color:var(--text)">{held}</strong></span>
    <div style="display:flex;gap:6px;align-items:center">
      <input type="number" class="qty-input" id="qty-{ticker}" min="1" value="1"
             style="width:58px;background:var(--bg3);border:1px solid var(--border2);border-radius:var(--radius);padding:5px 8px;color:var(--text);font-size:14px;text-align:center"/>
      <button class="btn btn-success btn-sm" onclick="doTrade('{ticker}','buy')">Buy</button>
      <button class="btn btn-danger btn-sm" id="sell-{ticker}" onclick="doTrade('{ticker}','sell')" {sell_attr}>Sell</button>
    </div>
  </div>
</div>"""

    news_html = ""
    if state["news_feed"]:
        for n in reversed(state["news_feed"]):
            impacts = ", ".join(f"{t} {'+' if v>0 else ''}{v}%" for t,v in n["impacts"].items())
            news_html += f"""
<div class="news-item">
  <div style="display:flex;justify-content:space-between;align-items:start;gap:10px">
    <div>
      <div style="font-weight:600;font-size:14px">{n['title']}</div>
      <div style="font-size:13px;color:var(--text2);margin-top:3px">{n['description']}</div>
    </div>
    <span class="badge badge-blue" style="white-space:nowrap;flex-shrink:0">{n['category']}</span>
  </div>
  <div style="margin-top:7px;font-size:12px;color:var(--text3)">{n['released_at']}</div>
</div>"""
    else:
        news_html = '<p style="color:var(--text3);font-size:14px;text-align:center;padding:20px 0">No news yet — stay tuned.</p>'

    port_rows = ""
    for ticker, qty in player["portfolio"].items():
        if qty > 0:
            cp = state["prices"][ticker]["current"]
            val = round(cp * qty, 2)
            port_rows += f"<tr><td style='color:{state['prices'][ticker]['color']};font-weight:600'>{ticker}</td><td>{state['prices'][ticker]['name']}</td><td>{qty}</td><td>₹{cp:,.2f}</td><td>₹{val:,.2f}</td></tr>"
    if not port_rows:
        port_rows = '<tr><td colspan="5" style="text-align:center;color:var(--text3);padding:20px">No positions yet.</td></tr>'

    game_banner = ""
    if not state["game_active"]:
        game_banner = '<div style="background:var(--bg3);border:1px solid var(--border2);border-radius:var(--radius);padding:12px 16px;font-size:14px;color:var(--text2);margin-bottom:16px;text-align:center">⏳ Game not started yet. Trading is disabled.</div>'

    # Serialize history for JS
    hist_json = json.dumps({t: state["price_history"][t] for t in state["prices"]})

    return page("Trading Floor", f"""
<style>
.topbar{{background:var(--bg2);border-bottom:1px solid var(--border);padding:12px 20px;display:flex;align-items:center;justify-content:space-between;gap:12px;position:sticky;top:0;z-index:50;flex-wrap:wrap}}
.brand{{font-size:16px;font-weight:700}}.brand em{{font-style:normal;color:var(--blue)}}
.brand .ex{{font-size:12px}}
.pill{{background:var(--bg3);border-radius:var(--radius);padding:5px 12px;font-size:13px;display:flex;align-items:center;gap:6px}}
.main{{max-width:1240px;margin:0 auto;padding:20px;display:grid;grid-template-columns:1fr 300px;gap:20px}}
@media(max-width:820px){{.main{{grid-template-columns:1fr}}}}
.p-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}}
.p-card{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px;transition:border-color .2s}}
.p-card:hover{{border-color:var(--border2)}}
.sidebar{{display:flex;flex-direction:column;gap:16px}}
.news-item{{padding:12px 0;border-bottom:1px solid var(--border)}}.news-item:last-child{{border-bottom:none}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:8px 12px;background:var(--bg3);color:var(--text3);font-size:11px;text-transform:uppercase;letter-spacing:.05em}}
td{{padding:9px 12px;border-bottom:1px solid var(--border)}}
#toast{{position:fixed;bottom:20px;right:20px;background:var(--card2);border:1px solid var(--border2);border-radius:var(--radius);padding:11px 18px;font-size:14px;display:none;z-index:300;box-shadow:0 8px 32px rgba(0,0,0,.5);transition:opacity .3s}}
.qty-input{{outline:none}}
#newsModal{{position:fixed;inset:0;background:rgba(0,0,0,.75);display:none;align-items:center;justify-content:center;z-index:500;backdrop-filter:blur(4px)}}
#newsModal.show{{display:flex}}
.nm-box{{background:var(--card2);border:2px solid var(--blue);border-radius:var(--radius-lg);max-width:520px;width:92%;padding:26px;animation:popIn .35s cubic-bezier(.34,1.56,.64,1);box-shadow:0 20px 60px rgba(0,0,0,.6)}}
@keyframes popIn{{from{{transform:scale(.7);opacity:0}}to{{transform:scale(1);opacity:1}}}}
.nm-badge{{display:inline-block;background:var(--red);color:#fff;font-size:11px;font-weight:700;letter-spacing:.1em;padding:4px 10px;border-radius:4px;margin-bottom:12px}}
.nm-title{{font-size:22px;font-weight:700;margin-bottom:8px}}
.nm-cat{{font-size:12px;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px}}
.nm-desc{{font-size:14px;color:var(--text2);line-height:1.55;margin-bottom:16px}}
.nm-impacts{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px}}
.nm-imp{{background:var(--bg3);border:1px solid var(--border2);border-radius:6px;padding:6px 10px;font-size:13px;font-weight:600}}
.nm-imp.up{{color:var(--green)}}.nm-imp.dn{{color:var(--red)}}
</style>
<div class="topbar">
<div class="brand" style="display:flex;align-items:center">{LOGO_SVG.replace("viewBox=\"0 0 680 260\"", "viewBox=\"0 0 680 260\" style=\"height:36px;width:auto\"")}</div>
  <div class="brand"><em>STOCK</em><span class="ex">EX</span> <span style="font-size:13px;color:var(--text3);font-weight:400">Trading Floor</span></div>
  <div style="display:flex;gap:8px;flex-wrap:wrap">
    <div class="pill">👤 {player['name']}</div>
    <div class="pill">🏷 {player['team']}</div>
    <div class="pill">💰 <strong id="live-bal">₹{player['balance']:,.2f}</strong></div>
    <div class="pill">📊 <strong id="live-pv" class="{gc}">₹{pv:,.2f}</strong></div>
    <div class="pill">P&L: <span id="live-pnl" class="{gc}">{gs}₹{abs(gain):,.2f}</span></div>
  </div>
</div>
{fh}
<div class="main">
  <div>
    {game_banner}
    <div class="label" style="margin-bottom:14px">STOCK<span style="font-size:8px">EX</span> Market</div>
    <div class="p-grid">{price_cards}</div>
    <div class="card" style="margin-top:20px;padding:0;overflow:hidden">
      <div class="label" style="padding:14px 16px 10px">My Portfolio</div>
      <table>
        <thead><tr><th>Ticker</th><th>Stock</th><th>Qty</th><th>Price</th><th>Value</th></tr></thead>
        <tbody id="port-body">{port_rows}</tbody>
      </table>
    </div>
  </div>
  <div class="sidebar">
    <div class="card">
      <div class="label" style="margin-bottom:10px">📰 News Feed</div>
      <div id="news-feed">{news_html}</div>
    </div>
  </div>
</div>
<div id="newsModal" onclick="closeNews(event)">
  <div class="nm-box" onclick="event.stopPropagation()">
    <div class="nm-badge">📢 BREAKING NEWS</div>
    <div class="nm-cat" id="nm-cat"></div>
    <div class="nm-title" id="nm-title"></div>
    <div class="nm-desc" id="nm-desc"></div>
    <button class="btn btn-primary" style="width:100%" onclick="closeNews()">Got it — back to trading</button>
  </div>
</div>
<div id="toast"></div>
<script>
const PRICES_META = {json.dumps({t:{"base":p["base"],"name":p["name"],"color":p["color"]} for t,p in state["prices"].items()})};
let priceHistory = {hist_json};
let lastNewsCount = {len(state["news_feed"])};
let newsFeedCache = {json.dumps(state["news_feed"])};

// ── Sparkline renderer ────────────────────────────────────────────────────────
function drawSpark(canvas, ticker, color) {{
  const hist = priceHistory[ticker];
  if (!hist || hist.length < 2) return;
  const vals = hist.map(h => h[1]);
  const min = Math.min(...vals), max = Math.max(...vals);
  const w = canvas.width, h = canvas.height;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, w, h);
  const range = max - min || 1;
  const pts = vals.map((v, i) => [i / (vals.length - 1) * w, h - ((v - min) / range) * (h - 4) - 2]);
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, color + '55');
  grad.addColorStop(1, color + '00');
  ctx.beginPath();
  ctx.moveTo(pts[0][0], h);
  pts.forEach(([x, y]) => ctx.lineTo(x, y));
  ctx.lineTo(pts[pts.length-1][0], h);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();
  ctx.beginPath();
  pts.forEach(([x, y], i) => i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y));
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.stroke();
}}

function drawAllSparks() {{
  document.querySelectorAll('.p-card').forEach(card => {{
    const ticker = card.id.replace('pc-', '');
    const canvas = card.querySelector('.pc-spark');
    const color = card.dataset.color;
    if (canvas) drawSpark(canvas, ticker, color);
  }});
}}

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, ok=true) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  t.style.borderColor = ok ? 'var(--green)' : 'var(--red)';
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.style.display = 'none', 3200);
}}

// ── Trade ─────────────────────────────────────────────────────────────────────
async function doTrade(ticker, action) {{
  const qty = parseInt(document.getElementById('qty-' + ticker).value) || 1;
  const res = await fetch('/api/trade', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ticker, action, qty}})
  }});
  const d = await res.json();
  toast(d.msg, d.ok);
  if (d.ok) refresh();
}}

// ── Refresh loop ──────────────────────────────────────────────────────────────
async function refresh() {{
  const r = await fetch('/api/state');
  const d = await r.json();
  if (!d.ok) return;
  // Prices & sparks
  priceHistory = d.price_history;
  Object.entries(d.prices).forEach(([t, p]) => {{
    const card = document.getElementById('pc-' + t);
    if (!card) return;
    const base = parseFloat(card.dataset.base);
    const chg  = ((p.current - base) / base * 100).toFixed(2);
    const up   = chg >= 0;
    card.querySelector('.pc-price').textContent = '₹' + p.current.toLocaleString('en-IN', {{minimumFractionDigits:2}});
    const chgEl = card.querySelector('.pc-chg');
    chgEl.textContent = (up ? '▲ ' : '▼ ') + Math.abs(chg) + '%';
    chgEl.className = 'pc-chg ' + (up ? 'up' : 'dn');
    const heldEl = card.querySelector('.pc-held');
    const held = d.player.portfolio[t] || 0;
    if (heldEl) heldEl.textContent = held;
    const sellBtn = document.getElementById('sell-' + t);
    if (sellBtn) sellBtn.disabled = held === 0;
  }});
  drawAllSparks();
  // Stats
  const bal = d.player.balance;
  const pv  = d.player.portfolio_value;
  const gain = pv - 100000;
  document.getElementById('live-bal').textContent = '₹' + bal.toLocaleString('en-IN', {{minimumFractionDigits:2}});
  const pvEl = document.getElementById('live-pv');
  pvEl.textContent = '₹' + pv.toLocaleString('en-IN', {{minimumFractionDigits:2}});
  pvEl.className = gain >= 0 ? 'up' : 'dn';
  const pnlEl = document.getElementById('live-pnl');
  pnlEl.textContent = (gain >= 0 ? '+' : '') + '₹' + Math.abs(gain).toLocaleString('en-IN', {{minimumFractionDigits:2}});
  pnlEl.className = gain >= 0 ? 'up' : 'dn';
  // Portfolio table
  const pb = document.getElementById('port-body');
  if (Object.keys(d.player.portfolio).filter(t => d.player.portfolio[t] > 0).length === 0) {{
    pb.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text3);padding:20px">No positions yet.</td></tr>';
  }} else {{
    pb.innerHTML = Object.entries(d.player.portfolio).filter(([,q]) => q > 0).map(([t, q]) => {{
      const pr = d.prices[t].current;
      const val = (pr * q).toLocaleString('en-IN', {{minimumFractionDigits:2}});
      return `<tr><td style="color:${{d.prices[t].color}};font-weight:600">${{t}}</td><td>${{PRICES_META[t].name}}</td><td>${{q}}</td><td>₹${{pr.toLocaleString('en-IN',{{minimumFractionDigits:2}})}}</td><td>₹${{val}}</td></tr>`;
    }}).join('');
  }}
  // News popup
  const feed = d.news_feed || [];
  if (feed.length > lastNewsCount) {{
    const latest = feed[feed.length - 1];
    showNewsModal(latest);
    lastNewsCount = feed.length;
    newsFeedCache = feed;
    setTimeout(() => location.reload(), 8000);
  }}
}}


function showNewsModal(n) {{
  document.getElementById('nm-cat').textContent = n.category || '';
  document.getElementById('nm-title').textContent = n.title || '';
  document.getElementById('nm-desc').textContent = n.description || '';
  document.getElementById('newsModal').classList.add('show');
  try {{
    const ctx = new (window.AudioContext||window.webkitAudioContext)();
    const o = ctx.createOscillator(); const g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.frequency.value = 880; g.gain.value = 0.08;
    o.start(); o.frequency.exponentialRampToValueAtTime(440, ctx.currentTime+0.25);
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime+0.4);
    o.stop(ctx.currentTime+0.4);
  }} catch(e) {{}}
}}
function closeNews(e) {{
  if (e && e.target.id !== 'newsModal' && e.type === 'click') {{}}
  document.getElementById('newsModal').classList.remove('show');
}}

drawAllSparks();
setInterval(refresh, 3500);
</script>""")

# ── Admin Panel ───────────────────────────────────────────────────────────────

def render_admin(flash="", flash_type="ok"):
    fh = f'<div class="flash flash-{flash_type}">{flash}</div>' if flash else ""
    game_active = state["game_active"]
    toggle_label = "⏸ Pause Game" if game_active else "▶ Start Game"
    toggle_cls   = "btn-danger" if game_active else "btn-success"
    game_badge   = ('<span class="badge badge-up">🟢 Live</span>' if game_active
                    else '<span class="badge badge-neu">⏸ Paused</span>')

    # ── Stat cards
    total_trades = sum(p["trades"] for p in state["players"].values())

    # ── Leaderboard
    sorted_players = sorted(state["players"].values(), key=portfolio_value, reverse=True)
    lb_rows = ""
    for i, p in enumerate(sorted_players):
        pv   = portfolio_value(p)
        gain = pv - 100000
        gc   = "up" if gain >= 0 else "dn"
        medal = ["🥇","🥈","🥉"][i] if i < 3 else f"#{i+1}"
        lb_rows += f"""
<tr>
  <td>{medal}</td>
  <td><strong>{p['name']}</strong></td>
  <td style="color:var(--text2)">{p['team']}</td>
  <td>₹{p['balance']:,.0f}</td>
  <td>₹{pv:,.0f}</td>
  <td class="{gc}">{'+'if gain>=0 else ''}₹{abs(gain):,.0f}</td>
  <td style="color:var(--text2)">{p['trades']}</td>
  <td style="font-size:12px;color:var(--text3)">{p['joined_at']}</td>
</tr>"""
    if not lb_rows:
        lb_rows = '<tr><td colspan="8" style="text-align:center;color:var(--text3);padding:24px">No players yet.</td></tr>'

    # ── News cards
    released_ids = {n["id"] for n in state["news_feed"]}
    news_cards = ""
    for n in state["news_pool"]:
        done = n["id"] in released_ids
        impacts = " · ".join(f"{t} {'+'if v>0 else ''}{v}%" for t,v in n["impacts"].items())
        badge   = '<span class="badge badge-up" style="font-size:11px">✓ Released</span>' if done else '<span class="badge badge-neu" style="font-size:11px">Pending</span>'
        btn     = ('<button class="btn btn-ghost btn-sm" disabled>Released</button>'
                   if done else
                   f'<form method="POST" action="/admin/release" style="display:inline"><input type="hidden" name="news_id" value="{n["id"]}"><button type="submit" class="btn btn-primary btn-sm">📢 Release</button></form>')
        news_cards += f"""
<div class="news-card {'released' if done else ''}">
  <div style="display:flex;justify-content:space-between;align-items:start;gap:10px;margin-bottom:8px">
    <div style="font-weight:600;font-size:14px">{n['title']}</div>{badge}
  </div>
  <div style="font-size:12px;color:var(--text3);margin-bottom:4px">{n['category']}</div>
  <div style="font-size:13px;color:var(--text2);margin-bottom:10px">{n['description']}</div>
  <div style="font-size:12px;color:var(--text3);margin-bottom:12px">↕ {impacts}</div>
  {btn}
</div>"""

    # ── Price rows
    price_rows = ""
    for ticker, p in state["prices"].items():
        chg = round((p["current"] - p["base"]) / p["base"] * 100, 2)
        gc  = "up" if chg >= 0 else "dn"
        hist = state["price_history"].get(ticker, [])
        spark_data = json.dumps([v for _, v in hist])
        price_rows += f"""
<tr>
  <td><strong style="color:{p['color']}">{ticker}</strong></td>
  <td>{p['name']}</td>
  <td>₹{p['base']}</td>
  <td class="{gc}" style="font-weight:600">₹{p['current']:,.2f}</td>
  <td class="{gc}">{'+'if chg>=0 else ''}{chg}%</td>
  <td><canvas id="as-{ticker}" width="120" height="28" data-vals='{spark_data}' data-color="{p['color']}" style="display:block"></canvas></td>
</tr>"""

    return page("Admin Panel", f"""
<style>
.topbar{{background:var(--bg2);border-bottom:1px solid var(--border);padding:14px 24px;display:flex;align-items:center;justify-content:space-between;gap:12px;position:sticky;top:0;z-index:50}}
.brand{{font-size:16px;font-weight:700}}.brand em{{font-style:normal;color:var(--blue)}}
.brand .ex{{font-size:12px}}
.main{{max-width:1340px;margin:0 auto;padding:24px}}
.sec-title{{font-size:17px;font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:8px}}
.stats-row{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px}}
.stat-box{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px 22px;min-width:130px}}
.stat-box .n{{font-size:26px;font-weight:700;color:var(--blue)}}.stat-box .l{{font-size:13px;color:var(--text2);margin-top:2px}}
.ctrl-row{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:28px;padding:18px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-lg)}}
.ctrl-row .spacer{{flex:1}}
.news-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;margin-bottom:32px}}
.news-card{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px;transition:border-color .15s}}
.news-card:hover{{border-color:var(--border2)}}.news-card.released{{opacity:.5}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:9px 14px;background:var(--bg3);color:var(--text3);font-size:11px;text-transform:uppercase;letter-spacing:.05em}}
td{{padding:10px 14px;border-bottom:1px solid var(--border)}}
.danger-zone{{margin-top:32px;padding:20px;border:1px solid var(--red-dim);border-radius:var(--radius-lg);background:#1a0a0a}}
.danger-zone h3{{color:var(--red);font-size:15px;margin-bottom:8px}}
.danger-zone p{{font-size:13px;color:var(--text2);margin-bottom:14px}}
</style>
<div class="topbar">
  <div class="brand"><em>STOCK</em><span class="ex">EX</span>&nbsp;<span style="font-size:13px;color:var(--gold);font-weight:500">⚡ Admin</span></div>
  <div style="display:flex;gap:10px;align-items:center">
    {game_badge}
    <a href="/" class="btn btn-ghost btn-sm" target="_blank">↗ Player view</a>
    <a href="/admin/logout" class="btn btn-ghost btn-sm">Logout</a>
  </div>
</div>
<div class="main">
  {fh}

  <!-- Stats -->
  <div class="stats-row">
    <div class="stat-box"><div class="n">{len(state['players'])}</div><div class="l">Players joined</div></div>
    <div class="stat-box"><div class="n">{len(state['news_feed'])}/{len(state['news_pool'])}</div><div class="l">News released</div></div>
    <div class="stat-box"><div class="n">{total_trades}</div><div class="l">Total trades</div></div>
    <div class="stat-box"><div class="n" id="adm-tick" style="font-size:20px">—</div><div class="l">Last price tick</div></div>
  </div>

  <!-- Controls -->
  <div class="ctrl-row">
    <span style="font-size:14px;font-weight:500">Game controls</span>
    <div class="spacer"></div>
    <form method="POST" action="/admin/toggle">
      <button type="submit" class="btn {toggle_cls}">{toggle_label}</button>
    </form>
    <form method="POST" action="/admin/reset_prices">
      <button type="submit" class="btn btn-ghost">↺ Reset prices</button>
    </form>
  </div>

  <!-- News -->
  <div class="sec-title">📰 News Clippings — Release Controls</div>
  <div class="news-grid">{news_cards}</div>

  <!-- Leaderboard -->
  <div class="sec-title">🏆 Live Leaderboard</div>
  <div class="card" style="padding:0;overflow:hidden;margin-bottom:28px">
    <table>
      <thead><tr><th>Rank</th><th>Name</th><th>Team</th><th>Cash</th><th>Portfolio</th><th>P&L</th><th>Trades</th><th>Joined</th></tr></thead>
      <tbody id="adm-lb">{lb_rows}</tbody>
    </table>
  </div>

  <!-- Price table -->
  <div class="sec-title">💹 Live Prices</div>
  <div class="card" style="padding:0;overflow:hidden;margin-bottom:28px">
    <table>
      <thead><tr><th>Ticker</th><th>Stock</th><th>Base</th><th>Current</th><th>Change</th><th>Trend</th></tr></thead>
      <tbody id="adm-prices">{price_rows}</tbody>
    </table>
  </div>

  <!-- Danger zone -->
  <div class="danger-zone">
    <h3>⚠ Danger Zone</h3>
    <p>Full reset clears all players, portfolios, trade history, and news — resets prices to base values. This cannot be undone.</p>
    <form method="POST" action="/admin/full_reset" onsubmit="return confirm('Full reset — are you absolutely sure? This deletes all players and resets everything.')">
      <button type="submit" class="btn btn-danger">🔄 Full Game Reset</button>
    </form>
  </div>
</div>

<script>
// Draw admin sparklines
function drawAdminSpark(canvas) {{
  const vals = JSON.parse(canvas.dataset.vals || '[]');
  const color = canvas.dataset.color;
  if (vals.length < 2) return;
  const w = canvas.width, h = canvas.height;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, w, h);
  const min = Math.min(...vals), max = Math.max(...vals);
  const range = max - min || 1;
  const pts = vals.map((v, i) => [i / (vals.length-1) * w, h - ((v-min)/range)*(h-3)-2]);
  ctx.beginPath();
  pts.forEach(([x,y],i) => i===0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y));
  ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
  // last dot
  ctx.beginPath();
  ctx.arc(pts[pts.length-1][0], pts[pts.length-1][1], 2.5, 0, Math.PI*2);
  ctx.fillStyle = color; ctx.fill();
}}
document.querySelectorAll('[id^="as-"]').forEach(drawAdminSpark);

// Live refresh
async function admRefresh() {{
  const r = await fetch('/api/admin_state');
  const d = await r.json();
  if (!d.ok) return;
  document.getElementById('adm-tick').textContent = d.last_tick || '—';
  // Rebuild price table rows inline
  const tbody = document.getElementById('adm-prices');
  if (tbody) {{
    tbody.querySelectorAll('canvas').forEach(c => {{
      const ticker = c.id.replace('as-','');
      const hist = d.price_history[ticker] || [];
      const vals = hist.map(h => h[1]);
      c.dataset.vals = JSON.stringify(vals);
      const p = d.prices[ticker];
      if (!p) return;
      const base = parseFloat(c.closest('tr').children[2].textContent.replace('₹','').replace(',',''));
      const chg = ((p.current - p.base) / p.base * 100).toFixed(2);
      const up = chg >= 0;
      const tr = c.closest('tr');
      tr.children[3].textContent = '₹' + p.current.toLocaleString('en-IN',{{minimumFractionDigits:2}});
      tr.children[3].className = up ? 'up' : 'dn';
      tr.children[4].textContent = (up?'+':'') + chg + '%';
      tr.children[4].className = up ? 'up' : 'dn';
      drawAdminSpark(c);
    }});
  }}
  // Leaderboard
  const lb = document.getElementById('adm-lb');
  if (lb && d.leaderboard) {{
    const medals = ['🥇','🥈','🥉'];
    lb.innerHTML = d.leaderboard.map((p,i) => {{
      const gc = p.gain >= 0 ? 'up' : 'dn';
      return `<tr>
        <td>${{medals[i] || '#'+(i+1)}}</td>
        <td><strong>${{p.name}}</strong></td>
        <td style="color:var(--text2)">${{p.team}}</td>
        <td>₹${{p.balance.toLocaleString('en-IN',{{maximumFractionDigits:0}})}}</td>
        <td>₹${{p.pv.toLocaleString('en-IN',{{maximumFractionDigits:0}})}}</td>
        <td class="${{gc}}">${{p.gain>=0?'+':''}}₹${{Math.abs(p.gain).toLocaleString('en-IN',{{maximumFractionDigits:0}})}}</td>
        <td style="color:var(--text2)">${{p.trades}}</td>
        <td style="font-size:12px;color:var(--text3)">${{p.joined_at}}</td>
      </tr>`;
    }}).join('') || '<tr><td colspan="8" style="text-align:center;color:var(--text3);padding:24px">No players yet.</td></tr>';
  }}
}}
setInterval(admRefresh, 3000);
</script>""")

def render_admin_login(flash=""):
    fh = f'<div class="flash flash-err">{flash}</div>' if flash else ""
    return page("Admin Login", f"""
<style>
body{{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
.wrap{{width:100%;max-width:380px}}
.logo{{font-size:24px;font-weight:700;text-align:center;margin-bottom:6px}}.logo em{{font-style:normal;color:var(--gold)}}
.sub{{text-align:center;color:var(--text2);font-size:14px;margin-bottom:28px}}
.field{{margin-bottom:16px}}.field label{{display:block;font-size:13px;color:var(--text2);margin-bottom:6px;font-weight:500}}
.field input{{width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:var(--radius);padding:10px 14px;color:var(--text);font-size:15px;outline:none}}
.field input:focus{{border-color:var(--gold)}}
</style>
<div class="wrap">
  <div class="logo"><em>⚡ Admin</em> Login</div>
  <div class="sub">STOCK<span style="font-size:10px">EX</span> — Control Panel</div>
  {fh}
  <div class="card">
    <form method="POST" action="/admin/login">
      <div class="field"><label>Admin password</label><input type="password" name="password" placeholder="Enter password" required autofocus/></div>
      <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center;padding:12px">Enter →</button>
    </form>
  </div>
</div>""")

# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(__name__)


def player_token():
    return request.cookies.get("player_token")


def get_current_player():
    tok = player_token()
    return get_player(tok) if tok else None


def is_admin():
    return request.cookies.get("admin_token") in state["admin_sessions"]


@app.before_request
def _tick_before_request():
    run_price_ticks()


# ── Pages (GET) ─────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
@app.route("/register", methods=["GET"])
def get_register():
    return render_register()


@app.route("/trade", methods=["GET"])
def get_trade():
    p = get_current_player()
    if not p:
        return flask_redirect("/")
    return render_trade(p)


@app.route("/admin", methods=["GET"])
def get_admin():
    if is_admin():
        return render_admin()
    return flask_redirect("/admin/login")


@app.route("/admin/login", methods=["GET"])
def get_admin_login():
    return render_admin_login()


@app.route("/admin/logout", methods=["GET"])
def get_admin_logout():
    tok = request.cookies.get("admin_token")
    state["admin_sessions"].discard(tok)
    resp = make_response(flask_redirect("/admin/login"))
    resp.set_cookie("admin_token", "", max_age=0, path="/")
    return resp


# ── JSON APIs (GET) ──────────────────────────────────────────────────────────

@app.route("/api/state", methods=["GET"])
def api_state():
    p = get_current_player()
    if not p:
        return jsonify({"ok": False})
    pv = portfolio_value(p)
    return jsonify({
        "ok": True,
        "prices": state["prices"],
        "price_history": state["price_history"],
        "player": {
            "balance": p["balance"],
            "portfolio_value": pv,
            "portfolio": p["portfolio"],
        },
        "news_feed": state["news_feed"],
    })


@app.route("/api/admin_state", methods=["GET"])
def api_admin_state():
    if not is_admin():
        return jsonify({"ok": False})
    hist = state["price_history"]
    last_tick = hist[list(hist.keys())[0]][-1][0] if any(hist[t] for t in hist) else None
    sorted_players = sorted(state["players"].values(), key=portfolio_value, reverse=True)
    lb = [{"name": p["name"], "team": p["team"], "balance": p["balance"],
           "pv": portfolio_value(p), "gain": portfolio_value(p) - 100000,
           "trades": p["trades"], "joined_at": p["joined_at"]} for p in sorted_players]
    return jsonify({
        "ok": True,
        "prices": state["prices"],
        "price_history": hist,
        "last_tick": last_tick,
        "leaderboard": lb,
    })


# ── Actions (POST) ───────────────────────────────────────────────────────────

@app.route("/register", methods=["POST"])
def post_register():
    name = (request.form.get("name") or "").strip()
    team = (request.form.get("team") or "").strip()
    if not name or not team:
        return render_register("Please fill in both fields.", "err")
    tok = generate_token()
    with state["lock"]:
        state["players"][tok] = {
            "name": name, "team": team,
            "balance": 100000.0,
            "portfolio": {},
            "trades": 0,
            "joined_at": datetime.now().strftime("%H:%M:%S"),
        }
    resp = make_response(flask_redirect("/trade"))
    resp.set_cookie("player_token", tok, path="/", httponly=True)
    return resp


@app.route("/api/trade", methods=["POST"])
def post_trade():
    p = get_current_player()
    if not p:
        return jsonify({"ok": False, "msg": "Not logged in."})
    if not state["game_active"]:
        return jsonify({"ok": False, "msg": "Game is paused. Wait for admin to start."})
    try:
        body = request.get_json(force=True)
        ticker = body["ticker"]
        action = body["action"]
        qty = int(body.get("qty", 1))
    except Exception:
        return jsonify({"ok": False, "msg": "Invalid request."})
    if ticker not in state["prices"] or qty < 1:
        return jsonify({"ok": False, "msg": "Invalid ticker or quantity."})
    price = state["prices"][ticker]["current"]
    with state["lock"]:
        if action == "buy":
            cost = price * qty
            if p["balance"] < cost:
                return jsonify({"ok": False, "msg": f"Need ₹{cost:,.2f} but only ₹{p['balance']:,.2f} available."})
            p["balance"] -= cost
            p["portfolio"][ticker] = p["portfolio"].get(ticker, 0) + qty
            p["trades"] += 1
            return jsonify({"ok": True, "msg": f"✓ Bought {qty}× {ticker} @ ₹{price:,.2f}"})
        elif action == "sell":
            held = p["portfolio"].get(ticker, 0)
            if held < qty:
                return jsonify({"ok": False, "msg": f"You only hold {held} units of {ticker}."})
            p["balance"] += price * qty
            p["portfolio"][ticker] = held - qty
            p["trades"] += 1
            return jsonify({"ok": True, "msg": f"✓ Sold {qty}× {ticker} @ ₹{price:,.2f}"})
        else:
            return jsonify({"ok": False, "msg": "Unknown action."})


@app.route("/admin/login", methods=["POST"])
def post_admin_login():
    if request.form.get("password") == ADMIN_PASSWORD:
        tok = generate_token()
        state["admin_sessions"].add(tok)
        resp = make_response(flask_redirect("/admin"))
        resp.set_cookie("admin_token", tok, path="/", httponly=True)
        return resp
    return render_admin_login("Wrong password.")


@app.route("/admin/toggle", methods=["POST"])
def post_admin_toggle():
    if not is_admin():
        return flask_redirect("/admin/login")
    with state["lock"]:
        state["game_active"] = not state["game_active"]
        state["last_tick_time"] = time.time()
    msg = "▶ Game started! Players can now trade." if state["game_active"] else "⏸ Game paused."
    return render_admin(msg, "ok")


@app.route("/admin/release", methods=["POST"])
def post_admin_release():
    if not is_admin():
        return flask_redirect("/admin/login")
    nid = request.form.get("news_id")
    released_ids = {n["id"] for n in state["news_feed"]}
    item = next((n for n in state["news_pool"] if n["id"] == nid), None)
    if not item:
        return render_admin("News item not found.", "err")
    if nid in released_ids:
        return render_admin("Already released.", "err")
    with state["lock"]:
        entry = dict(item)
        entry["released_at"] = datetime.now().strftime("%H:%M:%S")
        state["news_feed"].append(entry)
        apply_news_impact(entry)
    return render_admin(f"📢 Released: \"{item['title']}\" — prices updated!", "ok")


@app.route("/admin/reset_prices", methods=["POST"])
def post_admin_reset_prices():
    if not is_admin():
        return flask_redirect("/admin/login")
    with state["lock"]:
        for p in state["prices"].values():
            p["current"] = p["base"]
    return render_admin("↺ Prices reset to base values.", "ok")


@app.route("/admin/full_reset", methods=["POST"])
def post_admin_full_reset():
    if not is_admin():
        return flask_redirect("/admin/login")
    do_full_reset()
    state["last_tick_time"] = time.time()
    return render_admin("🔄 Full reset complete — all players cleared, prices back to base.", "ok")


@app.errorhandler(404)
def not_found(_e):
    return "<h1 style='font-family:sans-serif;padding:40px'>404</h1>", 404


# ── Local dev entry point ────────────────────────────────────────────────────
# On Vercel, this file is imported and `app` is served directly — this block
# never runs there. Locally, `python api/index.py` starts a dev server.
if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════════════════╗
║  STOCKex - THE ULTIMATE TRADING CHALLENGE        ║
╠══════════════════════════════════════════════════╣
║  Player URL :  http://localhost:{PORT:<17} ║
║  Admin URL  :  http://localhost:{PORT}/admin{'':<7} ║
║  Password   :  {ADMIN_PASSWORD:<33} ║
╚══════════════════════════════════════════════════╝
  Press Ctrl+C to stop.
""")
    app.run(host="0.0.0.0", port=PORT, debug=False)
