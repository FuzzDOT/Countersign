import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { HeroSection } from './HeroSection';
import { ThemeToggle } from '@/components/primitives/ThemeToggle';
import { buttonClasses } from '@/lib/buttonStyles';

function LogoMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="3" y="3" width="18" height="18" rx="3" stroke="currentColor" strokeWidth="1.6" />
      <path d="M8 12.5l2.5 2.5L16 9.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function NavItem({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="relative py-1 font-mono text-micro uppercase tracking-[0.14em] text-ink-200
                 transition-colors duration-quick ease-out hover:text-ink-50
                 after:absolute after:-bottom-0.5 after:left-0 after:right-0 after:h-px after:origin-left
                 after:scale-x-0 after:bg-verify after:transition-transform after:duration-quick after:ease-out
                 hover:after:scale-x-100"
    >
      {children}
    </Link>
  );
}

function LandingNav() {
  return (
    <header className="sticky top-0 z-30 border-b border-ink-500/40 bg-ink-900/85 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        <Link to="/" className="flex items-center gap-2.5 text-ink-50">
          <LogoMark />
          <span className="font-mono text-body-sm font-semibold uppercase tracking-[0.18em]">
            Countersign
          </span>
        </Link>

        <nav className="hidden items-center gap-9 md:flex">
          <NavItem to="/product">Product</NavItem>
          <NavItem to="/security">Security</NavItem>
        </nav>

        <div className="flex items-center gap-3">
          <ThemeToggle />
          <Link to="/register" className={buttonClasses('primary', 'sm')}>
            Get started
          </Link>
        </div>
      </div>
    </header>
  );
}

/** Numbers from the project's own eval targets. Swap for live values from
 *  GET /evals/* once the runs land — a placeholder shown as fact would
 *  undercut the whole "we report the real number" thesis. */
const EVIDENCE = [
  {
    figure: '0.62',
    label: 'Uncertainty predicts fragility',
    method:
      'Spearman correlation between our vacuity score and measured instability under adversarial perturbation.',
  },
  {
    figure: '87%',
    label: 'Resolved without an LLM',
    method: 'Only the 13% our classical model flags as genuinely uncertain is escalated to Nemotron.',
  },
  {
    figure: '1',
    label: 'Failure we found ourselves',
    method: 'Documented, explained, and shown on the evidence page rather than quietly dropped.',
  },
];

const APPROACH = [
  {
    n: '01',
    t: 'A claim, not a guess',
    d: 'Classical neural extraction only. The tagger cannot emit a token it was never trained to tag, which is the architectural guarantee behind no hallucination.',
  },
  {
    n: '02',
    t: 'Uncertainty that was tested',
    d: 'We adversarially attacked our own pipeline and measured whether our confidence score predicts real fragility. Then we published the number.',
  },
  {
    n: '03',
    t: 'A language model, used sparingly',
    d: 'Nemotron sees only the cases our classical model admits it does not understand. Everything else auto-files without an LLM call.',
  },
];

const PIPELINE = [
  { n: '01', t: 'Extract', d: 'A BiLSTM-CRF tagger and a graph attention network pull entities and relations. No generative text.' },
  { n: '02', t: 'Score', d: 'A Dirichlet evidential head separates genuine ambiguity from "never seen this before".' },
  { n: '03', t: 'Gate', d: 'Only the high-vacuity tail continues. The confident majority auto-files.' },
  { n: '04', t: 'Escalate', d: 'Nemotron triages the short list and returns a structured routing decision.' },
  { n: '05', t: 'Brief', d: 'A spoken summary, ranked by severity, every claim traced to its sentence.' },
];

