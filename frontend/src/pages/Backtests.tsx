import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { BacktestSummaryRow } from '../api/types';
import { Badge } from '../components/Badge';
import type { Column } from '../components/DataTable';
import { DataTable } from '../components/DataTable';
import { EmptyState } from '../components/EmptyState';
import { QueryGate } from '../components/QueryGate';
import { fmtDate, fmtDateTime, fmtNum, fmtPct, isNum } from '../lib/format';

export function backtestStatusTone(status: string): 'green' | 'red' | 'amber' | 'blue' | 'neutral' {
  switch (status.toLowerCase()) {
    case 'completed':
    case 'complete':
    case 'succeeded':
    case 'success':
    case 'done':
      return 'green';
    case 'failed':
    case 'error':
      return 'red';
    case 'running':
    case 'pending':
    case 'queued':
      return 'blue';
    default:
      return 'neutral';
  }
}

export function isBacktestRunning(status: string): boolean {
  return ['running', 'pending', 'queued'].includes(status.toLowerCase());
}

function NewBacktestForm() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [holdout, setHoldout] = useState('');
  const [stepDays, setStepDays] = useState('');
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api.newBacktest({
        name: name || undefined,
        start,
        end: end || undefined,
        holdout_start: holdout || undefined,
        step_days: stepDays ? Number(stepDays) : undefined,
      }),
    onSuccess: (res) => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ['backtests'] });
      navigate(`/backtests/${res.backtest_id}`);
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });

  return (
    <div className="card">
      <h2>New backtest</h2>
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <label className="filter-field">
          <span className="flabel">Name (optional)</span>
          <input
            type="text"
            value={name}
            placeholder="walk-forward v1"
            style={{ width: 170 }}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="filter-field">
          <span className="flabel">Start *</span>
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="filter-field">
          <span className="flabel">End</span>
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <label className="filter-field">
          <span className="flabel">Holdout start</span>
          <input type="date" value={holdout} onChange={(e) => setHoldout(e.target.value)} />
        </label>
        <label className="filter-field">
          <span className="flabel">Step (days)</span>
          <input
            type="number"
            min={1}
            step={1}
            value={stepDays}
            placeholder="5"
            style={{ width: 80 }}
            onChange={(e) => setStepDays(e.target.value)}
          />
        </label>
        <button
          className="btn sm primary"
          disabled={!start || create.isPending}
          onClick={() => create.mutate()}
        >
          {create.isPending ? 'Starting…' : 'Run backtest'}
        </button>
      </div>
      <div className="tiny faint" style={{ marginTop: 6 }}>
        Runs in a background thread on the API. The holdout window stays untouched by any tuning —
        results there are the honest ones.
      </div>
      {error && (
        <div className="small" style={{ color: 'var(--red)', marginTop: 6 }}>
          {error}
        </div>
      )}
    </div>
  );
}

export function BacktestsPage() {
  const navigate = useNavigate();
  const query = useQuery({
    queryKey: ['backtests'],
    queryFn: () => api.backtests(),
    refetchInterval: (q) =>
      (q.state.data?.items ?? []).some((b) => isBacktestRunning(b.status)) ? 4000 : false,
  });

  const columns: Column<BacktestSummaryRow>[] = [
    {
      key: 'id',
      label: '#',
      render: (b) => <span className="mono dim">{b.id}</span>,
      sortValue: (b) => b.id,
      width: '50px',
    },
    {
      key: 'name',
      label: 'Name',
      render: (b) => <span className="row-title">{b.name}</span>,
      sortValue: (b) => b.name,
    },
    {
      key: 'created',
      label: 'Created',
      render: (b) => <span className="dim small mono">{fmtDateTime(b.created_at)}</span>,
      sortValue: (b) => b.created_at,
    },
    {
      key: 'window',
      label: 'Window',
      render: (b) => (
        <span className="mono small">
          {fmtDate(b.start_date)} → {b.end_date ? fmtDate(b.end_date) : 'latest'}
        </span>
      ),
      sortValue: (b) => b.start_date,
    },
    {
      key: 'holdout',
      label: 'Holdout from',
      render: (b) => (
        <span className="mono small dim">{b.holdout_start ? fmtDate(b.holdout_start) : '—'}</span>
      ),
      sortValue: (b) => b.holdout_start,
    },
    {
      key: 'model',
      label: 'Model',
      render: (b) => (
        <Badge tone="neutral" outline>
          {b.model_version}
        </Badge>
      ),
      sortValue: (b) => b.model_version,
    },
    {
      key: 'status',
      label: 'Status',
      render: (b) => <Badge tone={backtestStatusTone(b.status)}>{b.status}</Badge>,
      sortValue: (b) => b.status,
      align: 'center',
    },
    {
      key: 'total_return',
      label: 'Total return',
      render: (b) => {
        const v = b.metrics?.total_return_pct;
        return (
          <span className={`mono ${isNum(v) && v < 0 ? 'neg' : 'pos'}`}>
            {isNum(v) ? fmtPct(v, 1, true) : '—'}
          </span>
        );
      },
      sortValue: (b) => b.metrics?.total_return_pct ?? null,
      align: 'right',
    },
    {
      key: 'hit_rate',
      label: 'Hit rate',
      render: (b) => {
        const v = b.metrics?.hit_rate;
        return <span className="mono">{isNum(v) ? fmtPct(v * 100, 0) : '—'}</span>;
      },
      sortValue: (b) => b.metrics?.hit_rate ?? null,
      align: 'right',
    },
    {
      key: 'sharpe',
      label: 'Sharpe',
      render: (b) => <span className="mono">{fmtNum(b.metrics?.sharpe ?? null, 2)}</span>,
      sortValue: (b) => b.metrics?.sharpe ?? null,
      align: 'right',
    },
    {
      key: 'trades',
      label: 'Trades',
      render: (b) => <span className="mono dim">{b.metrics?.n ?? '—'}</span>,
      sortValue: (b) => b.metrics?.n ?? null,
      align: 'right',
    },
  ];

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Backtests</h1>
          <div className="sub">
            Point-in-time replays through the same snapshot builder and engines as live scans — no
            separate formula copy.
          </div>
        </div>
      </div>

      <NewBacktestForm />

      <QueryGate
        query={query}
        isEmpty={(d) => d.items.length === 0}
        empty={
          <EmptyState
            message="No backtests have been run."
            hint="Start one above, or run vigil backtest from the CLI."
            showScanHint={false}
          />
        }
      >
        {(data) => (
          <DataTable
            rows={data.items}
            columns={columns}
            rowKey={(b) => b.id}
            onRowClick={(b) => navigate(`/backtests/${b.id}`)}
            defaultSortKey="created"
            defaultDesc
          />
        )}
      </QueryGate>
    </div>
  );
}
