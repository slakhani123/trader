"""Strategy core: pure logic, no broker imports.

The daily cycle (all times ET):
  ~75 to ~10 minutes before the close:  run_buy_session()
      - self-heals first (inline reconcile), then refuses when halted,
        not a trading day, outside the window, already submitted today,
        an unsold overnight position exists, or the account holds the
        symbol outside the journal (expect_flat)
      - sizes whole shares from a reference price, journals the buy
        BEFORE placing it (fail closed), places MOC, waits for the
        closing-cross fill, then places the OPG (market-on-open) sell
        for the FILLED quantity via the protected-sell protocol below
  next morning (>= ~09:35):             run_reconcile()
      - records the opening-cross sell fill, computes the night's P&L,
        escalates stuck/ambiguous states loudly, evaluates kill criteria

Protected-sell protocol (the sell leg can otherwise leave an order the
journal never heard of -> a second sell -> a naked short):
  1. journal status=PLACING_SELL with a fresh attempt-unique orderRef
     (ovnbot:SYM:DATE:S1, :S2, ...) BEFORE transmitting;
  2. transmit inside try/except;
  3. on ack -> PENDING_SELL_FILL with the order id;
  4. any later recovery path FIRST looks for a resting sell with our
     ref prefix at the broker and adopts it - it never blindly places
     a second order.

Broker interactions go through the minimal Broker protocol so the whole
flow is unit-testable with a fake.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Protocol

import calendar_us as cal
from journal import Journal, Night

# sentinel: broker reports the exchange closed today (unscheduled closure)
SESSION_CLOSED = datetime(1970, 1, 1, tzinfo=cal.ET)


# ---------------------------------------------------------------- config
@dataclass
class SymbolConfig:
    symbol: str
    clip_usd: float
    max_shares: int = 1000


@dataclass
class KillCriteria:
    max_drawdown_pct: float = 25.0        # peak-to-trough on cumulative P&L,
                                          # as % of average nightly notional
    max_consecutive_losses: int = 15
    trailing_window_nights: int = 252     # halt if trailing sum < 0 after...
    trailing_min_nights: int = 126        # ...at least this many nights
    enabled: bool = True


@dataclass
class BotConfig:
    symbols: list[SymbolConfig]
    account: str = ""
    host: str = "127.0.0.1"
    port: int = 4001
    client_id: int = 17
    journal_path: str = "overnight.sqlite"
    dry_run: bool = False
    round_up_to_one_share: bool = True    # buy 1 share even if price > clip
    max_clip_overshoot: float = 1.6       # ...but never above clip * this
    expect_flat: bool = True              # refuse to buy if the account holds
                                          # the symbol outside the journal
    fill_wait_minutes: int = 12           # wait past the close for MOC fill
    notify_url: str = ""                  # optional POST target for alerts
    kill: KillCriteria = field(default_factory=KillCriteria)


def load_config(path: str) -> BotConfig:
    import tomllib
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    syms = [SymbolConfig(symbol=s["symbol"].upper(),
                         clip_usd=float(s["clip_usd"]),
                         max_shares=int(s.get("max_shares", 1000)))
            for s in raw.get("symbols", [])]
    if not syms:
        raise ValueError("config has no [[symbols]] entries")
    kc = raw.get("kill", {})
    kill = KillCriteria(
        max_drawdown_pct=float(kc.get("max_drawdown_pct", 25.0)),
        max_consecutive_losses=int(kc.get("max_consecutive_losses", 15)),
        trailing_window_nights=int(kc.get("trailing_window_nights", 252)),
        trailing_min_nights=int(kc.get("trailing_min_nights", 126)),
        enabled=bool(kc.get("enabled", True)),
    )
    b = raw.get("bot", {})
    journal_path = str(b.get("journal_path", "overnight.sqlite"))
    if not os.path.isabs(journal_path):
        # anchor to the config file, NOT the CWD: a manual run from another
        # directory must never silently open a fresh empty journal (which
        # would forget the halt flag and every idempotency guard)
        journal_path = os.path.join(os.path.dirname(os.path.abspath(path)),
                                    journal_path)
    return BotConfig(
        symbols=syms,
        account=str(b.get("account", "")),
        host=str(b.get("host", "127.0.0.1")),
        port=int(b.get("port", 4001)),
        client_id=int(b.get("client_id", 17)),
        journal_path=journal_path,
        dry_run=bool(b.get("dry_run", False)),
        round_up_to_one_share=bool(b.get("round_up_to_one_share", True)),
        max_clip_overshoot=float(b.get("max_clip_overshoot", 1.6)),
        expect_flat=bool(b.get("expect_flat", True)),
        fill_wait_minutes=int(b.get("fill_wait_minutes", 12)),
        notify_url=str(b.get("notify_url", "")),
        kill=kill,
    )


# ---------------------------------------------------------------- broker
@dataclass
class OrderResult:
    order_id: int
    status: str               # Submitted / Filled / Cancelled / Rejected /
                              # Executions (recovered from execution records)
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    commission: float = 0.0
    detail: str = ""


class Broker(Protocol):
    def reference_price(self, symbol: str) -> float | None:
        """Delayed/snapshot price for sizing. None if unavailable."""

    def session_close_today(self, symbol: str) -> datetime | None:
        """Today's actual session close from live contract details.
        SESSION_CLOSED if the exchange reports today closed; None if
        unavailable (fall back to the static calendar)."""

    def position_qty(self, symbol: str) -> int:
        """Current signed position in the account for symbol."""

    def open_sell_orders(self, symbol: str) -> list[tuple[int, str, int]]:
        """Working SELL orders for symbol: (order_id, orderRef, qty)."""

    def place_moc_buy(self, symbol: str, qty: int, ref: str) -> OrderResult: ...

    def place_opg_sell(self, symbol: str, qty: int, ref: str) -> OrderResult: ...

    def wait_for_fill(self, order_id: int, deadline: datetime) -> OrderResult:
        """Block until the order reaches a terminal state or deadline;
        on deadline the implementation cancels the order and reports the
        post-cancel truth (a cancel can race a fill)."""

    def order_fill(self, order_id: int, ref: str = "") -> OrderResult | None:
        """Latest known state of an order placed earlier. Same-session
        lookups may return a terminal broker status; recovery from
        execution records returns status='Executions' with the executed
        quantity - the CALLER must compare quantities before treating it
        as a complete fill. None if nothing is known."""


TERMINAL_DEAD = ("Rejected", "Cancelled", "ApiCancelled", "Inactive")


def order_ref(symbol: str, d: date, side: str) -> str:
    """Deterministic tag placed on every order (Execution.orderRef)."""
    return f"ovnbot:{symbol}:{d.isoformat()}:{side}"


def sell_ref_prefix(symbol: str, d: date) -> str:
    return order_ref(symbol, d, "S")


def next_sell_ref(night: Night) -> str:
    """Attempt-unique sell ref: ...:S1, :S2, ... so recovery lookups can
    never aggregate executions across separate placement attempts."""
    prev = night.sell_ref or ""
    prefix = sell_ref_prefix(night.symbol, date.fromisoformat(night.buy_date))
    attempt = 1
    if prev.startswith(prefix):
        try:
            attempt = int(prev[len(prefix):]) + 1
        except ValueError:
            attempt = 1
    return f"{prefix}{attempt}"


def good_fill(fill: OrderResult) -> bool:
    """A same-session fill we are willing to journal."""
    return (fill.status == "Filled" and fill.filled_qty > 0
            and fill.avg_fill_price > 0)


# ---------------------------------------------------------------- sizing
def size_shares(clip_usd: float, price: float, max_shares: int,
                round_up_to_one: bool, max_overshoot: float) -> tuple[int, str]:
    if price <= 0:
        return 0, "no usable reference price"
    qty = math.floor(clip_usd / price)
    if qty == 0:
        if not round_up_to_one:
            return 0, f"price {price:.2f} exceeds clip {clip_usd:.0f}"
        if price > clip_usd * max_overshoot:
            return 0, (f"price {price:.2f} exceeds clip x overshoot "
                       f"({clip_usd:.0f} x {max_overshoot})")
        qty = 1
    return min(qty, max_shares), ""


# ------------------------------------------------------------- kill logic
def evaluate_kill(nights: list[Night], kill: KillCriteria) -> str | None:
    """Return a halt reason, or None. `nights` = CLOSED nights, ascending.

    P&L and notional are grouped per buy_date so a k-symbol basket is
    evaluated as one portfolio night, not k separate rows (per-row
    accounting overstates basket drawdown ~k-fold)."""
    if not kill.enabled:
        return None
    by_date: dict[str, list[Night]] = {}
    for n in nights:
        if n.pnl is not None:
            by_date.setdefault(n.buy_date, []).append(n)
    if not by_date:
        return None
    dates = sorted(by_date)
    pnls = [sum(n.pnl for n in by_date[d]) for d in dates]
    # consecutive losing nights
    streak = 0
    for p in reversed(pnls):
        if p < 0:
            streak += 1
        else:
            break
    if streak >= kill.max_consecutive_losses:
        return f"{streak} consecutive losing nights (limit {kill.max_consecutive_losses})"
    # trailing-window sum
    window = pnls[-kill.trailing_window_nights:]
    if len(window) >= kill.trailing_min_nights and sum(window) < 0:
        return (f"trailing {len(window)}-night P&L is negative "
                f"({sum(window):.2f})")
    # drawdown vs average nightly (portfolio) notional
    notionals = [sum(n.buy_fill * n.qty for n in by_date[d] if n.buy_fill)
                 for d in dates]
    notionals = [x for x in notionals if x > 0]
    avg_notional = sum(notionals) / len(notionals) if notionals else 0.0
    if avg_notional > 0:
        cum = peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            max_dd = min(max_dd, cum - peak)
        dd_pct = -max_dd / avg_notional * 100
        if dd_pct >= kill.max_drawdown_pct:
            return (f"cumulative P&L drawdown {dd_pct:.1f}% of avg nightly "
                    f"notional (limit {kill.max_drawdown_pct}%)")
    return None


# ------------------------------------------------------------- buy window
def buy_window(d: date, actual_close: datetime | None = None
               ) -> tuple[datetime, datetime]:
    """(earliest, latest) submission time for today's MOC."""
    close = actual_close or cal.close_dt(d)
    latest = close - cal.MOC_CUTOFF_BEFORE_CLOSE - timedelta(minutes=5)
    earliest = close - timedelta(minutes=75)
    return earliest, latest


