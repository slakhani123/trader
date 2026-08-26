/** Shimmering placeholder rows while a query is in flight. */
export function LoadingSkeleton({ rows = 4, height = 34 }: { rows?: number; height?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div className="skeleton" key={i} style={{ height, opacity: 1 - i * 0.15 }} />
      ))}
    </div>
  );
}
