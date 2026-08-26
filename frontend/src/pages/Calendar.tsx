import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { CalendarItem } from '../api/types';
import { Badge } from '../components/Badge';
import { EmptyState } from '../components/EmptyState';
import { FilterBar, FilterSelect, FilterToggle } from '../components/FilterBar';
import { QueryGate } from '../components/QueryGate';
import { fmtDate, titleCase } from '../lib/format';

function daysChipTone(days: number): 'red' | 'amber' | 'blue' | 'neutral' {
  if (days <= 3) return 'red';
  if (days <= 10) return 'amber';
  if (days <= 30) return 'blue';
  return 'neutral';
}

function DaysChip({ days }: { days: number }) {
  const label = days === 0 ? 'today' : days === 1 ? 'tomorrow' : `in ${days}d`;
  return <Badge tone={daysChipTone(days)}>{label}</Badge>;
}

function groupByDate(items: CalendarItem[]): [string, CalendarItem[]][] {
  const groups = new Map<string, CalendarItem[]>();
  for (const item of items) {
    const key = item.expected_date;
    const bucket = groups.get(key);
    if (bucket) bucket.push(item);
    else groups.set(key, [item]);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

const WINDOWS = [
  { value: '30', label: 'Next 30 days' },
  { value: '60', label: 'Next 60 days' },
  { value: '90', label: 'Next 90 days' },
  { value: '180', label: 'Next 180 days' },
];

export function CalendarPage() {
  const [days, setDays] = useState('60');
  const [binaryOnly, setBinaryOnly] = useState(false);
  const [kind, setKind] = useState('');

  const query = useQuery({
    queryKey: ['calendar', days, binaryOnly],
    queryFn: () => api.calendar(Number(days), binaryOnly),
  });

  const kinds = useMemo(
    () => [...new Set((query.data?.items ?? []).map((i) => i.kind))].sort(),
    [query.data],
  );

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Earnings &amp; Catalyst Calendar</h1>
          <div className="sub">
            Upcoming catalysts across the universe. Binary events dominate the stock either way —
            treat setups into them with extra care.
          </div>
        </div>
      </div>

      <FilterBar>
        <FilterSelect
          label="Window"
          value={days}
          onChange={(v) => setDays(v || '60')}
          options={WINDOWS}
          anyLabel="Next 60 days"
        />
        <FilterSelect
          label="Kind"
          value={kind}
          onChange={setKind}
          options={kinds.map((k) => ({ value: k, label: titleCase(k) }))}
        />
        <FilterToggle label="Binary events only" checked={binaryOnly} onChange={setBinaryOnly} />
      </FilterBar>

      <QueryGate
        query={query}
        isEmpty={(d) => d.items.filter((i) => !kind || i.kind === kind).length === 0}
        empty={
          <EmptyState
            message="No upcoming catalysts in this window."
            hint="Catalysts are ingested from filings, guidance and event providers."
          />
        }
      >
        {(data) => {
          const items = data.items.filter((i) => !kind || i.kind === kind);
          const groups = groupByDate(items);
          return (
            <div>
              <div className="dim small" style={{ marginBottom: 6 }}>
                {items.length} catalysts · {groups.length} dates
              </div>
              {groups.map(([date, entries]) => (
                <div className="card" key={date}>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'baseline',
                      gap: 10,
                      marginBottom: 8,
                    }}
                  >
                    <h2 style={{ margin: 0 }} className="mono">
                      {fmtDate(date)}
                    </h2>
                    <DaysChip days={entries[0].days} />
                    <span className="dim small">
                      {entries.length} event{entries.length === 1 ? '' : 's'}
                    </span>
                  </div>
                  <div className="table-wrap" style={{ marginBottom: 0 }}>
                    <table className="data">
                      <tbody>
                        {entries.map((c, i) => (
                          <tr key={`${c.instrument_id}-${i}`}>
                            <td style={{ width: 90 }}>
                              <Link to={`/companies/${c.instrument_id}`} className="mono" style={{ fontWeight: 700 }}>
                                {c.ticker}
                              </Link>
                            </td>
                            <td style={{ width: 220 }}>{c.name}</td>
                            <td style={{ width: 130 }}>
                              <Badge tone="blue" outline>
                                {titleCase(c.kind)}
                              </Badge>
                            </td>
                            <td style={{ width: 100 }} className="center">
                              {c.binary ? (
                                <Badge tone="red" title="Outcome dominates the stock either way">
                                  binary
                                </Badge>
                              ) : (
                                <span className="faint">—</span>
                              )}
                            </td>
                            <td style={{ width: 100 }} className="center">
                              {c.date_confirmed ? (
                                <Badge tone="green" outline>
                                  confirmed
                                </Badge>
                              ) : (
                                <Badge tone="neutral" outline>
                                  estimated
                                </Badge>
                              )}
                            </td>
                            <td className="small dim">{c.description}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          );
        }}
      </QueryGate>
    </div>
  );
}
