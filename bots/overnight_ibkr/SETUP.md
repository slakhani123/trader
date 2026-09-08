# Setup runbook — from nothing to first live night

Follow in order. Each stage ends with a check; don't move on until it
passes. UI menu names drift between IBKR releases — if a path below
doesn't match exactly, the setting still exists under a nearby name.

---

## Stage 1 — IBKR account (one-off, ~2–4 days incl. approvals)

1. **Open the account** at interactivebrokers.co.uk → Open Account →
   Individual. During the application:
   * Account type: **Margin** (not Cash). The bot never borrows — margin
     just removes T+1 settled-funds ambiguity in the daily
     sell-at-open / buy-at-close recycle. If you already have a cash
     account: Client Portal → Settings → Account Settings → Account
     Type → upgrade (1–2 days approval).
   * Trading permissions: **Stocks → United States**. (Add later under
     Settings → Account Settings → Trading Experience & Permissions.)
   * The W-8BEN is part of the application flow for UK residents.
2. **Two-factor**: install the **IBKR Mobile** app and activate IB Key
   (Client Portal → Settings → Security → Secure Login System). The
   unattended Gateway re-authenticates roughly weekly (Sunday) — you
   approve a push notification on your phone; that's the whole ritual.
3. **Pricing plan → Tiered**: Client Portal → Settings → Account
   Settings → Fee Structure (or "IB Pricing Plan") → **Tiered**.
   Fixed's $1.00/order minimum doubles this bot's cost. Takes effect
   next day.
4. **Fund in GBP**: Transfer & Pay → Transfer Funds → Deposit → GBP →
   bank transfer (Faster Payments, usually < 1 h). Size it to at least
   `clip_usd` + ~20% headroom.
5. **Convert GBP → USD once**: Client Portal → Transfer & Pay →
   **Convert Currency** → GBP to USD, full amount you intend to trade
   with. Cost: 0.002%, min $2. (Avoid per-trade auto-FX.)
6. **Do NOT** enable anything called "Reset API order ID sequence",
   ever. It can make historical order ids collide with journaled ones.

**Check:** Client Portal shows Margin account, US stock permission
approved, Tiered pricing, a USD cash balance, and IB Key active.

---

## Stage 2 — the server

Any always-on Linux box: a home machine/Raspberry Pi that never sleeps,
or a ~€4–6/mo VPS (Hetzner/Netcup/OVH — region irrelevant, auction
orders are not latency-sensitive). Ubuntu 22.04/24.04 assumed.

```bash
sudo timedatectl set-timezone Europe/London   # crontab.example assumes this
sudo apt update && sudo apt install -y python3 python3-venv git curl
# Docker (for the Gateway container route below):
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
```

**Check:** `date` prints London time; `docker run --rm hello-world` works;
`python3 --version` ≥ 3.11.

---

## Stage 3 — IB Gateway, running unattended

Two routes. **A (Docker) is the recommended one** — it bundles IB
Gateway + IBC (auto-login, daily auto-restart) in a maintained image.

### Route A — Docker (recommended)

```bash
mkdir -p ~/ib-gateway && cd ~/ib-gateway
cat > .env <<'EOF'
TWS_USERID=your_ibkr_username
TWS_PASSWORD=your_ibkr_password
EOF
chmod 600 .env

cat > docker-compose.yml <<'EOF'
services:
  ib-gateway:
    image: ghcr.io/gnzsnz/ib-gateway:stable
    restart: always
    env_file: .env
    environment:
      TRADING_MODE: live
      READ_ONLY_API: "no"
      TWOFA_TIMEOUT_ACTION: restart     # re-push the IB Key prompt until you approve
      AUTO_RESTART_TIME: 04:30 AM       # daily restart, outside all bot windows
      TIME_ZONE: Europe/London
      VNC_SERVER_PASSWORD: pick-something   # one-off GUI access, see below
    ports:
      - "127.0.0.1:4001:4003"   # bot connects to 127.0.0.1:4001 (live)
      - "127.0.0.1:5900:5900"   # VNC, localhost only
EOF

docker compose up -d && docker compose logs -f   # Ctrl-C once "logged in"
```

First start: approve the IB Key push on your phone. (Variable names are
the image's — if one is rejected, check
github.com/gnzsnz/ib-gateway-docker for the current README.)

**One-off GUI pass (important):** connect a VNC viewer to
`localhost:5900` (from your laptop:
`ssh -L 5900:127.0.0.1:5900 user@server`, then VNC to localhost). In
the Gateway window: Configure → Settings → API →
* **Settings**: "Enable ActiveX and Socket Clients" ✓, "Read-Only API" ✗,
  Socket port as shown (the container maps it), Trusted IPs: 127.0.0.1.
* **Precautions**: tick **"Bypass Order Precautions for API orders"** —
  otherwise size/value confirmation dialogs can silently strand an
  unattended order as Inactive.
Settings persist in the container volume across restarts.

### Route B — native IBC (no Docker)

Install IB Gateway standalone (Linux x64, "stable") from the IBKR site;
unzip IbcAlpha/IBC to `/opt/ibc`; set `IbLoginId/IbPassword`,
`TradingMode=live`, `ReadOnlyApi=no`, `TwofaTimeoutAction=restart` in
`~/ibc/config.ini`; run under `xvfb-run` from a systemd unit (Gateway is
a GUI app — headless needs a virtual display). Follow IBC's user guide
for the version variables in the start script. Same GUI pass as above
applies (via the Xvfb display + x11vnc, or do it once on a desktop and
copy `~/Jts/jts.xml` + settings dir over).

