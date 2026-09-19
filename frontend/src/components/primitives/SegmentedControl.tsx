import { useRef, type KeyboardEvent } from 'react';
import { cn } from '@/lib/cn';

interface Option<T extends string> {
  value: T;
  label: string;
}

interface SegmentedControlProps<T extends string> {
  options: readonly Option<T>[];
  value: T;
  onChange: (value: T) => void;
  /** Names the group for assistive tech. */
  ariaLabel: string;
  className?: string | undefined;
  size?: 'sm' | 'md' | undefined;
}

/** A radio group that looks like a segmented control. Arrow keys move the selection. */
export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  className,
  size = 'md',
}: SegmentedControlProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % options.length;
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp')
      next = (index - 1 + options.length) % options.length;
    else return;
    event.preventDefault();
    const option = options[next];
    if (option) {
      onChange(option.value);
      refs.current[next]?.focus();
    }
  };

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn('inline-flex rounded-input border border-ink-500 bg-ink-900 p-0.5', className)}
    >
      {options.map((option, index) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            ref={(el) => {
              refs.current[index] = el;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={cn(
              'rounded-input px-3 font-medium transition-colors duration-instant ease-out',
              size === 'sm' ? 'h-7 text-body-sm' : 'h-8 text-body-sm',
              selected
                ? 'bg-ink-500 text-ink-50 shadow-[inset_0_-2px_0_var(--verify)]'
                : 'text-ink-200 hover:text-ink-50',
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
