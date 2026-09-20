import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type Ref,
} from 'react';
import { MicIcon } from '@/components/primitives/icons';
import { Button } from '@/components/primitives/Button';
import { buttonClasses } from '@/lib/buttonStyles';
import { cn } from '@/lib/cn';

const MAX_MS = 30_000;
const WARN_MS = 25_000;
const MIN_MS = 500;
const IDLE_RELEASE_MS = 60_000;

type Phase = 'idle' | 'starting' | 'recording' | 'denied' | 'no-mic' | 'unsupported' | 'insecure';

interface PushToTalkProps {
  /** True while an answer is being fetched: the button is held disabled. */
  busy: boolean;
  onRecorded: (audio: Blob) => void;
  buttonRef?: Ref<HTMLButtonElement>;
}

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined;
  return MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus'
    : undefined;
}

function classify(error: unknown): Phase {
  const name = error instanceof DOMException ? error.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError' || name === 'PermissionDeniedError')
    return 'denied';
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'no-mic';
  return 'denied';
}

const clock = (ms: number) => {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

/**
 * Hold to record, release to send (brief §12.2). Records audio/webm;codecs=opus
 * with MediaRecorder, shows elapsed time, and counts down visibly from 25s to
 * the 30s hard cap. A denied microphone gets real instructions, not a dead
 * button.
 *
 * The microphone stream is kept for a minute after use so a second question
 * does not clip its first syllable, then released so the browser's recording
 * indicator switches off.
 */
export function PushToTalk({ busy, onRecorded, buttonRef }: PushToTalkProps) {
  const [phase, setPhase] = useState<Phase>(() => {
    if (typeof window !== 'undefined' && !window.isSecureContext) return 'insecure';
    if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices?.getUserMedia)
      return 'unsupported';
    return 'idle';
  });
  const [elapsed, setElapsed] = useState(0);
  const [hint, setHint] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState('');

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAt = useRef(0);
  const tickTimer = useRef<number | undefined>(undefined);
  const releaseTimer = useRef<number | undefined>(undefined);
  const wanted = useRef(false);
  const warned = useRef(false);
  const onRecordedRef = useRef(onRecorded);
  onRecordedRef.current = onRecorded;

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const scheduleRelease = useCallback(() => {
    window.clearTimeout(releaseTimer.current);
    releaseTimer.current = window.setTimeout(releaseStream, IDLE_RELEASE_MS);
  }, [releaseStream]);

  useEffect(
    () => () => {
      wanted.current = false;
      window.clearInterval(tickTimer.current);
      window.clearTimeout(releaseTimer.current);
      const recorder = recorderRef.current;
      if (recorder && recorder.state === 'recording') {
        recorder.onstop = null;
        recorder.stop();
      }
      releaseStream();
    },
    [releaseStream],
  );

  const stop = useCallback(() => {
    wanted.current = false;
    const recorder = recorderRef.current;
    if (recorder && recorder.state === 'recording') recorder.stop();
  }, []);

  const start = useCallback(async () => {
    if (busy || phase === 'recording' || phase === 'starting') return;
    wanted.current = true;
    warned.current = false;
    setHint(null);
    setPhase('starting');
    window.clearTimeout(releaseTimer.current);
    try {
      const stream = streamRef.current?.active
        ? streamRef.current
        : await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // The permission prompt takes time. If the user let go while it was open,
      // do not start recording behind their back.
      if (!wanted.current) {
        setPhase('idle');
        setHint('Microphone ready. Hold the button while you ask your question.');
        scheduleRelease();
        return;
      }

      const mimeType = pickMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        window.clearInterval(tickTimer.current);
        const length = Date.now() - startedAt.current;
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        setElapsed(0);
        setPhase('idle');
        setAnnouncement('');
        if (length < MIN_MS || blob.size === 0) {
          setHint('That was too short. Hold the button a little longer while you speak.');
        } else {
          onRecordedRef.current(blob);
        }
        scheduleRelease();
      };
      recorder.start();
      recorderRef.current = recorder;
      startedAt.current = Date.now();
      setPhase('recording');
      setAnnouncement('Recording');
      tickTimer.current = window.setInterval(() => {
        const ms = Date.now() - startedAt.current;
        setElapsed(ms);
        if (ms >= WARN_MS && !warned.current) {
          warned.current = true;
          setAnnouncement('Five seconds left');
        }
        if (ms >= MAX_MS) stop();
      }, 100);
    } catch (error) {
      releaseStream();
      setPhase(classify(error));
    }
  }, [busy, phase, releaseStream, scheduleRelease, stop]);

  const onPointerDown = (event: PointerEvent<HTMLButtonElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    void start();
  };
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== ' ' && event.key !== 'Enter') return;
    event.preventDefault();
    if (!event.repeat) void start();
  };
  const onKeyUp = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== ' ' && event.key !== 'Enter') return;
    event.preventDefault();
    stop();
  };

  if (phase === 'denied' || phase === 'no-mic' || phase === 'unsupported' || phase === 'insecure') {
    return <MicProblem phase={phase} onRetry={() => setPhase('idle')} />;
  }

  const recording = phase === 'recording';
  const remaining = Math.max(0, Math.ceil((MAX_MS - elapsed) / 1000));

  return (
    <div className="flex flex-col items-start gap-2">
      <button
        ref={buttonRef}
        type="button"
        disabled={busy}
        aria-pressed={recording}
        onPointerDown={onPointerDown}
        onPointerUp={stop}
        onPointerCancel={stop}
        onKeyDown={onKeyDown}
        onKeyUp={onKeyUp}
        onBlur={stop}
        onContextMenu={(event) => event.preventDefault()}
        className={buttonClasses(
          recording ? 'secondary' : 'primary',
          'lg',
          cn('touch-none', recording && 'border-verify'),
        )}
      >
        <MicIcon />
        {busy
          ? 'Working on your answer…'
          : recording
            ? 'Recording. Release to send'
            : phase === 'starting'
              ? 'Getting the microphone…'
              : 'Hold to ask a question'}
      </button>

      {recording ? (
        <p className="nums text-body-sm text-ink-50">
          {clock(elapsed)}
          {elapsed >= WARN_MS ? (
            <span className="ml-3 font-semibold">{remaining}s left, then it sends</span>
          ) : (
            <span className="ml-3 text-ink-200">30s maximum</span>
          )}
        </p>
      ) : hint ? (
        <p className="text-body-sm text-ink-200">{hint}</p>
      ) : (
        <p className="text-body-sm text-ink-200">
          Hold the button, ask, and let go. Space or Enter also works.
        </p>
      )}
      <p className="sr-only" role="status" aria-live="polite">
        {announcement}
      </p>
    </div>
  );
}