# ---------------------------------------------------------------- alerts
class Notifier:
    """Optional POST-a-line alerting (ntfy.sh style). Never raises, but
    a delivery failure is printed so cron logs show it."""

    def __init__(self, url: str) -> None:
        self.url = url

    def send(self, msg: str) -> None:
        if not self.url:
            return
        try:
            from urllib.request import Request, urlopen
            req = Request(self.url, data=msg.encode(),
                          headers={"Title": "overnight-bot"})
            urlopen(req, timeout=10)
        except Exception as exc:
            print(f"WARN  notify delivery failed: {exc}", flush=True)


# ------------------------------------------------- protected sell placing
def place_protected_sell(cfg: BotConfig, jr: Journal, broker: Broker,
                         notify: Notifier, night: Night, qty: int) -> int:
    """Journal-first OPG sell. Returns 0 resting / 1 rejected / 2 unknown."""
    ref = next_sell_ref(night)
    jr.update_night(night.id, sell_ref=ref, status="PLACING_SELL")
    try:
        sell = broker.place_opg_sell(night.symbol, qty, ref)
    except Exception as exc:
        msg = (f"{night.symbol}: OPG sell placement raised: {exc} - order "
               f"state unknown (ref {ref}); recovery will adopt or retry, "
               "never double-place")
        jr.log("ERROR", msg)
        notify.send(msg)
        return 2
    if sell.status in TERMINAL_DEAD:
        msg = (f"{night.symbol}: OPG sell REJECTED ({sell.detail}) - "
               "position unprotected; fallback will retry, else sell "
               "manually before 09:28 ET")
        jr.update_night(night.id, status="HELD", detail=msg)
        jr.log("ERROR", msg)
        notify.send(msg)
        return 1
    jr.update_night(night.id, sell_order_id=sell.order_id,
                    status="PENDING_SELL_FILL")
    jr.log("INFO", f"{night.symbol}: OPG SELL {qty} resting "
                   f"(order {sell.order_id}, ref {ref})")
    return 0


