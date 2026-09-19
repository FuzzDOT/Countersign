import { HeroSection } from "./HeroSection";

export default function LandingPage() {
  return (
    <div className="bg-ink-900 min-h-screen">
      <HeroSection />

      <section className="max-w-4xl mx-auto px-6 py-16 flex flex-col md:flex-row divide-y md:divide-y-0 md:divide-x divide-ink-500/40">
        {[
          "40 invoices a week.",
          "Nobody reads them for fraud.",
          "Enterprise tooling starts at five figures.",
        ].map((line) => (
          <p key={line} className="flex-1 text-h3 text-ink-50 px-6 py-4 text-center">
            {line}
          </p>
        ))}
      </section>

      <section className="max-w-5xl mx-auto px-6 py-16">
        <h2 className="text-display-2 text-ink-50 mb-10 text-center">How it works</h2>
        <div className="grid md:grid-cols-5 gap-6">
          {[
            "Extract entities and relations classically — no generative text.",
            "Score confidence and epistemic uncertainty with an evidential head.",
            "Gate: only the genuinely-uncertain tail escalates.",
            "Nemotron triages the escalated cases with a structured rationale.",
            "Brief it out loud, with every claim traced to its source.",
          ].map((step, i) => (
            <div key={step} className="flex flex-col gap-2">
              <span className="text-h1 text-verify font-mono">{i + 1}</span>
              <p className="text-body-sm text-ink-200">{step}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="max-w-4xl mx-auto px-6 py-20 text-center flex flex-col items-center gap-6">
        <h2 className="text-display-2 text-ink-50">See it on your own documents.</h2>
        
          <a
            href="/register"
          className="rounded-input bg-verify text-ink-900 px-6 py-3 font-medium hover:brightness-110 transition-colors duration-quick ease-out"
        >
          Create an account
        </a>
      </section>
    </div>
  );
}
