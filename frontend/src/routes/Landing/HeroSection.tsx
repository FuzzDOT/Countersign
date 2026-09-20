import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { CountUp } from '@/components/primitives/CountUp';
import { buttonClasses } from '@/lib/buttonStyles';

/** Staged reveal, mirroring brief §5.1: the claim is pulled out of a
 *  document and proves where it came from. Runs once, then offers replay. */
const STAGE_TIMES = [0, 350, 800, 1200, 1550, 1800, 2500];

/**
 * The numbers in this hero are the seeded demo insight's real ones, not
 * decoration: confidence 0.59, routing flag_for_review, citation chars
 * 411–498 of INV-4471. Re-check them against
 * `GET /insights?q=Advent Holdings on behalf` after any retrain or re-seed —
 * a landing page quoting a score the app does not show is the one
 * inconsistency a judge can spot from the back of the room.
 */
function InsightChipCard() {
  return (
    <div
      className="pointer-events-none absolute -right-4 top-4 hidden w-52 rotate-[5deg] xl:block"
      style={{ animation: 'float-card 7s ease-in-out infinite' }}
      aria-hidden="true"
    >
      <div className="panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <span className="font-mono text-micro uppercase tracking-widest text-ink-200">
            INV-4471
          </span>
          <span className="h-1.5 w-1.5 rounded-full bg-stamp-amber" />
        </div>
        <div className="flex flex-col gap-1.5">
          <div className="bg-ink-50/12 h-1.5 w-full rounded-full" />
          <div className="bg-ink-50/12 h-1.5 w-4/5 rounded-full" />
          <div className="h-1.5 w-2/3 rounded-full bg-stamp-amber/35" />
        </div>
        <div className="mt-4 flex items-baseline justify-between">
          <span className="font-mono text-micro text-ink-200">flagged</span>
          <span className="font-mono text-body-sm text-verify">0.59</span>
        </div>
      </div>
    </div>
  );
}

function WaveformCard() {
  return (
    <div
      className="pointer-events-none absolute -left-6 top-40 hidden w-44 -rotate-[6deg] xl:block"
      style={{ animation: 'float-card 8s ease-in-out infinite 0.8s' }}
      aria-hidden="true"
    >
      <div className="panel p-3">
        <div className="flex h-10 items-end gap-[3px]">
          {[0.25, 0.55, 0.4, 0.85, 0.65, 0.45, 0.75, 0.35, 0.6, 0.3].map((h, i) => (
            <div
              key={i}
              className="flex-1 rounded-full bg-verify/60"
              style={{ height: `${h * 100}%` }}
            />
          ))}
        </div>
        <p className="mt-2 font-mono text-micro text-ink-200">spoken briefing</p>
      </div>
    </div>
  );
}

function CycleCard() {
  return (
    <div
      className="pointer-events-none absolute -right-10 bottom-8 hidden w-40 rotate-[4deg] 2xl:block"
      style={{ animation: 'float-card 9s ease-in-out infinite 1.4s' }}
      aria-hidden="true"
    >
      <div className="panel p-4">
        <svg viewBox="0 0 100 80" className="w-full" aria-hidden="true">
          <line
            x1="50"
            y1="16"
            x2="22"
            y2="60"
            stroke="var(--stamp-red)"
            strokeWidth="1.5"
            opacity="0.6"
          />
          <line
            x1="50"
            y1="16"
            x2="78"
            y2="60"
            stroke="var(--stamp-red)"
            strokeWidth="1.5"
            opacity="0.6"
          />
          <line
            x1="22"
            y1="60"
            x2="78"
            y2="60"
            stroke="var(--stamp-red)"
            strokeWidth="1.5"
            opacity="0.6"
            strokeDasharray="3 3"
          />
          <circle
            cx="50"
            cy="16"
            r="7"
            fill="var(--ink-500)"
            stroke="var(--ink-200)"
            strokeWidth="1"
          />
          <circle
            cx="22"
            cy="60"
            r="7"
            fill="var(--ink-500)"
            stroke="var(--ink-200)"
            strokeWidth="1"
          />
          <rect
            x="71"
            y="53"
            width="14"
            height="14"
            rx="2"
            fill="var(--ink-500)"
            stroke="var(--ink-200)"
            strokeWidth="1"
          />
        </svg>
        <p className="mt-2 font-mono text-micro text-ink-200">ownership cycle</p>
      </div>
    </div>
  );
}

