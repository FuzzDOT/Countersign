import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type { AskResponse, VoiceIntent } from '@/api/types';
import { Badge } from '@/components/primitives/Badge';
import { Button, LinkButton } from '@/components/primitives/Button';
import { claimAudioFocus } from '@/lib/audioFocus';
import { loadAudio } from '@/lib/audio';
import { formatScore } from '@/lib/format';
import { CitationBlock } from '../CitationBlock';

const INTENT_LABELS: Record<VoiceIntent, string> = {
  explain_flag: 'Explain a flag',
  show_source: 'Show the source',
  list_flagged: 'List flagged items',
  entity_summary: 'Summarise an entity',
  confidence_query: 'Ask about confidence',
  dismiss: 'Dismiss',
  unknown: 'Not understood',
};

interface AnswerCardProps {
  answer: AskResponse;
  /** Play the spoken answer as soon as it arrives. Only the newest answer does. */
  autoplay: boolean;
  onRerecord: () => void;
}

/**
 * One answer, in the order the brief specifies (§12.2): what it heard, the
 * intent, the answer, the citation, the ablation reference, and the audio.
 * The transcription is shown honestly, including how sure the recogniser was.
 */
export function AnswerCard({ answer, autoplay, onRerecord }: AnswerCardProps) {
  const unknown = answer.intent === 'unknown';
  const unclear = answer.stt_confidence < 0.7;
  const insightId = answer.resolved_insight_id;

  return (
    <article className="flex flex-col gap-3 border-b border-ink-500/40 py-5 first:pt-0 last:border-b-0">
      {/* 1. What it heard */}
      <div>
        <p className="text-body-sm text-ink-200">You asked</p>
        <p className="text-body text-ink-50">&ldquo;{answer.heard}&rdquo;</p>
        <p className="nums text-body-sm text-ink-200">Speech recognition confidence {formatScore(answer.stt_confidence)}</p>
        {unclear ? (
          <p className="mt-1 flex flex-wrap items-center gap-3 text-body-sm text-ink-50">
            Didn&rsquo;t catch that clearly?
            <Button size="sm" variant="secondary" onClick={onRerecord}>
              Record it again
            </Button>
          </p>
        ) : null}
      </div>

      {/* 2. Intent */}
      <div>
        <Badge tone="outline">{INTENT_LABELS[answer.intent]}</Badge>
      </div>

      {/* 3. Answer. An unknown intent is a rephrase prompt with no citation. */}
      <p className="max-w-prose text-body text-ink-50">{answer.answer_text}</p>

      {/* 4. Citation */}
      {!unknown && answer.citation ? (
        <CitationBlock
          citation={answer.citation}
          action={
            <LinkButton
              variant="primary"
              state={{ from: 'previous' }}
              to={`/app/document/${answer.citation.document_id}${insightId ? `?span=${encodeURIComponent(insightId)}` : ''}`}
            >
              Open document
            </LinkButton>
          }
        />
      ) : null}

      {/* 5. Ablation reference */}
      {answer.ablation_run_id && insightId ? (
        <p className="text-body-sm">
          <Link to={`/app/feed/${insightId}#ablation`} className="text-ink-50 underline underline-offset-4">
            See the ablation history for this insight
          </Link>
        </p>
      ) : null}

      {/* 6. Audio */}
      {answer.audio_url ? <AnswerAudio url={answer.audio_url} autoplay={autoplay} /> : null}
    </article>
  );
}

function AnswerAudio({ url, autoplay }: { url: string; autoplay: boolean }) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [blocked, setBlocked] = useState(false);
  const [playing, setPlaying] = useState(false);
  const started = useRef(false);

  useEffect(() => {
    let cancelled = false;
    void loadAudio(url).then((loaded) => {
      if (!cancelled) setSrc(loaded.src);
    });
    return () => {
      cancelled = true;
    };
  }, [url]);

  useEffect(() => {
    const el = audioRef.current;
    if (!src || !autoplay || !el || started.current) return;
    started.current = true;
    void el.play().catch(() => setBlocked(true));
  }, [src, autoplay]);

  const toggle = () => {
    const el = audioRef.current;
    if (!el) return;
    if (el.paused) void el.play().catch(() => setBlocked(true));
    else el.pause();
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- the full transcript is always shown on the page */}
      <audio
        ref={audioRef}
        src={src ?? undefined}
        preload="auto"
        onPlay={() => {
          setPlaying(true);
          setBlocked(false);
          if (audioRef.current) claimAudioFocus(audioRef.current);
        }}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
      />
      <Button size="sm" variant="secondary" disabled={!src} onClick={toggle}>
        {playing ? 'Pause answer' : 'Play answer'}
      </Button>
      {blocked ? <span className="text-body-sm text-ink-200">Your browser blocked autoplay. Press play to hear it.</span> : null}
    </div>
  );
}
