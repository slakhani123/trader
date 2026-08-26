import type { ReactNode } from 'react';

export type BadgeTone = 'neutral' | 'green' | 'red' | 'amber' | 'blue' | 'purple';

export function Badge({
  tone = 'neutral',
  outline = false,
  title,
  children,
}: {
  tone?: BadgeTone;
  outline?: boolean;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span className={`badge ${tone}${outline ? ' outline' : ''}`} title={title}>
      {children}
    </span>
  );
}
