import { useEffect, useState } from 'react';
import { api } from '@/api/endpoints';

/**
 * The id of the org's most recent unfinished ingest job, or null.
 *
 * The shell needs this so the progress hairline survives navigation: the
 * ingest screen starts a job, you walk to the graph to watch it land, and the
 * bar should still be there. The ingest screen itself does not use this — it
 * has the job id from its own 202 and does not need to discover it.
 *
 * One request per mount, not a poll. Once a running job is found the
 * websocket owns every update after it, and when that job finishes this asks
 * once more in case a second upload started in the meantime.
 */
export function useActiveIngestJob(): string | null {
  const [jobId, setJobId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api.ingest
      .jobs(5)
      .then((page) => {
        if (cancelled) return;
        const running = page.data.find((job) => job.state !== 'done' && job.state !== 'failed');
        setJobId(running?.id ?? null);
      })
      // No job bar is the correct failure mode here: this is decoration on
      // top of whatever screen you are actually on.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  return jobId;
}
