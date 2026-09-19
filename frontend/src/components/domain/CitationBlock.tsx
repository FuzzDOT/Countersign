import type { ReactNode } from 'react';
import type { Citation } from '@/api/types';
import { cn } from '@/lib/cn';
import { PaperSurface } from './PaperSurface';

interface CitationBlockProps {
  citation: Citation;
  /** Rendered beneath the range, typically an "Open document" link. */
  action?: ReactNode;
  className?: string | undefined;
  /** Show the document title above the sentence. */
  showTitle?: boolean | undefined;
}

/**
 * The cited sentence, in Literata on a paper inset, with the character range in
 * mono beneath it. The range is a machine identifier, which is the only reason
 * it is set in mono. The text is a React text node: it is untrusted document
 * content and is never interpreted as markup.
 */
export function CitationBlock({ citation, action, className, showTitle = true }: CitationBlockProps) {
  return (
    <PaperSurface className={cn('px-5 py-4', className)}>
      {showTitle ? <p className="mb-2 font-sans text-body-sm text-paper-text/70">{citation.document_title}</p> : null}
      <blockquote className="max-w-reader text-reader-sm text-paper-text">
        <p className="whitespace-pre-wrap break-words">{citation.sentence_text}</p>
      </blockquote>
      <p className="mt-3 font-mono text-micro text-paper-text/70">
        characters {citation.char_start}&ndash;{citation.char_end}
      </p>
      {action ? <div className="mt-3">{action}</div> : null}
    </PaperSurface>
  );
}
