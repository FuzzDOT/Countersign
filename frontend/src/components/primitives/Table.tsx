import { useMemo, useState, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  /** Present means the column is sortable. */
  sortValue?: (row: T) => string | number | null;
  align?: 'left' | 'right';
  className?: string;
}

interface TableProps<T> {
  columns: readonly Column<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string;
  caption: string;
  initialSort?: { key: string; dir: 'asc' | 'desc' };
  /** Max height in px; the body scrolls inside it. */
  maxHeight?: number;
  emptyText?: string;
}

/** Sortable table. Sentence-case headers, hairline rows, no zebra, no shadow. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  caption,
  initialSort,
  maxHeight,
  emptyText = 'Nothing to show.',
}: TableProps<T>) {
  const [sort, setSort] = useState(initialSort ?? null);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const column = columns.find((c) => c.key === sort.key);
    const getter = column?.sortValue;
    if (!getter) return rows;
    const factor = sort.dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = getter(a);
      const bv = getter(b);
      if (av === bv) return 0;
      if (av === null) return 1; // nulls always last
      if (bv === null) return -1;
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * factor;
      return String(av).localeCompare(String(bv)) * factor;
    });
  }, [rows, columns, sort]);

  const toggle = (key: string) =>
    setSort((current) =>
      current?.key === key && current.dir === 'desc' ? { key, dir: 'asc' } : { key, dir: 'desc' },
    );

  return (
    <div
      className="overflow-auto rounded-panel border border-ink-500/40"
      style={maxHeight ? { maxHeight } : undefined}
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={0}
      role="region"
      aria-label={caption}
    >
      <table className="w-full border-collapse text-left text-body-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 z-10 bg-ink-700">
          <tr>
            {columns.map((column) => {
              const active = sort?.key === column.key;
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                  className={cn(
                    'border-b border-ink-500/40 px-3 py-2 font-medium text-ink-200',
                    column.align === 'right' && 'text-right',
                  )}
                >
                  {column.sortValue ? (
                    <button
                      type="button"
                      onClick={() => toggle(column.key)}
                      className="inline-flex items-center gap-1 hover:text-ink-50"
                    >
                      {column.header}
                      <span aria-hidden="true" className="w-3 text-ink-50">
                        {active ? (sort.dir === 'asc' ? '\u2191' : '\u2193') : ''}
                      </span>
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-3 py-6 text-center text-ink-200">
                {emptyText}
              </td>
            </tr>
          ) : (
            sorted.map((row) => (
              <tr key={rowKey(row)} className="border-b border-ink-500/20 last:border-0 hover:bg-ink-500/20">
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={cn(
                      'px-3 py-2 align-top text-ink-50',
                      column.align === 'right' && 'nums text-right',
                      column.className,
                    )}
                  >
                    {column.render(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