export function HeroSection() {
  const [stage, setStage] = useState(0);
  const [runId, setRunId] = useState(0);
  const timeouts = useRef<number[]>([]);

  useEffect(() => {
    timeouts.current.forEach(clearTimeout);
    timeouts.current = [];

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setStage(STAGE_TIMES.length);
      return;
    }

    setStage(0);
    STAGE_TIMES.forEach((time, index) => {
      const id = window.setTimeout(() => setStage(index + 1), time);
      timeouts.current.push(id);
    });

    return () => timeouts.current.forEach(clearTimeout);
  }, [runId]);

  const done = stage >= STAGE_TIMES.length;

  return (
    <section className="hero-glow grid-floor relative overflow-hidden px-6 pb-28 pt-24">
      <div className="relative mx-auto flex max-w-3xl flex-col items-center gap-8 text-center">
        <span className="eyebrow">
          <span className="h-1.5 w-1.5 rounded-full bg-verify" />
          Nothing is generated
        </span>

        <h1
          className="display-serif text-gradient text-[2.5rem] sm:text-[3.4rem] lg:text-[4.1rem]"
          style={{ fontWeight: stage >= 1 ? 560 : 480 }}
        >
          Every number can prove
          <br />
          where it came from.
        </h1>

        <p className="max-w-xl text-body text-ink-200 sm:text-h3 sm:leading-relaxed">
          COUNTERSIGN reads your invoices, vendor mail, and news feeds, and tells you what needs
          attention — traced to the exact sentence it came from.
        </p>

        <div className="mt-2 flex flex-wrap items-center justify-center gap-3">
          <Link to="/product" className={buttonClasses('primary', 'lg')}>
            See it on real documents
          </Link>
          <Link to="/login" className={buttonClasses('secondary', 'lg')}>
            Sign in
          </Link>
        </div>
      </div>

      {/* The proof: a real document, a real span, a real byte range. */}
      <div className="relative mx-auto mt-20 max-w-2xl">
        <InsightChipCard />
        <WaveformCard />
        <CycleCard />

        <div className="panel overflow-hidden p-0">
          <div className="flex items-center justify-between border-b border-ink-500/40 px-5 py-3">
            <span className="font-mono text-micro uppercase tracking-widest text-ink-200">
              invoice · INV-4471
            </span>
            <span className="font-mono text-micro text-ink-200">14 Sep</span>
          </div>

          <div className="paper-surface px-7 py-8">
            <p className="text-reader-body">
              Payment of $48,200 was routed through{' '}
              <span
                className="relative rounded-[2px]"
                style={{
                  backgroundImage:
                    'linear-gradient(to right, color-mix(in srgb, var(--stamp-amber) 32%, transparent), color-mix(in srgb, var(--stamp-amber) 32%, transparent))',
                  backgroundRepeat: 'no-repeat',
                  backgroundSize: stage >= 2 ? '100% 100%' : '0% 100%',
                  borderBottom:
                    stage >= 2
                      ? '2px solid color-mix(in srgb, var(--stamp-amber) 65%, transparent)'
                      : 'none',
                  transition: 'background-size var(--dur-move) var(--ease-out)',
                }}
              >
                Advent Holdings
              </span>{' '}
              on behalf of Meridian Supply LLC, dated September 14th.
            </p>
          </div>

          <div className="flex flex-col gap-4 px-5 py-5">
            <div
              className="flex items-center gap-2 transition-all duration-move ease-out"
              style={{
                opacity: stage >= 4 ? 1 : 0,
                transform: stage >= 4 ? 'translateY(0)' : 'translateY(6px)',
              }}
            >
              <span className="text-body-sm text-ink-50">Meridian Supply LLC</span>
              <span className="font-mono text-micro text-ink-200">wired funds to</span>
              <span className="text-body-sm text-ink-50">Advent Holdings</span>
            </div>

            <div
              className="flex items-end justify-between transition-all duration-move ease-out"
              style={{ opacity: stage >= 5 ? 1 : 0 }}
            >
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-[2.5rem] font-semibold leading-none text-verify">
                  <CountUp value={59} from={0} duration={600} format={(n) => `${Math.round(n)}`} />
                  <span className="text-h3">%</span>
                </span>
                <span className="text-body-sm text-ink-200">confidence</span>
              </div>

              <span
                className="font-mono text-micro text-ink-200 transition-opacity duration-move ease-out"
                style={{ opacity: stage >= 6 ? 1 : 0 }}
              >
                chars 411–498
              </span>
            </div>
          </div>
        </div>

        {done && (
          <div className="mt-5 text-center">
            <button
              onClick={() => setRunId((n) => n + 1)}
              className="font-mono text-micro uppercase tracking-widest text-ink-200 transition-colors duration-quick ease-out hover:text-verify"
            >
              Replay
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
