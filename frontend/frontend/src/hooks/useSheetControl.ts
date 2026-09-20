import { useCallback } from 'react';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';

/**
 * The insight detail sheet is opened from several screens, and its open state
 * is always in the URL so it survives a reload (brief §3):
 *
 *   on the feed          /app/feed/:insightId       (deep link, list stays mounted)
 *   everywhere else      ?insight=:insightId        (graph, reader, evidence, voice)
 *
 * Callers do not care which; they get `insightId`, `open` and `close`.
 */
export function useSheetControl() {
  const params = useParams<{ insightId?: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();

  const onFeed = location.pathname.startsWith('/app/feed');
  const insightId = onFeed ? (params.insightId ?? null) : search.get('insight');

  const open = useCallback(
    (id: string, opts: { replace?: boolean } = {}) => {
      if (onFeed) {
        navigate(
          { pathname: `/app/feed/${encodeURIComponent(id)}`, search: location.search },
          { replace: opts.replace ?? false },
        );
      } else {
        const next = new URLSearchParams(search);
        next.set('insight', id);
        setSearch(next, { replace: opts.replace ?? false });
      }
    },
    [onFeed, navigate, location.search, search, setSearch],
  );

  const close = useCallback(() => {
    if (onFeed) {
      navigate({ pathname: '/app/feed', search: location.search }, { replace: false });
    } else {
      const next = new URLSearchParams(search);
      next.delete('insight');
      setSearch(next, { replace: false });
    }
  }, [onFeed, navigate, location.search, search, setSearch]);

  return { insightId, open, close };
}
