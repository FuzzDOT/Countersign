import { useId, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

interface CheckboxProps {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** A trailing number, such as the count of insights in that bucket. */
  count?: number | undefined;
  disabled?: boolean | undefined;
  className?: string | undefined;
}

export function Checkbox({ label, checked, onChange, count, disabled, className }: CheckboxProps) {
  const id = useId();
  return (
    <div className={cn('flex items-center gap-2', className)}>
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="size-4 shrink-0 cursor-pointer accent-verify disabled:cursor-not-allowed"
      />
      <label htmlFor={id} className="flex-1 cursor-pointer text-body-sm text-ink-50">
        {label}
      </label>
      {count !== undefined ? <span className="nums text-body-sm text-ink-200">{count}</span> : null}
    </div>
  );
}
