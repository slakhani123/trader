import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import type { Horizon } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { FilterBar, FilterNumber, FilterSelect, FilterText, FilterToggle } from '../components/FilterBar';
import { OpportunitiesTable } from '../components/OpportunitiesTable';
import { QueryGate } from '../components/QueryGate';
import { HORIZONS, SIGNAL_FAMILIES, familyLabel, fmtDate, titleCase } from '../lib/format';

export function HorizonTabs({ value, onChange }: { value: Horizon; onChange: (h: Horizon) => void }) {
  return (
    <div className="tabs" role="tablist">
      {HORIZONS.map((h) => (
        <button
          key={h}
          role="tab"
          aria-selected={value === h}
          className={value === h ? 'active' : ''}
          onClick={() => onChange(h)}
        >
          {titleCase(h)}
        </button>
      ))}
    </div>
  );
}

export function OpportunitiesPage() {
  const [horizon, setHorizon] = useState<Horizon>('medium');
  const [market, setMarket] = useState('');
  const [sector, setSector] = useState('');
  const [family, setFamily] = useState('');
  const [minOpp, setMinOpp] = useState<number | ''>('');
  const [minConf, setMinConf] = useState<number | ''>('');
  const [maxRisk, setMaxRisk] = useState<number | ''>('');
  const [gatedOnly, setGatedOnly] = useState(false);
  const [owned, setOwned] = useState(false);
  const [watchlisted, setWatchlisted] = useState(false);

  const query = useQuery({
    queryKey: [
      'opportunities',
      { horizon, market, sector, family, minOpp, minConf, maxRisk, gatedOnly, owned, watchlisted },
    ],
    queryFn: () =>
      api.opportunities({
        horizon,
        market: market || undefined,
        sector: sector || undefined,
        family: family || undefined,
        min_opportunity: minOpp === '' ? undefined : minOpp,
        min_confidence: minConf === '' ? undefined : minConf,
        max_risk: maxRisk === '' ? undefined : maxRisk,
        gated_only: gatedOnly || undefined,
        owned: owned || undefined,
        watchlisted: watchlisted || undefined,
        limit: 200,
      }),
  });

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Ranked Opportunities</h1>
          <div className="sub">
            {query.data?.as_of
              ? `Latest completed run #${query.data.run_id ?? '—'} · as of ${fmtDate(query.data.as_of)}`
              : 'Composite Opportunity / Confidence / Risk scores from the latest scan'}
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
        <FilterText label="Sector" value={sector} onChange={setSector} placeholder="e.g. Technology" />
        <FilterSelect
          label="Signal family"
          value={family}
          onChange={setFamily}
          options={SIGNAL_FAMILIES.map((f) => ({ value: f, label: familyLabel(f) }))}
        />
        <FilterNumber label="Min opp" value={minOpp} onChange={setMinOpp} />
        <FilterNumber label="Min conf" value={minConf} onChange={setMinConf} />
        <FilterNumber label="Max risk" value={maxRisk} onChange={setMaxRisk} />
        <FilterToggle label="Gate-passing only" checked={gatedOnly} onChange={setGatedOnly} />
        <FilterToggle label="Owned" checked={owned} onChange={setOwned} />
        <FilterToggle label="Watchlisted" checked={watchlisted} onChange={setWatchlisted} />
      </FilterBar>

      <QueryGate
        query={query}
        isEmpty={(d) => d.items.length === 0}
        empty={
          <EmptyState
            message="No scored opportunities match these filters."
            hint="Try relaxing the filters or switching horizon."
          />
        }
      >
        {(data) => (
          <>
            <div className="dim small" style={{ marginBottom: 6 }}>
              {data.items.length} of {data.total} ranked names · sorted by opportunity
            </div>
            <OpportunitiesTable rows={data.items} />
          </>
        )}
      </QueryGate>
    </div>
  );
}