def adopt_resting_sell(jr: Journal, broker: Broker, night: Night) -> bool:
    """If a sell with our ref prefix is already working at the broker,
    adopt it into the journal instead of placing another. True if adopted
    (or already correctly recorded as resting)."""
    prefix = sell_ref_prefix(night.symbol,
                             date.fromisoformat(night.buy_date))
    for oid, ref, qty in broker.open_sell_orders(night.symbol):
        if ref.startswith(prefix):
            jr.update_night(night.id, sell_order_id=oid, sell_ref=ref,
                            status="PENDING_SELL_FILL")
            jr.log("INFO", f"{night.symbol}: adopted resting sell "
                           f"(order {oid}, ref {ref}, qty {qty})")
            return True
    return False


# ---------------------------------------------------------------- session
def run_buy_session(cfg: BotConfig, jr: Journal, broker: Broker,
                    now: datetime | None = None, force: bool = False) -> int:
    """Afternoon leg. Returns a process exit code (0 ok / 1 blocked or
    partial trouble / 2 hard error)."""
    notify = Notifier(cfg.notify_url)
    now = now or cal.now_et()
    today = now.date()

    if not cfg.dry_run and not cfg.notify_url:
        jr.log("WARN", "notify_url is empty - critical alerts will only "
                       "appear in this log")

    reason = jr.halted()
    if reason:
        jr.log("WARN", f"halted: {reason} - run 'resume' to re-enable")
        return 1
    if not cal.is_trading_day(today):
        jr.log("INFO", f"{today} is not a trading day; nothing to do")
        return 0

    # self-heal first: if the morning reconcile cron was missed, resolve
    # filled sells / recovered buys so stale open nights don't block today
    rc = 0
    if not cfg.dry_run:
        rc = run_reconcile(cfg, jr, broker, now)
    for sc in cfg.symbols:
        code = _buy_one(cfg, jr, broker, notify, sc, now, today, force)
        rc = max(rc, code)
    return rc


