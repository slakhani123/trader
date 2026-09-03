import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { api } from '../api/client';
import type { BacktestMetrics, BacktestTrade, CalibrationBlock } from '../api/types';
import { Badge } from '../components/Badge';
import { QueryGate } from '../components/QueryGate';
import { ScoreTriple } from '../components/ScoreChip';
import { fmtDate, fmtDateTime, fmtNum, fmtPct, isNum, titleCase } from '../lib/format';
import { backtestStatusTone, isBacktestRunning } from './Backtests';

function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: 'pos' | 'neg';
}) {
  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div className="stat">
        <span className="label">{label}</span>
        <span className={`value mono${tone ? ` ${tone}` : ''}`}>{value}</span>
        {hint && <span className="hint">{hint}</span>}
      </div>
    </div>
  );
}

function signTone(v: number | null | undefined): 'pos' | 'neg' | undefined {
  if (!isNum(v)) return undefined;
  return v >= 0 ? 'pos' : 'neg';
}

function hitRateLabel(m: BacktestMetrics): string {
  if (!isNum(m.hit_rate)) return '—';
  return fmtPct(m.hit_rate * 100, 0);
}

function hitRateHint(m: BacktestMetrics): string | undefined {
  const ci = m.hit_rate_ci95;
  if (!ci || !isNum(ci[0]) || !isNum(ci[1])) return undefined;
  return `95% CI ${fmtPct(ci[0] * 100, 0)} – ${fmtPct(ci[1] * 100, 0)}`;
}

function MetricsGrid({ metrics }: { metrics: BacktestMetrics }) {
  return (
    <>
      <div className="grid cols-4" style={{ marginBottom: 14 }}>
        <Stat
          label="Total return"
          value={fmtPct(metrics.total_return_pct, 1, true)}
          hint={isNum(metrics.cagr_pct) ? `CAGR ${fmtPct(metrics.cagr_pct, 1, true)}` : undefined}
          tone={signTone(metrics.total_return_pct)}
        />
        <Stat
          label="Alpha vs benchmark"
          value={fmtPct(metrics.avg_alpha_pct, 1, true)}
          hint="average per trade, matched benchmark"
          tone={signTone(metrics.avg_alpha_pct)}
        />
        <Stat label="Hit rate" value={hitRateLabel(metrics)} hint={hitRateHint(metrics)} />
        <Stat
          label="Sharpe / Sortino"
          value={`${fmtNum(metrics.sharpe, 2)} / ${fmtNum(metrics.sortino, 2)}`}
          hint={
            isNum(metrics.annualised_vol_pct)
              ? `annualised vol ${fmtPct(metrics.annualised_vol_pct, 1)}`
              : undefined
          }
        />
      </div>
      <div className="grid cols-4" style={{ marginBottom: 14 }}>
        <Stat
          label="Max drawdown"
          value={fmtPct(metrics.max_drawdown_pct, 1)}
          tone={isNum(metrics.max_drawdown_pct) ? 'neg' : undefined}
        />
        <Stat
          label="Turnover"
          value={isNum(metrics.trades_per_year) ? `${fmtNum(metrics.trades_per_year, 1)}/yr` : '—'}
          hint={`${metrics.n ?? '—'} closed trades${
            isNum(metrics.open_at_end) && metrics.open_at_end > 0
              ? ` · ${metrics.open_at_end} open at end`
              : ''
          }`}
        />
        <Stat
          label="Avg holding"
          value={isNum(metrics.avg_holding_days) ? `${fmtNum(metrics.avg_holding_days, 0)}d` : '—'}
          hint={
            isNum(metrics.win_loss_ratio)
              ? `win/loss ratio ${fmtNum(metrics.win_loss_ratio, 2)}`
              : undefined
          }
        />
        <Stat
          label="MAE / MFE"
          value={`${fmtPct(metrics.avg_mae_pct, 1)} / ${fmtPct(metrics.avg_mfe_pct, 1)}`}
          hint="avg adverse / favourable excursion"
        />
      </div>
      {metrics.exit_reasons && Object.keys(metrics.exit_reasons).length > 0 && (
        <div className="card">
          <h3>Exit reasons</h3>
          <div className="badge-row">
            {Object.entries(metrics.exit_reasons)
              .sort(([, a], [, b]) => b - a)
              .map(([reason, count]) => (
                <Badge key={reason} tone="neutral" outline>
                  {titleCase(reason)}: {count}
                </Badge>
              ))}
          </div>
        </div>
      )}
      {(metrics.inconclusive || metrics.note) && (
        <div className="banner warn">
          {metrics.inconclusive && <div className="title">Inconclusive sample</div>}
          {typeof metrics.note === 'string' && <div className="small">{metrics.note}</div>}
        </div>
      )}
    </>
  );
}

