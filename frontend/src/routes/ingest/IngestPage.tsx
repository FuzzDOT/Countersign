import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/endpoints';
import { queryKeys } from '@/api/queries';
import type { DocSource, JobOut, UploadResponse } from '@/api/types';
import { useAuth } from '@/auth/useAuth';
import { Badge } from '@/components/primitives/Badge';
import { Button } from '@/components/primitives/Button';
import { EmptyState } from '@/components/primitives/EmptyState';
import { ErrorState } from '@/components/primitives/ErrorState';
import { Meter } from '@/components/primitives/Meter';
import { Select } from '@/components/primitives/Select';
import { isTerminal, useIngestJobSocket } from '@/hooks/useIngestJobSocket';

/**
 * Ingest (demo beat 2: "drop a document set, watch the graph assemble").
 *
 * Upload returns 202 and a job id; everything after that comes off the job
 * websocket, so the five pipeline stages advance on their own with no polling
 * loop in this component and no manual refresh.
 */

// Mirrors DocSource in api/v1/schemas.py. The server rejects anything else —
// it is a routing signal for the tagger, not a free-text label.
const SOURCES: readonly { value: DocSource; label: string }[] = [
  { value: 'invoice', label: 'Invoice' },
  { value: 'email', label: 'Vendor email' },
  { value: 'press_release', label: 'Press release' },
  { value: 'rss', label: 'RSS item' },
  { value: 'gdelt', label: 'GDELT signal' },
  { value: 'note', label: 'Internal note' },
  { value: 'transaction_log', label: 'Transaction log' },
];

// Server-side caps (MAX_UPLOAD_BYTES, MAX_UPLOAD_FILES). Checked here too so a
// 2.1 MB file is refused before it is uploaded rather than after.
const MAX_FILES = 50;
const MAX_BYTES = 2 * 1024 * 1024;

const STAGES = ['tagging', 'parsing', 'relating', 'scoring', 'routing'] as const;

const STAGE_LABELS: Record<(typeof STAGES)[number], string> = {
  tagging: 'Tagging entities',
  parsing: 'Parsing sentences',
  relating: 'Extracting relations',
  scoring: 'Scoring confidence',
  routing: 'Routing decisions',
};

function stateLabel(job: JobOut): string {
  if (job.state === 'failed') return 'Failed';
  if (job.state === 'done') return 'Done';
  if (job.state === 'queued') return 'Queued';
  return 'Running';
}