def _buy_one(cfg: BotConfig, jr: Journal, broker: Broker, notify: Notifier,
             sc: SymbolConfig, now: datetime, today: date, force: bool) -> int:
    sym = sc.symbol

    # idempotency: exactly one night per symbol per date
    if jr.night_for(sym, today):
        jr.log("INFO", f"{sym}: already have a night for {today}; skipping")
        return 0
    # never stack: an unsold overnight position blocks new buys
    open_nights = jr.open_nights(sym)
    if open_nights:
        msg = (f"{sym}: unsold overnight position from "
               f"{open_nights[0].buy_date} (status {open_nights[0].status}) - "
               f"not buying; run 'reconcile' or intervene manually")
        jr.log("ERROR", msg)
        notify.send(msg)
        return 1

    # timing gate (live liquid-hours override wins over the static calendar)
    actual_close = None if cfg.dry_run else broker.session_close_today(sym)
    if actual_close is SESSION_CLOSED:
        jr.log("INFO", f"{sym}: exchange reports no session today; skipping")
        return 0
    earliest, latest = buy_window(today, actual_close)
    if not force and not (earliest <= now <= latest):
        jr.log("INFO", f"{sym}: outside buy window "
                       f"({earliest:%H:%M}-{latest:%H:%M} ET, now {now:%H:%M}); "
                       "skipping (use --force to override)")
        return 0

    # kill criteria, evaluated on everything closed so far
    reason = evaluate_kill(jr.closed_nights(), cfg.kill)
    if reason:
        jr.set_halted(reason)
        msg = f"KILL CRITERIA TRIPPED - halting: {reason}"
        jr.log("ERROR", msg)
        notify.send(msg)
        return 1

    # sizing
    price = broker.reference_price(sym)
    if price is None:
        if cfg.dry_run:
            jr.log("INFO", f"DRY-RUN {sym}: window/idempotency checks passed; "
                           "no price source connected so sizing skipped")
            return 0
        msg = f"{sym}: no reference price available; not buying"
        jr.log("ERROR", msg)
        notify.send(msg)
        return 1
    qty, why = size_shares(sc.clip_usd, price, sc.max_shares,
                           cfg.round_up_to_one_share, cfg.max_clip_overshoot)
    if qty <= 0:
        jr.log("WARN", f"{sym}: sized to 0 shares ({why}); skipping")
        return 1

    if cfg.dry_run:
        jr.log("INFO", f"DRY-RUN {sym}: would place MOC BUY {qty} @ ~{price:.2f} "
                       f"then OPG SELL {qty}")
        return 0

    # unmanaged-position guard: shares in the account with no open night
    # means either a prior crash left an orphan or the user holds this
    # symbol outside the bot - both are reasons not to add exposure.
    if cfg.expect_flat:
        pos = broker.position_qty(sym)
        if pos != 0:
            msg = (f"{sym}: account holds {pos} shares outside the journal - "
                   "not buying (set expect_flat=false only if you deliberately "
                   "hold this symbol alongside the bot)")
            jr.log("ERROR", msg)
            notify.send(msg)
            return 1

    # journal FIRST, then place: a crash between the two leaves a blocking
    # PENDING_BUY row (fail closed) instead of a live order the journal
    # has never heard of (double exposure).
    buy_ref = order_ref(sym, today, "B")
    night_id = jr.create_night(sym, today, qty, None, price,
                               status="PENDING_BUY",
                               detail=f"ref_price={price:.4f}")
    try:
        res = broker.place_moc_buy(sym, qty, buy_ref)
    except Exception as exc:
        msg = f"{sym}: MOC buy placement raised: {exc} - night left PENDING_BUY"
        jr.update_night(night_id, detail=msg)
        jr.log("ERROR", msg)
        notify.send(msg)
        return 2
    jr.update_night(night_id, buy_order_id=res.order_id)
    jr.log("INFO", f"{sym}: MOC BUY {qty} submitted (order {res.order_id})")

    # wait for the closing-cross fill
    close = actual_close or cal.close_dt(today)
    deadline = close + timedelta(minutes=cfg.fill_wait_minutes)
    fill = broker.wait_for_fill(res.order_id, deadline)
    if fill.status in TERMINAL_DEAD and fill.filled_qty <= 0:
        msg = (f"{sym}: MOC buy ended {fill.status} ({fill.detail}) - "
               "no overnight position tonight")
        jr.update_night(night_id, status="ERROR", detail=msg)
        jr.log("ERROR", msg)
        notify.send(msg)
        return 1
    if not good_fill(fill):
        # NOT a clean terminal fill (timeout, connection loss, zero-price
        # status race): the order may still fill after we stop looking.
        # Keep the night open and blocking; reconcile resolves it.
        msg = (f"{sym}: MOC buy state {fill.status} "
               f"(qty {fill.filled_qty} @ {fill.avg_fill_price}) at deadline - "
               "keeping night PENDING_BUY; reconcile will resolve it")
        jr.update_night(night_id, detail=msg)
        jr.log("ERROR", msg)
        notify.send(msg)
        return 2
    night = jr.get_night(night_id)
    jr.update_night(night_id, qty=fill.filled_qty, buy_fill=fill.avg_fill_price,
                    buy_commission=fill.commission, status="HELD")
    jr.log("INFO", f"{sym}: bought {fill.filled_qty} @ {fill.avg_fill_price:.4f} "
                   f"(comm {fill.commission:.2f})")

    # place the OPG sell for the FILLED quantity - rests until the open
    night = jr.get_night(night_id)
    code = place_protected_sell(cfg, jr, broker, notify, night,
                                fill.filled_qty)
    return 0 if code == 0 else max(code, 1)


