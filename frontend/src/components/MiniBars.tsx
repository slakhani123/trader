import { componentLabel, orderedComponents } from '../lib/format';

function barColor(v: number): string {
  if (v >= 6.5) return 'var(--green)';
  if (v >= 5) return 'var(--blue)';
  if (v >= 3.5) return 'var(--amber)';
  return 'var(--red)';
}

/** Per-component 0–10 mini bar strip (quality/growth/valuation/...). */
export function MiniBars({ components }: { components: Record<string, number> }) {
  const entries = orderedComponents(components);
  if (entries.length === 0) return <span className="faint tiny">—</span>;
  return (
    <span className="minibars">
      {entries.map(([key, value]) => (
        <span
          key={key}
          className="bar"
          title={`${componentLabel(key)}: ${value.toFixed(1)}`}
          style={{
            height: `${Math.max(6, (Math.min(10, Math.max(0, value)) / 10) * 100)}%`,
            background: barColor(value),
          }}
        />
      ))}
    </span>
  );
}

/** Labelled horizontal component bars for detail views. */
export function ComponentBars({ components }: { components: Record<string, number> }) {
  const entries = orderedComponents(components);
  if (entries.length === 0) return <div className="dim small">No component scores.</div>;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: '3px 8px' }}>
      {entries.map(([key, value]) => (
        <ComponentRow key={key} name={key} value={value} />
      ))}
    </div>
  );
}

function ComponentRow({ name, value }: { name: string; value: number }) {
  return (
    <>
      <span className="tiny dim" style={{ alignSelf: 'center' }}>
        {componentLabel(name)}
      </span>
      <span
        style={{
          alignSelf: 'center',
          height: 7,
          background: 'var(--bg)',
          border: '1px solid var(--border)',
          borderRadius: 2,
          position: 'relative',
          minWidth: 60,
        }}
      >
        <span
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            bottom: 0,
            width: `${(Math.min(10, Math.max(0, value)) / 10) * 100}%`,
            background: barColor(value),
            borderRadius: 2,
            opacity: 0.85,
          }}
        />
      </span>
      <span className="tiny mono" style={{ alignSelf: 'center' }}>
        {value.toFixed(1)}
      </span>
    </>
  );
}
