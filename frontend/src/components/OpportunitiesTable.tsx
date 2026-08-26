import { useNavigate } from 'react-router-dom';
import type { OpportunityRow } from '../api/types';
import { familyLabel, fmtMoney, stateTone, titleCase } from '../lib/format';
import { Badge } from './Badge';
import type { Column } from './DataTable';
import { DataTable } from './DataTable';
import { MiniBars } from './MiniBars';
import { ScoreChip } from './ScoreChip';

/** The ranked-opportunities table shared by the dashboard and screen pages. */
export function OpportunitiesTable({ rows }: { rows: OpportunityRow[] }) {
  const navigate = useNavigate();

  const columns: Column<OpportunityRow>[] = [
    {
      key: 'ticker',
      label: 'Ticker',
      render: (r) => (
        <span className="mono" style={{ fontWeight: 700 }}>
          {r.ticker}
        </span>
      ),
      sortValue: (r) => r.ticker,
      width: '80px',
    },
    {
      key: 'name',
      label: 'Name',
      render: (r) => (
        <span>
          {r.name}
          {r.owned && (
            <span title="In portfolio" className="tiny" style={{ marginLeft: 6, color: 'var(--purple)' }}>
              ◈
            </span>
          )}
          {r.watchlisted && (
            <span title="Watchlisted" className="tiny" style={{ marginLeft: 4, color: 'var(--blue)' }}>
              ☆
            </span>
          )}
        </span>
      ),
      sortValue: (r) => r.name,
    },
    {
      key: 'sector',
      label: 'Sector',
      render: (r) => <span className="dim">{r.sector || '—'}</span>,
      sortValue: (r) => r.sector,
    },
    {
      key: 'mcap',
      label: 'Mkt cap',
      render: (r) => <span className="dim small">{fmtMoney(r.market_cap_base)}</span>,
      sortValue: (r) => r.market_cap_base,
      align: 'right',
    },
    {
      key: 'opportunity',
      label: 'Opp',
      render: (r) => <ScoreChip value={r.opportunity} kind="opportunity" />,
      sortValue: (r) => r.opportunity,
      align: 'center',
      title: 'Opportunity 0–10',
    },
    {
      key: 'confidence',
      label: 'Conf',
      render: (r) => <ScoreChip value={r.confidence} kind="confidence" />,
      sortValue: (r) => r.confidence,
      align: 'center',
      title: 'Confidence 0–10',
    },
    {
      key: 'risk',
      label: 'Risk',
      render: (r) => <ScoreChip value={r.risk} kind="risk" />,
      sortValue: (r) => r.risk,
      align: 'center',
      title: 'Risk 0–10 (lower is better)',
    },
    {
      key: 'components',
      label: 'Components',
      render: (r) => <MiniBars components={r.components} />,
      align: 'center',
      title: 'Quality · Growth · Valuation · Technical · Momentum · Sentiment · Catalysts · Balance sheet · Data quality',
    },
    {
      key: 'best_fit',
      label: 'Best fit',
      render: (r) =>
        r.best_fit_horizon ? (
          <Badge tone={r.best_fit_horizon === r.horizon ? 'blue' : 'neutral'} outline={r.best_fit_horizon !== r.horizon}>
            {titleCase(r.best_fit_horizon)}
          </Badge>
        ) : (
          <span className="faint">—</span>
        ),
      sortValue: (r) => r.best_fit_horizon,
      align: 'center',
    },
    {
      key: 'signals',
      label: 'Active signals',
      render: (r) =>
        r.active_signals.length ? (
          <span className="badge-row">
            {r.active_signals.map((s, i) => (
              <Badge key={`${s.family}-${i}`} tone={stateTone(s.state)} title={`${familyLabel(s.family)} — ${s.state}`}>
                {familyLabel(s.family)} · {s.state}
              </Badge>
            ))}
          </span>
        ) : (
          <span className="faint">—</span>
        ),
    },
    {
      key: 'gate',
      label: 'Gate',
      render: (r) =>
        r.abstained ? (
          <Badge tone="neutral" outline title="Scoring abstained">
            abstained
          </Badge>
        ) : r.gate_passed ? (
          <Badge tone="green" title="All quality gates passed">
            ✓ pass
          </Badge>
        ) : (
          <Badge tone="amber" title="One or more gates failed">
            ✗ gated
          </Badge>
        ),
      sortValue: (r) => (r.abstained ? 0 : r.gate_passed ? 2 : 1),
      align: 'center',
    },
  ];

  return (
    <DataTable
      rows={rows}
      columns={columns}
      rowKey={(r) => `${r.instrument_id}-${r.horizon}`}
      onRowClick={(r) => navigate(`/companies/${r.instrument_id}`)}
      defaultSortKey="opportunity"
      defaultDesc
    />
  );
}