def run_sell_fallback(cfg: BotConfig, jr: Journal, broker: Broker,
                      now: datetime | None = None) -> int:
    """Safety net for HELD / PLACING_SELL nights: adopt a resting sell if
    one exists, otherwise place one (same evening, or before the 09:28 ET
    cutoff next morning)."""
    notify = Notifier(cfg.notify_url)
    now = now or cal.now_et()
    rc = 0
    for n in jr.open_nights():
        if n.status not in ("HELD", "PLACING_SELL"):
            continue
        # a sell may already be working at the broker (PLACING_SELL crash
        # window, or a lost journal update): adopt before placing anything
        if not cfg.dry_run and adopt_resting_sell(jr, broker, n):
            continue
        # Same-day evening recovery is always valid - the new OPG rests
        # for tomorrow's open. On a LATER day, an OPG only exits at
        # TODAY'S open, so past the 09:28 cutoff the exit is missed and
        # needs a human.
        buy_d = date.fromisoformat(n.buy_date)
        if now.date() > buy_d and now.time() >= cal.MOO_CUTOFF:
            msg = (f"{n.symbol}: past MOO cutoff with an unsold position "
                   f"({n.qty} shares from {n.buy_date}) - manual action needed")
            jr.log("ERROR", msg)
            notify.send(msg)
            rc = max(rc, 2)
            continue
        held = broker.position_qty(n.symbol)
        qty = min(n.qty, max(held, 0))
        if qty <= 0:
            msg = (f"{n.symbol}: journal says {n.status} but account position "
                   f"is {held} - if it was sold outside the bot, record the "
                   "exit manually")
            jr.update_night(n.id, status="ERROR", detail=msg)
            jr.log("ERROR", msg)
            notify.send(msg)
            rc = max(rc, 2)
            continue
        if qty < n.qty:
            jr.update_night(n.id, qty=qty,
                            detail=f"qty reduced to account position {qty} "
                                   f"(journal had {n.qty})")
            jr.log("WARN", f"{n.symbol}: selling {qty} (journal had {n.qty}; "
                           "account holds fewer)")
            n = jr.get_night(n.id)
        if cfg.dry_run:
            jr.log("INFO", f"DRY-RUN {n.symbol}: would place OPG SELL {qty}")
            continue
        rc = max(rc, place_protected_sell(cfg, jr, broker, notify, n, qty))
    return rc


