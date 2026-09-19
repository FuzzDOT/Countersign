import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { Skeleton } from '@/components/primitives/Skeleton';
import { useAuth } from './useAuth';

/** Protected-route gate. Preserves the destination in ?next= (brief §5.5). */
export function RequireAuth() {
  const { status } = useAuth();
  const location = useLocation();
  if (status === 'loading') return <Skeleton className="m-6 h-10 w-64" />;
  if (status === 'anonymous') {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return <Outlet />;
}
