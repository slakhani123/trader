import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { api } from '../api/client';
import type {
  CompanyDetail,
  EngineView,
  FinancialQuarter,
  HorizonScore,
  PriceBar,
  PriceMarker,
} from '../api/types';
import { Badge } from '../components/Badge';
import { EmptyState } from '../components/EmptyState';
import { EvidenceList } from '../components/EvidenceList';
import { ComponentBars } from '../components/MiniBars';
import { ErrorPanel, QueryGate } from '../components/QueryGate';
import { ScoreTriple } from '../components/ScoreChip';
import { Sparkline } from '../components/Sparkline';
import { LoadingSkeleton } from '../components/LoadingSkeleton';
import {
  HORIZONS,
  familyLabel,
  fmtDate,
  fmtDateTime,
  fmtMoney,
  fmtNum,
  fmtScore,
  isNum,
  stateTone,
  titleCase,
} from '../lib/format';

// ---------------------------------------------------------------------------
// Price chart
// ---------------------------------------------------------------------------

interface ChartPoint {
  date: string;
  adj_close: number;
  volume: number;
  markerY: number | null;
  marker: PriceMarker | null;
}

function buildChartData(bars: PriceBar[], markers: PriceMarker[]): ChartPoint[] {
  const markerByDate = new Map<string, PriceMarker>();
  for (const m of markers) markerByDate.set(m.date, m);
  return bars.map((b) => {
    const marker = markerByDate.get(b.date) ?? null;
    return {
      date: b.date,
      adj_close: b.adj_close,
      volume: b.volume,
      markerY: marker ? b.adj_close : null,
      marker,
    };
  });
}

function PriceTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { payload?: ChartPoint }[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const point = payload[0]?.payload;
  if (!point) return null;
  return (
    <div className="chart-tooltip">
      <div className="t-title">{point.date}</div>
      <div>
        Adj close <span className="mono">{fmtNum(point.adj_close)}</span>
      </div>
      <div className="dim">
        Volume <span className="mono">{fmtMoney(point.volume)}</span>
      </div>
      {point.marker && (
        <div style={{ marginTop: 4, color: 'var(--amber)' }}>
          ◆ Alert: {familyLabel(point.marker.family)} · {point.marker.state} ({point.marker.transition})
        </div>
      )}
    </div>
  );
}

const RANGE_CHOICES = [
  { label: '3m', days: 63 },
  { label: '6m', days: 126 },
  { label: '1y', days: 252 },
  { label: '2y', days: 730 },
];