const SECURITY_FACTS = [
  'argon2id password hashing',
  'rotating refresh tokens with reuse detection',
  'org-scoped queries at the data layer',
  'synthetic and public data only',
  'no API keys ever reach the browser',
  'rate limited on every sensitive path',
];

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-ink-900">
      <LandingNav />
      <HeroSection />

      {/* ── IVORY BAND: the approach + a dark inset dashboard card ───────── */}
      <section className="surface-ivory">
        <div className="mx-auto max-w-6xl px-6 py-24">
          <span className="eyebrow mb-12 inline-flex">The Countersign approach</span>

          <div className="grid gap-14 lg:grid-cols-2">
            {/* Dark inset card — the "portfolio dashboard" treatment */}
            <div className="flex flex-col gap-10">
              <div className="card-ink p-7">
                <div className="mb-6 flex items-baseline justify-between">
                  <span className="font-mono text-micro uppercase tracking-[0.16em] text-ink-200">
                    This week
                  </span>
                  <span className="font-mono text-micro text-ink-200">Q3 / 2026</span>
                </div>

                <p className="display-serif text-[2.75rem] text-ink-50">214</p>
                <p className="mt-1 text-body-sm text-ink-200">insights extracted</p>

                <hr className="my-6 border-0 border-t border-ink-050/10" />

                <dl className="flex flex-col gap-3">
                  {[
                    ['Auto-filed classically', '186'],
                    ['Flagged for review', '20'],
                    ['Escalated now', '8'],
                  ].map(([k, v]) => (
                    <div key={k} className="flex items-baseline justify-between">
                      <dt className="text-body-sm text-ink-200">{k}</dt>
                      <dd className="font-mono text-body-sm text-ink-50">{v}</dd>
                    </div>
                  ))}
                </dl>
              </div>

              <h2 className="display-serif text-[2.25rem] text-ivory-text">
                Analysis that considers
                <br />
                the whole of the record.
              </h2>
            </div>

            {/* Numbered approach list */}
            <div className="flex flex-col">
              {APPROACH.map((item, i) => (
                <div
                  key={item.n}
                  className={`flex gap-6 py-7 ${i !== 0 ? 'border-t border-ivory-rule' : ''}`}
                >
                  <span className="font-mono text-micro text-ivory-text/45">{item.n}</span>
                  <div className="flex flex-col gap-2">
                    <h3 className="text-h3 text-ivory-text">{item.t}</h3>
                    <p className="text-body-sm leading-relaxed text-ivory-text/70">{item.d}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ── INK: the problem, stated flatly ─────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-6 py-24">
        <div className="grid gap-px overflow-hidden rounded-panel border border-ink-500/50 bg-ink-500/50 md:grid-cols-3">
          {[
            '40 invoices a week.',
            'Nobody reads them for fraud.',
            'Enterprise tooling starts at five figures.',
          ].map((line) => (
            <div key={line} className="bg-ink-900 px-7 py-10">
              <p className="display-serif text-[1.5rem] text-ink-50">{line}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── INK: evidence ───────────────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-6 pb-24">
        <div className="mb-14 flex flex-col items-center gap-5 text-center">
          <span className="eyebrow">Evidence</span>
          <h2 className="display-serif max-w-2xl text-[2.25rem] text-ink-50 sm:text-[2.75rem]">
            Everyone else&rsquo;s confidence score is decoration. Ours is tested.
          </h2>
        </div>

        <div className="grid gap-5 md:grid-cols-3">
          {EVIDENCE.map((item) => (
            <div key={item.label} className="panel panel-hover flex flex-col gap-3 p-7">
              <span className="display-serif text-[3rem] text-verify">{item.figure}</span>
              <span className="text-body font-semibold text-ink-50">{item.label}</span>
              <span className="text-body-sm leading-relaxed text-ink-200">{item.method}</span>
            </div>
          ))}
        </div>
      </section>

      {/* ── IVORY BAND: the pipeline ────────────────────────────────────── */}
      <section className="surface-ivory">
        <div className="mx-auto max-w-5xl px-6 py-24">
          <div className="mb-14 flex flex-col items-center gap-5 text-center">
            <span className="eyebrow">Pipeline</span>
            <h2 className="display-serif text-[2.25rem] text-ivory-text sm:text-[2.75rem]">
              Five stages. One of them a language model.
            </h2>
          </div>

          <div className="flex flex-col">
            {PIPELINE.map((step, i) => (
              <div
                key={step.n}
                className={`flex flex-col gap-2 py-6 sm:flex-row sm:items-baseline sm:gap-8 ${
                  i !== 0 ? 'border-t border-ivory-rule' : ''
                }`}
              >
                <span className="font-mono text-micro text-ivory-text/45 sm:w-8">{step.n}</span>
                <span className="text-h3 text-ivory-text sm:w-40">{step.t}</span>
                <span className="flex-1 text-body-sm leading-relaxed text-ivory-text/70">{step.d}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── INK: the paper moment ───────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-6 py-24">
        <div className="panel grid items-stretch gap-0 overflow-hidden p-0 lg:grid-cols-2">
          <div className="flex flex-col gap-5 p-10">
            <span className="eyebrow self-start">Provenance</span>
            <h2 className="display-serif text-[1.875rem] text-ink-50">
              Click any claim. The document opens to the exact sentence.
            </h2>
            <p className="text-body-sm leading-relaxed text-ink-200">
              Byte offsets are tracked through tokenization, so every extracted triple carries the
              document, the character range, and the sentence it came from. Not a similarity score —
              a location.
            </p>
            <Link to="/product" className={`${buttonClasses('secondary', 'md')} mt-2 self-start`}>
              Walk through a real case
            </Link>
          </div>

          <div className="paper-surface px-10 py-12">
            <p className="text-reader-body">
              The transfer settled against invoice{' '}
              <span className="rounded-[2px] bg-[color-mix(in_srgb,var(--stamp-amber)_28%,transparent)] pb-0.5 [border-bottom:2px_solid_color-mix(in_srgb,var(--stamp-amber)_60%,transparent)]">
                INV-4471
              </span>
              , issued to a shared registered address in Wilmington, Delaware.
            </p>
            <p className="mt-6 font-mono text-micro text-paper-text/55">doc_4471 · chars 618–662</p>
          </div>
        </div>
      </section>

      {/* ── SAGE BAND: the considered perspective ──────────────────────── */}
      <section className="surface-sage">
        <div className="mx-auto max-w-3xl px-6 py-28 text-center">
          <span className="eyebrow mb-10 inline-flex">A considered perspective</span>
          <blockquote className="display-serif text-[2rem] leading-[1.25] sm:text-[2.5rem]">
            &ldquo;A confidence score you haven&rsquo;t attacked is a decoration. We attacked ours,
            and we will show you exactly where it bends.&rdquo;
          </blockquote>
        </div>
      </section>

      {/* ── INK: security posture ──────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-6 py-24">
        <div className="flex flex-col gap-10">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div className="flex flex-col gap-4">
              <span className="eyebrow self-start">Posture</span>
              <h2 className="display-serif text-[1.875rem] text-ink-50">
                Built like it holds real money.
              </h2>
            </div>
            <Link to="/security" className="text-body-sm text-verify hover:underline">
              Full security page
            </Link>
          </div>

          <div className="grid gap-x-12 gap-y-4 sm:grid-cols-2">
            {SECURITY_FACTS.map((fact) => (
              <div key={fact} className="flex items-baseline gap-3 border-b border-ink-500/40 pb-3">
                <span className="font-mono text-micro text-verify">✓</span>
                <span className="text-body-sm text-ink-200">{fact}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Closing CTA ─────────────────────────────────────────────────── */}
      <section className="hero-glow relative mx-auto mb-24 max-w-5xl overflow-hidden rounded-panel border border-ink-500/50 px-6 py-24">
        <div className="flex flex-col items-center gap-6 text-center">
          <h2 className="display-serif max-w-xl text-[2.25rem] text-ink-50 sm:text-[2.75rem]">
            See it on your own documents.
          </h2>
          <p className="max-w-md text-body-sm text-ink-200">
            Synthetic and public data only. Nothing you upload leaves your organization.
          </p>
          <Link to="/register" className={buttonClasses('primary', 'lg')}>
            Create an account
          </Link>
        </div>
      </section>

      <footer className="border-t border-ink-500/40">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-8 sm:flex-row">
          <div className="flex items-center gap-2.5 text-ink-200">
            <LogoMark />
            <span className="font-mono text-micro uppercase tracking-[0.18em]">Countersign</span>
          </div>
          <div className="flex items-center gap-6">
            <Link to="/product" className="font-mono text-micro text-ink-200 hover:text-ink-50">
              Product
            </Link>
            <Link to="/security" className="font-mono text-micro text-ink-200 hover:text-ink-50">
              Security
            </Link>
          </div>
          <p className="font-mono text-micro text-ink-200">SteelHacks XIII · synthetic data only</p>
        </div>
      </footer>
    </div>
  );
}
