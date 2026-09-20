import { useState } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { useAuth } from "../../lib/auth-context";
import { ApiError } from "../../api/errors";
import { Input } from "../../components/primitives/Input";
import { Button } from "../../components/primitives/Button";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const notice = params.get("notice");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [lockedUntil, setLockedUntil] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFieldErrors({});
    setFormError(null);
    setPending(true);

    try {
      await login(email, password);
      const next = params.get("next") ?? "/app/feed";
      navigate(next, { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "VALIDATION_FAILED" && err.details?.fields) {
          setFieldErrors(err.details.fields as Record<string, string>);
        } else if (err.code === "ACCOUNT_LOCKED" && err.details?.locked_until) {
          setLockedUntil(err.details.locked_until as string);
        } else {
          setFormError(err.message);
        }
      } else {
        setFormError("Something went wrong. Try again.");
      }
    } finally {
      setPending(false);
    }
  }

  const isLocked = lockedUntil ? new Date(lockedUntil) > new Date() : false;

  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-900">
      <div className="w-[420px] panel p-8 flex flex-col gap-4">
        <h1 className="text-h1 text-ink-50">Sign in</h1>

        {notice === "session_ended" && (
          <p role="alert" className="text-body-sm text-stamp-amber">
            Your session was ended for security. Please sign in again.
          </p>
        )}
        {formError && (
          <p role="alert" className="text-body-sm text-stamp-red">
            {formError}
          </p>
        )}

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <Input
            label="Email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            error={fieldErrors.email}
            required
          />
          <Input
            label="Password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={fieldErrors.password}
            required
          />

          {isLocked && lockedUntil && (
            <LockoutCountdown lockedUntil={lockedUntil} />
          )}

          <Button type="submit" pending={pending} pendingLabel="Signing in…" disabled={isLocked}>
            Sign in
          </Button>
        </form>

        <p className="text-body-sm text-ink-200">
          No account?{" "}
          <Link to="/register" className="text-verify hover:underline">
            Create one
          </Link>
        </p>
      </div>
    </div>
  );
}

function LockoutCountdown({ lockedUntil }: { lockedUntil: string }) {
  const [remaining, setRemaining] = useState(() =>
    Math.max(0, new Date(lockedUntil).getTime() - Date.now())
  );

  useState(() => {
    const interval = setInterval(() => {
      setRemaining(Math.max(0, new Date(lockedUntil).getTime() - Date.now()));
    }, 1000);
    return () => clearInterval(interval);
  });

  const seconds = Math.ceil(remaining / 1000);

  return (
    <p role="alert" className="text-body-sm text-stamp-red font-mono">
      Too many attempts. Try again in {seconds}s.
    </p>
  );
}