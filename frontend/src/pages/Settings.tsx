import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, getToken, setToken } from '../api/client';
import { QueryGate } from '../components/QueryGate';

const CONFIG_SECTIONS: { key: string; label: string; blurb: string }[] = [
  { key: 'universe', label: 'Universe', blurb: 'Market cap and liquidity thresholds for coverage.' },
  { key: 'horizons', label: 'Horizons', blurb: 'Short / medium / long window definitions.' },
  { key: 'gates', label: 'Gates', blurb: 'Minimum confidence, liquidity, reward/risk and data-quality gates.' },
  { key: 'alert_policy', label: 'Alert policy', blurb: 'Cooldowns, dedup and material-change thresholds.' },
  { key: 'risk_policy', label: 'Risk policy', blurb: 'Position and sector exposure limits.' },
  { key: 'scan', label: 'Scan', blurb: 'Scheduling and scan behaviour.' },
];

function TokenCard() {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(getToken());
  const [saved, setSaved] = useState(false);

  const save = () => {
    setToken(value.trim());
    setSaved(true);
    void queryClient.invalidateQueries();
    window.setTimeout(() => setSaved(false), 2500);
  };

  return (
    <div className="card">
      <h2>API token</h2>
      <div className="dim small" style={{ marginBottom: 8 }}>
        Sent as <code>Authorization: Bearer …</code> on every request and kept in this browser's
        localStorage (<code>vigil_token</code>). Leave empty for local dev when the API runs with{' '}
        <code>VIGIL_DEBUG=true</code> and no token configured.
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          type="password"
          value={value}
          placeholder="VIGIL_API_TOKEN"
          style={{ width: 340 }}
          autoComplete="off"
          onChange={(e) => setValue(e.target.value)}
        />
        <button className="btn sm primary" onClick={save}>
          Save token
        </button>
        {value && (
          <button
            className="btn sm"
            onClick={() => {
              setValue('');
              setToken('');
              void queryClient.invalidateQueries();
            }}
          >
            Clear
          </button>
        )}
        {saved && <span className="small" style={{ color: 'var(--green)' }}>Saved.</span>}
      </div>
    </div>
  );
}

export function SettingsPage() {
  const configQ = useQuery({ queryKey: ['config'], queryFn: () => api.config() });

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <div className="sub">Local UI settings and a read-only view of the server configuration.</div>
        </div>
      </div>

      <TokenCard />

      <div className="banner info">
        <div className="title">No trading connectivity — by design</div>
        Vigil is research support only. There is deliberately no brokerage integration, no order
        routing and no execution capability anywhere in the codebase, and none will be added. The
        portfolio and watchlist pages are record-keeping overlays for your own decisions.
      </div>

      <div className="section-title">
        <h2>Server configuration (read-only)</h2>
        <div className="rule" />
      </div>
      <QueryGate query={configQ} skeletonRows={5}>
        {(config) => {
          const known = new Set(CONFIG_SECTIONS.map((s) => s.key));
          const scalarEntries = Object.entries(config).filter(
            ([k, v]) => !known.has(k) && (typeof v !== 'object' || v === null),
          );
          const extraObjects = Object.entries(config).filter(
            ([k, v]) => !known.has(k) && typeof v === 'object' && v !== null,
          );
          return (
            <>
              {scalarEntries.length > 0 && (
                <div className="card">
                  <div className="kv">
                    {scalarEntries.map(([k, v]) => (
                      <SettingRow key={k} name={k} value={v} />
                    ))}
                  </div>
                </div>
              )}
              <div className="grid cols-2">
                {CONFIG_SECTIONS.map(({ key, label, blurb }) => {
                  const section = config[key];
                  return (
                    <div className="card" key={key} style={{ marginBottom: 0 }}>
                      <h3>{label}</h3>
                      <div className="tiny faint" style={{ marginBottom: 6 }}>
                        {blurb}
                      </div>
                      {section === undefined || section === null ? (
                        <div className="dim small">Not exposed by this API build.</div>
                      ) : (
                        <pre className="json">{JSON.stringify(section, null, 2)}</pre>
                      )}
                    </div>
                  );
                })}
                {extraObjects.map(([k, v]) => (
                  <div className="card" key={k} style={{ marginBottom: 0 }}>
                    <h3>{k}</h3>
                    <pre className="json">{JSON.stringify(v, null, 2)}</pre>
                  </div>
                ))}
              </div>
            </>
          );
        }}
      </QueryGate>
    </div>
  );
}

function SettingRow({ name, value }: { name: string; value: unknown }) {
  return (
    <>
      <dt>{name}</dt>
      <dd className="mono">{value === null ? '—' : String(value)}</dd>
    </>
  );
}
