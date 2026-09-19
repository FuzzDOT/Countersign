import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

/**
 * tailwind-merge does not know our custom font-size tokens, so without this
 * `cn('text-body-sm', 'text-ink-200')` would treat both as colours and drop
 * the size. Register every size token from tailwind.config.ts.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [
        {
          text: [
            'micro',
            'body-sm',
            'body',
            'h3',
            'h2',
            'h1',
            'display-2',
            'display-1',
            'reader-sm',
            'reader-body',
          ],
        },
      ],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
