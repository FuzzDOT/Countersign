import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';

interface SheetProps {
  open: boolean;
  onClose: () => void;
  /** id of the heading inside `children` that names this dialog. */
  labelledBy: string;
  children: ReactNode;
}

const EASE_OUT = [0.16, 1, 0.3, 1] as const;
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Right-hand sheet, 560px on >=1280px and full-screen below (brief §7.1).
 *
 * It overlays the list instead of replacing it, dims the page by 10%, and does
 * NOT capture pointer events on the scrim, so the user can click another row
 * and keep their place. Focus moves in on open, is trapped with Tab, Esc
 * closes, and focus returns to whatever opened it.
 */
export function Sheet({ open, onClose, labelledBy, children }: SheetProps) {
  return (
    <AnimatePresence>
      {open ? (
        <SheetPanel key="sheet" onClose={onClose} labelledBy={labelledBy}>
          {children}
        </SheetPanel>
      ) : null}
    </AnimatePresence>
  );
}

function SheetPanel({ onClose, labelledBy, children }: Omit<SheetProps, 'open'>) {
  const ref = useRef<HTMLElement>(null);
  const reduced = useReducedMotion();
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    ref.current?.focus();
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      trigger?.focus();
    };
  }, []);

  const trapTab = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Tab' || !ref.current) return;
    const nodes = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (!first || !last) {
      event.preventDefault();
      return;
    }
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === ref.current)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const duration = reduced ? 0.1 : 0.42;
  return (
    <motion.div
      className="pointer-events-none fixed inset-0 z-30"
      initial={{ opacity: 1 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 1 }}
      transition={{ duration }}
    >
      <motion.div
        className="absolute inset-0 bg-ink-900"
        initial={{ opacity: 0 }}
        animate={{ opacity: 0.1 }}
        exit={{ opacity: 0 }}
        transition={{ duration }}
      />
      <motion.aside
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        onKeyDown={trapTab}
        className="pointer-events-auto absolute inset-y-0 right-0 flex w-full flex-col overflow-y-auto border-l border-ink-500/40 bg-ink-700 shadow-overlay outline-none xl:w-[var(--sheet-width)]"
        initial={reduced ? { opacity: 0 } : { x: '100%' }}
        animate={reduced ? { opacity: 1 } : { x: 0 }}
        exit={reduced ? { opacity: 0 } : { x: '100%' }}
        transition={{ duration, ease: EASE_OUT }}
      >
        {children}
      </motion.aside>
    </motion.div>
  );
}
