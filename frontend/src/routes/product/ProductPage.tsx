import { useState } from "react";
import { Link } from "react-router-dom";
import { MarketingHeader } from "@/components/shell/MarketingHeader";
import { PaperSurface } from "../../components/primitives/PaperSurface";
import { CountUp } from "../../components/primitives/CountUp";
import { Badge } from "../../components/primitives/Badge";

const STEPS = ["Ingest", "Graph", "Gate", "Prove it"] as const;
type Step = (typeof STEPS)[number];

const DOCUMENT_TEXT =
  "Payment of $48,200 was routed through Advent Holdings on behalf of Meridian Supply LLC, dated September 14th. The transfer settled against invoice INV-4471, issued to a shared registered address in Wilmington, Delaware.";

// Entity spans for step 1 — char offsets into DOCUMENT_TEXT, illustrative for the demo.
// const ENTITY_SPANS = [
//   { start: 11, end: 18, type: "MONEY", label: "$48,200" },
//   { start: 39, end: 55, type: "ORG", label: "Advent Holdings" },
//   { start: 74, end: 92, type: "ORG", label: "Meridian Supply LLC" },
//   { start: 98, end: 112, type: "DATE", label: "September 14th" },
// ];

const ENTITY_COLORS: Record<string, string> = {
  MONEY: "text-verify",
  ORG: "text-stamp-amber",
  DATE: "text-ink-200",
};

const INSIGHTS = [
  { id: "1", subject: "Meridian Supply LLC", relation: "wired funds to", object: "Advent Holdings", vacuity: 0.62, routing: "escalate_now" as const },
  { id: "2", subject: "Advent Holdings", relation: "shares address with", object: "Meridian Supply LLC", vacuity: 0.58, routing: "flag_for_review" as const },
  { id: "3", subject: "INV-4471", relation: "invoiced by", object: "Meridian Supply LLC", vacuity: 0.08, routing: "auto_file" as const },
  { id: "4", subject: "Wilmington, DE", relation: "registered address of", object: "Advent Holdings", vacuity: 0.05, routing: "auto_file" as const },
]; 
const ENTITY_LABELS: { text: string; type: string }[] = [
  { text: "$48,200", type: "MONEY" },
  { text: "Advent Holdings", type: "ORG" },
  { text: "Meridian Supply LLC", type: "ORG" },
  { text: "September 14th", type: "DATE" },
];

function buildSpans(text: string) {
  const found = ENTITY_LABELS
    .map((entity) => {
      const start = text.indexOf(entity.text);
      return start === -1 ? null : { start, end: start + entity.text.length, type: entity.type };
    })
    .filter((s): s is { start: number; end: number; type: string } => s !== null)
    .sort((a, b) => a.start - b.start);
  return found;
}

const AUTO_FILED_COUNT = 186;

export default function ProductPage() {
  const [step, setStep] = useState<Step>("Ingest");

  return (
    <div className="bg-ink-900 min-h-screen">
      <MarketingHeader title="Product" />
      <div className="max-w-5xl mx-auto px-6 py-20 flex flex-col gap-10">
        <div>
          <h1 className="display-serif text-[2.5rem] text-ink-50">See it on real documents</h1>
          <p className="text-body text-ink-200 mt-3 max-w-prose">
            One real case, four stages. Jump to any step — nothing here autoplays.
          </p>
        </div>

        {/* Segmented control, not dots, per the brief. Solid fills so the
            inactive steps stay legible against the navy ground. */}
        <div className="inline-flex self-start overflow-hidden rounded-input border border-ink-200/40 bg-ink-700">
          {STEPS.map((s) => (
            <button
              key={s}
              onClick={() => setStep(s)}
              aria-pressed={step === s}
              className={`px-5 py-2.5 text-body-sm font-medium transition-colors duration-quick ease-out
                          ${step === s ? "bg-ink-50 text-ink-900" : "text-ink-50/80 hover:bg-ink-500/60 hover:text-ink-50"}`}
            >
              {s}
            </button>
          ))}
        </div>

        <div className="min-h-[420px]">
          {step === "Ingest" && <IngestStep />}
          {step === "Graph" && <GraphStep />}
          {step === "Gate" && <GateStep />}
          {step === "Prove it" && <ProveItStep />}
        </div>

        <div className="flex flex-wrap gap-3 border-t border-ink-500/40 pt-8">
          <Link
            to="/"
            className="inline-flex items-center gap-2 rounded-input border border-ink-200/40 bg-ink-700 px-5 py-2.5
                       text-body-sm font-semibold text-ink-50 transition-colors duration-quick ease-out
                       hover:border-verify hover:bg-ink-500/70"
          >
            Back to home
          </Link>
          <Link
            to="/register"
            className="inline-flex items-center gap-2 rounded-input bg-ink-50 px-5 py-2.5 text-body-sm
                       font-semibold text-ink-900 transition-all duration-quick ease-out
                       hover:-translate-y-0.5 hover:brightness-105"
          >
            Try it on your documents
          </Link>
        </div>
      </div>
    </div>
  );
}

