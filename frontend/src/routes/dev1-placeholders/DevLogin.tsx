import { useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '@/api/endpoints';
import { fieldErrors, isApiError } from '@/api/errors';
import { useAuth } from '@/auth/useAuth';
import { Button } from '@/components/primitives/Button';
import { Input } from '@/components/primitives/Input';

/**
 * PLACEHOLDER: Frontend dev 1 owns /login and /register (lockout countdown,
 * password checklist, autocomplete attributes, etc). This bare version exists
 * so the app can authenticate against the mock backend in development.
 */
export default function DevLogin() {
  const { signIn, status, notice } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState('ops@meridian.example');
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});

  const next = params.get('next');
  const destination = next?.startsWith('/app') ? next : '/app/feed';
  if (status === 'authenticated') return <Navigate to={destination} replace />;

  const submit = async () => {
    setPending(true);
    setError(null);
    setFields({});
    try {
      await signIn(await api.auth.login(email, password));
      navigate(destination, { replace: true });
    } catch (caught) {
      setFields(fieldErrors(caught));
      setError(isApiError(caught) ? caught.message : 'Sign in failed.');
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-ink-900 p-6">
      <div className="panel flex w-[420px] max-w-full flex-col gap-4 p-6">
        <h1 className="text-h2 text-ink-50">Sign in</h1>
        {notice ? <p className="text-body-sm text-ink-200">{notice}</p> : null}
        <Input label="Email" type="email" autoComplete="email" value={email} error={fields['email']} onChange={(e) => setEmail(e.target.value)} />
        <Input label="Password" type="password" autoComplete="current-password" value={password} error={fields['password']} onChange={(e) => setPassword(e.target.value)} />
        {error ? <p role="alert" className="text-body-sm text-ink-50">{error}</p> : null}
        <Button variant="primary" pending={pending} pendingLabel="Signing in…" onClick={() => void submit()}>
          Sign in
        </Button>
      </div>
    </div>
  );
}
