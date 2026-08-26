import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { OpportunityRow, PositionRow } from '../api/types';
import { Badge } from '../components/Badge';
import type { Column } from '../components/DataTable';
import { DataTable } from '../components/DataTable';
import { EmptyState } from '../components/EmptyState';
import { QueryGate } from '../components/QueryGate';
import { ScoreTriple } from '../components/ScoreChip';
import {
  HORIZONS,
  familyLabel,
  fmtDate,
  fmtMoney,
  fmtNum,
  fmtPct,
  isNum,
  stateTone,
  titleCase,
} from '../lib/format';

/** Signal families that express a stance on an existing holding. */
const STANCE_FAMILIES = new Set(['hold', 'trim', 'full_exit', 'avoid', 'thesis_invalidated']);

function stanceTone(family: string): 'green' | 'red' | 'amber' | 'blue' | 'neutral' {
  switch (family) {
    case 'hold':
      return 'green';
    case 'trim':
      return 'amber';
    case 'full_exit':
    case 'thesis_invalidated':
    case 'avoid':
      return 'red';
    default:
      return 'neutral';
  }
}

/** Latest owned-name scores fetched per horizon and joined by instrument id. */
function useOwnedScores() {
  return useQuery({
    queryKey: ['portfolio', 'scores'],
    queryFn: async () => {
      const responses = await Promise.all(
        HORIZONS.map((horizon) => api.opportunities({ horizon, owned: true, limit: 500 })),
      );
      const byInstrument = new Map<number, Partial<Record<string, OpportunityRow>>>();
      responses.forEach((res, i) => {
        for (const row of res.items) {
          const entry = byInstrument.get(row.instrument_id) ?? {};
          entry[HORIZONS[i]] = row;
          byInstrument.set(row.instrument_id, entry);
        }
      });
      return byInstrument;
    },
  });
}

function SectorExposure({
  weights,
  limitPct,
}: {
  weights: Record<string, number>;
  limitPct: number;
}) {
  const entries = Object.entries(weights).sort(([, a], [, b]) => b - a);
  if (entries.length === 0) return <div className="dim small">No sector exposure yet.</div>;
  const maxShown = Math.max(limitPct * 1.4, ...entries.map(([, v]) => v), 1);
  return (
    <div>
      {entries.map(([sector, pct]) => {
        const breach = pct > limitPct;
        return (
          <div className="exposure-row" key={sector}>
            <span className="dim">{sector || 'Unclassified'}</span>
            <span className="exposure-track">
              <span
                className={`exposure-fill${breach ? ' breach' : ''}`}
                style={{ width: `${Math.min(100, (pct / maxShown) * 100)}%` }}
              />
              <span
                className="exposure-limit"
                title={`Sector limit ${fmtPct(limitPct, 0)}`}
                style={{ left: `${Math.min(100, (limitPct / maxShown) * 100)}%` }}
              />
            </span>
            <span className={`mono num${breach ? ' neg' : ''}`}>{fmtPct(pct, 1)}</span>
          </div>
        );
      })}
      <div className="tiny faint" style={{ marginTop: 4 }}>
        Vertical mark = configured sector limit ({fmtPct(limitPct, 0)}).
      </div>
    </div>
  );
}

