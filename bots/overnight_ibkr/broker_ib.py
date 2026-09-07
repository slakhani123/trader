"""IBKR adapter implementing the strategy.Broker protocol via ib_async.

ib_async (https://github.com/ib-api-reloaded/ib_async) is the maintained
successor of ib_insync. Connects to a running IB Gateway (live port 4001,
paper 4002) or TWS (7496/7497).

Notes
-----
* Orders: MOC buy = orderType 'MOC'; market-on-open sell = orderType
  'MKT' with tif='OPG'. Whole shares only (fractional orders are not
  auction-eligible at IBKR).
* Reference price for sizing uses delayed-frozen market data (type 4),
  which needs no paid subscription. 15-minute-delayed is plenty for
  whole-share sizing.
* session_close_today() parses liquidHours from contract details so an
  unscheduled early close moves the submission window (the static
  calendar is the offline fallback).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from strategy import Broker, OrderResult

ET = ZoneInfo("America/New_York")


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
        self._ib.connect(host, port, clientId=client_id, timeout=20)
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
            if not qualified:
                raise RuntimeError(f"could not qualify contract for {symbol}")
            self._contracts[symbol] = qualified[0]
        return self._contracts[symbol]

    @staticmethod
    def _trade_result(trade) -> OrderResult:
        commission = 0.0
        for f in trade.fills:
            rep = getattr(f, "commissionReport", None)
            if rep and rep.commission == rep.commission:  # not NaN
                commission += rep.commission
        st = trade.orderStatus
        return OrderResult(
            order_id=trade.order.orderId,
            status=st.status,
            filled_qty=int(st.filled),
            avg_fill_price=float(st.avgFillPrice or 0.0),
            commission=commission,
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
        self._ib.sleep(3)
        for candidate in (ticker.last, ticker.close, ticker.marketPrice()):
            if candidate and candidate == candidate and candidate > 0:
                return float(candidate)
        return None

    def session_close_today(self, symbol: str) -> datetime | None:
        try:
            details = self._ib.reqContractDetails(self._contract(symbol))
            hours = details[0].liquidHours or ""
        except Exception:
            return None
        today = datetime.now(tz=ET).strftime("%Y%m%d")
        for chunk in hours.split(";"):
            chunk = chunk.strip()
            if not chunk.startswith(today) or chunk.endswith("CLOSED"):
                continue
            # format: 20260907:0930-20260907:1600
            try:
                end = chunk.split("-")[1]
                d, hm = end.split(":")
                return datetime.strptime(d + hm, "%Y%m%d%H%M").replace(tzinfo=ET)
            except (IndexError, ValueError):
                return None
        return None

    def position_qty(self, symbol: str) -> int:
        total = 0
        for pos in self._ib.positions(self.account):
            if pos.contract.symbol == symbol and pos.contract.secType == "STK":
                total += int(pos.position)
        return total

    def place_moc_buy(self, symbol: str, qty: int, ref: str) -> OrderResult:
        return self._place(symbol, "BUY", qty, "MOC", "DAY", ref)

    def place_opg_sell(self, symbol: str, qty: int, ref: str) -> OrderResult:
        return self._place(symbol, "SELL", qty, "MKT", "OPG", ref)

    def wait_for_fill(self, order_id: int, deadline: datetime) -> OrderResult:
        trade = self._trades.get(order_id)
        if trade is None:
            found = self.order_fill(order_id)
            return found or OrderResult(order_id, "Unknown")
        terminal = {"Filled", "Cancelled", "ApiCancelled", "Inactive", "Rejected"}
        while trade.orderStatus.status not in terminal:
            if datetime.now(tz=ET) >= deadline:
                break
            self._ib.sleep(5)
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
        # finally today's executions. Prefer the deterministic orderRef tag:
        # orderId is only unique per clientId, so matching by id alone could
        # hit an unrelated manual/TWS order after a restart.
        def _matches(f) -> bool:
            fref = getattr(f.execution, "orderRef", "") or ""
            if ref:
                return fref == ref
            return f.execution.orderId == order_id
        fills = [f for f in self._ib.reqExecutions() if _matches(f)]
        if fills:
            qty = int(sum(f.execution.shares for f in fills))
            value = sum(f.execution.shares * f.execution.price for f in fills)
            comm = sum(f.commissionReport.commission for f in fills
                       if f.commissionReport
                       and f.commissionReport.commission == f.commissionReport.commission)
            return OrderResult(order_id, "Filled", qty,
                               value / qty if qty else 0.0, comm)
        return None

    def disconnect(self) -> None:
        self._ib.disconnect()
