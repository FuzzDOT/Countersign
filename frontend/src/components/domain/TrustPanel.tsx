import type { TrustScores } from '@/api/types';
import { Meter } from '@/components/primitives/Meter';
import { formatScore } from '@/lib/format';

const ROWS: { key: 'confidence' | 'vacuity' | 'dissonance' | 'fragility'; label: string; hint: string }[] = [
  { key: 'confidence', label: 'Confidence', hint: 'How strongly the model backs this relation' },
  { key: 'vacuity', label: 'Vacuity', hint: 'How much the model does not know' },
  { key: 'dissonance', label: 'Dissonance', hint: 'How much the evidence conflicts' },
  { key: 'fragility', label: 'Fragility', hint: 'How easily the label flips under perturbation' },
];

/**
 * Four numbers and four meters. `fragility === null` means the fuzzer has not
 * run against this insight, which is different from "tested and robust", so it
 * says so in words instead of showing 0 or a bare dash.
 */
export function TrustPanel({ trust }: { trust: TrustScores }) {
  return (
    <table className="w-full text-body-sm">
      <caption className="sr-only">Trust scores for this insight</caption>
      <tbody>
        {ROWS.map((row) => {
          const value = trust[row.key];
          return (
            <tr key={row.key} className="border-b border-ink-500/30 last:border-0">
              <th scope="row" className="w-28 py-2 pr-3 text-left font-medium text-ink-50">
                <span title={row.hint}>{row.label}</span>
              </th>
              <td className="py-2 pr-3">
                {value === null ? (
                  <span className="text-ink-200">not yet measured</span>
                ) : (
                  <span className="nums text-ink-50">{formatScore(value)}</span>
                )}
              </td>
              <td className="py-2 text-right">
                {value === null ? (
                  <div aria-hidden="true" className="ml-auto h-1 w-[120px] rounded-input bg-ink-500/40" />
                ) : (
                  <Meter value={value} label={row.label} className="inline-block" />
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
