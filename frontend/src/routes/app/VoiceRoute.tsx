import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/endpoints';
import { isApiError } from '@/api/errors';
import { queryKeys, useVoiceFallback } from '@/api/queries';
import type { AskResponse, BriefingResponse, BriefingScope } from '@/api/types';
import { useAuth } from '@/auth/useAuth';
import { InsightSheet } from '@/components/domain/InsightSheet';
import { AnswerCard } from '@/components/domain/voice/AnswerCard';
import { BriefingInsightCards } from '@/components/domain/voice/BriefingInsightCards';
import { BriefingPlayer } from '@/components/domain/voice/BriefingPlayer';
import { PushToTalk } from '@/components/domain/voice/PushToTalk';
import { Button } from '@/components/primitives/Button';
import { EmptyState } from '@/components/primitives/EmptyState';
import { IconButton } from '@/components/primitives/IconButton';
import { MinusIcon, PlusIcon } from '@/components/primitives/icons';
import { SegmentedControl } from '@/components/primitives/SegmentedControl';
import { useSheetControl } from '@/hooks/useSheetControl';
import { preloadAudio } from '@/lib/audio';
import { pushToast } from '@/lib/toastBus';

const SCOPES = [
  { value: 'flagged', label: 'Flagged' },
  { value: 'escalated', label: 'Escalated' },
  { value: 'all_new', label: 'All new' },
] as const;

interface ActiveBriefing {
  data: BriefingResponse;
  fallback: boolean;
}

/**
 * /app/voice: briefing on top, ask below (brief §12).
 *
 * Fallback (§12.3): if the live briefing fails for ANY reason, or takes longer
 * than 8s (the client aborts there), the recorded briefing is fetched and
 * played automatically. No modal, no retry dialog: the demo continues, with an
 * honest note. The recorded audio is preloaded on mount.
 */
