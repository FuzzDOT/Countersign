import { useEffect, useState, useRef } from "react";
import { PaperSurface } from "../../components/primitives/PaperSurface";
import { CountUp } from "../../components/primitives/CountUp";

const STAGE_TIMES = [0, 400, 900, 1300, 1600, 1800, 2600];

export function HeroSection() {
  const [stage, setStage] = useState(0);
  const [runId, setRunId] = useState(0);
  const timeouts = useRef<number[]>([]);

  useEffect(() => {
    timeouts.current.forEach(clearTimeout);
    timeouts.current = [];

    const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (prefersReduced) {
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
    <section className="grid md:grid-cols-2 gap-12 items-center px-6 py-20 max-w-6xl mx-auto">
      <div className="flex flex-col gap-6">
        <h1
          className="text-display-1 text-ink-50 transition-[font-weight] duration-quick ease-out"
          style={{ fontWeight: stage >= 1 ? 600 : 500 }}
        >
          Every number in this report can prove where it came from.
        </h1>
        <p className="text-body text-ink-200 max-w-prose">
          COUNTERSIGN reads your invoices, vendor mail, and news feeds, and tells you what
          needs attention. Every claim traces back to the exact sentence it came from.
          Nothing is generated.
        </p>
        <div className="flex gap-4">
          
             <a
            href="/product"
             className="rounded-input bg-verify text-ink-900 px-4 py-2 font-medium hover:brightness-110 transition-colors duration-quick ease-out"
          >
            See it on real documents
          </a>
          
            <a
            href="/login"
            className="rounded-input border border-ink-500/40 text-ink-50 px-4 py-2 font-medium hover:bg-ink-500/20 transition-colors duration-quick ease-out"
          >
            Sign in
          </a>
        </div>
      </div>

      <div className="relative">
        <PaperSurface className="max-w-[480px]">
          <p className="reader-body text-paper-text">
            Payment of $48,200 was routed through{" "}
            <span
              className="relative"
              style={{
                backgroundImage:
                  stage >= 2
                    ? "linear-gradient(to right, rgba(184,120,44,0.35) 100%, transparent 0%)"
                    : "linear-gradient(to right, rgba(184,120,44,0.35) 0%, transparent 0%)",
                backgroundRepeat: "no-repeat",
                transition: "background-image var(--dur-quick) var(--ease-out)",
              }}
            >
              Advent Holdings
            </span>{" "}
            on behalf of Meridian Supply LLC, dated September 14th.
          </p>

          {stage >= 3 && (
            <svg className="absolute -right-6 top-1/2 w-12 h-1" aria-hidden="true">
              <line
                x1="0"
                y1="4"
                x2="48"
                y2="4"
                stroke="var(--ink-200)"
                strokeWidth="1"
                strokeDasharray="48"
                strokeDashoffset={0}
                style={{ transition: "stroke-dashoffset var(--dur-quick) var(--ease-out)" }}
              />
            </svg>
          )}
        </PaperSurface>

        {stage >= 4 && (
          <div
            className="mt-4 inline-flex items-center gap-2 rounded-panel bg-ink-700 border border-ink-500/40 px-4 py-3"
            style={{
              opacity: 1,
              transform: "scale(1)",
              transition: "opacity var(--dur-quick) var(--ease-out), transform var(--dur-quick) var(--ease-out)",
            }}
          >
            <span className="text-body-sm text-ink-50">
              Meridian Supply LLC <span className="text-ink-200">wired funds to</span> Advent Holdings
            </span>
          </div>
        )}

        {stage >= 5 && (
          <div className="mt-3 flex flex-col gap-1">
            <div className="flex items-baseline gap-2">
              <span className="text-h1 text-verify font-mono">
                <CountUp from={0} to={81} durationMs={500} decimals={0} suffix="%" />
              </span>
              <span className="text-body-sm text-ink-200">confidence</span>
            </div>
            {stage >= 6 && (
              <span className="text-body-sm text-ink-200 font-mono">
                traced to line 14, characters 412–501
              </span>
            )}
          </div>
        )}

        {done && (
          <button
            onClick={() => setRunId((n) => n + 1)}
            className="mt-4 text-body-sm text-verify hover:underline"
          >
            Replay
          </button>
        )}
      </div>
    </section>
  );
}
