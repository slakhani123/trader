import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import type { ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import type { AlertPayload, ScoreView, Scenario, SupportZone } from '../api/types';
import { Badge } from '../components/Badge';
import { EvidenceList } from '../components/EvidenceList';
import { ComponentBars } from '../components/MiniBars';
import { QueryGate } from '../components/QueryGate';
import { ScoreChip } from '../components/ScoreChip';
import {
  familyLabel,
  fmtDate,
  fmtDateTime,
  fmtMoney,
  fmtNum,
  fmtPct,
  fmtPrice,
  fmtScore,
  fmtSignedScore,
  isNum,
  priorityTone,
  stateTone,
  titleCase,
} from '../lib/format';

function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <div className="section-title">
      <h2>{children}</h2>
      <div className="rule" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Price plan band: stop · entry zone · last price · target range on one axis
// ---------------------------------------------------------------------------

function PlanBand({ payload }: { payload: AlertPayload }) {
  const points: { label: string; value: number; color: string }[] = [];
  const price = payload.price.price;
  points.push({ label: `last ${fmtNum(price)}`, value: price, color: 'var(--blue)' });
  if (isNum(payload.stop)) points.push({ label: `stop ${fmtNum(payload.stop)}`, value: payload.stop, color: 'var(--red)' });
  if (payload.target_range) {
    points.push({ label: `t.low ${fmtNum(payload.target_range.low)}`, value: payload.target_range.low, color: 'var(--green)' });
    points.push({ label: `t.high ${fmtNum(payload.target_range.high)}`, value: payload.target_range.high, color: 'var(--green)' });
  }
  const zone = payload.entry_zone;
  const values = [...points.map((p) => p.value), ...(zone ? [zone.low, zone.high] : [])];
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const lo = min - span * 0.06;
  const hi = max + span * 0.06;
  const posOf = (v: number) => `${(((v - lo) / (hi - lo)) * 100).toFixed(2)}%`;
  return (
    <div style={{ margin: '4px 0 26px' }}>
      <div className="zone-band">
        {zone && (
          <span
            className="zone"
            title={`Entry zone ${fmtNum(zone.low)} – ${fmtNum(zone.high)}`}
            style={{
              left: posOf(zone.low),
              width: `calc(${posOf(zone.high)} - ${posOf(zone.low)})`,
            }}
          />
        )}
        {points.map((p, i) => (
          <span key={i}>
            <span className="tick" style={{ left: posOf(p.value), background: p.color }} />
            <span className="tick-label" style={{ left: posOf(p.value), color: p.color }}>
              {p.label}
            </span>
          </span>
        ))}
      </div>
      {zone && (
        <div className="small dim">
          Entry zone {fmtNum(zone.low)} – {fmtNum(zone.high)} (zones, never points)
        </div>
      )}
    </div>
  );
}

function ScenarioCard({ scenario, currency }: { scenario: Scenario; currency: string }) {
  return (
    <div className={`scenario ${scenario.name}`}>
      <div className="tiny dim" style={{ textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        {scenario.name}
      </div>
      <div className="price">{fmtPrice(scenario.price, currency)}</div>
      {scenario.probability_note && <div className="small dim">{scenario.probability_note}</div>}
      <div className="small" style={{ marginTop: 4 }}>
        {scenario.rationale}
      </div>
    </div>
  );
}

function ConditionList({ title, items, tone }: { title: string; items: string[]; tone?: 'red' | 'amber' }) {
  return (
    <div className="card tight" style={{ marginBottom: 0 }}>
      <h3 style={tone === 'red' ? { color: 'var(--red)' } : tone === 'amber' ? { color: 'var(--amber)' } : undefined}>
        {title}
      </h3>
      {items.length === 0 ? (
        <div className="dim small">None specified.</div>
      ) : (
        <ul className="plain small">
          {items.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function HorizonComparison({ scores, all }: { scores: ScoreView; all: Record<string, ScoreView> }) {
  const order = ['short', 'medium', 'long'];
  const horizons = [
    ...order.filter((h) => h in all),
    ...Object.keys(all).filter((h) => !order.includes(h)).sort(),
  ];
  return (
    <div className="table-wrap" style={{ marginBottom: 0 }}>
      <table className="data">
        <thead>
          <tr>
            <th>Horizon</th>
            <th className="center">Opportunity</th>
            <th className="center">Confidence</th>
            <th className="center">Risk</th>
          </tr>
        </thead>
        <tbody>
          {horizons.map((h) => {
            const s = all[h];
            if (!s) return null;
            const alerted = h === scores.horizon;
            return (
              <tr key={h} style={alerted ? { background: 'var(--blue-bg)' } : undefined}>
                <td>
                  {titleCase(h)}
                  {alerted && (
                    <span className="tiny" style={{ color: 'var(--blue)', marginLeft: 6 }}>
                      ← alerted
                    </span>
                  )}
                </td>
                <td className="center">
                  <ScoreChip value={s.opportunity} kind="opportunity" small />
                </td>
                <td className="center">
                  <ScoreChip value={s.confidence} kind="confidence" small />
                </td>
                <td className="center">
                  <ScoreChip value={s.risk} kind="risk" small />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function zoneCell(z: SupportZone, key: 'low' | 'high'): string {
  const v = z[key];
  return isNum(v) ? fmtNum(v) : '—';
}

// ---------------------------------------------------------------------------

const THESIS_QUESTIONS: { key: keyof AlertPayload['thesis']; label: string }[] = [
  { key: 'why_this_company', label: '1 · Why this company?' },
  { key: 'why_now', label: '2 · Why now?' },
  { key: 'what_market_misunderstands', label: '3 · What does the market misunderstand?' },
  { key: 'what_would_prove_wrong', label: '4 · What would prove the thesis wrong?' },
  { key: 'expected_holding_period', label: '5 · Expected holding period' },
  { key: 'early_trim_or_exit_causes', label: '6 · What would cause an early trim or exit?' },
  { key: 'three_largest_risks', label: '7 · Three largest risks' },
];

export function AlertDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['alert', id],
    queryFn: () => api.alert(id ?? ''),
    enabled: Boolean(id),
  });

  const markRead = useMutation({
    mutationFn: (alertId: number) => api.markAlertRead(alertId),
    onSuccess: () => {
      queryClient.setQueryData(['alert', id], (old: unknown) =>
        old && typeof old === 'object' ? { ...old, read: true } : old,
      );
      void queryClient.invalidateQueries({ queryKey: ['alerts'] });
    },
  });

  const alert = query.data;
  useEffect(() => {
    if (alert && !alert.read && !markRead.isPending) {
      markRead.mutate(alert.id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alert?.id, alert?.read]);

  return (
    <QueryGate query={query} skeletonRows={8}>
      {(a) => {
        const p = a.payload;
        const localCcy = p.price.currency;
        return (
          <div>
            {/* ------------------------------------------------ header */}
            <div className="page-head">
              <div>
                <div className="badge-row" style={{ marginBottom: 6 }}>
                  <Badge tone="blue" outline>
                    {familyLabel(p.signal_family)}
                  </Badge>
                  <Badge tone={stateTone(p.lifecycle_state)}>{p.lifecycle_state}</Badge>
                  <Badge tone="neutral" outline title="Lifecycle transition">
                    {p.transition}
                  </Badge>
                  <Badge tone={priorityTone(p.priority)}>{p.priority} priority</Badge>
                  <Badge tone="neutral">{titleCase(p.horizon)} horizon</Badge>
                  {p.best_fit_horizon && (
                    <Badge tone="purple" outline title="Horizon the evidence best supports">
                      best fit: {titleCase(p.best_fit_horizon)}
                    </Badge>
                  )}
                </div>
                <h1>
                  <Link to={`/companies/${a.instrument_id}`}>{p.company.name}</Link>{' '}
                  <span className="mono dim">({p.company.ticker} · {p.company.exchange})</span>
                </h1>
                <div className="sub">
                  {p.company.market} · {p.company.sector} · {p.company.industry} · Market cap{' '}
                  {fmtMoney(p.company.market_cap_local, p.company.local_currency)}
                  {isNum(p.company.market_cap_base) &&
                    ` (${fmtMoney(p.company.market_cap_base, p.company.base_currency)})`}
                </div>
                <div className="dim small" style={{ marginTop: 4 }}>{a.title}</div>
              </div>

              <div className="card tight" style={{ minWidth: 240, marginBottom: 0 }}>
                <div className="stat">
                  <span className="label">Last price</span>
                  <span className="value mono">{fmtPrice(p.price.price, localCcy)}</span>
                  <span className="hint mono">
                    bar {fmtDateTime(p.price.bar_timestamp)} · as of {fmtDate(p.price.as_of_date)}
                  </span>
                  <span className="hint">
                    {p.price.staleness_trading_days > 0 ? (
                      <span style={{ color: 'var(--amber)' }}>
                        ⚠ price is {p.price.staleness_trading_days} trading day
                        {p.price.staleness_trading_days === 1 ? '' : 's'} stale
                      </span>
                    ) : (
                      <span style={{ color: 'var(--green)' }}>fresh (0 trading days stale)</span>
                    )}
                  </span>
                  {isNum(p.price.fx_to_base) && (
                    <span className="hint mono">
                      FX→{p.company.base_currency} {fmtNum(p.price.fx_to_base, 4)}
                      {p.price.fx_as_of ? ` (as of ${fmtDate(p.price.fx_as_of)})` : ''}
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* ------------------------------------------------ binary event warning */}
            {p.binary_event_warning && (
              <div className="banner danger">
                <div className="title">⚠ Binary event ahead</div>
                {p.binary_event_warning}
              </div>
            )}

            {/* ------------------------------------------------ scores */}
            <SectionTitle>Scores — {titleCase(p.scores.horizon)} horizon</SectionTitle>
            <div className="grid cols-3">
              <div className="card">
                <div className="stat">
                  <span className="label">Opportunity</span>
                  <span className="value">
                    <ScoreChip value={p.scores.opportunity} kind="opportunity" /> / 10
                  </span>
                </div>
                <div style={{ marginTop: 10 }}>
                  <ComponentBars components={p.scores.components} />
                </div>
              </div>
              <div className="card">
                <div className="stat">
                  <span className="label">Confidence</span>
                  <span className="value">
                    <ScoreChip value={p.scores.confidence} kind="confidence" /> / 10
                  </span>
                  <span className="hint">Computed from data quality, evidence agreement and coverage.</span>
                </div>
                <div className="stat" style={{ marginTop: 10 }}>
                  <span className="label">Risk</span>
                  <span className="value">
                    <ScoreChip value={p.scores.risk} kind="risk" /> / 10
                  </span>
                  <span className="hint">Volatility, drawdown, leverage, events, regime.</span>
                </div>
              </div>
              <div className="card">
                <h3>Per-horizon comparison</h3>
                <HorizonComparison scores={p.scores} all={p.all_horizons} />
              </div>
            </div>
            {p.scores.explanation.length > 0 && (
              <details className="expander card tight">
                <summary>Score contribution lines ({p.scores.explanation.length})</summary>
                <ul className="explain-lines">
                  {p.scores.explanation.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
              </details>
            )}

            {/* ------------------------------------------------ what changed */}
            <SectionTitle>What changed</SectionTitle>
            <div className="card">
              <div className="kv" style={{ marginBottom: p.change.changed.length ? 8 : 0 }}>
                <dt>Previous alert</dt>
                <dd>{p.change.previous_alert_at ? fmtDateTime(p.change.previous_alert_at) : 'None — first alert for this signal'}</dd>
                <dt>Previous state</dt>
                <dd>{p.change.previous_state ?? '—'}</dd>
                <dt>Opportunity Δ</dt>
                <dd className={isNum(p.change.opportunity_delta) && p.change.opportunity_delta >= 0 ? 'pos' : 'neg'}>
                  {fmtSignedScore(p.change.opportunity_delta)}
                </dd>
                <dt>Risk Δ</dt>
                <dd className={isNum(p.change.risk_delta) && p.change.risk_delta <= 0 ? 'pos' : 'neg'}>
                  {fmtSignedScore(p.change.risk_delta)}
                </dd>
                <dt>Price change</dt>
                <dd className={isNum(p.change.price_change_pct) && p.change.price_change_pct >= 0 ? 'pos' : 'neg'}>
                  {fmtPct(p.change.price_change_pct, 1, true)}
                </dd>
              </div>
              {p.change.changed.length > 0 && (
                <ul className="plain small">
                  {p.change.changed.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
              )}
            </div>

            {/* ------------------------------------------------ thesis */}
            <SectionTitle>
              Thesis{' '}
              <Badge tone={p.narrative_source === 'llm' ? 'purple' : 'neutral'} title="How the narrative was produced">
                {p.narrative_source === 'llm' ? 'LLM narrative (validated)' : 'template narrative'}
              </Badge>
            </SectionTitle>
            <div className="card">
              <div className="small" style={{ marginBottom: 8, color: 'var(--text)' }}>
                {p.thesis_summary}
              </div>
              {THESIS_QUESTIONS.map(({ key, label }) => (
                <div className="qa-item" key={key}>
                  <div className="qa-q">{label}</div>
                  {key === 'three_largest_risks' ? (
                    <ol className="plain small" style={{ margin: 0, paddingLeft: 18 }}>
                      {p.thesis.three_largest_risks.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ol>
                  ) : (
                    <div className="small">{p.thesis[key]}</div>
                  )}
                </div>
              ))}
            </div>

            {/* ------------------------------------------------ evidence */}
            <SectionTitle>Evidence</SectionTitle>
            <div className="two-col" style={{ marginBottom: 14 }}>
              <div className="card" style={{ marginBottom: 0 }}>
                <h3 style={{ color: 'var(--green)' }}>Supporting ({p.supporting.length})</h3>
                <EvidenceList items={p.supporting} emptyText="No supporting evidence recorded." />
              </div>
              <div className="card" style={{ marginBottom: 0 }}>
                <h3 style={{ color: 'var(--red)' }}>Contradicting ({p.contradicting.length})</h3>
                <EvidenceList items={p.contradicting} emptyText="No contradicting evidence recorded." />
              </div>
            </div>

            {/* ------------------------------------------------ valuation */}
            <SectionTitle>Valuation</SectionTitle>
            <div className="two-col" style={{ marginBottom: 14 }}>
              <div className="card" style={{ marginBottom: 0 }}>
                <h3>Multiples{p.valuation.primary_multiple ? ` (primary: ${p.valuation.primary_multiple})` : ''}</h3>
                {Object.keys(p.valuation.multiples).length === 0 ? (
                  <div className="dim small">No multiples available.</div>
                ) : (
                  <div className="table-wrap" style={{ marginBottom: 8 }}>
                    <table className="data">
                      <tbody>
                        {Object.entries(p.valuation.multiples).map(([k, v]) => (
                          <tr key={k}>
                            <td className="dim">{k.toUpperCase().replace(/_/g, ' ')}</td>
                            <td className="num mono">{fmtNum(v)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <div className="kv">
                  <dt>Vs own history</dt>
                  <dd>
                    {isNum(p.valuation.vs_history_percentile)
                      ? `${fmtNum(p.valuation.vs_history_percentile, 0)}th percentile`
                      : '—'}
                  </dd>
                  <dt>Vs peers</dt>
                  <dd>{p.valuation.vs_peers_note ?? '—'}</dd>
                  <dt>Fair value range</dt>
                  <dd className="mono">
                    {isNum(p.valuation.fair_value_low) && isNum(p.valuation.fair_value_high)
                      ? `${fmtPrice(p.valuation.fair_value_low, localCcy)} – ${fmtPrice(p.valuation.fair_value_high, localCcy)}`
                      : '—'}
                  </dd>
                </div>
              </div>
              <div className="card" style={{ marginBottom: 0 }}>
                <h3>Analyst consensus target</h3>
                {p.valuation.analyst_target ? (
                  <div className="kv">
                    <dt>Mean target</dt>
                    <dd className="mono">
                      {isNum(p.valuation.analyst_target.mean) ? fmtPrice(p.valuation.analyst_target.mean, localCcy) : '—'}
                    </dd>
                    <dt>Implied upside</dt>
                    <dd
                      className={
                        isNum(p.valuation.analyst_target.implied_upside_pct) &&
                        p.valuation.analyst_target.implied_upside_pct >= 0
                          ? 'pos'
                          : 'neg'
                      }
                    >
                      {fmtPct(p.valuation.analyst_target.implied_upside_pct ?? null, 1, true)}
                    </dd>
                    <dt>Analysts</dt>
                    <dd>{isNum(p.valuation.analyst_target.count) ? p.valuation.analyst_target.count : '—'}</dd>
                    <dt>Dispersion</dt>
                    <dd>{fmtPct(p.valuation.analyst_target.dispersion_pct ?? null)}</dd>
                    <dt>Median age</dt>
                    <dd>
                      {isNum(p.valuation.analyst_target.median_age_days)
                        ? `${fmtNum(p.valuation.analyst_target.median_age_days, 0)} days`
                        : '—'}
                    </dd>
                  </div>
                ) : (
                  <div className="dim small">No analyst target data. Targets are supporting evidence, never truth.</div>
                )}
              </div>
            </div>

            {/* ------------------------------------------------ technicals */}
            <SectionTitle>Technicals</SectionTitle>
            <div className="two-col" style={{ marginBottom: 14 }}>
              <div className="card" style={{ marginBottom: 0 }}>
                <div className="kv" style={{ marginBottom: 8 }}>
                  <dt>Trend</dt>
                  <dd>{p.technicals.trend_state ? titleCase(p.technicals.trend_state) : '—'}</dd>
                  <dt>RSI-14</dt>
                  <dd className="mono">{fmtNum(p.technicals.rsi14, 1)}</dd>
                  <dt>ATR %</dt>
                  <dd className="mono">{fmtPct(p.technicals.atr_pct)}</dd>
                  <dt>Reward / risk</dt>
                  <dd className="mono">{isNum(p.technicals.reward_risk) ? `${fmtNum(p.technicals.reward_risk, 2)}×` : '—'}</dd>
                  <dt>Resistance</dt>
                  <dd className="mono">
                    {p.technicals.resistance_levels.length
                      ? p.technicals.resistance_levels.map((r) => fmtNum(r)).join(' · ')
                      : '—'}
                  </dd>
                </div>
                {p.technicals.notes.length > 0 && (
                  <ul className="plain small dim">
                    {p.technicals.notes.map((n, i) => (
                      <li key={i}>{n}</li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="card" style={{ marginBottom: 0 }}>
                <h3>Support zones</h3>
                {p.technicals.support_zones.length === 0 ? (
                  <div className="dim small">No support zones identified.</div>
                ) : (
                  <div className="table-wrap" style={{ marginBottom: 0 }}>
                    <table className="data">
                      <thead>
                        <tr>
                          <th>Low</th>
                          <th>High</th>
                          <th>Kind</th>
                          <th>Strength</th>
                        </tr>
                      </thead>
                      <tbody>
                        {p.technicals.support_zones.map((z, i) => (
                          <tr key={i}>
                            <td className="mono">{zoneCell(z, 'low')}</td>
                            <td className="mono">{zoneCell(z, 'high')}</td>
                            <td className="dim">{typeof z.kind === 'string' ? titleCase(z.kind) : '—'}</td>
                            <td className="dim">
                              {isNum(z.strength) ? fmtNum(z.strength, 1) : typeof z.strength === 'string' ? z.strength : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>

            {/* ------------------------------------------------ catalysts */}
            <SectionTitle>Catalysts</SectionTitle>
            {p.catalysts.length === 0 ? (
              <div className="card dim small">No upcoming catalysts identified.</div>
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Kind</th>
                      <th>Date</th>
                      <th className="num">Days</th>
                      <th className="center">Confirmed</th>
                      <th className="center">Binary</th>
                      <th className="center">Priced in</th>
                      <th>Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {p.catalysts.map((c, i) => (
                      <tr key={i}>
                        <td>
                          <Badge tone="blue" outline>
                            {titleCase(c.kind)}
                          </Badge>
                        </td>
                        <td className="mono">{fmtDate(c.date)}</td>
                        <td className="num mono">{c.days}</td>
                        <td className="center">{c.confirmed ? <Badge tone="green">confirmed</Badge> : <Badge tone="neutral" outline>estimated</Badge>}</td>
                        <td className="center">{c.binary ? <Badge tone="red">binary</Badge> : <span className="faint">—</span>}</td>
                        <td className="center">
                          {isNum(c.priced_in_pct) ? <Badge tone="amber">{fmtNum(c.priced_in_pct, 0)}% priced in</Badge> : <span className="faint">—</span>}
                        </td>
                        <td className="small">{c.description}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* ------------------------------------------------ plan */}
            <SectionTitle>Plan</SectionTitle>
            <div className="card">
              <PlanBand payload={p} />
              <div className="grid cols-3" style={{ marginBottom: 12 }}>
                {p.scenarios.map((s) => (
                  <ScenarioCard key={s.name} scenario={s} currency={localCcy} />
                ))}
              </div>
              <div className="kv" style={{ marginBottom: 12 }}>
                <dt>Stop</dt>
                <dd className="mono">{isNum(p.stop) ? fmtPrice(p.stop, localCcy) : '—'}</dd>
                <dt>Target range</dt>
                <dd className="mono">
                  {p.target_range
                    ? `${fmtPrice(p.target_range.low, localCcy)} – ${fmtPrice(p.target_range.high, localCcy)}`
                    : '—'}
                </dd>
              </div>
              <div className="grid cols-2">
                <ConditionList title="Conditions before entry" items={p.conditions_before_entry} />
                <ConditionList title="Price invalidation" items={p.invalidation_conditions} tone="red" />
                <ConditionList title="Fundamental invalidation" items={p.fundamental_invalidation} tone="red" />
                <ConditionList title="Trim conditions" items={p.trim_conditions} tone="amber" />
                <ConditionList title="Exit conditions" items={p.exit_conditions} tone="amber" />
              </div>
            </div>

            {/* ------------------------------------------------ data quality */}
            {(p.data_warnings.length > 0 || p.missing_data.length > 0) && (
              <div className="banner warn">
                <div className="title">Data caveats</div>
                {p.data_warnings.map((w, i) => (
                  <div key={`w${i}`} className="small">
                    ⚠ {w}
                  </div>
                ))}
                {p.missing_data.length > 0 && (
                  <div className="small" style={{ marginTop: 4 }}>
                    Missing: {p.missing_data.join(', ')}
                  </div>
                )}
              </div>
            )}

            {/* ------------------------------------------------ sources */}
            <SectionTitle>Sources</SectionTitle>
            <div className="card">
              {p.sources.length === 0 ? (
                <div className="dim small">No source lines recorded.</div>
              ) : (
                <div className="table-wrap" style={{ marginBottom: 0 }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Provider</th>
                        <th>Type</th>
                        <th>Reference</th>
                        <th>Published</th>
                        <th className="num">Freshness</th>
                      </tr>
                    </thead>
                    <tbody>
                      {p.sources.map((s, i) => (
                        <tr key={i}>
                          <td>{s.provider}</td>
                          <td className="dim">{titleCase(s.source_type)}</td>
                          <td className="mono small" style={{ wordBreak: 'break-all' }}>
                            {s.reference}
                          </td>
                          <td className="mono small dim">{s.published_at ? fmtDateTime(s.published_at) : '—'}</td>
                          <td className="num mono small">
                            {isNum(s.freshness_days) ? `${s.freshness_days.toFixed(0)}d` : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* ------------------------------------------------ provenance */}
            <div className="badge-row" style={{ marginTop: 10 }}>
              <Badge tone="neutral" outline>
                model {p.model_version}
              </Badge>
              <Badge tone="neutral" outline>
                generated {fmtDateTime(p.generated_at)}
              </Badge>
              <Badge tone={p.narrative_source === 'llm' ? 'purple' : 'neutral'}>
                narrative: {p.narrative_source}
              </Badge>
              <Badge tone="neutral" outline>
                alert #{a.id} · created {fmtDateTime(a.created_at)} · O {fmtScore(a.opportunity)} C {fmtScore(a.confidence)} R {fmtScore(a.risk)}
              </Badge>
            </div>

            <div className="disclaimer">{p.disclaimer}</div>
          </div>
        );
      }}
    </QueryGate>
  );
}
