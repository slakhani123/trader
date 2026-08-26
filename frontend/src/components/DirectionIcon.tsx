import type { Direction } from '../api/types';

const GLYPH: Record<Direction, string> = {
  supports: '▲',
  contradicts: '▼',
  neutral: '◆',
};

export function DirectionIcon({ direction }: { direction: Direction }) {
  return (
    <span className={`dir ${direction}`} title={direction} aria-label={direction}>
      {GLYPH[direction] ?? '◆'}
    </span>
  );
}
