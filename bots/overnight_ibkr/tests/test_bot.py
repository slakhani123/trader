"""Unit tests for the overnight bot core (stdlib only, no ib_async).

Run:  python3 -m unittest discover -s tests -v   (from bots/overnight_ibkr)
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calendar_us as cal
import strategy
from journal import Journal
from strategy import (BotConfig, KillCriteria, OrderResult, SymbolConfig,
                      evaluate_kill, size_shares)


# ------------------------------------------------------------------ fakes
class FakeBroker:
    def __init__(self, price=1000.0, close_dt=None, position=0):
        self.price = price
        self.close_dt = close_dt
        self.position = position
        self.placed = []              # (kind, symbol, qty)
        self.next_order_id = 100
        self.buy_result = "fill"      # fill | reject | timeout
        self.sell_result = "accept"   # accept | reject
        self.fills: dict[int, OrderResult] = {}

    def reference_price(self, symbol):
        return self.price

    def session_close_today(self, symbol):
        return self.close_dt

    def position_qty(self, symbol):
        return self.position

    def place_moc_buy(self, symbol, qty, ref):
        if self.buy_result == "raise":
            raise ConnectionError("socket dropped")
        oid = self.next_order_id
        self.next_order_id += 1
        self.placed.append(("MOC_BUY", symbol, qty))
        return OrderResult(oid, "Submitted")

    def place_opg_sell(self, symbol, qty, ref):
        oid = self.next_order_id
        self.next_order_id += 1
        self.placed.append(("OPG_SELL", symbol, qty))
        if self.sell_result == "reject":
            return OrderResult(oid, "Rejected", detail="no permissions")
        return OrderResult(oid, "PreSubmitted")

    def wait_for_fill(self, order_id, deadline):
        if self.buy_result == "fill":
            return OrderResult(order_id, "Filled", filled_qty=self.placed[-2][2]
                               if len(self.placed) >= 2 else self.placed[-1][2],
                               avg_fill_price=self.price, commission=0.37)
        if self.buy_result == "reject":
            return OrderResult(order_id, "Rejected", detail="rejected")
        return OrderResult(order_id, "Submitted", detail="no fill by deadline")

    def order_fill(self, order_id, ref=""):
        return self.fills.get(order_id)


def make_cfg(tmp, **kw) -> BotConfig:
    return BotConfig(
        symbols=[SymbolConfig("MU", clip_usd=kw.pop("clip", 1000.0),
                              max_shares=kw.pop("max_shares", 100))],
        journal_path=os.path.join(tmp, "j.sqlite"),
        **kw,
    )


TUE = date(2026, 9, 8)  # a regular Tuesday
ET = cal.ET


def et(d: date, h: int, m: int) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=ET)


# --------------------------------------------------------------- calendar
class TestCalendar(unittest.TestCase):
    def test_known_holidays_2026(self):
        hs = cal.market_holidays(2026)
        for d in [date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
                  date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
                  date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26),
                  date(2026, 12, 25)]:
            self.assertIn(d, hs, d)

    def test_observed_shifts(self):
        # Jul 4 2026 is a Saturday -> observed Friday Jul 3
        self.assertIn(date(2026, 7, 3), cal.market_holidays(2026))
        # Jan 1 2022 was a Saturday -> NOT observed on Dec 31 2021
        self.assertNotIn(date(2021, 12, 31), cal.market_holidays(2021))
        self.assertTrue(cal.is_trading_day(date(2021, 12, 31)))
        # Jun 19 2027 is a Saturday -> observed Friday Jun 18 2027
        self.assertIn(date(2027, 6, 18), cal.market_holidays(2027))
        # Christmas 2027 is a Saturday -> observed Friday Dec 24
        self.assertIn(date(2027, 12, 24), cal.market_holidays(2027))
        self.assertNotIn(date(2027, 12, 24), cal.early_closes(2027))

    def test_early_closes(self):
        self.assertIn(date(2026, 11, 27), cal.early_closes(2026))
        self.assertIn(date(2026, 12, 24), cal.early_closes(2026))   # Thursday
        # Jul 3 2026 is the observed holiday, not an early close
        self.assertNotIn(date(2026, 7, 3), cal.early_closes(2026))
        # Jul 3 2028 (Monday, Jul 4 Tuesday) IS an early close
        self.assertIn(date(2028, 7, 3), cal.early_closes(2028))
        self.assertEqual(cal.close_time(date(2026, 11, 27)),
                         cal.EARLY_CLOSE)

    def test_good_friday_easter(self):
        self.assertIn(date(2027, 3, 26), cal.market_holidays(2027))
        self.assertIn(date(2028, 4, 14), cal.market_holidays(2028))

    def test_next_trading_day_skips(self):
        # Friday 2026-09-04 -> Monday 2026-09-07 is Labor Day -> Tuesday
        self.assertEqual(cal.next_trading_day(date(2026, 9, 4)),
                         date(2026, 9, 8))


# ----------------------------------------------------------------- sizing
class TestSizing(unittest.TestCase):
    def test_floor(self):
        self.assertEqual(size_shares(5000, 999.0, 100, True, 1.6)[0], 5)

    def test_round_up_to_one(self):
        qty, _ = size_shares(1000, 1016.0, 100, True, 1.6)
        self.assertEqual(qty, 1)

    def test_overshoot_cap(self):
        qty, why = size_shares(1000, 1700.0, 100, True, 1.6)
        self.assertEqual(qty, 0)
        self.assertIn("overshoot", why)

    def test_no_round_up(self):
        self.assertEqual(size_shares(1000, 1016.0, 100, False, 1.6)[0], 0)

    def test_max_shares(self):
        self.assertEqual(size_shares(100000, 10.0, 100, True, 1.6)[0], 100)

    def test_bad_price(self):
        self.assertEqual(size_shares(1000, 0.0, 100, True, 1.6)[0], 0)


# ------------------------------------------------------------ kill logic
class _N:
    """Minimal Night stand-in for evaluate_kill."""
    def __init__(self, pnl, buy_fill=1000.0, qty=1):
        self.pnl = pnl
        self.buy_fill = buy_fill
        self.qty = qty


class TestKill(unittest.TestCase):
    def test_consecutive_losses(self):
        nights = [_N(1.0)] * 5 + [_N(-1.0)] * 15
        kill = KillCriteria(max_consecutive_losses=15)
        self.assertIn("consecutive", evaluate_kill(nights, kill))

    def test_trailing_negative(self):
        nights = [_N(-0.5)] * 130
        kill = KillCriteria(max_consecutive_losses=999,
                            trailing_min_nights=126)
        self.assertIn("trailing", evaluate_kill(nights, kill))

    def test_drawdown(self):
        # +300 up, then -260: drawdown 26% of the ~$1000 nightly notional
        nights = [_N(3.0)] * 100 + [_N(-13.0), _N(-13.0)] * 10
        kill = KillCriteria(max_consecutive_losses=999,
                            trailing_min_nights=9999,
                            max_drawdown_pct=25.0)
        self.assertIn("drawdown", evaluate_kill(nights, kill))

    def test_clean(self):
        nights = [_N(1.0), _N(-0.5)] * 100
        self.assertIsNone(evaluate_kill(nights, KillCriteria()))

    def test_disabled(self):
        nights = [_N(-1.0)] * 300
        self.assertIsNone(evaluate_kill(nights, KillCriteria(enabled=False)))


# --------------------------------------------------------------- buy flow
class TestBuySession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = make_cfg(self.tmp)
        self.jr = Journal(self.cfg.journal_path)
        self.broker = FakeBroker(price=1016.0)
        self.now = et(TUE, 15, 30)

    def test_happy_path(self):
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        self.assertEqual(rc, 0)
        kinds = [p[0] for p in self.broker.placed]
        self.assertEqual(kinds, ["MOC_BUY", "OPG_SELL"])
        n = self.jr.night_for("MU", TUE)
        self.assertEqual(n.status, "PENDING_SELL_FILL")
        self.assertEqual(n.qty, 1)
        self.assertAlmostEqual(n.buy_fill, 1016.0)

    def test_idempotent_same_day(self):
        strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        placed = len(self.broker.placed)
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.broker.placed), placed)  # nothing new

    def test_open_position_blocks_buy(self):
        self.jr.create_night("MU", TUE - timedelta(days=1), 1, 1,
                             1000.0, status="HELD")
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        self.assertEqual(rc, 1)
        self.assertEqual(self.broker.placed, [])

    def test_window_gate(self):
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker,
                                      et(TUE, 12, 0))
        self.assertEqual(rc, 0)
        self.assertEqual(self.broker.placed, [])
        # early-close override from live contract details
        self.broker.close_dt = et(TUE, 13, 0)
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker,
                                      et(TUE, 12, 30))
        self.assertEqual(rc, 0)
        self.assertEqual([p[0] for p in self.broker.placed],
                         ["MOC_BUY", "OPG_SELL"])

    def test_force_bypasses_window(self):
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker,
                                      et(TUE, 12, 0), force=True)
        self.assertEqual(rc, 0)
        self.assertTrue(self.broker.placed)

    def test_halted_blocks(self):
        self.jr.set_halted("test")
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        self.assertEqual(rc, 1)
        self.assertEqual(self.broker.placed, [])

    def test_non_trading_day(self):
        sat = date(2026, 9, 5)
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker,
                                      et(sat, 15, 30))
        self.assertEqual(rc, 0)
        self.assertEqual(self.broker.placed, [])

    def test_buy_rejected_is_terminal(self):
        self.broker.buy_result = "reject"
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        self.assertEqual(rc, 1)
        self.assertEqual(self.jr.night_for("MU", TUE).status, "ERROR")
        # a dead buy does not block the next day
        wed = TUE + timedelta(days=1)
        self.broker.buy_result = "fill"
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker,
                                      et(wed, 15, 30))
        self.assertEqual(rc, 0)

    def test_buy_timeout_fails_closed_then_self_heals(self):
        # non-terminal at deadline: the night stays open and BLOCKS
        self.broker.buy_result = "timeout"
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        self.assertEqual(rc, 2)
        self.assertEqual(self.jr.night_for("MU", TUE).status, "PENDING_BUY")
        wed = TUE + timedelta(days=1)
        self.broker.buy_result = "fill"
        # next-day buy: inline reconcile finds no execution AND a flat
        # account -> clears the stale night, then buys normally
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker,
                                      et(wed, 15, 30))
        self.assertEqual(rc, 0)
        self.assertEqual(self.jr.night_for("MU", TUE).status, "ERROR")
        self.assertEqual(self.jr.night_for("MU", wed).status,
                         "PENDING_SELL_FILL")

    def test_buy_timeout_with_shares_stays_blocked(self):
        # order actually filled after the timeout: account is NOT flat,
        # so nothing self-clears and the next day refuses to buy
        self.broker.buy_result = "timeout"
        strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        self.broker.position = 1
        self.broker.buy_result = "fill"
        wed = TUE + timedelta(days=1)
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker,
                                      et(wed, 15, 30))
        self.assertEqual(rc, 1)
        self.assertIsNone(self.jr.night_for("MU", wed))

    def test_place_raises_leaves_blocking_night(self):
        self.broker.buy_result = "raise"
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        self.assertEqual(rc, 2)
        n = self.jr.night_for("MU", TUE)
        self.assertEqual(n.status, "PENDING_BUY")
        self.assertIsNone(n.buy_order_id)

    def test_expect_flat_blocks_unmanaged_position(self):
        self.broker.position = 7   # user holds MU outside the journal
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        self.assertEqual(rc, 1)
        self.assertEqual(self.broker.placed, [])
        self.cfg.expect_flat = False
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        self.assertEqual(rc, 0)
        self.assertTrue(self.broker.placed)

    def test_missed_reconcile_self_heals_before_buy(self):
        # yesterday's sell filled but the morning cron never ran
        mon = TUE - timedelta(days=1)
        nid = self.jr.create_night("MU", mon, 1, 40, 1000.0, status="HELD")
        self.jr.update_night(nid, buy_fill=1000.0, sell_order_id=41,
                             status="PENDING_SELL_FILL")
        self.broker.fills[41] = OrderResult(41, "Filled", 1, 1010.0, 0.37)
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        self.assertEqual(rc, 0)
        self.assertEqual(self.jr.get_night(nid).status, "CLOSED")
        self.assertEqual(self.jr.night_for("MU", TUE).status,
                         "PENDING_SELL_FILL")

    def test_sell_reject_leaves_held(self):
        self.broker.sell_result = "reject"
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        self.assertEqual(rc, 1)
        n = self.jr.night_for("MU", TUE)
        self.assertEqual(n.status, "HELD")

    def test_dry_run_places_nothing(self):
        self.cfg.dry_run = True
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        self.assertEqual(rc, 0)
        self.assertEqual(self.broker.placed, [])
        self.assertIsNone(self.jr.night_for("MU", TUE))

    def test_kill_trip_blocks_buy(self):
        for i in range(20):
            d = date(2026, 1, 5) + timedelta(days=i)
            nid = self.jr.create_night("MU", d, 1, i, 1000.0, status="HELD")
            self.jr.update_night(nid, buy_fill=1000.0, buy_commission=0.0)
            self.jr.close_night(nid, d + timedelta(days=1), 990.0, 0.0)
        self.cfg.kill = KillCriteria(max_consecutive_losses=15)
        rc = strategy.run_buy_session(self.cfg, self.jr, self.broker, self.now)
        self.assertEqual(rc, 1)
        self.assertEqual(self.broker.placed, [])
        self.assertIsNotNone(self.jr.halted())


# ------------------------------------------------------- sell + reconcile
class TestSellAndReconcile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = make_cfg(self.tmp)
        self.jr = Journal(self.cfg.journal_path)
        self.broker = FakeBroker(price=1016.0, position=1)

    def _held_night(self, qty=1) -> int:
        nid = self.jr.create_night("MU", TUE, qty, 50, 1000.0, status="HELD")
        self.jr.update_night(nid, buy_fill=1010.0, buy_commission=0.37)
        return nid

    def test_fallback_places_sell(self):
        nid = self._held_night()
        rc = strategy.run_sell_fallback(self.cfg, self.jr, self.broker,
                                        et(TUE + timedelta(days=1), 9, 5))
        self.assertEqual(rc, 0)
        self.assertEqual(self.broker.placed[0][0], "OPG_SELL")
        self.assertEqual(self.jr.get_night(nid).status, "PENDING_SELL_FILL")

    def test_fallback_past_cutoff(self):
        self._held_night()
        rc = strategy.run_sell_fallback(self.cfg, self.jr, self.broker,
                                        et(TUE + timedelta(days=1), 9, 45))
        self.assertEqual(rc, 2)
        self.assertEqual(self.broker.placed, [])

    def test_fallback_same_evening_recovery(self):
        # OPG placement failed at 16:05 on buy day: re-placing that evening
        # is valid (rests for tomorrow's open), not a missed cutoff.
        nid = self._held_night()
        rc = strategy.run_sell_fallback(self.cfg, self.jr, self.broker,
                                        et(TUE, 16, 30))
        self.assertEqual(rc, 0)
        self.assertEqual(self.broker.placed[0][0], "OPG_SELL")
        self.assertEqual(self.jr.get_night(nid).status, "PENDING_SELL_FILL")

    def test_fallback_no_position(self):
        nid = self._held_night()
        self.broker.position = 0
        rc = strategy.run_sell_fallback(self.cfg, self.jr, self.broker,
                                        et(TUE + timedelta(days=1), 9, 5))
        self.assertEqual(rc, 2)
        self.assertEqual(self.jr.get_night(nid).status, "ERROR")

    def test_fallback_caps_at_position(self):
        nid = self._held_night(qty=5)
        self.broker.position = 3      # user sold some manually
        strategy.run_sell_fallback(self.cfg, self.jr, self.broker,
                                   et(TUE + timedelta(days=1), 9, 5))
        self.assertEqual(self.broker.placed[0][2], 3)
        _ = nid

    def test_reconcile_closes_night(self):
        nid = self._held_night()
        self.jr.update_night(nid, sell_order_id=77, status="PENDING_SELL_FILL")
        self.broker.fills[77] = OrderResult(77, "Filled", 1, 1020.0, 0.37)
        rc = strategy.run_reconcile(self.cfg, self.jr, self.broker,
                                    et(TUE + timedelta(days=1), 9, 50))
        self.assertEqual(rc, 0)
        n = self.jr.get_night(nid)
        self.assertEqual(n.status, "CLOSED")
        # (1020 - 1010) * 1 - 0.37 - 0.37
        self.assertAlmostEqual(n.pnl, 9.26, places=2)

    def test_reconcile_sell_rejected_back_to_held(self):
        nid = self._held_night()
        self.jr.update_night(nid, sell_order_id=77, status="PENDING_SELL_FILL")
        self.broker.fills[77] = OrderResult(77, "Cancelled", detail="cx")
        rc = strategy.run_reconcile(self.cfg, self.jr, self.broker,
                                    et(TUE + timedelta(days=1), 9, 50))
        self.assertEqual(rc, 2)
        self.assertEqual(self.jr.get_night(nid).status, "HELD")

    def test_reconcile_recovers_lost_buy_fill(self):
        nid = self.jr.create_night("MU", TUE, 2, 60, 1000.0,
                                   status="PENDING_BUY")
        self.broker.fills[60] = OrderResult(60, "Filled", 2, 1005.0, 0.5)
        rc = strategy.run_reconcile(self.cfg, self.jr, self.broker,
                                    et(TUE, 16, 30))
        self.assertEqual(rc, 1)   # recovered but still needs a sell
        n = self.jr.get_night(nid)
        self.assertEqual(n.status, "HELD")
        self.assertEqual(n.qty, 2)

    def test_reconcile_trips_kill(self):
        for i in range(16):
            d = date(2026, 2, 2) + timedelta(days=i)
            nid = self.jr.create_night("MU", d, 1, i, 1000.0, status="HELD")
            self.jr.update_night(nid, buy_fill=1000.0)
            self.jr.close_night(nid, d + timedelta(days=1), 995.0, 0.0)
        self.cfg.kill = KillCriteria(max_consecutive_losses=15)
        rc = strategy.run_reconcile(self.cfg, self.jr, self.broker,
                                    et(TUE, 9, 50))
        self.assertEqual(rc, 1)
        self.assertIsNotNone(self.jr.halted())


# ---------------------------------------------------------------- journal
class TestJournal(unittest.TestCase):
    def test_halt_roundtrip(self):
        tmp = tempfile.mkdtemp()
        jr = Journal(os.path.join(tmp, "j.sqlite"))
        self.assertIsNone(jr.halted())
        jr.set_halted("why")
        self.assertEqual(jr.halted(), "why")
        jr.set_halted(None)
        self.assertIsNone(jr.halted())

    def test_unique_night_per_day(self):
        tmp = tempfile.mkdtemp()
        jr = Journal(os.path.join(tmp, "j.sqlite"))
        jr.create_night("MU", TUE, 1, 1, 1000.0)
        with self.assertRaises(Exception):
            jr.create_night("MU", TUE, 1, 2, 1000.0)


if __name__ == "__main__":
    unittest.main()