const BUCKET_ORDER = ['strategy', 'family', 'horizon', 'regime', 'cap', 'cap_band', 'score_bucket'];

function BucketTables({ byBucket }: { byBucket: Record<string, Record<string, BacktestMetrics>> }) {
  const dims = [
    ...BUCKET_ORDER.filter((d) => d in byBucket),
    ...Object.keys(byBucket)
      .filter((d) => !BUCKET_ORDER.includes(d))
      .sort(),
  ];
  return (
    <div className="grid cols-2" style={{ marginBottom: 14 }}>
      {dims.map((dim) => {
        const buckets = byBucket[dim];
        const rows = Object.entries(buckets);
        return (
          <div className="card" key={dim} style={{ marginBottom: 0 }}>
            <h3>By {titleCase(dim)}</h3>
            <div className="table-wrap" style={{ marginBottom: 0 }}>
              <table className="data">
                <thead>
                  <tr>
                    <th>Bucket</th>
                    <th className="num">n</th>
                    <th className="num">Hit rate</th>
                    <th className="num">Avg return</th>
                    <th className="num">Avg alpha</th>
                    <th className="num">Avg hold</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(([bucket, m]) => (
                    <tr key={bucket}>
                      <td>{titleCase(bucket)}</td>
                      <td className="num mono">{m.n ?? '—'}</td>
                      <td className="num mono">
                        {isNum(m.hit_rate) ? fmtPct(m.hit_rate * 100, 0) : '—'}
                      </td>
                      <td
                        className={`num mono ${isNum(m.avg_return_pct) && m.avg_return_pct < 0 ? 'neg' : 'pos'}`}
                      >
                        {fmtPct(m.avg_return_pct, 1, true)}
                      </td>
                      <td
                        className={`num mono ${isNum(m.avg_alpha_pct) && m.avg_alpha_pct < 0 ? 'neg' : 'pos'}`}
                      >
                        {fmtPct(m.avg_alpha_pct, 1, true)}
                      </td>
                      <td className="num mono">
                        {isNum(m.avg_holding_days) ? `${fmtNum(m.avg_holding_days, 0)}d` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </div>
  );
}

interface ReliabilityPoint {
  predicted: number;
  observed: number | null;
  diagonal: number;
  count: number;
}

function ReliabilityTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { payload?: ReliabilityPoint }[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const point = payload[0]?.payload;
  if (!point) return null;
  return (
    <div className="chart-tooltip">
      <div className="t-title">predicted {fmtPct(point.predicted * 100, 0)}</div>
      <div>
        observed{' '}
        <span className="mono">{isNum(point.observed) ? fmtPct(point.observed * 100, 0) : '—'}</span>
      </div>
      <div className="dim">n = {point.count}</div>
    </div>
  );
}

function CalibrationCard({ calibration }: { calibration: CalibrationBlock }) {
  const bins = calibration.reliability ?? [];
  const data: ReliabilityPoint[] = [
    { predicted: 0, observed: null, diagonal: 0, count: 0 },
    ...bins
      .slice()
      .sort((a, b) => a.predicted - b.predicted)
      .map((b) => ({
        predicted: b.predicted,
        observed: b.observed,
        diagonal: b.predicted,
        count: b.count,
      })),
    { predicted: 1, observed: null, diagonal: 1, count: 0 },
  ];
  return (
    <div className="two-col" style={{ marginBottom: 14 }}>
      <div className="card" style={{ marginBottom: 0 }}>
        <h3>Reliability curve — predicted vs observed</h3>
        {bins.length === 0 ? (
          <div className="dim small">Not enough resolved trades to build a reliability curve.</div>
        ) : (
          <div style={{ width: '100%', height: 260 }}>
            <ResponsiveContainer>
              <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="2 4" />
                <XAxis
                  dataKey="predicted"
                  type="number"
                  domain={[0, 1]}
                  tickFormatter={(v: number) => v.toFixed(1)}
                  tick={{ fill: 'var(--text-faint)', fontSize: 10 }}
                  tickLine={false}
                  axisLine={{ stroke: 'var(--border-strong)' }}
                />
                <YAxis
                  type="number"
                  domain={[0, 1]}
                  tickFormatter={(v: number) => v.toFixed(1)}
                  tick={{ fill: 'var(--text-faint)', fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                  width={34}
                />
                <Tooltip content={<ReliabilityTooltip />} />
                <Line
                  dataKey="diagonal"
                  stroke="var(--text-faint)"
                  strokeDasharray="4 4"
                  dot={false}
                  isAnimationActive={false}
                  name="perfect calibration"
                />
                <Line
                  dataKey="observed"
                  stroke="var(--blue)"
                  strokeWidth={1.8}
                  connectNulls
                  dot={{ r: 3, fill: 'var(--blue)' }}
                  isAnimationActive={false}
                  name="observed"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        <div className="tiny faint">
          Dashed diagonal = perfectly calibrated confidence. Points above it mean the model was
          under-confident; below it, over-confident.
        </div>
      </div>
      <div className="card" style={{ marginBottom: 0 }}>
        <h3>Calibration</h3>
        <div className="kv">
          <dt>Brier score</dt>
          <dd className="mono">
            {isNum(calibration.brier_score) ? calibration.brier_score.toFixed(4) : '—'}
          </dd>
          <dt>Base rate</dt>
          <dd className="mono">
            {isNum(calibration.base_rate) ? fmtPct(calibration.base_rate * 100, 1) : '—'}
          </dd>
          <dt>Sample size</dt>
          <dd className="mono">{calibration.n}</dd>
        </div>
        {calibration.definition && (
          <div className="dim small" style={{ marginTop: 8 }}>
            {calibration.definition}
          </div>
        )}
        <div className="tiny faint" style={{ marginTop: 8 }}>
          Lower Brier is better; a score at the base rate means confidence carried no information.
        </div>
      </div>
    </div>
  );
}

const PAGE_SIZE = 50;

function TradesTable({ trades }: { trades: BacktestTrade[] }) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(trades.length / PAGE_SIZE));
  const clamped = Math.min(page, pages - 1);
  const visible = useMemo(
    () => trades.slice(clamped * PAGE_SIZE, (clamped + 1) * PAGE_SIZE),
    [trades, clamped],
  );
  if (trades.length === 0)
    return <div className="card dim small">No trades recorded (yet).</div>;
  return (
    <>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Family</th>
              <th className="center">Horizon</th>
              <th>Signal</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>Reason</th>
              <th className="num">Days</th>
              <th className="num">Return</th>
              <th className="num">Benchmark</th>
              <th className="num">MAE</th>
              <th className="num">MFE</th>
              <th className="num">Costs</th>
              <th className="center">O/C/R</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((t, i) => (
              <tr key={`${t.instrument_id}-${t.signal_date}-${i}`}>
                <td>
                  <Link
                    to={`/companies/${t.instrument_id}`}
                    className="mono"
                    style={{ fontWeight: 700 }}
                  >
                    {t.ticker}
                  </Link>
                </td>
                <td>
                  <Badge tone="neutral" outline>
                    {titleCase(t.family)}
                  </Badge>
                </td>
                <td className="center dim small">{titleCase(t.horizon)}</td>
                <td className="mono small dim">{fmtDate(t.signal_date)}</td>
                <td className="mono small">
                  {t.entry_date ? `${fmtDate(t.entry_date)} @ ${fmtNum(t.entry_price)}` : '—'}
                </td>
                <td className="mono small">
                  {t.exit_date ? `${fmtDate(t.exit_date)} @ ${fmtNum(t.exit_price)}` : 'open'}
                </td>
                <td className="dim small">{t.exit_reason ? titleCase(t.exit_reason) : '—'}</td>
                <td className="num mono">{t.holding_days ?? '—'}</td>
                <td className={`num mono ${isNum(t.return_pct) && t.return_pct < 0 ? 'neg' : 'pos'}`}>
                  {fmtPct(t.return_pct, 1, true)}
                </td>
                <td className="num mono dim">{fmtPct(t.benchmark_return_pct, 1, true)}</td>
                <td className="num mono dim">{fmtPct(t.mae_pct, 1)}</td>
                <td className="num mono dim">{fmtPct(t.mfe_pct, 1)}</td>
                <td className="num mono dim">
                  {isNum(t.costs_bps) ? `${fmtNum(t.costs_bps, 0)}bps` : '—'}
                </td>
                <td className="center">
                  <ScoreTriple opportunity={t.opportunity} confidence={t.confidence} risk={t.risk} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="pager">
          <button className="btn sm" disabled={clamped === 0} onClick={() => setPage(clamped - 1)}>
            ← prev
          </button>
          <span>
            page {clamped + 1} / {pages} · {trades.length} trades
          </span>
          <button
            className="btn sm"
            disabled={clamped >= pages - 1}
            onClick={() => setPage(clamped + 1)}
          >
            next →
          </button>
        </div>
      )}
    </>
  );
}

export function BacktestDetailPage() {
  const { id } = useParams<{ id: string }>();
  const query = useQuery({
    queryKey: ['backtest', id],
    queryFn: () => api.backtest(id ?? ''),
    enabled: Boolean(id),
    refetchInterval: (q) =>
      q.state.data && isBacktestRunning(q.state.data.status) ? 3000 : false,
  });

  return (
    <QueryGate query={query} skeletonRows={8}>
      {(bt) => (
        <div>
          <div className="page-head">
            <div>
              <div className="badge-row" style={{ marginBottom: 6 }}>
                <Badge tone={backtestStatusTone(bt.status)}>{bt.status}</Badge>
                <Badge tone="neutral" outline>
                  model {bt.model_version}
                </Badge>
                {bt.holdout_start && (
                  <Badge tone="purple" outline title="Untouched holdout begins here">
                    holdout from {fmtDate(bt.holdout_start)}
                  </Badge>
                )}
              </div>
              <h1>{bt.name}</h1>
              <div className="sub">
                {fmtDate(bt.start_date)} → {bt.end_date ? fmtDate(bt.end_date) : 'latest'} · created{' '}
                {fmtDateTime(bt.created_at)} · backtest #{bt.id}
              </div>
            </div>
          </div>

          {isBacktestRunning(bt.status) && (
            <div className="banner info">
              Backtest is running — this page refreshes automatically every few seconds.
            </div>
          )}
          {bt.status.toLowerCase() === 'failed' && (
            <div className="banner danger">
              <div className="title">Backtest failed</div>
              <div className="small">{bt.detail ?? 'No detail recorded.'}</div>
            </div>
          )}

          {bt.metrics ? (
            <MetricsGrid metrics={bt.metrics} />
          ) : (
            !isBacktestRunning(bt.status) && (
              <div className="card dim small">No metrics recorded for this backtest.</div>
            )
          )}

          {bt.by_bucket && Object.keys(bt.by_bucket).length > 0 && (
            <>
              <div className="section-title">
                <h2>Per-bucket results</h2>
                <div className="rule" />
              </div>
              <BucketTables byBucket={bt.by_bucket} />
            </>
          )}

          {bt.calibration && (
            <>
              <div className="section-title">
                <h2>Confidence calibration</h2>
                <div className="rule" />
              </div>
              <CalibrationCard calibration={bt.calibration} />
            </>
          )}

          <div className="section-title">
            <h2>Trades</h2>
            <div className="rule" />
          </div>
          {bt.trades.length === 0 && bt.notes && (
            <div className="banner warn" style={{ marginBottom: 12 }}>
              <strong>Why no trades?</strong> {bt.notes}
            </div>
          )}
          <TradesTable trades={bt.trades} />

          <div className="dim small" style={{ marginTop: 16 }}>
            <Link to="/backtests">← all backtests</Link>
          </div>
        </div>
      )}
    </QueryGate>
  );
}