def run_reconcile(cfg: BotConfig, jr: Journal, broker: Broker,
                  now: datetime | None = None) -> int:
    """Record sell fills and P&L; escalate stuck states; evaluate kill
    criteria. Honest by construction: a night is only CLOSED on a fill
    whose quantity matches and whose price is positive - anything else
    is surfaced, never silently booked."""
    notify = Notifier(cfg.notify_url)
    now = now or cal.now_et()
    rc = 0
    for n in jr.open_nights():
        buy_d = date.fromisoformat(n.buy_date)
        stale = now.date() > buy_d       # the exit open has passed

        if n.status == "PLACING_SELL":
            # crash window: order may or may not have reached the broker
            if adopt_resting_sell(jr, broker, n):
                n = jr.get_night(n.id)   # fall through as PENDING_SELL_FILL
            else:
                fill = broker.order_fill(-1, ref=n.sell_ref)
                if fill and fill.filled_qty > 0:
                    rc = max(rc, _book_sell(jr, notify, n, fill, now))
                    continue
                jr.update_night(n.id, status="HELD",
                                detail="PLACING_SELL resolved: no resting "
                                       "order, no executions")
                jr.log("WARN", f"{n.symbol}: sell never reached the broker; "
                               "back to HELD for the fallback")
                rc = max(rc, 1)
                continue

        if n.status == "PENDING_SELL_FILL" and n.sell_order_id is not None:
            fill = broker.order_fill(n.sell_order_id, ref=n.sell_ref)
            if fill is None:
                if not stale:
                    jr.log("INFO", f"{n.symbol}: sell resting for the next "
                                   "open; nothing to record yet")
                    continue
                # exit open has passed and we know nothing: distinguish
                # "still resting for tomorrow" / "gone with position" /
                # "gone and flat"
                if any(ref.startswith(sell_ref_prefix(n.symbol, buy_d))
                       for _, ref, _ in broker.open_sell_orders(n.symbol)):
                    jr.log("INFO", f"{n.symbol}: sell still resting at the "
                                   "broker (next open)")
                    continue
                pos = broker.position_qty(n.symbol)
                if pos > 0:
                    msg = (f"{n.symbol}: resting sell vanished (cancelled/"
                           f"expired?) with {pos} shares still held - "
                           "reverting to HELD for the fallback")
                    jr.update_night(n.id, status="HELD", detail=msg)
                    jr.log("ERROR", msg)
                    notify.send(msg)
                    rc = max(rc, 2)
                else:
                    msg = (f"{n.symbol}: account is flat but the sale could "
                           "not be recovered from execution records (they "
                           "expire same-day) - record the exit price "
                           "manually in the journal")
                    jr.update_night(n.id, status="ERROR", detail=msg)
                    jr.log("ERROR", msg)
                    notify.send(msg)
                    rc = max(rc, 2)
                continue
            if fill.filled_qty > 0 or fill.status in TERMINAL_DEAD:
                rc = max(rc, _book_sell(jr, notify, n, fill, now))
            else:
                jr.log("WARN", f"{n.symbol}: sell order {n.sell_order_id} "
                               f"still {fill.status}")
                rc = max(rc, 1)

        elif n.status == "PENDING_BUY":
            # buy session died before recording the fill (possibly even
            # before recording the order id - the orderRef still finds it
            # same-day)
            fill = broker.order_fill(n.buy_order_id or -1,
                                     order_ref(n.symbol, buy_d, "B"))
            pos = broker.position_qty(n.symbol)
            if fill and fill.filled_qty > 0 and fill.avg_fill_price > 0:
                jr.update_night(n.id, qty=fill.filled_qty,
                                buy_fill=fill.avg_fill_price,
                                buy_commission=fill.commission, status="HELD")
                jr.log("INFO", f"{n.symbol}: recovered buy fill "
                               f"{fill.filled_qty} @ {fill.avg_fill_price:.4f}")
                rc = max(rc, 1)   # still needs a sell -> fallback below
            elif fill and fill.status in TERMINAL_DEAD and fill.filled_qty <= 0:
                jr.update_night(n.id, status="ERROR",
                                detail=f"buy ended {fill.status}")
                jr.log("ERROR", f"{n.symbol}: buy order ended {fill.status}")
            elif fill is None and pos == 0:
                # no execution anywhere and the account is flat: the order
                # never reached the broker - safe to clear the block
                jr.update_night(n.id, status="ERROR",
                                detail="no execution found and account flat")
                jr.log("WARN", f"{n.symbol}: PENDING_BUY {n.buy_date} cleared - "
                               "no execution found, account flat")
            elif stale and pos > 0:
                # executions expired overnight but the shares are there:
                # protect the exit NOW; the fill price is estimated
                est = n.prev_close or 0.0
                qty = min(n.qty, pos)
                msg = (f"{n.symbol}: buy fill from {n.buy_date} recovered by "
                       f"POSITION ({qty} shares); fill price unknown - using "
                       f"reference {est:.4f}; P&L for this night is an "
                       "ESTIMATE - correct it from your statement")
                jr.update_night(n.id, qty=qty, buy_fill=est or None,
                                status="HELD", detail=msg)
                jr.log("ERROR", msg)
                notify.send(msg)
                rc = max(rc, 2)
            else:
                jr.log("WARN", f"{n.symbol}: buy order state unknown; "
                               "keeping night open")
                rc = max(rc, 1)

    # a HELD night coming out of the loop still needs its exit protected;
    # do it here rather than depending on cron ordering (during the
    # GMT/EDT mismatch weeks the sell cron can land after reconcile)
    if any(n.status in ("HELD", "PLACING_SELL") for n in jr.open_nights()):
        rc = max(rc, run_sell_fallback(cfg, jr, broker, now))

    reason = evaluate_kill(jr.closed_nights(), cfg.kill)
    if reason and not jr.halted():
        jr.set_halted(reason)
        msg = f"KILL CRITERIA TRIPPED - halting: {reason}"
        jr.log("ERROR", msg)
        notify.send(msg)
        rc = max(rc, 1)
    return rc


