import { useState, useEffect } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { useAuth } from "../../auth/useAuth";
import { api } from "../../api/endpoints";
import { isApiError, fieldErrors } from "../../api/errors";
import { Input } from "../../components/primitives/Input";
import { Button } from "../../components/primitives/Button";

export default function LoginPage() {
  const { signIn, notice } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [lockedUntil, setLockedUntil] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErrors({});
    setFormError(null);
    setPending(true);

    try {
      const response = await api.auth.login(email, password);
      await signIn(response);
      const next = params.get("next") ?? "/app/feed";
      navigate(next, { replace: true });
    } catch (err) {
      if (isApiError(err)) {
        if (err.code === "VALIDATION_FAILED") {
          setErrors(fieldErrors(err));
        } else if (err.code === "ACCOUNT_LOCKED") {
          const until = err.details["locked_until"];
          if (typeof until === "string") setLockedUntil(until);
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
    <div className="hero-glow grid-floor min-h-screen flex items-center justify-center bg-ink-900 px-6">
      <div className="panel w-full max-w-[420px] p-8 flex flex-col gap-5">
        <h1 className="text-h1 text-ink-50">Sign in</h1>

        {notice && (
          <p role="alert" className="text-body-sm text-stamp-amber">
            {notice}
          </p>
        )}
        {formError && (
          <p role="alert" className="text-body-sm text-stamp-red">
            {formError}
          </p>
        )}

        <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-4">
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
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={errors.password}
            required
          />

          {isLocked && lockedUntil && <LockoutCountdown lockedUntil={lockedUntil} />}

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

  useEffect(() => {
    const interval = setInterval(() => {
      setRemaining(Math.max(0, new Date(lockedUntil).getTime() - Date.now()));
    }, 1000);
    return () => clearInterval(interval);
  }, [lockedUntil]);

  const seconds = Math.ceil(remaining / 1000);

  return (
    <p role="alert" className="text-body-sm text-stamp-red font-mono">
      Too many attempts. Try again in {seconds}s.
    </p>
  );
}