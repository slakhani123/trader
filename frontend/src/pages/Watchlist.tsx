import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { OpportunityRow, WatchlistItem } from '../api/types';
import { Badge } from '../components/Badge';
import type { Column } from '../components/DataTable';
import { DataTable } from '../components/DataTable';
import { EmptyState } from '../components/EmptyState';
import { QueryGate } from '../components/QueryGate';
import { ScoreTriple } from '../components/ScoreChip';
import { HORIZONS, familyLabel, fmtDate, stateTone, titleCase } from '../lib/format';

type ScoresByHorizon = Partial<Record<(typeof HORIZONS)[number], OpportunityRow>>;

/** Latest watchlisted scores fetched per horizon and joined by instrument id. */
function useWatchlistScores() {
  return useQuery({
    queryKey: ['watchlist', 'scores'],
    queryFn: async () => {
      const responses = await Promise.all(
        HORIZONS.map((horizon) => api.opportunities({ horizon, watchlisted: true, limit: 500 })),
      );
      const byInstrument = new Map<number, ScoresByHorizon>();
      responses.forEach((res, i) => {
        const horizon = HORIZONS[i];
        for (const row of res.items) {
          const entry = byInstrument.get(row.instrument_id) ?? {};
          entry[horizon] = row;
          byInstrument.set(row.instrument_id, entry);
        }
      });
      return byInstrument;
    },
  });
}

function AddToWatchlist() {
  const queryClient = useQueryClient();
  const [q, setQ] = useState('');
  const [error, setError] = useState<string | null>(null);

  const search = useQuery({
    queryKey: ['instruments', 'search', q],
    queryFn: () => api.instruments({ q, active: true, limit: 8 }),
    enabled: q.trim().length >= 1,
  });

  const add = useMutation({
    mutationFn: (instrumentId: number) => api.addToWatchlist(instrumentId),
    onSuccess: () => {
      setQ('');
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ['watchlist'] });
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });

  return (
    <div className="card tight">
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <label className="filter-field">
          <span className="flabel">Add a company (ticker or name)</span>
          <input
            type="text"
            value={q}
            placeholder="e.g. ACME"
            style={{ width: 220 }}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        {search.isFetching && <span className="dim small">searching…</span>}
      </div>
      {q.trim().length >= 1 && search.data && (
        <div style={{ marginTop: 8 }}>
          {search.data.items.length === 0 ? (
            <div className="dim small">No matching instruments in the universe.</div>
          ) : (
            <div className="badge-row">
              {search.data.items.map((inst) => (
                <button
                  key={inst.id}
                  className="btn sm"
                  disabled={add.isPending}
                  onClick={() => add.mutate(inst.id)}
                  title={`${inst.name} · ${inst.exchange}`}
                >
                  + {inst.ticker} <span className="dim">{inst.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {error && (
        <div className="small" style={{ color: 'var(--red)', marginTop: 6 }}>
          {error}
        </div>
      )}
    </div>
  );
}

export function WatchlistPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const listQ = useQuery({ queryKey: ['watchlist'], queryFn: () => api.watchlist() });
  const scoresQ = useWatchlistScores();

  const remove = useMutation({
    mutationFn: (watchlistItemId: number) => api.removeFromWatchlist(watchlistItemId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  });

  const scores = scoresQ.data;

  const horizonColumns: Column<WatchlistItem>[] = HORIZONS.map((h) => ({
    key: `scores-${h}`,
    label: `${titleCase(h)} O/C/R`,
    render: (w) => {
      const row = scores?.get(w.instrument_id)?.[h];
      if (!row) return <span className="faint">—</span>;
      return (
        <ScoreTriple opportunity={row.opportunity} confidence={row.confidence} risk={row.risk} />
      );
    },
    sortValue: (w) => scores?.get(w.instrument_id)?.[h]?.opportunity ?? null,
    align: 'center',
  }));

  const columns: Column<WatchlistItem>[] = [
    {
      key: 'ticker',
      label: 'Ticker',
      render: (w) => (
        <span className="mono" style={{ fontWeight: 700 }}>
          {w.ticker}
        </span>
      ),
      sortValue: (w) => w.ticker,
      width: '80px',
    },
    {
      key: 'name',
      label: 'Name',
      render: (w) => w.name,
      sortValue: (w) => w.name,
    },
    {
      key: 'added',
      label: 'Added',
      render: (w) => <span className="dim small mono">{fmtDate(w.added_at)}</span>,
      sortValue: (w) => w.added_at,
    },
    ...horizonColumns,
    {
      key: 'signals',
      label: 'Active signals',
      render: (w) => {
        const anyRow = HORIZONS.map((h) => scores?.get(w.instrument_id)?.[h]).find(Boolean);
        if (!anyRow || anyRow.active_signals.length === 0) return <span className="faint">—</span>;
        return (
          <span className="badge-row">
            {anyRow.active_signals.map((s, i) => (
              <Badge key={`${s.family}-${i}`} tone={stateTone(s.state)}>
                {familyLabel(s.family)} · {s.state}
              </Badge>
            ))}
          </span>
        );
      },
    },
    {
      key: 'notes',
      label: 'Notes',
      render: (w) => <span className="dim small">{w.notes ?? '—'}</span>,
    },
    {
      key: 'actions',
      label: '',
      render: (w) => (
        <button
          className="btn sm danger"
          disabled={remove.isPending}
          onClick={(e) => {
            e.stopPropagation();
            remove.mutate(w.id);
          }}
        >
          Remove
        </button>
      ),
      align: 'right',
    },
  ];

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Watchlist</h1>
          <div className="sub">Names you follow, joined with the latest per-horizon scores.</div>
        </div>
      </div>

      <AddToWatchlist />

      <QueryGate
        query={listQ}
        isEmpty={(d) => d.items.length === 0}
        empty={
          <EmptyState
            message="The watchlist is empty."
            hint="Search above, or use the star on any company page."
          />
        }
      >
        {(data) => (
          <>
            {scoresQ.isError && (
              <div className="banner warn">
                Could not load the latest scores — showing the plain watchlist.
              </div>
            )}
            <DataTable
              rows={data.items}
              columns={columns}
              rowKey={(w) => w.id}
              onRowClick={(w) => navigate(`/companies/${w.instrument_id}`)}
              defaultSortKey="added"
              defaultDesc
            />
          </>
        )}
      </QueryGate>
    </div>
  );
}