**Check:** `docker compose ps` shows the container Up; the weekly IB Key
push arrives and, once approved, logs show a successful login. Port
test from the server: `python3 -c "import socket;
socket.create_connection(('127.0.0.1',4001),5); print('gateway port ok')"`.

---

## Stage 4 — the bot

```bash
git clone <your-repo-url> ~/trader
cd ~/trader/bots/overnight_ibkr
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # installs ib_async
cp config.example.toml config.toml
```

Edit `config.toml`:

| field | set it to |
|---|---|
| `host`/`port` | `127.0.0.1` / `4001` (as mapped above) |
| `client_id` | leave `17` — then never change it for the life of this deployment |
| `account` | leave `""` for now; fill with the `U…` id that `check` prints |
| `journal_path` | leave as is (it resolves next to config.toml) |
| `notify_url` | see ntfy below — do not leave empty |
| `[[symbols]]` | `MU`, `clip_usd = 1000` for the pilot month (≈1 share) |
| `[kill]` | leave the defaults on — they are the pre-registered stops |

**Alerts (2 minutes, do it):** install the ntfy app on your phone,
subscribe to a topic with a long random name (it's the only secret),
then set `notify_url = "https://ntfy.sh/<your-topic>"`. Test:
`curl -d "bot alert test" https://ntfy.sh/<your-topic>` → phone buzzes.

**Checks:**

```bash
.venv/bin/python -m unittest discover -s tests   # 52 tests, no broker needed
.venv/bin/python overnight_bot.py check          # connects to the Gateway
# expect: MU: ref_price=<~current price> position=0 today_close=<16:00 ET> account=U1234567
.venv/bin/python overnight_bot.py buy --dry-run  # full logic, zero orders
```

Put the printed account id into `config.toml` → `account`.

---

## Stage 5 — schedule it

```bash
crontab -e
```

Paste the rows from `crontab.example`, with two edits on every line:
the real path, and the venv's python:

```
30 20 * * 1-5  cd /home/YOU/trader/bots/overnight_ibkr && flock -n .lock .venv/bin/python overnight_bot.py buy >> bot.log 2>&1
```

Why the rows look redundant: the bot gates itself on US-Eastern market
time, so each leg fires at several London times to cover the ~3 weeks a
year when London–New York is 4 h instead of 5, plus 13:00 ET half-days;
wrong firings exit silently, and `flock -n` guarantees one process at a
time. The **evening reconcile rows are not optional** — IBKR only serves
execution records same-day, and that run is what recovers a fill if the
buy process ever dies mid-wait.

Log hygiene (optional): `sudo tee /etc/logrotate.d/overnight-bot <<<'
/home/YOU/trader/bots/overnight_ibkr/bot.log { monthly rotate 6 compress missingok }'`

**Check:** next weekday, `grep -c "" bot.log` grows at the scheduled
times; off-window firings log "outside buy window … skipping".

---

## Stage 6 — the first live night, minute by minute (regular day, GMT)

| London | ET | what happens | where you see it |
|---|---|---|---|
| 20:30 | 15:30 | cron `buy`: sizes from delayed quote, journals, submits **MOC BUY** | bot.log "MOC BUY 1 submitted"; IBKR app shows the order |
| 21:00 | 16:00 | Nasdaq Closing Cross fills it | app: filled at the official close |
| ~21:01 | ~16:01 | bot records the fill, places the **OPG SELL** which rests overnight at IBKR | bot.log "bought 1 @ …" then "OPG SELL 1 resting" |
| 21:30 | 16:30 | cron `reconcile`: normally "nothing to record yet" | bot.log |
| 14:30 next day | 09:30 | Opening Cross executes the resting sell | app: filled at the official open |
| 14:50 | 09:50 | cron `reconcile` books it | bot.log "night … closed @ …  P&L +x.xx" |

Any deviation raises an ERROR line and an ntfy push. `overnight_bot.py
status` shows the running tally at any time.

---

## Stage 7 — the pilot month, then scaling

* **What the month is for:** measuring execution, not the edge. Check
  each journalled `buy_fill`/`sell_fill` against the official close/open
  prints (IBKR app daily bars, or any quote site's OHLC for MU). MOC/MOO
  fills should equal the prints to the cent; your realized round-trip
  cost should be just the ~$0.70 commissions (~7 bps at $1k).
* **Don't read the P&L as signal**: 20 nights of a 2.6%-σ leg is ±$120
  of pure noise on a $1k clip against a ~$32 expected gross.
* **Scale** by raising `clip_usd` (the $0.35/order minimum is flat up to
  ~100 shares, so bps cost falls as the clip grows). Adding basket
  names (TSM, MRVL, AMD, AVGO) adds ~$180/yr of minimums each —
  see `research/overnight_intraday/live_costs.md`.
* **When a kill criterion trips** it halts and pushes an alert. That
  was pre-registered for a reason: re-read the research before any
  `resume`, and treat "trailing-12M sum < 0" as the strategy telling
  you it may be over — the index-level version died exactly that way.

## Stage 8 — records

Everything needed for UK CGT lives in the journal:

```bash
sqlite3 overnight.sqlite -header -csv "select * from nights where status='CLOSED';" > nights.csv
```

~250 disposals/yr, same-day/30-day matching, each computed in GBP at
that day's rate — hand `nights.csv` plus IBKR's annual activity
statement to your tax software or accountant.

## Quick reference

```bash
overnight_bot.py status        # journal summary (no connection)
overnight_bot.py check         # gateway/account/price sanity
overnight_bot.py halt --reason "going on holiday"
overnight_bot.py resume
overnight_bot.py buy --force   # manual catch-up inside market hours
```

Invariants (from REVIEW.md): one host, one journal, one client_id,
forever; never TWS's "Reset API order ID sequence"; `notify_url` set.
