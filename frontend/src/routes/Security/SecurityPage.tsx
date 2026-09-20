type SecurityControl = {
  control: string;
  whatItDoes: string;
  whyItMatters: string;
};

const CONTROLS: SecurityControl[] = [
  {
    control: "Argon2id password hashing",
    whatItDoes: "Passwords are hashed with Argon2id before storage, never stored in plain text.",
    whyItMatters: "Even a full database leak doesn't expose usable passwords.",
  },
  {
    control: "Rotating refresh tokens with reuse detection",
    whatItDoes: "Each refresh issues a new token and invalidates the old one; reusing a stale token ends the session.",
    whyItMatters: "A stolen refresh token has a single use before the theft is detected and the session is killed.",
  },
  {
    control: "Org-scoped queries",
    whatItDoes: "Every database query is filtered by organization ID at the query layer, not just the API layer.",
    whyItMatters: "One organization can never see another organization's documents or insights, even via a bug.",
  },
  {
    control: "Synthetic data only",
    whatItDoes: "All documents, invoices, and financial records in this system are synthetically generated or drawn from public sources.",
    whyItMatters: "No real account numbers, credentials, or financial records exist anywhere in this build.",
  },
  {
    control: "No API keys in the browser",
    whatItDoes: "ElevenLabs and Nemotron credentials live only on the backend; the frontend never sees them.",
    whyItMatters: "Inspecting network traffic or browser storage cannot leak an upstream API key.",
  },
  {
    control: "Rate limiting",
    whatItDoes: "Sensitive endpoints (ablation, recalibration, voice) are rate-limited per user.",
    whyItMatters: "A scripted or accidental request flood can't exhaust upstream quota or run up cost.",
  },
];

export default function SecurityPage() {
  return (
    <div className="bg-ink-900 min-h-screen">
      <div className="max-w-3xl mx-auto px-6 py-16 flex flex-col gap-10">
        <div>
          <h1 className="text-display-2 text-ink-50">Security &amp; data posture</h1>
          <p className="text-body text-ink-200 mt-3 max-w-prose">
            All data in this demo is synthetic or drawn from public sources. No real account
            numbers, credentials, or financial records are used anywhere in this system.
          </p>
        </div>

        <table className="w-full text-body-sm">
          <thead>
            <tr className="border-b border-ink-500/40 text-left text-ink-200">
              <th className="py-3 pr-4 font-normal w-1/4">Control</th>
              <th className="py-3 pr-4 font-normal w-2/5">What it does</th>
              <th className="py-3 font-normal w-2/5">Why it matters</th>
            </tr>
          </thead>
          <tbody>
            {CONTROLS.map((c) => (
              <tr key={c.control} className="border-b border-ink-500/40 last:border-0 align-top">
                <td className="py-4 pr-4 text-ink-50 font-medium">{c.control}</td>
                <td className="py-4 pr-4 text-ink-200">{c.whatItDoes}</td>
                <td className="py-4 text-ink-200">{c.whyItMatters}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="pt-6 border-t border-ink-500/40">
          <a href="/" className="text-verify hover:underline text-body-sm">
            ← Back to home
          </a>
        </div>
      </div>
    </div>
  );
}