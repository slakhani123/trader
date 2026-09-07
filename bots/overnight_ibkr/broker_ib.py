"""IBKR adapter implementing the strategy.Broker protocol via ib_async.

ib_async (https://github.com/ib-api-reloaded/ib_async) is the maintained
successor of ib_insync. Connects to a running IB Gateway (live port 4001,
paper 4002) or TWS (7496/7497).

Notes
-----
* Orders: MOC buy = orderType 'MOC'; market-on-open sell = orderType
  'MKT' with tif='OPG'. Whole shares only (fractional orders are not
  auction-eligible at IBKR). Every order carries a deterministic
  orderRef so recovery after a crash/restart matches by tag, never by
  the per-clientId orderId.
* Orders transmitted with transmit=True live at IBKR's servers: API
  disconnects and the Gateway's nightly restart do NOT cancel them
  (verified against IBKR docs; OPG rests until the next open).
* Reference price for sizing uses delayed-frozen market data (type 4),
  which needs no paid subscription.
* session_close_today() parses liquidHours from contract details so an
  unscheduled early close moves the submission window, and reports
  SESSION_CLOSED for an unscheduled full closure. US stocks only (the
  ET assumption matches ContractDetails.timeZoneId for US listings).
* client_id must stay FIXED for the life of the deployment: open orders
  re-bind to the same clientId on reconnect. Two processes cannot share
  it concurrently - a second connect fails loudly (that failure is the
  de-facto same-host lock; see crontab.example's flock for the rest).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from strategy import Broker, OrderResult, SESSION_CLOSED

ET = ZoneInfo("America/New_York")

TERMINAL = {"Filled", "Cancelled", "ApiCancelled", "Inactive", "Rejected"}


class IBBroker(Broker):
    def __init__(self, host: str, port: int, client_id: int,
                 account: str = "") -> None:
        try:
            from ib_async import IB
        except ImportError as exc:                      # pragma: no cover
            raise SystemExit(
                "ib_async is not installed - run: pip install ib_async"
            ) from exc
        self._ib = IB()
        try:
            self._ib.connect(host, port, clientId=client_id, timeout=20)
        except Exception as exc:
            raise SystemExit(
                f"could not connect to IB Gateway at {host}:{port} "
                f"(clientId {client_id}): {exc}. Gateway down, API disabled, "
                "or another bot process holds this clientId."
            ) from exc
        self.client_id = client_id
        self.account = account or self._default_account()
        self._contracts: dict[str, object] = {}
        self._trades: dict[int, object] = {}

    # ------------------------------------------------------------ helpers
    def _default_account(self) -> str:
        accts = self._ib.managedAccounts()
        if not accts:
            raise SystemExit("no managed accounts on this connection")
        return accts[0]

    def _contract(self, symbol: str):
        from ib_async import Stock
        if symbol not in self._contracts:
            c = Stock(symbol, "SMART", "USD", primaryExchange="NASDAQ")
            qualified = self._ib.qualifyContracts(c)
            # ib_async returns a positional list that can contain None for
            # an unresolvable/ambiguous contract - [None] is truthy!
            if not qualified or qualified[0] is None:
                raise RuntimeError(f"could not qualify contract for {symbol}")
            self._contracts[symbol] = qualified[0]
        return self._contracts[symbol]

    @staticmethod
    def _commission_sum(trade) -> float:
        total = 0.0
        for f in trade.fills:
            rep = getattr(f, "commissionReport", None)
            if rep and getattr(rep, "execId", ""):
                total += rep.commission
        return total

    def _trade_result(self, trade) -> OrderResult:
        st = trade.orderStatus
        return OrderResult(
            order_id=trade.order.orderId,
            status=st.status,
            filled_qty=int(st.filled),
            avg_fill_price=float(st.avgFillPrice or 0.0),
            commission=self._commission_sum(trade),
            detail=(trade.log[-1].message if trade.log else ""),
        )

    def _place(self, symbol: str, action: str, qty: int,
               order_type: str, tif: str, ref: str) -> OrderResult:
        from ib_async import Order
        order = Order(action=action, orderType=order_type, totalQuantity=qty,
                      tif=tif, account=self.account, transmit=True,
                      outsideRth=False, orderRef=ref)
        trade = self._ib.placeOrder(self._contract(symbol), order)
        self._ib.sleep(2)  # let the initial ack/reject arrive
        self._trades[trade.order.orderId] = trade
        return self._trade_result(trade)

    # ----------------------------------------------------- Broker protocol
    def reference_price(self, symbol: str) -> float | None:
        self._ib.reqMarketDataType(4)  # delayed-frozen, no subscription
        ticker = self._ib.reqMktData(self._contract(symbol), "", snapshot=True)
        # a delayed snapshot can take ~11s to complete - poll, don't guess
        for _ in range(12):
            self._ib.sleep(1)
            for candidate in (ticker.last, ticker.close):
                if candidate and candidate == candidate and candidate > 0:
                    return float(candidate)
        mp = ticker.marketPrice()
        if mp and mp == mp and mp > 0:
            return float(mp)
        return None

    def session_close_today(self, symbol: str) -> datetime | None:
        try:
            details = self._ib.reqContractDetails(self._contract(symbol))
            hours = details[0].liquidHours or ""
        except Exception:
            return None
        today = datetime.now(tz=ET).strftime("%Y%m%d")
        best: datetime | None = None
        saw_today = False
        for chunk in hours.split(";"):
            chunk = chunk.strip()
            if not chunk.startswith(today):
                continue
            saw_today = True
            if chunk.endswith("CLOSED"):
                continue
            # format: 20260907:0930-20260907:1600 (latest session end wins)
            try:
                end = chunk.split("-")[1]
                d, hm = end.split(":")
                t = datetime.strptime(d + hm, "%Y%m%d%H%M").replace(tzinfo=ET)
                if best is None or t > best:
                    best = t
            except (IndexError, ValueError):
                return None
        if best is not None:
            return best
        return SESSION_CLOSED if saw_today else None

    def position_qty(self, symbol: str) -> int:
        total = 0
        for pos in self._ib.positions(self.account):
            if pos.contract.symbol == symbol and pos.contract.secType == "STK":
                total += int(pos.position)
        return total

    def open_sell_orders(self, symbol: str) -> list[tuple[int, str, int]]:
        self._ib.reqAllOpenOrders()
        self._ib.sleep(1)
        out = []
        for t in self._ib.openTrades():
            if (t.contract.symbol == symbol
                    and t.order.action == "SELL"
                    and t.orderStatus.status not in TERMINAL):
                out.append((t.order.orderId,
                            getattr(t.order, "orderRef", "") or "",
                            int(t.order.totalQuantity)))
        return out

    def place_moc_buy(self, symbol: str, qty: int, ref: str) -> OrderResult:
        return self._place(symbol, "BUY", qty, "MOC", "DAY", ref)

    def place_opg_sell(self, symbol: str, qty: int, ref: str) -> OrderResult:
        return self._place(symbol, "SELL", qty, "MKT", "OPG", ref)

    def wait_for_fill(self, order_id: int, deadline: datetime) -> OrderResult:
        trade = self._trades.get(order_id)
        if trade is None:
            found = self.order_fill(order_id)
            return found or OrderResult(order_id, "Unknown")
        while trade.orderStatus.status not in TERMINAL:
            if datetime.now(tz=ET) >= deadline:
                # never walk away from a working order: cancel it so a
                # late fill cannot create a position the journal thinks
                # does not exist (the cancel can race a fill - the final
                # status below is the truth either way)
                try:
                    self._ib.cancelOrder(trade.order)
                except Exception:
                    pass
                for _ in range(12):
                    self._ib.sleep(5)
                    if trade.orderStatus.status in TERMINAL:
                        break
                break
            self._ib.sleep(5)
        if trade.orderStatus.status == "Filled":
            # commission reports trail the fill by a beat; and avgFillPrice
            # can momentarily read 0 right at the status flip
            for _ in range(6):
                res = self._trade_result(trade)
                if res.avg_fill_price > 0 and res.commission != 0.0:
                    return res
                self._ib.sleep(2)
        return self._trade_result(trade)

    def order_fill(self, order_id: int, ref: str = "") -> OrderResult | None:
        # same-session trades first
        trade = self._trades.get(order_id)
        if trade is not None:
            return self._trade_result(trade)
        # open/known trades from this API session (match by ref, else id)
        for t in self._ib.trades():
            if (ref and getattr(t.order, "orderRef", "") == ref) \
                    or (not ref and t.order.orderId == order_id):
                return self._trade_result(t)
        # finally today's execution records (they expire at midnight ET).
        # Match by our exact deterministic ref when we have one - orderId
        # alone is only unique per clientId, so id-matching is restricted
        # to executions from OUR clientId.
        def _matches(f) -> bool:
            fref = getattr(f.execution, "orderRef", "") or ""
            if ref:
                return fref == ref
            return (f.execution.orderId == order_id
                    and f.execution.clientId == self.client_id)
        fills = [f for f in self._ib.reqExecutions() if _matches(f)]
        if fills:
            qty = int(sum(f.execution.shares for f in fills))
            value = sum(f.execution.shares * f.execution.price for f in fills)
            comm = sum(f.commissionReport.commission for f in fills
                       if f.commissionReport
                       and getattr(f.commissionReport, "execId", ""))
            # honest status: executions prove shares traded, NOT that the
            # order completed - the caller compares quantities
            return OrderResult(order_id, "Executions", qty,
                               value / qty if qty else 0.0, comm)
        return None

    def disconnect(self) -> None:
        self._ib.disconnect()
