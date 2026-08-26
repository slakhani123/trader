import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import type { Horizon, OpportunitiesResponse, OpportunityRow } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { FilterBar, FilterNumber, FilterSelect, FilterToggle } from '../components/FilterBar';
import { OpportunitiesTable } from '../components/OpportunitiesTable';
import { QueryGate } from '../components/QueryGate';
import { Badge } from '../components/Badge';
import { familyLabel, fmtDate } from '../lib/format';
import { HorizonTabs } from './Opportunities';

/** Merge several per-family responses, deduping by instrument (keep max opportunity). */
function mergeResponses(responses: OpportunitiesResponse[]): OpportunitiesResponse {
  const byInstrument = new Map<number, OpportunityRow>();
  for (const res of responses) {
    for (const row of res.items) {
      const existing = byInstrument.get(row.instrument_id);
      if (!existing || row.opportunity > existing.opportunity) {
        byInstrument.set(row.instrument_id, row);
      }
    }
  }
  const items = [...byInstrument.values()].sort((a, b) => b.opportunity - a.opportunity);
  const first = responses[0];
  return {
    as_of: first ? first.as_of : null,
    run_id: first ? first.run_id : null,
    items,
    total: items.length,
  };
}

/** Pre-filtered opportunity view for one screening strategy. */
export function ScreenPage({
  title,
  description,
  families,
  defaultHorizon,
}: {
  title: string;
  description: string;
  families: string[];
  defaultHorizon: Horizon;
}) {
  const [horizon, setHorizon] = useState<Horizon>(defaultHorizon);
  const [market, setMarket] = useState('');
  const [minOpp, setMinOpp] = useState<number | ''>('');
  const [gatedOnly, setGatedOnly] = useState(false);

  const query = useQuery({
    queryKey: ['screen', { families, horizon, market, minOpp, gatedOnly }],
    queryFn: async () => {
      const responses = await Promise.all(
        families.map((family) =>
          api.opportunities({
            horizon,
            family,
            market: market || undefined,
            min_opportunity: minOpp === '' ? undefined : minOpp,
            gated_only: gatedOnly || undefined,
            limit: 200,
          }),
        ),
      );
      return mergeResponses(responses);
    },
  });

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>{title} Screen</h1>
          <div className="sub">{description}</div>
          <div className="badge-row" style={{ marginTop: 6 }}>
            {families.map((f) => (
              <Badge key={f} tone="blue" outline>
                {familyLabel(f)}
              </Badge>
            ))}
          </div>
        </div>
        <HorizonTabs value={horizon} onChange={setHorizon} />
      </div>

      <FilterBar>
        <FilterSelect
          label="Market"
          value={market}
          onChange={setMarket}
          options={[
            { value: 'US', label: 'US' },
            { value: 'UK', label: 'UK' },
          ]}
        />
        <FilterNumber label="Min opp" value={minOpp} onChange={setMinOpp} />
        <FilterToggle label="Gate-passing only" checked={gatedOnly} onChange={setGatedOnly} />
      </FilterBar>

      <QueryGate
        query={query}
        isEmpty={(d) => d.items.length === 0}
        empty={
          <EmptyState
            message={`No names currently carry an active ${title.toLowerCase()} signal at this horizon.`}
            hint="Signals appear after a scan detects the pattern."
          />
        }
      >
        {(data) => (
          <>
            <div className="dim small" style={{ marginBottom: 6 }}>
              {data.items.length} matches
              {data.as_of ? ` · as of ${fmtDate(data.as_of)}` : ''}
            </div>
            <OpportunitiesTable rows={data.items} />
          </>
        )}
      </QueryGate>
    </div>
  );
}
