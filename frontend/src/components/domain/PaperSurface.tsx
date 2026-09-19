import type { HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

/**
 * The evidence surface: warm paper, a text serif, square corners. Wherever a
 * source sentence appears it sits on one of these, so the eye learns that
 * paper means "this came from a document" (brief §2.1).
 */
export function PaperSurface({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('paper-surface', className)} {...rest} />;
}
