import { createRef } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { DocumentDetail } from '@/api/types';
import { DocumentReader } from './DocumentReader';

const HOSTILE = 'Pay <img src=x onerror="window.pwned=1"> now. **Not bold.** <script>window.pwned=2</script> Done.';

function doc(overrides: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    id: 'd1',
    title: 'Test',
    source: 'invoice',
    received_at: '2026-09-14T10:00:00Z',
    raw_text: HOSTILE,
    spans: [
      { insight_id: 'a', char_start: 0, char_end: 30, relation: 'WIRED_FUNDS_TO', confidence: 0.8, routing: 'escalate_now' },
      { insight_id: 'b', char_start: 20, char_end: 60, relation: 'OWNED_BY', confidence: 0.6, routing: 'flag_for_review' },
    ],
    mentions: [],
    ...overrides,
  };
}

function renderReader(d: DocumentDetail) {
  return render(
    <DocumentReader
      doc={d}
      showMentions
      onlyEscalated={false}
      focusSpanId={null}
      scrollRef={createRef<HTMLElement>()}
      onOpenInsight={() => {}}
    />,
  );
}

describe('DocumentReader', () => {
  it('renders raw_text as text, never as markup', () => {
    const { container } = renderReader(doc());
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByTestId('reader-text').textContent).toBe(HOSTILE);
    expect((window as unknown as { pwned?: number }).pwned).toBeUndefined();
  });

  it('survives overlapping and malformed spans without losing text', () => {
    const { container } = renderReader(
      doc({
        spans: [
          { insight_id: 'a', char_start: 0, char_end: 40, relation: 'WIRED_FUNDS_TO', confidence: 0.8, routing: 'escalate_now' },
          { insight_id: 'b', char_start: 10, char_end: 50, relation: 'OWNED_BY', confidence: 0.6, routing: 'flag_for_review' },
          { insight_id: 'bad', char_start: -4, char_end: 99999, relation: 'OWNED_BY', confidence: 0.6, routing: 'auto_file' },
          { insight_id: 'nan', char_start: Number.NaN, char_end: 3, relation: 'OWNED_BY', confidence: 0.6, routing: 'auto_file' },
        ],
      }),
    );
    expect(screen.getByTestId('reader-text').textContent).toBe(HOSTILE);
    expect(container.querySelectorAll('.cite-mark').length).toBeGreaterThan(0);
  });

  it('hides non-escalated spans when only escalated spans are requested', () => {
    const { container } = render(
      <DocumentReader
        doc={doc()}
        showMentions
        onlyEscalated
        focusSpanId={null}
        scrollRef={createRef<HTMLElement>()}
        onOpenInsight={() => {}}
      />,
    );
    const colours = Array.from(container.querySelectorAll<HTMLElement>('.cite-mark')).map((el) =>
      el.style.getPropertyValue('--span-color'),
    );
    expect(colours.length).toBeGreaterThan(0);
    expect(colours.every((c) => c === 'var(--stamp-red)')).toBe(true);
    expect(screen.getByTestId('reader-text').textContent).toBe(HOSTILE);
  });
});
