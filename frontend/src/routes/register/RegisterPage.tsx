import { useState, useMemo } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useAuth } from "../../lib/auth-context";
import { ApiError } from "../../api/errors";
import { Input } from "../../components/primitives/Input";
import { Button } from "../../components/primitives/Button";

const COMMON_PASSWORDS = new Set(["password123", "12345678901", "qwertyuiop"]); // trimmed illustrative list

function checkPasswordRequirements(password: string) {
  return {
    length: password.length >= 12,
    notCommon: password.length > 0 && !COMMON_PASSWORDS.has(password.toLowerCase()),
  };
}

export default function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [orgName, setOrgName] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const requirements = useMemo(() => checkPasswordRequirements(password), [password]);
  const passwordsMatch = confirm.length > 0 && password === confirm;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFieldErrors({});
    setFormError(null);

    if (!passwordsMatch) {
      setFieldErrors({ confirm: "Passwords don't match" });
      return;
    }

    setPending(true);
    try {
      await register(email, password, orgName);
      navigate("/app/feed", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.code === "VALIDATION_FAILED" && err.details?.fields) {
        setFieldErrors(err.details.fields as Record<string, string>);
      } else if (err instanceof ApiError) {
        setFormError(err.message);
      } else {
        setFormError("Something went wrong. Try again.");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-900">
      <div className="w-[420px] panel p-8 flex flex-col gap-4">
        <h1 className="text-h1 text-ink-50">Create account</h1>

        {formError && (
          <p role="alert" className="text-body-sm text-stamp-red">
            {formError}
          </p>
        )}

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <Input
            label="Organization name"
            autoComplete="organization"
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
            error={fieldErrors.org_name}
            required
          />
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
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={fieldErrors.password}
            required
          />

          {/* Courtesy checklist only — server is the source of truth for these rules */}
          <ul className="text-body-sm flex flex-col gap-1">
            <li className={requirements.length ? "text-verify" : "text-ink-200"}>
              {requirements.length ? "✓" : "○"} At least 12 characters
            </li>
            <li className={requirements.notCommon ? "text-verify" : "text-ink-200"}>
              {requirements.notCommon ? "✓" : "○"} Not a common password
            </li>
          </ul>

          <Input
            label="Confirm password"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            error={fieldErrors.confirm}
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