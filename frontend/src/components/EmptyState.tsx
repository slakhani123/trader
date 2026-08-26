import type { ReactNode } from 'react';

/** Friendly empty panel with a hint on how to get data flowing. */
export function EmptyState({
  message = 'Nothing here yet.',
  hint,
  showScanHint = true,
}: {
  message?: string;
  hint?: ReactNode;
  showScanHint?: boolean;
}) {
  return (
    <div className="card">
      <div className="empty-state">
        <div className="icon">◎</div>
        <div>{message}</div>
        {hint && <div className="hint">{hint}</div>}
        {showScanHint && (
          <div className="hint">
            Empty database? Seed the demo universe and run a scan:{' '}
            <code>vigil seed &amp;&amp; vigil scan</code>
          </div>
        )}
      </div>
    </div>
  );
}
