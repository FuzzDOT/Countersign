import { useState, useMemo } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useAuth } from "../../auth/useAuth";
import { request } from "../../api/client";
import { isApiError, fieldErrors } from "../../api/errors";
import type { AuthResponse } from "../../api/types";
import { Input } from "../../components/primitives/Input";
import { Button } from "../../components/primitives/Button";

// GAP: no api.auth.register wrapper exists yet in endpoints.ts — confirm
// this exact path/shape with Faaz before relying on it further.
function registerAccount(email: string, password: string, orgName: string) {
  return request<AuthResponse>("/auth/register", {
    method: "POST",
    json: { email, password, org_name: orgName },
    auth: false,
  });
}

function checkPasswordRequirements(password: string) {
  return {
    length: password.length >= 12,
  };
}

export default function RegisterPage() {
  const { signIn } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [orgName, setOrgName] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const requirements = useMemo(() => checkPasswordRequirements(password), [password]);
  const passwordsMatch = confirm.length > 0 && password === confirm;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErrors({});
    setFormError(null);

    if (!passwordsMatch) {
      setErrors({ confirm: "Passwords don't match" });
      return;
    }

    setPending(true);
    try {
      const response = await registerAccount(email, password, orgName);
      await signIn(response);
      navigate("/app/feed", { replace: true });
    } catch (err) {
      if (isApiError(err) && err.code === "VALIDATION_FAILED") {
        setErrors(fieldErrors(err));
      } else if (isApiError(err)) {
        setFormError(err.message);
      } else {
        setFormError("Something went wrong. Try again.");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="relative min-h-screen flex items-center justify-center bg-ink-900 px-6">
      {/* Decoration only. Kept in its own layer because .grid-floor applies a
          mask-image that would otherwise fade the card itself. */}
      <div className="hero-glow grid-floor pointer-events-none absolute inset-0" aria-hidden="true" />
      <div className="relative z-10 w-full max-w-[420px] rounded-panel border border-card-border
                      bg-card p-8 flex flex-col gap-5
                      shadow-[0_28px_70px_-20px_rgba(0,0,0,0.75)]">
                <Link
          to="/"
          className="mb-2 inline-flex items-center gap-2 self-start rounded-input
                     border border-ctl-border bg-ctl-alt px-3.5 py-2
                     font-mono text-micro uppercase tracking-[0.14em] text-ctl-alt-fg
                     transition-colors duration-quick ease-out
                     hover:border-verify hover:bg-ctl-alt-hover"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true">
            <path d="M15 18l-6-6 6-6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Back to home
        </Link>
        <h1 className="display-serif text-[2rem] text-card-fg">Create account</h1>

        {formError && (
          <p role="alert" className="text-body-sm text-stamp-red">
            {formError}
          </p>
        )}

        <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-4">
          <Input
            label="Organization name"
            autoComplete="organization"
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
            error={errors.org_name}
            required
          />
          <Input
            label="Email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            error={errors.email}
            required
          />
          <Input
            label="Password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={errors.password}
            required
          />

          <ul className="text-body-sm flex flex-col gap-1">
            <li className={requirements.length ? "text-verify" : "text-ink-200"}>
              {requirements.length ? "✓" : "○"} At least 12 characters
            </li>
          </ul>

          <Input
            label="Confirm password"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            error={errors.confirm}
            required
          />

          <Button type="submit" pending={pending} pendingLabel="Creating account…">
            Create account
          </Button>
        </form>

        <p className="text-body-sm text-ink-200">
          Already have an account?{" "}
          <Link to="/login" className="text-verify hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}