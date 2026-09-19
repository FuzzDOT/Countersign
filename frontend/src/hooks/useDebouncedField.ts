import { useEffect, useRef, useState } from 'react';
import { useDebouncedValue } from './useDebounce';

/**
 * A text or slider control whose value is owned by the URL but edited locally.
 *
 * Typing updates `local` immediately (so the input never lags), and the change
 * is committed to the URL 300ms after the user pauses. If the URL changes for
 * some other reason (Clear all, browser back), the local value follows it.
 *
 * `external` must be referentially stable between renders when nothing changed
 * (a primitive, or a memoised tuple), otherwise the sync effect would fight the
 * user's typing.
 */
export function useDebouncedField<T>(
  external: T,
  commit: (value: T) => void,
  isEqual: (a: T, b: T) => boolean = Object.is,
  delayMs = 300,
): [T, (value: T) => void] {
  const [local, setLocal] = useState<T>(external);
  const debounced = useDebouncedValue(local, delayMs);

  const commitRef = useRef(commit);
  const externalRef = useRef(external);
  const equalRef = useRef(isEqual);
  commitRef.current = commit;
  externalRef.current = external;
  equalRef.current = isEqual;

  useEffect(() => {
    if (!equalRef.current(debounced, externalRef.current)) commitRef.current(debounced);
  }, [debounced]);

  useEffect(() => {
    setLocal(external);
  }, [external]);

  return [local, setLocal];
}
