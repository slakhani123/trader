import { useQueries, useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { EntryPlan, SignalView } from '../api/types';
import { Badge } from '../components/Badge';
import { EmptyState } from '../components/EmptyState';
import { QueryGate } from '../components/QueryGate';
import { ScoreTriple } from '../components/ScoreChip';
import {
  ACTIVE_STATES,
  TERMINAL_STATES,
  familyLabel,
  fmtAge,
  fmtDate,
  fmtNum,
  fmtPct,
  isNum,
  stateTone,
  titleCase,
} from '../lib/format';

/** How many distinct instruments we are willing to price client-side. */
const MAX_PRICED_INSTRUMENTS = 40;

function PlanPopover({ plan }: { plan: EntryPlan | null }) {
  if (!plan) return <span className="faint">—</span>;
  const zone =
    isNum(plan.zone_low) && isNum(plan.zone_high)
      ? `${fmtNum(plan.zone_low)} – ${fmtNum(plan.zone_high)}`
      : '—';
  const target =
    isNum(plan.target_low) && isNum(plan.target_high)
      ? `${fmtNum(plan.target_low)} – ${fmtNum(plan.target_high)}`
      : '—';
  return (
    <details className="popover">
      <summary>
        <span className="btn sm">plan…</span>
      </summary>
      <div className="popover-panel">
        <div className="kv" style={{ marginBottom: 8 }}>
          <dt>Entry zone</dt>
          <dd className="mono">{zone}</dd>
          <dt>Stop</dt>
          <dd className="mono">{fmtNum(plan.stop)}</dd>
          <dt>Target range</dt>
          <dd className="mono">{target}</dd>
          <dt>Reward / risk</dt>
          <dd className="mono">{isNum(plan.reward_risk) ? `${fmtNum(plan.reward_risk, 2)}×` : '—'}</dd>
        </div>
        {plan.conditions_before_entry.length > 0 && (
          <>
            <div className="tiny dim" style={{ textTransform: 'uppercase' }}>
              Before entry
            </div>
            <ul className="plain small">
              {plan.conditions_before_entry.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </>
        )}
        {plan.invalidation_conditions.length > 0 && (
          <>
            <div className="tiny" style={{ textTransform: 'uppercase', color: 'var(--red)' }}>
              Invalidation
            </div>
            <ul className="plain small">
              {plan.invalidation_conditions.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </>
        )}
        {plan.exit_conditions.length > 0 && (
          <>
            <div className="tiny" style={{ textTransform: 'uppercase', color: 'var(--amber)' }}>
              Exit
            </div>
            <ul className="plain small">
              {plan.exit_conditions.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </>
        )}
      </div>
    </details>
  );
}

function SignalRows({
  signals,
  lastPrices,
}: {
  signals: SignalView[];
  lastPrices: Map<number, number>;
}) {
  return (
    <div className="table-wrap" style={{ marginBottom: 0 }}>
      <table className="data">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Name</th>
            <th>Family</th>
            <th className="center">Horizon</th>
            <th className="num">Age</th>
            <th className="num">Anchor</th>
            <th className="num" title="Last close vs anchor price">
              Since anchor
            </th>
            <th className="center">Last O/C/R</th>
            <th className="center">Plan</th>
            <th>Expires</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((s) => {
            const last = lastPrices.get(s.instrument_id);
            const move =
              isNum(s.anchor_price) && isNum(last) && s.anchor_price !== 0
                ? (last / s.anchor_price - 1) * 100
                : null;
            return (
              <tr key={s.id}>
                <td>
                  <Link to={`/companies/${s.instrument_id}`} className="mono" style={{ fontWeight: 700 }}>
                    {s.ticker}
                  </Link>
                </td>
                <td>{s.name}</td>
                <td>
                  <Badge tone="blue" outline>
                    {familyLabel(s.family)}
                  </Badge>
                </td>
                <td className="center dim">{titleCase(s.horizon)}</td>
                <td className="num mono" title={`created ${fmtDate(s.created_at)}`}>
                  {fmtAge(s.created_at)}
                </td>
                <td className="num mono small">
                  {isNum(s.anchor_price) ? (
                    <span title={`anchored ${fmtDate(s.anchor_date)}`}>
                      {fmtNum(s.anchor_price)}
                    </span>
                  ) : (
                    '—'
                  )}
                </td>
                <td className={`num mono ${isNum(move) && move < 0 ? 'neg' : 'pos'}`}>
                  {isNum(move) ? fmtPct(move, 1, true) : '—'}
                </td>
                <td className="center">
                  {s.last_scores ? (
                    <ScoreTriple
                      opportunity={s.last_scores.opportunity}
                      confidence={s.last_scores.confidence}
                      risk={s.last_scores.risk}
                    />
                  ) : (
                    <span className="faint">—</span>
                  )}
                </td>
                <td className="center">
                  <PlanPopover plan={s.entry_plan} />
                </td>
                <td className="dim small mono">{s.expires_at ? fmtDate(s.expires_at) : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function SignalsPage() {
  const query = useQuery({
    queryKey: ['signals', 'all'],
    queryFn: () => api.signals({ limit: 500 }),
  });

  const signals = useMemo(() => query.data?.items ?? [], [query.data]);
  const active = useMemo(() => signals.filter((s) => s.active), [signals]);
  const terminal = useMemo(() => signals.filter((s) => !s.active), [signals]);

  const pricedInstruments = useMemo(
    () => [...new Set(active.map((s) => s.instrument_id))].slice(0, MAX_PRICED_INSTRUMENTS),
    [active],
  );

  // One tiny price query per active instrument so we can show anchor-vs-now.
  const priceQueries = useQueries({
    queries: pricedInstruments.map((id) => ({
      queryKey: ['signal-price', id],
      queryFn: () => api.companyPrices(id, 10),
      staleTime: 5 * 60_000,
      retry: 0,
    })),
  });

  const lastPrices = useMemo(() => {
    const map = new Map<number, number>();
    priceQueries.forEach((q, i) => {
      const bars = q.data?.bars;
      if (bars && bars.length > 0) map.set(pricedInstruments[i], bars[bars.length - 1].close);
    });
    return map;
  }, [priceQueries, pricedInstruments]);

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Thesis Tracker</h1>
          <div className="sub">
            Every live signal and where it sits in the lifecycle: WATCHING → TRIGGERED → REINFORCED
            → WEAKENING → TRIM → exit states.
          </div>
        </div>
      </div>

      <QueryGate
        query={query}
        isEmpty={(d) => d.items.length === 0}
        empty={
          <EmptyState
            message="No signals recorded yet."
            hint="Signals appear once a scan detects a family pattern."
          />
        }
      >
        {() => (
          <>
            {ACTIVE_STATES.map((state) => {
              const group = active.filter((s) => s.state === state);
              if (group.length === 0) return null;
              return (
                <div className="card" key={state}>
                  <div
                    style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}
                  >
                    <Badge tone={stateTone(state)}>{state}</Badge>
                    <span className="dim small">
                      {group.length} signal{group.length === 1 ? '' : 's'}
                    </span>
                  </div>
                  <SignalRows signals={group} lastPrices={lastPrices} />
                </div>
              );
            })}
            {active.length === 0 && (
              <div className="card dim small">No active signals right now.</div>
            )}

            <details className="expander card tight" style={{ marginTop: 14 }}>
              <summary>
                Terminal signals — {TERMINAL_STATES.join(' / ')} ({terminal.length})
              </summary>
              {terminal.length === 0 ? (
                <div className="dim small" style={{ marginTop: 6 }}>
                  Nothing has exited, expired or been invalidated yet.
                </div>
              ) : (
                <div style={{ marginTop: 8 }}>
                  <div className="table-wrap" style={{ marginBottom: 0 }}>
                    <table className="data">
                      <thead>
                        <tr>
                          <th>Ticker</th>
                          <th>Family</th>
                          <th className="center">State</th>
                          <th className="center">Horizon</th>
                          <th>Created</th>
                          <th>Closed</th>
                          <th className="num">Anchor</th>
                          <th>History</th>
                        </tr>
                      </thead>
                      <tbody>
                        {terminal.map((s) => (
                          <tr key={s.id}>
                            <td>
                              <Link
                                to={`/companies/${s.instrument_id}`}
                                className="mono"
                                style={{ fontWeight: 700 }}
                              >
                                {s.ticker}
                              </Link>
                            </td>
                            <td>
                              <Badge tone="neutral" outline>
                                {familyLabel(s.family)}
                              </Badge>
                            </td>
                            <td className="center">
                              <Badge tone={stateTone(s.state)}>{s.state}</Badge>
                            </td>
                            <td className="center dim">{titleCase(s.horizon)}</td>
                            <td className="dim small mono">{fmtDate(s.created_at)}</td>
                            <td className="dim small mono">{fmtDate(s.updated_at)}</td>
                            <td className="num mono small">{fmtNum(s.anchor_price)}</td>
                            <td className="tiny dim">
                              {s.state_history.map((h) => h.state).join(' → ') || '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </details>
          </>
        )}
      </QueryGate>
    </div>
  );
}
