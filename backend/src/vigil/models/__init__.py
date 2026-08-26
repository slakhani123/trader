"""SQLAlchemy ORM models. Importing this package registers every mapping."""

from vigil.models.backtest import BacktestRun, BacktestTrade
from vigil.models.events import Catalyst, InsiderTx, NewsItem, ShortInterestObs
from vigil.models.fundamentals import EstimateSnap, FundamentalReport, TargetSnap
from vigil.models.market import CorporateAction, FxRate, MacroObservation, PriceBar
from vigil.models.ops import (
    AuditLog,
    JobRun,
    ModelVersion,
    NotificationDelivery,
    ProviderHealthRecord,
    RawPayload,
)
from vigil.models.portfolio import PortfolioPosition, WatchlistItem
from vigil.models.reference import Instrument, SharesOutstandingObs, TickerChange
from vigil.models.scoring import Alert, EngineOutput, ScoreBundleRow, ScoreRecord, ScoreRun, Signal

__all__ = [
    "Alert",
    "AuditLog",
    "BacktestRun",
    "BacktestTrade",
    "Catalyst",
    "CorporateAction",
    "EngineOutput",
    "EstimateSnap",
    "FundamentalReport",
    "FxRate",
    "InsiderTx",
    "Instrument",
    "JobRun",
    "MacroObservation",
    "ModelVersion",
    "NewsItem",
    "NotificationDelivery",
    "PortfolioPosition",
    "PriceBar",
    "ProviderHealthRecord",
    "RawPayload",
    "ScoreBundleRow",
    "ScoreRecord",
    "ScoreRun",
    "SharesOutstandingObs",
    "ShortInterestObs",
    "Signal",
    "TargetSnap",
    "TickerChange",
    "WatchlistItem",
]
