import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { AlertSummary } from '../api/types';
import { Badge } from '../components/Badge';
import type { Column } from '../components/DataTable';
import { DataTable } from '../components/DataTable';
import { EmptyState } from '../components/EmptyState';
import { FilterBar, FilterSelect, FilterToggle } from '../components/FilterBar';
import { QueryGate } from '../components/QueryGate';
import { ScoreTriple } from '../components/ScoreChip';
import {
  LIFECYCLE_STATES,
  SIGNAL_FAMILIES,
  familyLabel,
  fmtDateTime,
  priorityTone,
  stateTone,
  titleCase,
} from '../lib/format';

const PRIORITIES = ['critical', 'high', 'medium', 'low'];

export function AlertsPage() {
  const navigate = useNavigate();
  const [family, setFamily] = useState('');
  const [state, setState] = useState('');
  const [priority, setPriority] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);

  const query = useQuery({
    queryKey: ['alerts', { family, state, priority, unreadOnly }],
    queryFn: () =>
      api.alerts({
        family: family || undefined,
        state: state || undefined,
        priority: priority || undefined,
        unread_only: unreadOnly || undefined,
        limit: 200,
      }),
  });

  const columns: Column<AlertSummary>[] = [
    {
      key: 'created_at',
      label: 'When',
      render: (a) => <span className="dim small mono">{fmtDateTime(a.created_at)}</span>,
      sortValue: (a) => a.created_at,
      width: '140px',
    },
    {
      key: 'ticker',
      label: 'Ticker',
      render: (a) => (
        <span className="mono" style={{ fontWeight: 700 }}>
          {a.ticker}
        </span>
      ),
      sortValue: (a) => a.ticker,
      width: '75px',
    },
    {
      key: 'title',
      label: 'Alert',
      render: (a) => (
        <div>
          <div className="row-title">
            {!a.read && <span title="Unread" style={{ color: 'var(--blue)', marginRight: 6 }}>●</span>}
            {a.title}
          </div>
          <div className="dim small" style={{ maxWidth: 640 }}>
            {a.thesis_summary}
          </div>
        </div>
      ),
    },
    {
      key: 'family',
      label: 'Family / state',
      render: (a) => (
        <span className="badge-row">
          <Badge tone="blue" outline>
            {familyLabel(a.family)}
          </Badge>
          <Badge tone={stateTone(a.lifecycle_state)}>{a.lifecycle_state}</Badge>
          <Badge tone="neutral" outline title="Lifecycle transition">
            {a.transition}
          </Badge>
        </span>
      ),
      sortValue: (a) => a.family,
    },
    {
      key: 'horizon',
      label: 'Horizon',
      render: (a) => <span className="dim">{titleCase(a.horizon)}</span>,
      sortValue: (a) => a.horizon,
      align: 'center',
    },
    {
      key: 'priority',
      label: 'Priority',
      render: (a) => <Badge tone={priorityTone(a.priority)}>{a.priority}</Badge>,
      sortValue: (a) => PRIORITIES.indexOf(a.priority.toLowerCase()),
      align: 'center',
    },
    {
      key: 'scores',
      label: 'O / C / R',
      render: (a) => <ScoreTriple opportunity={a.opportunity} confidence={a.confidence} risk={a.risk} />,
      sortValue: (a) => a.opportunity,
      align: 'center',
    },
  ];

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Alerts</h1>
          <div className="sub">Immutable research alerts, newest first. Opening an alert marks it read.</div>
        </div>
      </div>

      <FilterBar>
        <FilterSelect
          label="Family"
          value={family}
          onChange={setFamily}
          options={SIGNAL_FAMILIES.map((f) => ({ value: f, label: familyLabel(f) }))}
        />
        <FilterSelect
          label="Lifecycle state"
          value={state}
          onChange={setState}
          options={LIFECYCLE_STATES.map((s) => ({ value: s, label: s }))}
        />
        <FilterSelect
          label="Priority"
          value={priority}
          onChange={setPriority}
          options={PRIORITIES.map((p) => ({ value: p, label: titleCase(p) }))}
        />
        <FilterToggle label="Unread only" checked={unreadOnly} onChange={setUnreadOnly} />
      </FilterBar>

      <QueryGate
        query={query}
        isEmpty={(d) => d.items.length === 0}
        empty={
          <EmptyState
            message="No alerts match these filters."
            hint="Alerts are produced when a scan detects a signal transition or a material change."
          />
        }
      >
        {(data) => (
          <>
            <div className="dim small" style={{ marginBottom: 6 }}>
              {data.items.length} of {data.total} alerts
            </div>
            <DataTable
              rows={data.items}
              columns={columns}
              rowKey={(a) => a.id}
              onRowClick={(a) => navigate(`/alerts/${a.id}`)}
              defaultSortKey="created_at"
              defaultDesc
              rowClass={(a) => (a.read ? undefined : 'unread')}
            />
          </>
        )}
      </QueryGate>
    </div>
  );
}
