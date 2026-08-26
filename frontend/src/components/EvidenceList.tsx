import type { Evidence } from '../api/types';
import { fmtDateTime, isNum, titleCase } from '../lib/format';
import { Badge } from './Badge';
import { DirectionIcon } from './DirectionIcon';

function sourceLine(e: Evidence): string {
  const bits: string[] = [e.source.provider, e.source.reference];
  if (e.source.published_at) bits.push(`published ${fmtDateTime(e.source.published_at)}`);
  if (isNum(e.source.freshness_days)) bits.push(`${e.source.freshness_days.toFixed(0)}d old`);
  return bits.join(' · ');
}

/** Sourced evidence items: statement, value, pillar tag, provenance line. */
export function EvidenceList({ items, emptyText }: { items: Evidence[]; emptyText?: string }) {
  if (items.length === 0) {
    return <div className="dim small" style={{ padding: '8px 0' }}>{emptyText ?? 'None.'}</div>;
  }
  return (
    <div>
      {items.map((e, i) => (
        <div className="evidence-item" key={`${e.key}-${i}`}>
          <div className="evidence-statement">
            <DirectionIcon direction={e.direction} />
            <span style={{ flex: 1 }}>{e.statement}</span>
            {e.value !== null && e.value !== undefined && (
              <span className="mono small dim">
                {isNum(e.value) ? e.value.toLocaleString('en-GB', { maximumFractionDigits: 2 }) : e.value}
              </span>
            )}
            <Badge tone="neutral" outline title={`pillar: ${e.pillar}`}>
              {titleCase(e.pillar)}
            </Badge>
          </div>
          <div className="evidence-source">
            {sourceLine(e)} · as of {e.as_of}
          </div>
        </div>
      ))}
    </div>
  );
}