function IngestStep() {
  const spans = buildSpans(DOCUMENT_TEXT);
  const segments: { text: string; type: string | null }[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start > cursor) segments.push({ text: DOCUMENT_TEXT.slice(cursor, span.start), type: null });
    segments.push({ text: DOCUMENT_TEXT.slice(span.start, span.end), type: span.type });
    cursor = span.end;
  }
  if (cursor < DOCUMENT_TEXT.length) segments.push({ text: DOCUMENT_TEXT.slice(cursor), type: null });

  return (
    <div className="flex flex-col gap-4">
      <p className="text-body-sm text-ink-200">
        Entity tags appear inline as the classical tagger reads the document. No generative text.
      </p>
      <PaperSurface className="max-w-2xl">
        <p className="reader-body text-paper-text">
          {segments.map((seg, i) =>
            seg.type ? (
              <span key={i} className={`font-medium ${ENTITY_COLORS[seg.type] ?? "text-paper-text"}`}>
                {seg.text}
                <span className="text-micro font-mono ml-1 opacity-60">{seg.type}</span>
              </span>
            ) : (
              <span key={i}>{seg.text}</span>
            )
          )}
        </p>
      </PaperSurface>
    </div>
  );
}

function GraphStep() {
  // A tiny hand-placed 3-node cycle, illustrative — not d3-force, this is a
  // fixed demo layout, not the real graph canvas (that's dev 2's build).
  return (
    <div className="flex flex-col gap-4">
      <p className="text-body-sm text-ink-200">
        Entities assemble into a graph. The three-node ownership cycle resolves and is emphasized.
      </p>
      <div className="panel p-8 flex items-center justify-center">
        <svg viewBox="0 0 400 300" className="w-full max-w-md">
          <g opacity="0.4">
            <circle cx="200" cy="60" r="30" className="stroke-stamp-red" strokeWidth="2" fill="none" />
          </g>
          <line x1="200" y1="90" x2="110" y2="220" stroke="var(--stamp-red)" strokeWidth="2" opacity="0.7" />
          <line x1="200" y1="90" x2="290" y2="220" stroke="var(--stamp-red)" strokeWidth="2" opacity="0.7" />
          <line x1="110" y1="220" x2="290" y2="220" stroke="var(--stamp-red)" strokeWidth="2" opacity="0.7" strokeDasharray="4 4" />

          <circle cx="200" cy="60" r="24" className="fill-ink-700 stroke-ink-200" strokeWidth="1.5" />
          <text x="200" y="65" textAnchor="middle" className="fill-ink-50 text-[10px]">Meridian</text>

          <circle cx="110" cy="220" r="24" className="fill-ink-700 stroke-ink-200" strokeWidth="1.5" />
          <text x="110" y="225" textAnchor="middle" className="fill-ink-50 text-[10px]">Advent</text>

          <rect x="255" y="196" width="70" height="48" rx="2" className="fill-ink-700 stroke-ink-200" strokeWidth="1.5" />
          <text x="290" y="225" textAnchor="middle" className="fill-ink-50 text-[10px]">INV-4471</text>
        </svg>
      </div>
      <p className="text-body-sm text-stamp-red">Cycle detected: Meridian ↔ Advent ↔ shared address</p>
    </div>
  );
}

