import type { UseQueryResult } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import { LoadingSkeleton } from './LoadingSkeleton';

export function ErrorPanel({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const isApi = error instanceof ApiError;
  const detail = isApi ? error.detail : error instanceof Error ? error.message : String(error);
  const status = isApi ? error.status : null;
  return (
    <div className="error-panel">
      <div className="title">Request failed{status ? ` (${status})` : ''}</div>
      <div className="small">{detail}</div>
      {status === 401 || status === 403 ? (
        <div className="small dim" style={{ marginTop: 6 }}>
          The API rejected the bearer token. Set it under <Link to="/settings">Settings</Link>.
        </div>
      ) : null}
      {onRetry && (
        <button className="btn sm" style={{ marginTop: 10 }} onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

/**
 * Standard loading / error / empty / data flow for one query.
 * Keeps every page honest about all four states.
 */
export function QueryGate<T>({
  query,
  isEmpty,
  empty,
  skeletonRows = 5,
  children,
}: {
  query: UseQueryResult<T>;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  skeletonRows?: number;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <LoadingSkeleton rows={skeletonRows} />;
  if (query.isError) return <ErrorPanel error={query.error} onRetry={() => void query.refetch()} />;
  const data = query.data;
  if (isEmpty?.(data)) return <>{empty}</>;
  return <>{children(data)}</>;
}