function PriceChart({ bars, markers }: { bars: PriceBar[]; markers: PriceMarker[] }) {
  const navigate = useNavigate();
  const [rangeDays, setRangeDays] = useState(252);
  const data = useMemo(() => {
    const sliced = bars.slice(-rangeDays);
    const cutoff = sliced.length > 0 ? sliced[0].date : '';
    return buildChartData(sliced, markers.filter((m) => m.date >= cutoff));
  }, [bars, markers, rangeDays]);
  const maxVolume = useMemo(() => Math.max(1, ...data.map((d) => d.volume)), [data]);

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2>Price &amp; alerts</h2>
        <div className="tabs" style={{ marginBottom: 8 }}>
          {RANGE_CHOICES.map((r) => (
            <button key={r.label} className={rangeDays === r.days ? 'active' : ''} onClick={() => setRangeDays(r.days)}>
              {r.label}
            </button>
          ))}
        </div>
      </div>
      <div style={{ width: '100%', height: 320 }}>
        <ResponsiveContainer>
          <ComposedChart data={data} margin={{ top: 6, right: 10, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fill: 'var(--text-faint)', fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: 'var(--border-strong)' }}
              minTickGap={50}
            />
            <YAxis
              yAxisId="price"
              orientation="right"
              domain={['auto', 'auto']}
              tick={{ fill: 'var(--text-faint)', fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              width={55}
            />
            <YAxis yAxisId="vol" hide domain={[0, maxVolume * 4]} />
            <Tooltip content={<PriceTooltip />} />
            <Bar yAxisId="vol" dataKey="volume" fill="var(--border-strong)" opacity={0.6} isAnimationActive={false} />
            <Line
              yAxisId="price"
              type="monotone"
              dataKey="adj_close"
              stroke="var(--blue)"
              strokeWidth={1.6}
              dot={false}
              isAnimationActive={false}
            />
            <Scatter
              yAxisId="price"
              dataKey="markerY"
              fill="var(--amber)"
              shape="diamond"
              isAnimationActive={false}
              onClick={(entry: unknown) => {
                const point = (entry as { payload?: ChartPoint }).payload;
                if (point?.marker) navigate(`/alerts/${point.marker.alert_id}`);
              }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="tiny faint">◆ diamonds mark alerts on this name — click one to open the alert.</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Financial trend charts
// ---------------------------------------------------------------------------

function FinTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: { name?: string; value?: number | string; color?: string }[];
  label?: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="chart-tooltip">
      <div className="t-title">{label}</div>
      {payload.map((e, i) => (
        <div key={i} style={{ color: e.color }}>
          {e.name}: <span className="mono">{typeof e.value === 'number' ? fmtNum(e.value) : String(e.value ?? '—')}</span>
        </div>
      ))}
    </div>
  );
}

function axisProps() {
  return {
    tick: { fill: 'var(--text-faint)', fontSize: 9.5 },
    tickLine: false,
  } as const;
}

function FinancialCharts({ quarters }: { quarters: FinancialQuarter[] }) {
  const data = quarters.map((q) => ({ ...q, label: q.period_end.slice(0, 7) }));
  return (
    <div className="grid cols-2">
      <div className="card" style={{ marginBottom: 0 }}>
        <h3>Revenue &amp; margins</h3>
        <div style={{ width: '100%', height: 190 }}>
          <ResponsiveContainer>
            <ComposedChart data={data} margin={{ top: 4, right: 6, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="2 4" vertical={false} />
              <XAxis dataKey="label" {...axisProps()} minTickGap={30} axisLine={{ stroke: 'var(--border-strong)' }} />
              <YAxis yAxisId="rev" {...axisProps()} axisLine={false} width={48} tickFormatter={(v: number) => fmtMoney(v)} />
              <YAxis yAxisId="pct" orientation="right" {...axisProps()} axisLine={false} width={34} unit="%" />
              <Tooltip content={<FinTooltip />} />
              <Bar yAxisId="rev" dataKey="revenue" name="Revenue" fill="var(--blue)" opacity={0.7} isAnimationActive={false} />
              <Line yAxisId="pct" dataKey="gross_margin_pct" name="Gross margin %" stroke="var(--green)" dot={false} isAnimationActive={false} />
              <Line yAxisId="pct" dataKey="operating_margin_pct" name="Op margin %" stroke="var(--amber)" dot={false} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="card" style={{ marginBottom: 0 }}>
        <h3>Diluted EPS</h3>
        <div style={{ width: '100%', height: 190 }}>
          <ResponsiveContainer>
            <ComposedChart data={data} margin={{ top: 4, right: 6, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="2 4" vertical={false} />
              <XAxis dataKey="label" {...axisProps()} minTickGap={30} axisLine={{ stroke: 'var(--border-strong)' }} />
              <YAxis {...axisProps()} axisLine={false} width={44} />
              <Tooltip content={<FinTooltip />} />
              <Bar dataKey="eps_diluted" name="EPS (diluted)" fill="var(--purple)" opacity={0.75} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="card" style={{ marginBottom: 0 }}>
        <h3>Free cash flow</h3>
        <div style={{ width: '100%', height: 190 }}>
          <ResponsiveContainer>
            <ComposedChart data={data} margin={{ top: 4, right: 6, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="2 4" vertical={false} />
              <XAxis dataKey="label" {...axisProps()} minTickGap={30} axisLine={{ stroke: 'var(--border-strong)' }} />
              <YAxis {...axisProps()} axisLine={false} width={48} tickFormatter={(v: number) => fmtMoney(v)} />
              <Tooltip content={<FinTooltip />} />
              <Bar dataKey="fcf" name="FCF" fill="var(--green)" opacity={0.7} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="card" style={{ marginBottom: 0 }}>
        <h3>Net debt</h3>
        <div style={{ width: '100%', height: 190 }}>
          <ResponsiveContainer>
            <ComposedChart data={data} margin={{ top: 4, right: 6, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="2 4" vertical={false} />
              <XAxis dataKey="label" {...axisProps()} minTickGap={30} axisLine={{ stroke: 'var(--border-strong)' }} />
              <YAxis {...axisProps()} axisLine={false} width={48} tickFormatter={(v: number) => fmtMoney(v)} />
              <Tooltip content={<FinTooltip />} />
              <Line dataKey="net_debt" name="Net debt" stroke="var(--red)" dot={false} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Horizon score cards
// ---------------------------------------------------------------------------

function HorizonCard({ horizon, score, bestFit }: { horizon: string; score: HorizonScore; bestFit: boolean }) {
  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>
          {titleCase(horizon)}
          {bestFit && (
            <Badge tone="purple" title="Evidence best supports this horizon">
              best fit
            </Badge>
          )}
        </h2>
        <ScoreTriple opportunity={score.opportunity} confidence={score.confidence} risk={score.risk} small={false} />
      </div>
      {score.abstained ? (
        <div className="banner warn" style={{ marginBottom: 8 }}>
          <div className="title">Abstained</div>
          <ul className="plain small" style={{ margin: 0 }}>
            {score.abstain_reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      ) : (
        <ComponentBars components={score.components} />
      )}
      <div style={{ marginTop: 8 }} className="badge-row">
        {score.gate ? (
          score.gate.passed ? (
            <Badge tone="green">gates ✓{isNum(score.gate.reward_risk) ? ` · R/R ${fmtNum(score.gate.reward_risk, 2)}×` : ''}</Badge>
          ) : (
            <Badge tone="amber" title={score.gate.failures.join('; ')}>
              gated: {score.gate.failures.length ? score.gate.failures.join(', ') : 'failed'}
            </Badge>
          )
        ) : (
          <Badge tone="neutral" outline>
            no gate result
          </Badge>
        )}
      </div>
      {score.explanation.length > 0 && (
        <details className="expander" style={{ marginTop: 8 }}>
          <summary>Contribution lines ({score.explanation.length})</summary>
          <ul className="explain-lines">
            {score.explanation.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Engine tabs
// ---------------------------------------------------------------------------

function EngineTabs({ engines }: { engines: EngineView[] }) {
  const [selected, setSelected] = useState(0);
  if (engines.length === 0) return <div className="dim small">No engine results for the latest run.</div>;
  const active = engines[Math.min(selected, engines.length - 1)];
  return (
    <div>
      <div className="tabs">
        {engines.map((e, i) => (
          <button key={e.engine} className={i === selected ? 'active' : ''} onClick={() => setSelected(i)}>
            {titleCase(e.engine)}{' '}
            <span className="mono" style={{ opacity: 0.8 }}>
              {e.score === null ? '–' : fmtScore(e.score)}
            </span>
          </button>
        ))}
      </div>
      <div className="two-col">
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="kv" style={{ marginBottom: 10 }}>
            <dt>Score</dt>
            <dd>{active.score === null ? 'abstained' : `${fmtScore(active.score)} / 10`}</dd>
            <dt>Data quality</dt>
            <dd className="mono">{fmtNum(active.data_quality, 2)}</dd>
          </div>
          <h3>Components</h3>
          <ComponentBars components={active.components} />
          {active.warnings.length > 0 && (
            <div className="banner warn" style={{ marginTop: 10, marginBottom: 0 }}>
              {active.warnings.map((w, i) => (
                <div key={i} className="small">
                  ⚠ {w}
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="card" style={{ marginBottom: 0 }}>
          <h3>Evidence ({active.evidence.length})</h3>
          <EvidenceList items={active.evidence} emptyText="This engine recorded no evidence." />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Ownership / watchlist toggles
// ---------------------------------------------------------------------------

function OwnershipControls({ company }: { company: CompanyDetail }) {
  const queryClient = useQueryClient();
  const instrumentId = company.instrument.id;
  const [showBuyForm, setShowBuyForm] = useState(false);
  const [quantity, setQuantity] = useState('');
  const [avgCost, setAvgCost] = useState('');
  const [openedAt, setOpenedAt] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['company', String(instrumentId)] });
    void queryClient.invalidateQueries({ queryKey: ['portfolio'] });
    void queryClient.invalidateQueries({ queryKey: ['watchlist'] });
  };
  const onError = (e: unknown) => setActionError(e instanceof Error ? e.message : String(e));

  const addWatch = useMutation({
    mutationFn: () => api.addToWatchlist(instrumentId),
    onSuccess: invalidate,
    onError,
  });
  const removeWatch = useMutation({
    mutationFn: async () => {
      const wl = await api.watchlist();
      const item = wl.items.find((w) => w.instrument_id === instrumentId);
      if (!item) throw new Error('Not on the watchlist any more.');
      await api.removeFromWatchlist(item.id);
    },
    onSuccess: invalidate,
    onError,
  });
  const addPosition = useMutation({
    mutationFn: () =>
      api.addPosition({
        instrument_id: instrumentId,
        quantity: Number(quantity),
        avg_cost_local: Number(avgCost),
        opened_at: openedAt || new Date().toISOString().slice(0, 10),
      }),
    onSuccess: () => {
      setShowBuyForm(false);
      invalidate();
    },
    onError,
  });
  const closePosition = useMutation({
    mutationFn: async () => {
      const pf = await api.portfolio();
      const pos = pf.positions.find((x) => x.instrument_id === instrumentId);
      if (!pos) throw new Error('No open position found for this instrument.');
      await api.closePosition(pos.id);
    },
    onSuccess: invalidate,
    onError,
  });

  return (
    <div>
      <div className="badge-row">
        {company.watchlisted ? (
          <button className="btn sm" disabled={removeWatch.isPending} onClick={() => removeWatch.mutate()}>
            ★ Watchlisted — remove
          </button>
        ) : (
          <button className="btn sm" disabled={addWatch.isPending} onClick={() => addWatch.mutate()}>
            ☆ Add to watchlist
          </button>
        )}
        {company.owned ? (
          <button className="btn sm danger" disabled={closePosition.isPending} onClick={() => closePosition.mutate()}>
            ◈ Owned — close position
          </button>
        ) : (
          <button className="btn sm" onClick={() => setShowBuyForm((s) => !s)}>
            ◇ Record position…
          </button>
        )}
      </div>
      {showBuyForm && !company.owned && (
        <div className="card tight" style={{ marginTop: 8, marginBottom: 0 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <label className="filter-field">
              <span className="flabel">Quantity</span>
              <input type="number" min={0} step="any" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
            </label>
            <label className="filter-field">
              <span className="flabel">Avg cost ({company.instrument.currency})</span>
              <input type="number" min={0} step="any" value={avgCost} onChange={(e) => setAvgCost(e.target.value)} />
            </label>
            <label className="filter-field">
              <span className="flabel">Opened</span>
              <input type="date" value={openedAt} onChange={(e) => setOpenedAt(e.target.value)} />
            </label>
            <button
              className="btn sm primary"
              disabled={addPosition.isPending || !quantity || !avgCost}
              onClick={() => addPosition.mutate()}
            >
              Save
            </button>
          </div>
          <div className="tiny faint" style={{ marginTop: 4 }}>
            Record-keeping only — Vigil has no brokerage connectivity.
          </div>
        </div>
      )}
      {actionError && (
        <div className="small" style={{ color: 'var(--red)', marginTop: 6 }}>
          {actionError}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function CompanyPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const instrumentId = id ?? '';

  const companyQ = useQuery({
    queryKey: ['company', instrumentId],
    queryFn: () => api.company(instrumentId),
    enabled: Boolean(instrumentId),
  });
  const pricesQ = useQuery({
    queryKey: ['company', instrumentId, 'prices'],
    queryFn: () => api.companyPrices(instrumentId, 730),
    enabled: Boolean(instrumentId),
  });
  const financialsQ = useQuery({
    queryKey: ['company', instrumentId, 'financials'],
    queryFn: () => api.companyFinancials(instrumentId),
    enabled: Boolean(instrumentId),
  });
  const enginesQ = useQuery({
    queryKey: ['company', instrumentId, 'engines'],
    queryFn: () => api.companyEngines(instrumentId),
    enabled: Boolean(instrumentId),
  });
  const peersQ = useQuery({
    queryKey: ['company', instrumentId, 'peers'],
    queryFn: () => api.companyPeers(instrumentId),
    enabled: Boolean(instrumentId),
  });
  const alertsQ = useQuery({
    queryKey: ['company', instrumentId, 'alerts'],
    queryFn: () => api.companyAlerts(instrumentId),
    enabled: Boolean(instrumentId),
  });
  const signalsQ = useQuery({
    queryKey: ['company', instrumentId, 'signals'],
    queryFn: () => api.companySignals(instrumentId),
    enabled: Boolean(instrumentId),
  });
  const calendarQ = useQuery({
    queryKey: ['calendar', 180, false],
    queryFn: () => api.calendar(180, false),
  });

  if (companyQ.isPending) return <LoadingSkeleton rows={8} />;
  if (companyQ.isError) return <ErrorPanel error={companyQ.error} onRetry={() => void companyQ.refetch()} />;
  const company = companyQ.data;
  const inst = company.instrument;
  const latest = company.latest;
  const warnings = [...(company.warnings ?? []), ...(latest?.warnings ?? [])];
  const catalysts = (calendarQ.data?.items ?? []).filter((c) => c.instrument_id === inst.id);
  const sparkValues = (pricesQ.data?.bars ?? []).slice(-90).map((b) => b.adj_close);

  return (
    <div>
      {/* header */}
      <div className="page-head">
        <div>
          <h1>
            {inst.name} <span className="mono dim">({inst.ticker} · {inst.exchange})</span>
          </h1>
          <div className="sub">
            {inst.market} · {inst.sector} · {inst.industry} · {inst.currency}
            {!inst.is_active && (
              <Badge tone="red" outline>
                delisted {inst.delisted_at ? fmtDate(inst.delisted_at) : ''}
              </Badge>
            )}
          </div>
          <div className="dim small" style={{ marginTop: 4 }}>
            Market cap {fmtMoney(company.liquidity?.market_cap_base ?? null)} (base) · Median daily traded{' '}
            {fmtMoney(company.liquidity?.median_daily_traded_value_base ?? null)}
            {isNum(company.liquidity?.price_staleness_days) && company.liquidity.price_staleness_days > 0 && (
              <span style={{ color: 'var(--amber)' }}>
                {' '}· ⚠ price {company.liquidity.price_staleness_days}d stale
              </span>
            )}
          </div>
          <div style={{ marginTop: 8 }}>
            <OwnershipControls company={company} />
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          {sparkValues.length > 1 && <Sparkline values={sparkValues} width={160} height={40} tone="auto" />}
          <div className="tiny faint">last 90 sessions</div>
        </div>
      </div>

      {warnings.length > 0 && (
        <div className="banner warn">
          {warnings.map((w, i) => (
            <div key={i} className="small">
              ⚠ {w}
            </div>
          ))}
        </div>
      )}

      {/* horizon scores */}
      {latest ? (
        <>
          <div className="dim small" style={{ marginBottom: 6 }}>
            Latest assessment: run #{latest.run_id} · as of {fmtDate(latest.as_of)}
          </div>
          <div className="grid cols-3">
            {HORIZONS.map((h) => {
              const s = latest.horizons[h];
              return s ? (
                <HorizonCard key={h} horizon={h} score={s} bestFit={latest.best_fit_horizon === h} />
              ) : (
                <div className="card" key={h} style={{ marginBottom: 0 }}>
                  <h2>{titleCase(h)}</h2>
                  <div className="dim small">Not scored at this horizon.</div>
                </div>
              );
            })}
          </div>
        </>
      ) : (
        <EmptyState
          message="This company has never been scored."
          hint="It will appear in the rankings after the next scan that covers it."
        />
      )}

      {/* price chart */}
      <QueryGate query={pricesQ} isEmpty={(d) => d.bars.length === 0} empty={<EmptyState message="No price history stored." />}>
        {(prices) => <PriceChart bars={prices.bars} markers={prices.markers} />}
      </QueryGate>

      {/* financials */}
      <div className="section-title">
        <h2>Financial trends</h2>
        <div className="rule" />
      </div>
      <QueryGate
        query={financialsQ}
        isEmpty={(d) => d.quarters.length === 0}
        empty={<EmptyState message="No fundamentals stored for this company." showScanHint={false} />}
      >
        {(fin) => <FinancialCharts quarters={fin.quarters} />}
      </QueryGate>

      {/* engines */}
      <div className="section-title">
        <h2>Engine evidence</h2>
        <div className="rule" />
      </div>
      <QueryGate query={enginesQ} isEmpty={(d) => d.engines.length === 0} empty={<EmptyState message="No engine results yet." />}>
        {(data) => <EngineTabs engines={data.engines} />}
      </QueryGate>

      {/* peers */}
      <div className="section-title">
        <h2>Peers</h2>
        <div className="rule" />
      </div>
      <QueryGate
        query={peersQ}
        isEmpty={(d) => d.peers.length === 0}
        empty={<EmptyState message="No peer set for this company." showScanHint={false} />}
      >
        {(data) => {
          const metricKeys = [...new Set(data.peers.flatMap((p) => Object.keys(p.metrics)))].sort();
          return (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>Name</th>
                    {metricKeys.map((k) => (
                      <th key={k} className="num">
                        {k.replace(/_/g, ' ')}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.peers.map((peer) => (
                    <tr
                      key={peer.instrument_id}
                      className="clickable"
                      onClick={() => navigate(`/companies/${peer.instrument_id}`)}
                    >
                      <td className="mono" style={{ fontWeight: 700 }}>
                        {peer.ticker}
                      </td>
                      <td>{peer.name}</td>
                      {metricKeys.map((k) => (
                        <td key={k} className="num mono small">
                          {fmtNum(peer.metrics[k] ?? null)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }}
      </QueryGate>

      {/* catalysts */}
      <div className="section-title">
        <h2>Upcoming catalysts</h2>
        <div className="rule" />
      </div>
      {catalysts.length === 0 ? (
        <div className="card dim small">No upcoming catalysts in the next 180 days.</div>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Date</th>
                <th className="num">Days</th>
                <th className="center">Binary</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {catalysts.map((c, i) => (
                <tr key={i}>
                  <td>
                    <Badge tone="blue" outline>
                      {titleCase(c.kind)}
                    </Badge>
                  </td>
                  <td className="mono">
                    {fmtDate(c.expected_date)}
                    {!c.date_confirmed && <span className="faint tiny"> (est.)</span>}
                  </td>
                  <td className="num mono">{c.days}</td>
                  <td className="center">{c.binary ? <Badge tone="red">binary</Badge> : <span className="faint">—</span>}</td>
                  <td className="small">{c.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* prior alerts */}
      <div className="section-title">
        <h2>Prior alerts</h2>
        <div className="rule" />
      </div>
      <QueryGate
        query={alertsQ}
        isEmpty={(d) => d.items.length === 0}
        empty={<div className="card dim small">No alerts have been raised for this company.</div>}
      >
        {(data) => (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Title</th>
                  <th>Family / state</th>
                  <th className="center">O / C / R</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((a) => (
                  <tr key={a.id} className="clickable" onClick={() => navigate(`/alerts/${a.id}`)}>
                    <td className="mono small dim">{fmtDateTime(a.created_at)}</td>
                    <td>{a.title}</td>
                    <td>
                      <span className="badge-row">
                        <Badge tone="blue" outline>
                          {familyLabel(a.family)}
                        </Badge>
                        <Badge tone={stateTone(a.lifecycle_state)}>{a.lifecycle_state}</Badge>
                      </span>
                    </td>
                    <td className="center">
                      <ScoreTriple opportunity={a.opportunity} confidence={a.confidence} risk={a.risk} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryGate>

      {/* signal history */}
      <div className="section-title">
        <h2>Signal history</h2>
        <div className="rule" />
      </div>
      <QueryGate
        query={signalsQ}
        isEmpty={(d) => d.items.length === 0}
        empty={<div className="card dim small">No signals recorded for this company.</div>}
      >
        {(data) => (
          <div className="grid cols-2">
            {data.items.map((sig) => (
              <div className="card" key={sig.id} style={{ marginBottom: 0 }}>
                <div className="badge-row" style={{ marginBottom: 8 }}>
                  <Badge tone="blue" outline>
                    {familyLabel(sig.family)}
                  </Badge>
                  <Badge tone={stateTone(sig.state)}>{sig.state}</Badge>
                  <Badge tone="neutral" outline>
                    {titleCase(sig.horizon)}
                  </Badge>
                  {!sig.active && <Badge tone="neutral">inactive</Badge>}
                  {isNum(sig.anchor_price) && (
                    <span className="tiny dim mono">
                      anchor {fmtNum(sig.anchor_price)} @ {fmtDate(sig.anchor_date)}
                    </span>
                  )}
                </div>
                {sig.state_history.length === 0 ? (
                  <div className="dim small">No state transitions recorded.</div>
                ) : (
                  <ul className="timeline">
                    {sig.state_history.map((h, i) => (
                      <li key={i} className={`tone-${stateTone(h.state)}`}>
                        <span className="mono dim">{fmtDate(h.as_of)}</span> → <strong>{h.state}</strong>
                        {h.reason && <div className="tiny dim">{h.reason}</div>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        )}
      </QueryGate>

      {/* deep link back */}
      <div className="dim small" style={{ marginTop: 16 }}>
        <Link to="/">← back to ranked opportunities</Link>
      </div>
    </div>
  );
}
