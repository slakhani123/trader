import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { Badge } from '../components/Badge';
import { EmptyState } from '../components/EmptyState';
import { QueryGate } from '../components/QueryGate';
import { fmtDate, fmtDateTime, titleCase } from '../lib/format';

function jobTone(status: string): 'green' | 'red' | 'amber' | 'blue' | 'neutral' {
  switch (status.toLowerCase()) {
    case 'completed':
    case 'success':
    case 'succeeded':
    case 'ok':
      return 'green';
    case 'failed':
    case 'error':
      return 'red';
    case 'running':
      return 'blue';
    default:
      return 'neutral';
  }
}

function notificationTone(status: string): 'green' | 'red' | 'amber' | 'neutral' {
  switch (status.toLowerCase()) {
    case 'sent':
    case 'delivered':
    case 'ok':
      return 'green';
    case 'failed':
    case 'error':
      return 'red';
    case 'skipped':
    case 'suppressed':
      return 'amber';
    default:
      return 'neutral';
  }
}

function ScanButton() {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const scan = useMutation({
    mutationFn: () => api.scan(),
    onSuccess: (res) => {
      setMessage(`Scan accepted — run #${res.run_id}. Results land as the run completes.`);
      window.setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: ['health'] });
        void queryClient.invalidateQueries({ queryKey: ['opportunities'] });
      }, 3000);
    },
    onError: (e) => setMessage(e instanceof Error ? e.message : String(e)),
  });
  return (
    <div style={{ textAlign: 'right' }}>
      <button className="btn sm primary" disabled={scan.isPending} onClick={() => scan.mutate()}>
        {scan.isPending ? 'Requesting…' : 'Trigger scan now'}
      </button>
      {message && (
        <div className="tiny dim" style={{ marginTop: 4, maxWidth: 260 }}>
          {message}
        </div>
      )}
    </div>
  );
}

export function HealthPage() {
  const healthQ = useQuery({
    queryKey: ['health', 'data'],
    queryFn: () => api.dataHealth(),
    refetchInterval: 60_000,
  });
  const notificationsQ = useQuery({
    queryKey: ['notifications'],
    queryFn: () => api.notifications(100),
  });

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Data Health</h1>
          <div className="sub">
            Provider capabilities, job runs, freshness and the notification delivery log.
          </div>
        </div>
        <ScanButton />
      </div>

      <QueryGate query={healthQ} skeletonRows={6}>
        {(health) => (
          <>
            <div className="grid cols-4" style={{ marginBottom: 14 }}>
              <div className="card">
                <div className="stat">
                  <span className="label">Instruments</span>
                  <span className="value mono">{health.data.instruments}</span>
                  <span className="hint">active universe size</span>
                </div>
              </div>
              <div className="card">
                <div className="stat">
                  <span className="label">Last bar date</span>
                  <span className="value mono">{fmtDate(health.data.last_bar_date)}</span>
                  <span className="hint">most recent stored price bar</span>
                </div>
              </div>
              <div className="card">
                <div className="stat">
                  <span className="label">Last scan</span>
                  <span className="value mono">{fmtDateTime(health.data.last_run_at)}</span>
                  <span className="hint">latest score run</span>
                </div>
              </div>
              <div className="card">
                <div className="stat">
                  <span className="label">Price staleness</span>
                  <span
                    className="value mono"
                    style={{
                      color:
                        (health.data.price_staleness_days ?? 0) > 1
                          ? 'var(--amber)'
                          : 'var(--green)',
                    }}
                  >
                    {health.data.price_staleness_days ?? '—'}d
                  </span>
                  <span className="hint">trading days behind</span>
                </div>
              </div>
            </div>

            <div className="section-title">
              <h2>Providers</h2>
              <div className="rule" />
            </div>
            {health.providers.length === 0 ? (
              <div className="card dim small">No providers registered.</div>
            ) : (
              <div className="grid cols-3" style={{ marginBottom: 14 }}>
                {health.providers.map((p, i) => (
                  <div className="card" key={`${p.provider}-${p.capability}-${i}`}>
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'baseline',
                      }}
                    >
                      <h3 style={{ margin: 0 }}>
                        {p.provider} <span className="dim small">· {titleCase(p.capability)}</span>
                      </h3>
                      <span className="badge-row">
                        <Badge tone={p.configured ? 'blue' : 'neutral'} outline={!p.configured}>
                          {p.configured ? 'configured' : 'not configured'}
                        </Badge>
                        <Badge tone={p.ok ? 'green' : 'red'}>{p.ok ? 'ok' : 'down'}</Badge>
                      </span>
                    </div>
                    <div className="dim small" style={{ marginTop: 6 }}>
                      {p.message ?? 'No status message.'}
                    </div>
                    <div className="tiny faint" style={{ marginTop: 4 }}>
                      checked {p.checked_at ? fmtDateTime(p.checked_at) : 'never'}
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div className="section-title">
              <h2>Job runs (last 20)</h2>
              <div className="rule" />
            </div>
            {health.jobs.length === 0 ? (
              <EmptyState message="No jobs have run yet." />
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Job</th>
                      <th>Started</th>
                      <th>Finished</th>
                      <th className="center">Status</th>
                      <th>Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {health.jobs.map((j, i) => (
                      <tr key={`${j.job_name}-${j.started_at}-${i}`}>
                        <td className="mono small">{j.job_name}</td>
                        <td className="mono small dim">{fmtDateTime(j.started_at)}</td>
                        <td className="mono small dim">
                          {j.finished_at ? fmtDateTime(j.finished_at) : '—'}
                        </td>
                        <td className="center">
                          <Badge tone={jobTone(j.status)}>{j.status}</Badge>
                        </td>
                        <td className="small dim">{j.detail ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </QueryGate>

      <div className="section-title">
        <h2>Notification log</h2>
        <div className="rule" />
      </div>
      <QueryGate
        query={notificationsQ}
        isEmpty={(d) => d.items.length === 0}
        empty={
          <div className="card dim small">
            Nothing has been delivered yet. Alert and data-failure notices land here.
          </div>
        }
      >
        {(data) => (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Channel</th>
                  <th className="center">Status</th>
                  <th>Alert</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((n) => (
                  <tr key={n.id}>
                    <td className="mono small dim">{fmtDateTime(n.created_at)}</td>
                    <td>
                      <Badge tone="neutral" outline>
                        {n.channel}
                      </Badge>
                    </td>
                    <td className="center">
                      <Badge tone={notificationTone(n.status)}>{n.status}</Badge>
                    </td>
                    <td>
                      {n.alert_id !== null ? (
                        <Link to={`/alerts/${n.alert_id}`} className="mono small">
                          #{n.alert_id}
                        </Link>
                      ) : (
                        <Badge tone="amber" outline title="Not tied to an alert — e.g. a data-failure notice">
                          system
                        </Badge>
                      )}
                    </td>
                    <td className="small dim">{n.detail ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryGate>
    </div>
  );
}
