#!/usr/bin/env python3
"""Overnight bot CLI - buy MU (or a basket) at the Nasdaq closing cross,
sell at the next opening cross, via IBKR.

Commands
--------
  buy         afternoon leg: MOC buy, wait for the 16:00 fill, place the
              resting OPG sell for tomorrow's opening cross
  sell        morning fallback: place OPG sells for any HELD night that
              lost its resting sell (run before 09:28 ET)
  reconcile   morning: record sell fills, compute P&L, evaluate kill
              criteria
  status      print journal summary (no broker connection)
  halt        manually halt future buys (records a reason)
  resume      clear a halt (manual or kill-criteria)
  check       connect to IB Gateway, print account/position/price sanity

Usage:  overnight_bot.py <command> [--config config.toml] [--dry-run]
        [--force]

Exit codes: 0 ok / 1 blocked or degraded / 2 needs attention.
See README.md for cron examples and setup.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import strategy
from journal import Journal


def make_broker(cfg: strategy.BotConfig):
    from broker_ib import IBBroker
    return IBBroker(cfg.host, cfg.port, cfg.client_id, cfg.account)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["buy", "sell", "reconcile", "status",
                                        "halt", "resume", "check"])
    ap.add_argument("--config", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.toml"))
    ap.add_argument("--dry-run", action="store_true",
                    help="log intended orders without placing them")
    ap.add_argument("--force", action="store_true",
                    help="bypass the submission-window gate (manual use)")
    ap.add_argument("--reason", default="manual halt",
                    help="reason recorded by the halt command")
    args = ap.parse_args()

    cfg = strategy.load_config(args.config)
    if args.dry_run:
        cfg.dry_run = True
    jr = Journal(cfg.journal_path)

    if args.command == "status":
        print(strategy.status_summary(jr))
        return 0
    if args.command == "halt":
        jr.set_halted(args.reason)
        jr.log("WARN", f"halted manually: {args.reason}")
        return 0
    if args.command == "resume":
        jr.set_halted(None)
        jr.log("INFO", "halt cleared; buys re-enabled")
        return 0

    broker = None
    try:
        if cfg.dry_run and args.command == "buy":
            # dry-run prefers real prices but works offline too
            try:
                broker = make_broker(cfg)
            except Exception as exc:
                jr.log("INFO", f"dry-run without broker connection ({exc})")
                broker = _DryRunBroker()
        else:
            broker = make_broker(cfg)
        if args.command == "check":
            for sc in cfg.symbols:
                px = broker.reference_price(sc.symbol)
                pos = broker.position_qty(sc.symbol)
                close = broker.session_close_today(sc.symbol)
                print(f"{sc.symbol}: ref_price={px} position={pos} "
                      f"today_close={close} account={broker.account}")
            return 0
        if args.command == "buy":
            return strategy.run_buy_session(cfg, jr, broker, force=args.force)
        if args.command == "sell":
            return strategy.run_sell_fallback(cfg, jr, broker)
        if args.command == "reconcile":
            return strategy.run_reconcile(cfg, jr, broker)
        return 2
    finally:
        if broker is not None and hasattr(broker, "disconnect"):
            broker.disconnect()


class _DryRunBroker:
    """Just enough broker for `buy --dry-run` without a connection."""

    def reference_price(self, symbol: str):
        return None  # dry-run buy logs the skip reason instead of trading

    def session_close_today(self, symbol: str):
        return None

    def position_qty(self, symbol: str) -> int:
        return 0

    def place_moc_buy(self, *_):        # pragma: no cover
        raise AssertionError("dry-run must not place orders")

    def place_opg_sell(self, *_):       # pragma: no cover
        raise AssertionError("dry-run must not place orders")

    def wait_for_fill(self, *_):        # pragma: no cover
        raise AssertionError("dry-run must not place orders")

    def order_fill(self, *_, **__):
        return None


if __name__ == "__main__":
    sys.exit(main())
