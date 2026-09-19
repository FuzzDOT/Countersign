import { useEffect, useRef, useState } from 'react';
import { easeOut } from '@/lib/easing';
import { usePrefersReducedMotion } from './usePrefersReducedMotion';

interface CountUpOptions {
  /** Duration in ms. Defaults to 600. */
  duration?: number;
  /**
   * Start value. Together with `replayKey` this makes the number jump to
   * `from` and animate to `target`, which is how "the ECE counts down from
   * before to after" works even if the page already shows the after value.
   */
  from?: number;
  /** Change this to replay the animation from `from`. */
  replayKey?: string | number | null;
}

/**
 * The one number animator (brief §14: write it once, never hand-roll a second).
 *
 * Whenever `target` changes the returned value eases toward it from wherever it
 * currently is, so a value is never snap-replaced by another value: the
 * movement is the evidence. Under prefers-reduced-motion it returns `target`
 * immediately.
 */
export function useCountUp(target: number, options: CountUpOptions = {}): number {
  const { duration = 600, from, replayKey = null } = options;
  const reduced = usePrefersReducedMotion();
  const [value, setValue] = useState<number>(from ?? target);
  const current = useRef<number>(from ?? target);
  const lastReplay = useRef<string | number | null>(null);

  useEffect(() => {
    if (reduced || duration <= 0) {
      current.current = target;
      setValue(target);
      return undefined;
    }

    let start = current.current;
    if (from !== undefined && replayKey !== null && replayKey !== lastReplay.current) {
      start = from;
    }
    lastReplay.current = replayKey;

    if (start === target) {
      current.current = target;
      setValue(target);
      return undefined;
    }

    let frame = 0;
    const startedAt = performance.now();
    const tick = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / duration);
      const next = start + (target - start) * easeOut(progress);
      current.current = next;
      setValue(next);
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    current.current = start;
    setValue(start);
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // `from` is intentionally read only when replayKey changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, duration, reduced, replayKey]);

  return value;
}