export default function VoiceRoute() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const sheet = useSheetControl();

  const [scope, setScope] = useState<BriefingScope>('flagged');
  const [maxItems, setMaxItems] = useState(3);
  const [pending, setPending] = useState(false);
  const [briefing, setBriefing] = useState<ActiveBriefing | null>(null);
  const [briefingError, setBriefingError] = useState<string | null>(null);
  const [activeInsight, setActiveInsight] = useState<string | null>(null);
  const [answers, setAnswers] = useState<AskResponse[]>([]);
  const pttRef = useRef<HTMLButtonElement>(null);
  const answersEnd = useRef<HTMLDivElement>(null);

  // Get the recorded briefing (JSON and audio bytes) into the cache now.
  const fallbackQuery = useVoiceFallback();
  useEffect(() => {
    if (fallbackQuery.data) preloadAudio(fallbackQuery.data.audio_url);
  }, [fallbackQuery.data]);

  const playBriefing = async () => {
    setPending(true);
    setBriefingError(null);
    setActiveInsight(null);
    try {
      const data = await api.voice.briefing({ scope, max_items: maxItems });
      setBriefing({ data, fallback: data.is_fallback });
    } catch {
      try {
        const data = await queryClient.fetchQuery({
          queryKey: queryKeys.voiceFallback(),
          queryFn: api.voice.fallback,
          staleTime: 5 * 60_000,
        });
        setBriefing({ data, fallback: true });
      } catch (error) {
        setBriefing(null);
        setBriefingError(
          isApiError(error)
            ? error.message
            : 'Neither the live nor the recorded briefing could be loaded.',
        );
      }
    } finally {
      setPending(false);
    }
  };

  const ask = useMutation({
    mutationFn: (audio: Blob) => api.voice.ask(audio, sheet.insightId ?? activeInsight),
    onSuccess: (answer) => setAnswers((current) => [...current, answer]),
    onError: (error) => {
      pushToast({
        tone: 'error',
        title: 'The question did not go through',
        description: isApiError(error) ? error.message : 'Try asking again.',
        requestId: isApiError(error) ? error.requestId : null,
      });
    },
  });

  useEffect(() => {
    if (answers.length > 0) answersEnd.current?.scrollIntoView({ block: 'nearest' });
  }, [answers.length]);

  if (!can('voice:use')) {
    return (
      <EmptyState title="You do not have access to voice">
        Ask an owner to grant voice access.
      </EmptyState>
    );
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-6xl flex-col gap-12 px-5 py-6">
        <header>
          <h1 className="text-display-2 text-ink-50">Voice console</h1>
        </header>

        <section aria-labelledby="briefing-heading" className="flex flex-col gap-4">
          <h2 id="briefing-heading" className="text-h2 text-ink-50">
            Briefing
          </h2>

          <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
            <div className="flex flex-col gap-1">
              <span className="text-body-sm font-semibold text-card-fg">What to cover</span>
              <SegmentedControl
                ariaLabel="Briefing scope"
                options={SCOPES}
                value={scope}
                onChange={setScope}
              />
            </div>
            <div role="group" aria-label="Maximum items" className="flex flex-col gap-1">
              <span className="text-body-sm font-semibold text-card-fg">Maximum items</span>
              <div className="flex items-center gap-1">
                <IconButton
                  label="Fewer items"
                  disabled={maxItems <= 1}
                  onClick={() => setMaxItems((n) => Math.max(1, n - 1))}
                >
                  <MinusIcon />
                </IconButton>
                <span className="nums w-6 text-center text-body text-ink-50" aria-live="polite">
                  {maxItems}
                </span>
                <IconButton
                  label="More items"
                  disabled={maxItems >= 5}
                  onClick={() => setMaxItems((n) => Math.min(5, n + 1))}
                >
                  <PlusIcon />
                </IconButton>
              </div>
            </div>
            <Button
              variant="primary"
              pending={pending}
              pendingLabel="Preparing briefing…"
              onClick={() => void playBriefing()}
            >
              Play briefing
            </Button>
          </div>

          <div aria-live="polite">
            {briefing?.fallback ? (
              <p className="text-body-sm text-ink-200">
                Playing recorded briefing; live synthesis is unavailable.
              </p>
            ) : null}
          </div>
          {briefingError ? (
            <p role="alert" className="text-body-sm text-ink-50">
              {briefingError}
            </p>
          ) : null}

          {briefing ? (
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
              <BriefingPlayer
                key={briefing.data.briefing_id}
                briefing={briefing.data}
                onActiveInsight={setActiveInsight}
              />
              <BriefingInsightCards
                insightIds={briefing.data.insight_ids}
                activeInsightId={activeInsight}
                onOpen={(id) => sheet.open(id)}
              />
            </div>
          ) : (
            <p className="max-w-prose text-body text-ink-200">
              Choose what to cover and press Play briefing. The transcript follows the audio, and
              each insight it mentions lights up with the sentence it came from.
            </p>
          )}
        </section>

        <section aria-labelledby="ask-heading" className="flex flex-col gap-4">
          <h2 id="ask-heading" className="text-h2 text-ink-50">
            Ask
          </h2>

          {answers.length > 0 ? (
            <div aria-live="polite" aria-label="Questions asked this session">
              {answers.map((answer, index) => (
                <AnswerCard
                  key={answer.question_id}
                  answer={answer}
                  autoplay={index === answers.length - 1}
                  onRerecord={() => pttRef.current?.focus()}
                />
              ))}
              <div ref={answersEnd} />
            </div>
          ) : (
            <p className="max-w-prose text-body text-ink-200">
              Ask about a flagged item, for example why it was flagged or where the claim came from.
              Your questions and the answers stay here for this session.
            </p>
          )}

          <PushToTalk
            busy={ask.isPending}
            onRecorded={(audio) => ask.mutate(audio)}
            buttonRef={pttRef}
          />
        </section>
      </div>

      <InsightSheet insightId={sheet.insightId} onClose={sheet.close} />
    </div>
  );
}