function MicProblem({ phase, onRetry }: { phase: Phase; onRetry: () => void }) {
  const content: Record<string, { title: string; steps: string[] }> = {
    denied: {
      title: 'Microphone access is blocked',
      steps: [
        'Click the lock or site-settings icon at the left of the address bar.',
        'Set Microphone to Allow for this site.',
        'Reload the page, then hold the button again.',
        'On iPhone and iPad: Settings, then Safari, then Microphone, and allow this site.',
      ],
    },
    'no-mic': {
      title: 'No microphone was found',
      steps: [
        'Plug in or enable a microphone, then try again.',
        'Check that another app is not holding it exclusively.',
      ],
    },
    unsupported: {
      title: 'This browser cannot record audio',
      steps: ['Use a current version of Chrome, Edge, Firefox or Safari.'],
    },
    insecure: {
      title: 'The microphone needs a secure connection',
      steps: ['Open this app over https, or from localhost.'],
    },
  };
  const info = content[phase] ?? content['denied'];
  return (
    <div role="alert" className="max-w-prose rounded-panel border border-ink-500 p-4">
      <h3 className="text-body font-semibold text-ink-50">{info?.title}</h3>
      <ol className="mt-2 list-decimal pl-5 text-body-sm text-ink-200">
        {info?.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      {phase === 'denied' || phase === 'no-mic' ? (
        <Button variant="secondary" size="sm" className="mt-3" onClick={onRetry}>
          Try the microphone again
        </Button>
      ) : null}
    </div>
  );
}