function formatBytes(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function IngestPage() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);

  const [files, setFiles] = useState<File[]>([]);
  const [source, setSource] = useState<DocSource>('invoice');
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [rejected, setRejected] = useState<string[]>([]);
  const [receipt, setReceipt] = useState<UploadResponse | null>(null);

  const { job, transport } = useIngestJobSocket(receipt?.job_id ?? null);
  const canUpload = can('documents:upload');

  // The feed, the stats header and the graph are all stale the moment a job
  // finishes. Invalidate once, on the transition, rather than on every frame.
  const settled = isTerminal(job);
  useEffect(() => {
    if (!settled) return;
    void queryClient.invalidateQueries({ queryKey: ['insights'] });
    void queryClient.invalidateQueries({ queryKey: ['graph'] });
    void queryClient.invalidateQueries({ queryKey: queryKeys.insightStats() });
  }, [settled, queryClient]);

  const accept = useCallback((incoming: FileList | null) => {
    if (!incoming) return;
    setError(null);
    const tooBig: string[] = [];
    const kept: File[] = [];
    for (const file of Array.from(incoming)) {
      if (file.size > MAX_BYTES) tooBig.push(`${file.name} (${formatBytes(file.size)})`);
      else kept.push(file);
    }
    setRejected(tooBig);
    setFiles((current) => {
      const byName = new Map(current.map((file) => [file.name, file]));
      for (const file of kept) byName.set(file.name, file);
      return Array.from(byName.values()).slice(0, MAX_FILES);
    });
  }, []);

  const submit = async () => {
    if (!files.length) return;
    setUploading(true);
    setError(null);
    setReceipt(null);
    try {
      setReceipt(await api.documents.upload(files, source));
      setFiles([]);
    } catch (cause) {
      setError(cause);
    } finally {
      setUploading(false);
    }
  };

  const totalBytes = useMemo(() => files.reduce((sum, file) => sum + file.size, 0), [files]);

  if (!canUpload) {
    return (
      <EmptyState title="You cannot upload documents">
        Ask an owner for the <span className="font-mono">documents:upload</span> permission.
      </EmptyState>
    );
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-10 px-6 py-10">
      <div>
        <h1 className="text-display-2 text-ink-50">Ingest</h1>
        <p className="mt-1 max-w-prose text-body-sm text-ink-200">
          Drop invoices, vendor mail or news items. Every insight they produce keeps a byte-exact
          pointer back to the sentence it came from.
        </p>
      </div>

      <section className="flex flex-col gap-4">
        <div
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            accept(event.dataTransfer.files);
          }}
          className={[
            'rounded-panel border border-dashed p-10 text-center transition-colors duration-quick ease-out',
            dragging ? 'border-verify bg-ink-700' : 'border-ink-500',
          ].join(' ')}
        >
          <p className="text-body text-ink-50">Drop files here</p>
          <p className="mt-1 text-body-sm text-ink-200">
            .txt, .md, .csv or .pdf · up to {MAX_FILES} files · 2 MB each
          </p>
          <Button className="mt-4" onClick={() => inputRef.current?.click()}>
            Choose files
          </Button>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".txt,.md,.csv,.pdf,text/plain,text/markdown,text/csv,application/pdf"
            className="sr-only"
            onChange={(event) => {
              accept(event.target.files);
              // Reset so re-picking the same file fires onChange again.
              event.target.value = '';
            }}
          />
        </div>

        {rejected.length ? (
          <p className="text-body-sm text-stamp-red">
            Too large, not queued: {rejected.join(', ')}
          </p>
        ) : null}

        {files.length ? (
          <div className="flex flex-col gap-3">
            <ul className="divide-y divide-ink-500 rounded-panel border border-ink-500">
              {files.map((file) => (
                <li key={file.name} className="flex items-center justify-between gap-4 px-4 py-2">
                  <span className="truncate text-body-sm text-ink-50">{file.name}</span>
                  <span className="shrink-0 text-body-sm text-ink-200">
                    {formatBytes(file.size)}
                  </span>
                </li>
              ))}
            </ul>

            <div className="flex flex-wrap items-end justify-between gap-4">
              <Select
                label="Source type"
                options={SOURCES}
                value={source}
                onChange={(value) => setSource(value as DocSource)}
              />
              <div className="flex items-center gap-3">
                <span className="text-body-sm text-ink-200">
                  {files.length} file{files.length === 1 ? '' : 's'}, {formatBytes(totalBytes)}
                </span>
                <Button onClick={() => setFiles([])}>Clear</Button>
                <Button
                  variant="primary"
                  onClick={() => void submit()}
                  pending={uploading}
                  pendingLabel="Uploading…"
                >
                  Ingest {files.length} file{files.length === 1 ? '' : 's'}
                </Button>
              </div>
            </div>
          </div>
        ) : null}

        {Boolean(error) && <ErrorState error={error} compact />}
      </section>

      {receipt ? (
        <section className="flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <h2 className="text-h2 text-ink-50">Pipeline</h2>
            {job ? <Badge>{stateLabel(job)}</Badge> : <Badge tone="outline">Connecting</Badge>}
            {transport === 'polling' ? (
              <Badge tone="outline" title="The websocket did not open; falling back to polling">
                polling
              </Badge>
            ) : null}
          </div>

          <p className="text-body-sm text-ink-200">
            {receipt.documents.length} document{receipt.documents.length === 1 ? '' : 's'} accepted
            {receipt.duplicates_skipped > 0
              ? `, ${receipt.duplicates_skipped} already ingested and skipped`
              : ''}
            .
          </p>

          <div className="flex flex-col gap-3 rounded-panel border border-ink-500 p-5">
            {STAGES.map((stage) => {
              const value = job?.stage_progress[stage] ?? 0;
              return (
                <div key={stage} className="flex items-center justify-between gap-4">
                  <span className="text-body-sm text-ink-50">{STAGE_LABELS[stage]}</span>
                  <div className="flex items-center gap-3">
                    <span className="nums w-10 text-right text-body-sm text-ink-200">
                      {Math.round(value * 100)}%
                    </span>
                    <Meter value={value} label={STAGE_LABELS[stage]} width={180} />
                  </div>
                </div>
              );
            })}
          </div>

          {job?.state === 'failed' ? (
            <p className="text-body-sm text-stamp-red">
              {job.error ?? 'The pipeline failed. Nothing was added to the feed.'}
            </p>
          ) : null}

          {job?.state === 'done' ? (
            <div className="flex items-center gap-3">
              <p className="text-body text-ink-50">
                {job.insights_found} insight{job.insights_found === 1 ? '' : 's'} from{' '}
                {job.docs_done} document{job.docs_done === 1 ? '' : 's'}.
              </p>
              <Button variant="primary" onClick={() => navigate('/app/feed')}>
                Open the feed
              </Button>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
