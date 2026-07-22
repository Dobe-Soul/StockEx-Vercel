# STOCKex — The Ultimate Trading Challenge

A live, multiplayer stock-trading simulation game: players register, buy and
sell shares in 12 fictional companies, and an admin panel drives the game by
starting/pausing it and releasing news events that move prices. Originally a
single-file Python script using only `http.server`; converted here into a
[Flask](https://flask.palletsprojects.com/) app so it can be deployed on
[Vercel](https://vercel.com).

## What changed from the original

The original ran as a standalone script: it opened a socket with
`http.server`/`socketserver` and used a background thread to tick prices
every 2 seconds forever. Vercel's Python functions are **serverless** — there
is no long-running process and no background thread — so two things were
adapted:

1. **HTTP layer** — rewritten from a raw `http.server.BaseHTTPRequestHandler`
   into Flask routes (`api/index.py`). All the page/HTML-generation logic and
   game rules are unchanged.
2. **Price ticking** — instead of a thread sleeping and ticking forever, the
   app now records a timestamp of the last tick and, on every incoming
   request, computes how many `TICK_INTERVAL` windows have elapsed and
   "catches up" by applying that many price updates. Functionally this
   produces the same mean-reversion random-walk behavior as before, driven by
   requests instead of a timer.

## ⚠️ Important limitation: in-memory state

Game state (players, balances, prices, news feed) is still kept in a
plain Python dict in memory, same as the original. On Vercel this means:

- State **persists only as long as the same function instance stays warm**.
  A cold start (first request after inactivity, or a new deployment) resets
  everything back to the initial state.
- If Vercel scales your function out to multiple concurrent instances under
  load, each instance has **its own separate copy of the state** — players
  on one instance won't see trades from another. For a small group playing
  together in real time on a single warm instance, this generally works fine
  in practice, but it is not guaranteed.

This is fine for a casual game/demo with a handful of players. If you need
guaranteed consistency (bigger groups, high traffic, or you just don't want
state to vanish on redeploy), you have two options:
- **Simplest fix:** deploy this same Flask app to a platform that runs a
  persistent process instead of serverless functions — e.g. Render, Railway,
  Fly.io, or a small VPS — no code changes needed.
- **Stay on Vercel:** swap the in-memory `state` dict for an external store
  such as [Vercel KV](https://vercel.com/docs/storage/vercel-kv) or a hosted
  Postgres/Redis instance. This is a real refactor (every read/write of
  `state` would need to go through the store) and isn't done here.

## Project structure

```
.
├── api/
│   └── index.py       # Flask app — all routes, game logic, and HTML/JS
├── requirements.txt    # Python dependencies (Flask)
├── vercel.json         # Routes every path to the Flask app
├── .gitignore
└── README.md
```

## Running locally

```bash
pip install -r requirements.txt
python api/index.py
```

Then open:
- Player view: http://localhost:8080
- Admin panel: http://localhost:8080/admin (default password: `admin123`)

## Deploying to Vercel

1. Push this project to a GitHub repo.
2. In the [Vercel dashboard](https://vercel.com/new), import the repo. Vercel
   will detect `requirements.txt` and `api/index.py` and use the Python
   runtime automatically — no build step needed.
3. **Set an admin password.** Before (or right after) deploying, add an
   environment variable in Vercel's project settings:
   - `ADMIN_PASSWORD` — set this to something other than the default
     `admin123`, since the code will be public on GitHub.
4. Deploy. Your player view will be at your Vercel domain root, and the
   admin panel at `/admin`.

Or via the CLI:

```bash
npm i -g vercel
vercel
vercel env add ADMIN_PASSWORD
vercel --prod
```

## How to play

1. Share the root URL with players — they register with a name and team.
2. Open `/admin` (password from `ADMIN_PASSWORD`, or `admin123` locally) and
   click **Start Game** once everyone has joined.
3. Prices fluctuate on their own (mean-reverting random walk). Release news
   events from the admin panel to move specific stocks up or down.
4. Watch the live leaderboard in the admin panel; portfolio value = cash +
   holdings at current prices.
5. **Full Game Reset** in the admin panel wipes all players and prices back
   to the starting state.
