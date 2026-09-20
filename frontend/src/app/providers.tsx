import { useState, type ReactNode } from 'react';
import { QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { isApiError, isRetryable } from '@/api/errors';
import { AuthProvider } from '@/auth/AuthProvider';
import { ToastViewport } from '@/components/primitives/Toast';
import { pushToast } from '@/lib/toastBus';
import { useTheme } from '@/hooks/useTheme';

function makeClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        // A screen that has nothing to show renders its own ErrorState. Only a
        // failed *background* refetch (data already on screen) needs a toast.
        if (query.state.data === undefined) return;
        if (
          isApiError(error) &&
          ['FORBIDDEN', 'TOKEN_EXPIRED', 'REFRESH_REUSED'].includes(error.code)
        )
          return;
        pushToast({
          tone: 'error',
          title: 'Could not refresh this view',
          description: isApiError(error) ? error.message : 'Unexpected error.',
          requestId: isApiError(error) ? error.requestId : null,
        });
      },
    }),
    defaultOptions: {
      queries: {
        // 10% of mock responses fail on purpose. Retry transport and 5xx errors
        // quietly; a 4xx will not get better by asking again.
        retry: (count, error) => count < 2 && isRetryable(error),
        retryDelay: (attempt) => Math.min(400 * 2 ** attempt, 3000),
        refetchOnWindowFocus: false,
      },
    },
  });
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(makeClient);
  // Mounted here so `data-theme` is applied once at the app root and every
  // token-driven surface follows, rather than each screen managing it.
  useTheme();
  return (
    <QueryClientProvider client={client}>
      <AuthProvider>
        {children}
        <ToastViewport />
      </AuthProvider>
    </QueryClientProvider>
  );
}
