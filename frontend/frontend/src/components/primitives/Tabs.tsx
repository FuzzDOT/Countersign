import { useRef, type KeyboardEvent, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

interface Tab<T extends string> {
  id: T;
  label: string;
}

interface TabsProps<T extends string> {
  tabs: readonly Tab<T>[];
  value: T;
  onChange: (id: T) => void;
  ariaLabel: string;
}

export const tabId = (id: string) => `tab-${id}`;
export const panelId = (id: string) => `tabpanel-${id}`;

/** Tab list. The active tab lives in the URL, so `value` and `onChange` are wired to search params. */
export function Tabs<T extends string>({ tabs, value, onChange, ariaLabel }: TabsProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    else return;
    event.preventDefault();
    const tab = tabs[next];
    if (tab) {
      onChange(tab.id);
      refs.current[next]?.focus();
    }
  };

  return (
    <div role="tablist" aria-label={ariaLabel} className="flex gap-1 border-b border-ink-500/40">
      {tabs.map((tab, index) => {
        const selected = tab.id === value;
        return (
          <button
            key={tab.id}
            ref={(el) => {
              refs.current[index] = el;
            }}
            id={tabId(tab.id)}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={panelId(tab.id)}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={cn(
              '-mb-px border-b-2 px-4 py-2 text-body font-medium transition-colors duration-instant ease-out',
              selected
                ? 'border-verify text-ink-50'
                : 'border-transparent text-ink-200 hover:text-ink-50',
            )}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({ id, children }: { id: string; children: ReactNode }) {
  return (
    <div id={panelId(id)} role="tabpanel" aria-labelledby={tabId(id)} tabIndex={0}>
      {children}
    </div>
  );
}
