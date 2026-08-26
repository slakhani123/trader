import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';

export interface Column<T> {
  key: string;
  label: ReactNode;
  render: (row: T) => ReactNode;
  /** Provide to make the column sortable. */
  sortValue?: (row: T) => number | string | null;
  align?: 'left' | 'right' | 'center';
  width?: string;
  title?: string;
}

function compare(a: number | string | null, b: number | string | null): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1; // nulls last
  if (b === null) return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b));
}

/** Generic dense sortable table. */
export function DataTable<T>({
  rows,
  columns,
  rowKey,
  onRowClick,
  defaultSortKey,
  defaultDesc = true,
  rowClass,
  maxHeight,
}: {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T, index: number) => string | number;
  onRowClick?: (row: T) => void;
  defaultSortKey?: string;
  defaultDesc?: boolean;
  rowClass?: (row: T) => string | undefined;
  maxHeight?: number;
}) {
  const [sortKey, setSortKey] = useState<string | null>(defaultSortKey ?? null);
  const [desc, setDesc] = useState<boolean>(defaultDesc);

  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    const col = columns.find((c) => c.key === sortKey);
    if (!col?.sortValue) return rows;
    const sv = col.sortValue;
    const copy = [...rows];
    copy.sort((a, b) => (desc ? -1 : 1) * compare(sv(a), sv(b)));
    return copy;
  }, [rows, columns, sortKey, desc]);

  const toggleSort = (key: string, sortable: boolean) => {
    if (!sortable) return;
    if (sortKey === key) setDesc((d) => !d);
    else {
      setSortKey(key);
      setDesc(true);
    }
  };

  return (
    <div className="table-wrap" style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}>
      <table className="data">
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                className={[
                  c.sortValue ? 'sortable' : '',
                  c.align === 'right' ? 'num' : c.align === 'center' ? 'center' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                style={c.width ? { width: c.width } : undefined}
                title={c.title}
                onClick={() => toggleSort(c.key, Boolean(c.sortValue))}
              >
                {c.label}
                {sortKey === c.key ? (desc ? ' ↓' : ' ↑') : ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => (
            <tr
              key={rowKey(row, i)}
              className={[onRowClick ? 'clickable' : '', rowClass?.(row) ?? '']
                .filter(Boolean)
                .join(' ')}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={c.align === 'right' ? 'num' : c.align === 'center' ? 'center' : ''}
                >
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