export function PortfolioPage() {
  const navigate = useNavigate();
  const portfolioQ = useQuery({ queryKey: ['portfolio'], queryFn: () => api.portfolio() });
  const scoresQ = useOwnedScores();
  const scores = scoresQ.data;

  const columns: Column<PositionRow>[] = [
    {
      key: 'ticker',
      label: 'Ticker',
      render: (p) => (
        <span className="mono" style={{ fontWeight: 700 }}>
          {p.ticker}
        </span>
      ),
      sortValue: (p) => p.ticker,
      width: '80px',
    },
    { key: 'name', label: 'Name', render: (p) => p.name, sortValue: (p) => p.name },
    {
      key: 'sector',
      label: 'Sector',
      render: (p) => <span className="dim">{p.sector || '—'}</span>,
      sortValue: (p) => p.sector,
    },
    {
      key: 'quantity',
      label: 'Qty',
      render: (p) => <span className="mono">{fmtNum(p.quantity, 0)}</span>,
      sortValue: (p) => p.quantity,
      align: 'right',
    },
    {
      key: 'avg_cost',
      label: 'Avg cost',
      render: (p) => (
        <span className="mono small">
          {fmtNum(p.avg_cost_local)} <span className="faint">{p.currency}</span>
        </span>
      ),
      sortValue: (p) => p.avg_cost_local,
      align: 'right',
    },
    {
      key: 'last_price',
      label: 'Last',
      render: (p) => <span className="mono">{fmtNum(p.last_price)}</span>,
      sortValue: (p) => p.last_price,
      align: 'right',
    },
    {
      key: 'value',
      label: 'Value (base)',
      render: (p) => <span className="mono">{fmtMoney(p.value_base)}</span>,
      sortValue: (p) => p.value_base,
      align: 'right',
    },
    {
      key: 'weight',
      label: 'Weight',
      render: (p) => <span className="mono">{fmtPct(p.weight_pct)}</span>,
      sortValue: (p) => p.weight_pct,
      align: 'right',
    },
    {
      key: 'unrealised',
      label: 'Unrealised',
      render: (p) => (
        <span className={`mono ${isNum(p.unrealised_pct) && p.unrealised_pct < 0 ? 'neg' : 'pos'}`}>
          {fmtPct(p.unrealised_pct, 1, true)}
        </span>
      ),
      sortValue: (p) => p.unrealised_pct,
      align: 'right',
    },
    {
      key: 'scores',
      label: 'Latest O/C/R',
      title: 'Best-fit horizon where set, otherwise medium',
      render: (p) => {
        const byHorizon = scores?.get(p.instrument_id);
        if (!byHorizon) return <span className="faint">—</span>;
        const any = HORIZONS.map((h) => byHorizon[h]).find(Boolean);
        const chosen =
          (any?.best_fit_horizon ? byHorizon[any.best_fit_horizon] : undefined) ??
          byHorizon['medium'] ??
          any;
        if (!chosen) return <span className="faint">—</span>;
        return (
          <span title={`${titleCase(chosen.horizon)} horizon`}>
            <ScoreTriple
              opportunity={chosen.opportunity}
              confidence={chosen.confidence}
              risk={chosen.risk}
            />
          </span>
        );
      },
      align: 'center',
    },
    {
      key: 'stance',
      label: 'Stance signals',
      title: 'Active hold / trim / exit signals on this holding',
      render: (p) => {
        const byHorizon = scores?.get(p.instrument_id);
        const any = byHorizon ? HORIZONS.map((h) => byHorizon[h]).find(Boolean) : undefined;
        const stances = (any?.active_signals ?? []).filter((s) => STANCE_FAMILIES.has(s.family));
        const others = (any?.active_signals ?? []).filter((s) => !STANCE_FAMILIES.has(s.family));
        if (!any || (stances.length === 0 && others.length === 0))
          return <span className="faint">—</span>;
        return (
          <span className="badge-row">
            {stances.map((s, i) => (
              <Badge key={`s${i}`} tone={stanceTone(s.family)} title={`state: ${s.state}`}>
                {familyLabel(s.family)}
              </Badge>
            ))}
            {others.map((s, i) => (
              <Badge key={`o${i}`} tone={stateTone(s.state)} outline>
                {familyLabel(s.family)} · {s.state}
              </Badge>
            ))}
          </span>
        );
      },
    },
    {
      key: 'opened',
      label: 'Opened',
      render: (p) => <span className="dim small mono">{fmtDate(p.opened_at)}</span>,
      sortValue: (p) => p.opened_at,
    },
  ];

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Portfolio Monitor</h1>
          <div className="sub">
            Record-keeping and research overlay only — Vigil has no brokerage connectivity.
          </div>
        </div>
      </div>

      <QueryGate
        query={portfolioQ}
        isEmpty={(d) => d.positions.length === 0}
        empty={
          <EmptyState
            message="No open positions recorded."
            hint="Record a position from any company page to monitor it here."
            showScanHint={false}
          />
        }
      >
        {(data) => {
          const { totals } = data;
          return (
            <>
              {totals.breaches.length > 0 && (
                <div className="banner danger">
                  <div className="title">Exposure limit breaches</div>
                  {totals.breaches.map((b, i) => (
                    <div key={i} className="small">
                      ⚠ {b}
                    </div>
                  ))}
                </div>
              )}

              <div className="grid cols-3" style={{ marginBottom: 14 }}>
                <div className="card">
                  <div className="stat">
                    <span className="label">Total value (base)</span>
                    <span className="value mono">{fmtMoney(totals.value_base)}</span>
                    <span className="hint">{data.positions.length} open positions</span>
                  </div>
                </div>
                <div className="card">
                  <div className="stat">
                    <span className="label">Limits</span>
                    <span className="value mono">
                      {fmtPct(totals.limits.max_position_exposure_pct, 0)} /{' '}
                      {fmtPct(totals.limits.max_sector_exposure_pct, 0)}
                    </span>
                    <span className="hint">max single position / max sector exposure</span>
                  </div>
                </div>
                <div className="card">
                  <div className="stat">
                    <span className="label">Breaches</span>
                    <span
                      className="value"
                      style={{
                        color: totals.breaches.length ? 'var(--red)' : 'var(--green)',
                      }}
                    >
                      {totals.breaches.length === 0 ? 'none' : totals.breaches.length}
                    </span>
                    <span className="hint">policy checks from /api/portfolio</span>
                  </div>
                </div>
              </div>

              <div className="card">
                <h2>Sector exposure vs limit</h2>
                <SectorExposure
                  weights={totals.sector_weights}
                  limitPct={totals.limits.max_sector_exposure_pct}
                />
              </div>

              {scoresQ.isError && (
                <div className="banner warn">
                  Could not load the latest scores — positions shown without O/C/R overlay.
                </div>
              )}

              <DataTable
                rows={data.positions}
                columns={columns}
                rowKey={(p) => p.id}
                onRowClick={(p) => navigate(`/companies/${p.instrument_id}`)}
                defaultSortKey="weight"
                defaultDesc
              />
            </>
          );
        }}
      </QueryGate>
    </div>
  );
}
