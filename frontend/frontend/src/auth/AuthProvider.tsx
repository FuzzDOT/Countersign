import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/endpoints';
import { refreshSession } from '@/api/client';
import { onSessionEnded, setAccessToken } from '@/api/token';
import type { AuthResponse, MeResponse, Permission } from '@/api/types';
import { AuthContext, type AuthStatus, type AuthValue } from './authContext';

/**
 * Session state: one React context, the token in a module variable, nothing
 * persisted. On load it tries the HttpOnly refresh cookie once; if that
 * works the user never sees a login screen.
 *
 * Shared scaffolding. Frontend dev 1 owns the login and register screens and
 * protected routing that sit on top of this contract.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [me, setMe] = useState<MeResponse | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await refreshSession();
        const profile = await api.auth.me();
        if (!cancelled) {
          setMe(profile);
          setStatus('authenticated');
        }
      } catch {
        if (!cancelled) setStatus('anonymous');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(
    () =>
      onSessionEnded((reason) => {
        queryClient.clear();
        setMe(null);
        setStatus('anonymous');
        setNotice(
          reason === 'reused'
            ? 'Your session was ended for security. Please sign in again.'
            : 'Your session expired. Please sign in again.',
        );
      }),
    [queryClient],
  );

  const signIn = useCallback(async (response: AuthResponse) => {
    setAccessToken(response.access_token);
    const profile = await api.auth.me();
    setMe(profile);
    setNotice(null);
    setStatus('authenticated');
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.auth.logout();
    } catch {
      // Logging out must always succeed locally.
    }
    setAccessToken(null);
    queryClient.clear();
    setMe(null);
    setStatus('anonymous');
  }, [queryClient]);

  const value = useMemo<AuthValue>(
    () => ({
      status,
      me,
      notice,
      signIn,
      signOut,
      can: (permission: Permission) => me?.permissions.includes(permission) ?? false,
    }),
    [status, me, notice, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