function GateStep() {
  const [nemotronStamped, setNemotronStamped] = useState(false);
  const escalated = INSIGHTS.filter((i) => i.vacuity >= 0.3);

  return (
    <div className="flex flex-col gap-4">
      <p className="text-body-sm text-ink-200">
        Insights sort by vacuity. Low-uncertainty ones fold away; the high-uncertainty tail stays —
        that's the short list Nemotron actually sees.
      </p>

      <div className="panel px-4 py-3 text-body-sm text-ink-200">
        Auto-filed, {AUTO_FILED_COUNT} items — classically resolved, no LLM call.
      </div>

      <div className="flex flex-col gap-2">
        {escalated.map((insight) => (
          <div key={insight.id} className="panel px-4 py-3 flex items-center justify-between gap-4">
            <div className="text-body-sm text-ink-50">
              {insight.subject} <span className="text-ink-200">{insight.relation}</span> {insight.object}
            </div>
            <div className="flex items-center gap-3">
              <span className="text-body-sm text-ink-200 font-mono">vac {insight.vacuity.toFixed(2)}</span>
              {nemotronStamped && <Badge>{insight.routing.replace("_", " ")}</Badge>}
            </div>
          </div>
        ))}
      </div>

      <button
        onClick={() => setNemotronStamped(true)}
        disabled={nemotronStamped}
        className="self-start rounded-input bg-verify text-ink-900 px-4 py-2 text-body-sm font-medium
                   hover:brightness-110 disabled:opacity-50 disabled:cursor-not-allowed
                   transition-colors duration-quick ease-out"
      >
        {nemotronStamped ? "Nemotron decision applied" : "Run Nemotron on the short list"}
      </button>
    </div>
  );
}

function ProveItStep() {
  const [masked, setMasked] = useState(false);
  const [ran, setRan] = useState(false);

  const before = 0.81;
  const after = 0.4;

  function runAblation() {
    setRan(true);
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-body-sm text-ink-200">
        Zero the key edge and re-run inference. If confidence actually changes, the explanation is causal.
      </p>

      <div className="panel p-6 flex flex-col gap-4">
        <p className="text-body text-ink-50">
          Meridian Supply LLC{" "}
          <button
            type="button"
            onClick={() => !ran && setMasked((m) => !m)}
            aria-pressed={masked}
            disabled={ran}
            className={`underline underline-offset-2 transition-colors duration-quick ease-out disabled:cursor-default ${
              masked ? "text-ink-200 line-through" : "text-verify hover:brightness-110"
            }`}
          >
            wired funds to
          </button>{" "}
          Advent Holdings
        </p>

        <button
          onClick={runAblation}
          disabled={!masked || ran}
          className="self-start rounded-input bg-verify text-ink-900 px-4 py-2 text-body-sm font-medium
                     hover:brightness-110 disabled:opacity-50 disabled:cursor-not-allowed
                     transition-colors duration-quick ease-out"
        >
          Run ablation
        </button>

        <div className="flex items-baseline gap-2">
          <span className="text-h1 text-verify font-mono">
                        <CountUp
            value={ran ? after : before}
            from={before}
            duration={600}
            format={(n) => n.toFixed(2)}
            replayKey={ran ? "ablated" : "before"}
            />
          </span>
          <span className="text-body-sm text-ink-200">confidence</span>
        </div>

        {ran && (
          <p className="text-body text-ink-50">
            This connection is causally responsible for the flag.
          </p>
        )}
      </div>

      {ran && (
        <PaperSurface className="max-w-2xl">
          <p className="reader-body text-paper-text">
            Payment of $48,200 was routed through{" "}
            <span className="bg-stamp-red/20 border-b-2 border-stamp-red">Advent Holdings</span> on
            behalf of Meridian Supply LLC, dated September 14th.
          </p>
          <p className="text-body-sm text-ink-200 font-mono mt-2">
            traced to line 14, characters 412–501
          </p>
        </PaperSurface>
      )}
    </div>
  );
}