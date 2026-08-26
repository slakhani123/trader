import { fmtScore, isNum } from '../lib/format';

export type ScoreKind = 'opportunity' | 'confidence' | 'risk';

function bucket(value: number, kind: ScoreKind): 'good' | 'ok' | 'warn' | 'bad' {
  // Risk is inverted: high risk is bad.
  const v = kind === 'risk' ? 10 - value : value;
  if (v >= 6.5) return 'good';
  if (v >= 5) return 'ok';
  if (v >= 3.5) return 'warn';
  return 'bad';
}

const KIND_LETTER: Record<ScoreKind, string> = {
  opportunity: 'O',
  confidence: 'C',
  risk: 'R',
};

/** 0–10 score rendered as a colour-coded chip. */
export function ScoreChip({
  value,
  kind = 'opportunity',
  small = false,
  showKind = false,
}: {
  value: number | null | undefined;
  kind?: ScoreKind;
  small?: boolean;
  showKind?: boolean;
}) {
  const cls = isNum(value) ? bucket(value, kind) : 'na';
  return (
    <span
      className={`chip ${cls}${small ? ' sm' : ''}`}
      title={`${kind} ${fmtScore(value)} / 10`}
    >
      {showKind ? `${KIND_LETTER[kind]} ` : ''}
      {fmtScore(value)}
    </span>
  );
}

/** Compact O / C / R triple. */
export function ScoreTriple({
  opportunity,
  confidence,
  risk,
  small = true,
}: {
  opportunity: number | null | undefined;
  confidence: number | null | undefined;
  risk: number | null | undefined;
  small?: boolean;
}) {
  return (
    <span style={{ display: 'inline-flex', gap: 4 }}>
      <ScoreChip value={opportunity} kind="opportunity" small={small} />
      <ScoreChip value={confidence} kind="confidence" small={small} />
      <ScoreChip value={risk} kind="risk" small={small} />
    </span>
  );
}
