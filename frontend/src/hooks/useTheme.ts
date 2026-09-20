import { useCallback, useEffect, useState } from 'react';
import { getCookie, setCookie } from '@/lib/cookies';

export type Theme = 'dark' | 'light';

const COOKIE_NAME = 'cs_theme';

/**
 * Theme state. Persisted in a cookie, not localStorage — browser storage is
 * banned repo-wide and CI greps for it (frontend brief §18).
 *
 * The initial value is read from the cookie, then the OS preference, then
 * falls back to dark. `data-theme` on <html> is what every token reads.
 */
function readInitialTheme(): Theme {
  const stored = getCookie(COOKIE_NAME);
  if (stored === 'dark' || stored === 'light') return stored;
  if (typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: light)').matches) {
    return 'light';
  }
  return 'dark';
}

export function useTheme(): { theme: Theme; toggle: () => void; setTheme: (t: Theme) => void } {
  const [theme, setThemeState] = useState<Theme>(readInitialTheme);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    setCookie(COOKIE_NAME, theme);
  }, [theme]);

  const setTheme = useCallback((next: Theme) => setThemeState(next), []);
  const toggle = useCallback(() => setThemeState((t) => (t === 'dark' ? 'light' : 'dark')), []);

  return { theme, toggle, setTheme };
}
