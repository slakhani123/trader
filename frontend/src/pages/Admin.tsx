import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { Badge } from '../components/Badge';
import { EmptyState } from '../components/EmptyState';
import { QueryGate } from '../components/QueryGate';
import { fmtDateTime } from '../lib/format';

export function AdminPage() {
  const versionsQ = useQuery({
    queryKey: ['model-versions'],
    queryFn: () => api.modelVersions(),
  });
  const auditQ = useQuery({ queryKey: ['audit'], queryFn: () => api.audit(200) });

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Model Versions &amp; Audit</h1>
          <div className="sub">
            Every score is reproducible from (model version, snapshot date, instrument). Weights
            are stored verbatim with a config hash.
          </div>
        </div>
      </div>

      <div className="section-title">
        <h2>Model versions</h2>
        <div className="rule" />
      </div>
      <QueryGate
        query={versionsQ}
        isEmpty={(d) => d.length === 0}
        empty={<EmptyState message="No model versions registered." />}
      >
        {(versions) => (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Created</th>
                  <th>Config hash</th>
                  <th className="center">Active</th>
                  <th>Notes</th>
                  <th>Weights</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.version}>
                    <td className="mono" style={{ fontWeight: 700 }}>
                      {v.version}
                    </td>
                    <td className="mono small dim">{fmtDateTime(v.created_at)}</td>
                    <td className="mono small dim" style={{ wordBreak: 'break-all' }}>
                      {v.config_hash}
                    </td>
                    <td className="center">
                      {v.active ? (
                        <Badge tone="green">active</Badge>
                      ) : (
                        <Badge tone="neutral" outline>
                          inactive
                        </Badge>
                      )}
                    </td>
                    <td className="small dim">{v.notes ?? '—'}</td>
                    <td>
                      <details className="expander">
                        <summary>weights JSON</summary>
                        <pre className="json">{JSON.stringify(v.weights, null, 2)}</pre>
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryGate>

      <div className="section-title">
        <h2>Audit log</h2>
        <div className="rule" />
      </div>
      <QueryGate
        query={auditQ}
        isEmpty={(d) => d.items.length === 0}
        empty={<div className="card dim small">No audit entries yet.</div>}
      >
        {(data) => (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Actor</th>
                  <th>Action</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((row, i) => (
                  <tr key={`${row.at}-${i}`}>
                    <td className="mono small dim">{fmtDateTime(row.at)}</td>
                    <td>
                      <Badge tone="neutral" outline>
                        {row.actor}
                      </Badge>
                    </td>
                    <td className="mono small">{row.action}</td>
                    <td className="small dim">{row.detail ?? '—'}</td>
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