def _book_sell(jr: Journal, notify: Notifier, n: Night, fill: OrderResult,
               now: datetime) -> int:
    """Book a sell outcome honestly. Returns rc contribution."""
    if fill.filled_qty == n.qty and fill.avg_fill_price > 0:
        pnl = jr.close_night(n.id, now.date(), fill.avg_fill_price,
                             fill.commission)
        jr.log("INFO", f"{n.symbol}: night {n.buy_date} closed "
                       f"@ {fill.avg_fill_price:.4f}  P&L {pnl:+.2f}")
        return 0
    if fill.filled_qty <= 0 and fill.status in TERMINAL_DEAD:
        msg = (f"{n.symbol}: resting sell ended {fill.status} unfilled - "
               "position still open; reverting to HELD")
        jr.update_night(n.id, status="HELD", detail=msg)
        jr.log("ERROR", msg)
        notify.send(msg)
        return 2
    # partial fill or zero-price record: never book silently
    msg = (f"{n.symbol}: sell recovered as qty {fill.filled_qty} of {n.qty} "
           f"@ {fill.avg_fill_price} ({fill.status}) - NOT auto-booking; "
           "resolve manually (partial exits are not auto-managed)")
    jr.update_night(n.id, status="ERROR", detail=msg)
    jr.log("ERROR", msg)
    notify.send(msg)
    return 2


def status_summary(jr: Journal) -> str:
    closed = jr.closed_nights()
    pnls = [n.pnl for n in closed if n.pnl is not None]
    lines = []
    halted = jr.halted()
    lines.append(f"halted: {halted or 'no'}")
    lines.append(f"open nights: {len(jr.open_nights())}")
    for n in jr.open_nights():
        lines.append(f"  {n.symbol} {n.buy_date} qty={n.qty} status={n.status}")
    lines.append(f"closed nights: {len(closed)}")
    if pnls:
        total = sum(pnls)
        wins = sum(1 for p in pnls if p > 0)
        lines.append(f"  total P&L: {total:+.2f}  win rate: {wins/len(pnls):.1%}")
        lines.append(f"  last 5: {[round(p, 2) for p in pnls[-5:]]}")
    return "\n".join(lines)
