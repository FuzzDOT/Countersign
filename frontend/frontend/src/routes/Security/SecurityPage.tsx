import { Link } from "react-router-dom";
import { MarketingHeader } from "@/components/shell/MarketingHeader";

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
      <MarketingHeader title="Security" />
      <div className="max-w-3xl mx-auto px-6 py-20 flex flex-col gap-12">
        <div>
          <h1 className="display-serif text-[2.5rem] text-ink-50">Security &amp; data posture</h1>
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

        <div className="flex flex-wrap gap-3 border-t border-ink-500/40 pt-8">
          <Link
            to="/"
            className="inline-flex items-center gap-2 rounded-input border border-ctl-border bg-ctl-alt px-5 py-2.5
                       text-body-sm font-semibold text-ctl-alt-fg transition-colors duration-quick ease-out
                       hover:border-verify hover:bg-ctl-alt-hover"
          >
            Back to home
          </Link>
          <Link
            to="/product"
            className="inline-flex items-center gap-2 rounded-input border border-ctl-border bg-ctl-alt px-5 py-2.5
                       text-body-sm font-semibold text-ctl-alt-fg transition-colors duration-quick ease-out
                       hover:border-verify hover:bg-ctl-alt-hover"
          >
            See the walkthrough
          </Link>
        </div>
      </div>
    </div>
  );
}