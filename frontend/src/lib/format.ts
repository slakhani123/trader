/** Deterministic display formatting helpers. No locale surprises: fixed en-GB-ish output. */

export function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 7.4 — scores are always shown with one decimal. */
export function fmtScore(v: number | null | undefined): string {
  return isNum(v) ? v.toFixed(1) : '—';
}

export function fmtNum(v: number | null | undefined, dp = 2): string {
  if (!isNum(v)) return '—';
  return v.toLocaleString('en-GB', { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

/** Signed percent, e.g. +4.2% / -1.8%. Input is already in percent units. */
export function fmtPct(v: number | null | undefined, dp = 1, signed = false): string {
  if (!isNum(v)) return '—';
  const s = `${v.toFixed(dp)}%`;
  return signed && v > 0 ? `+${s}` : s;
}

export function fmtSignedScore(v: number | null | undefined): string {
  if (!isNum(v)) return '—';
  const s = v.toFixed(1);
  return v > 0 ? `+${s}` : s;
}

/** Compact money: 1.24B / 356M / 12.5k, with an optional currency prefix. */
export function fmtMoney(v: number | null | undefined, currency?: string): string {
  if (!isNum(v)) return '—';
  const prefix = currency ? `${currency} ` : '';
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1e12) return `${prefix}${sign}${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${prefix}${sign}${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${prefix}${sign}${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e4) return `${prefix}${sign}${(abs / 1e3).toFixed(1)}k`;
  return `${prefix}${sign}${abs.toFixed(2)}`;
}

export function fmtPrice(v: number | null | undefined, currency?: string, dp = 2): string {
  if (!isNum(v)) return '—';
  const prefix = currency ? `${currency} ` : '';
  return `${prefix}${v.toLocaleString('en-GB', { minimumFractionDigits: dp, maximumFractionDigits: dp })}`;
}

/** ISO date or datetime → YYYY-MM-DD. */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  return iso.slice(0, 10);
}

/** ISO datetime → 'YYYY-MM-DD HH:MM UTC' (assumes API timestamps are UTC). */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`
  );
}

/** Rough humanised age, e.g. '3d', '5h', '2mo'. */
export function fmtAge(iso: string | null | undefined): string {
  if (!iso) return '—';
  const then = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`).getTime();
  if (Number.isNaN(then)) return '—';
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 60) return `${mins}m`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours}h`;
  const days = Math.round(hours / 24);
  if (days < 60) return `${days}d`;
  const months = Math.round(days / 30);
  if (months < 24) return `${months}mo`;
  return `${Math.round(months / 12)}y`;
}

/** Days between an ISO date and today (UTC), positive when in the past. */
export function daysSince(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const then = new Date(`${iso.slice(0, 10)}T00:00:00Z`).getTime();
  if (Number.isNaN(then)) return null;
  return Math.floor((Date.now() - then) / 86400000);
}

/** Render any API value as text. Detail fields are declared string|null but
 * older API builds sent raw JSON objects — never hand an object to React. */
export function asText(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'string') return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

/** snake_case / SCREAMING_CASE → Title Case. */
export function titleCase(s: string | null | undefined): string {
  if (!s) return '—';
  return s
    .toLowerCase()
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/** Signal-family label, e.g. deep_value → Deep Value. */
export const familyLabel = titleCase;

/** Component key → short label for dense chips/bars. */
export function componentLabel(key: string): string {
  const map: Record<string, string> = {
    quality: 'Qual',
    growth: 'Grw',
    valuation: 'Val',
    technical: 'Tech',
    momentum: 'Mom',
    sentiment: 'Sent',
    catalysts: 'Cat',
    balance_sheet: 'BS',
    data_quality: 'DQ',
    regime: 'Reg',
  };
  return map[key] ?? titleCase(key);
}

/** Canonical component ordering so mini-bars line up across rows. */
export const COMPONENT_ORDER = [
  'quality',
  'growth',
  'valuation',
  'technical',
  'momentum',
  'sentiment',
  'catalysts',
  'balance_sheet',
  'data_quality',
];

export function orderedComponents(components: Record<string, number>): [string, number][] {
  const known = COMPONENT_ORDER.filter((k) => k in components).map(
    (k) => [k, components[k]] as [string, number],
  );
  const extra = Object.entries(components)
    .filter(([k]) => !COMPONENT_ORDER.includes(k))
    .sort(([a], [b]) => a.localeCompare(b));
  return [...known, ...extra];
}

export const HORIZONS: readonly ['short', 'medium', 'long'] = ['short', 'medium', 'long'];

export const SIGNAL_FAMILIES = [
  'deep_value',
  'quality_compounder',
  'oversold_at_support',
  'constructive_pullback',
  'breakout_continuation',
  'fundamental_inflection',
  'estimate_momentum',
  'watch_setup',
  'hold',
  'avoid',
  'trim',
  'full_exit',
  'thesis_invalidated',
] as const;

export const LIFECYCLE_STATES = [
  'WATCHING',
  'TRIGGERED',
  'REINFORCED',
  'WEAKENING',
  'TRIM',
  'EXITED',
  'INVALIDATED',
  'EXPIRED',
] as const;

export const ACTIVE_STATES = ['WATCHING', 'TRIGGERED', 'REINFORCED', 'WEAKENING', 'TRIM'] as const;
export const TERMINAL_STATES = ['EXITED', 'INVALIDATED', 'EXPIRED'] as const;

/** Tone bucket for a lifecycle state badge. */
export function stateTone(state: string): 'green' | 'red' | 'amber' | 'blue' | 'neutral' {
  switch (state) {
    case 'TRIGGERED':
    case 'REINFORCED':
      return 'green';
    case 'WEAKENING':
    case 'TRIM':
      return 'amber';
    case 'INVALIDATED':
    case 'EXITED':
      return 'red';
    case 'WATCHING':
      return 'blue';
    default:
      return 'neutral';
  }
}

export function priorityTone(priority: string): 'green' | 'red' | 'amber' | 'blue' | 'neutral' {
  switch (priority.toLowerCase()) {
    case 'critical':
    case 'high':
      return 'red';
    case 'medium':
      return 'amber';
    case 'low':
      return 'blue';
    default:
      return 'neutral';
  }
}
