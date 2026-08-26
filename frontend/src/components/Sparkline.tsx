/** Tiny inline SVG line chart — no chart library overhead. */
export function Sparkline({
  values,
  width = 120,
  height = 28,
  tone,
}: {
  values: number[];
  width?: number;
  height?: number;
  tone?: 'green' | 'red' | 'blue' | 'auto';
}) {
  if (values.length < 2) return <span className="faint tiny">n/a</span>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const first = values[0];
  const last = values[values.length - 1];
  const pad = 2;
  const points = values
    .map((v, i) => {
      const x = pad + (i / (values.length - 1)) * (width - 2 * pad);
      const y = pad + (1 - (v - min) / span) * (height - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const resolved =
    tone === 'auto' || tone === undefined
      ? last >= first
        ? 'var(--green)'
        : 'var(--red)'
      : tone === 'green'
        ? 'var(--green)'
        : tone === 'red'
          ? 'var(--red)'
          : 'var(--blue)';
  return (
    <svg width={width} height={height} style={{ display: 'block' }} aria-hidden="true">
      <polyline points={points} fill="none" stroke={resolved} strokeWidth={1.5} />
    </svg>
  );
}
