import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import type { BriefingResponse } from '@/api/types';
import { IconButton } from '@/components/primitives/IconButton';
import { PauseIcon, PlayIcon } from '@/components/primitives/icons';
import { SegmentedControl } from '@/components/primitives/SegmentedControl';
import { claimAudioFocus } from '@/lib/audioFocus';
import { computePeaks, loadAudio } from '@/lib/audio';
import { cn } from '@/lib/cn';
import { formatClock } from '@/lib/format';
import { TranscriptSync } from './TranscriptSync';

const BAR_COUNT = 48;
const RATES = [
  { value: '0.75', label: '0.75\u00d7' },
  { value: '1', label: '1\u00d7' },
  { value: '1.25', label: '1.25\u00d7' },
] as const;
type Rate = (typeof RATES)[number]['value'];

interface BriefingPlayerProps {
  briefing: BriefingResponse;
  /** The insight cited by the segment being spoken right now, or null. */
  onActiveInsight: (insightId: string | null) => void;
}

/**
 * Custom transport over a hidden <audio> element: play/pause, seek, and
 * 0.75x/1x/1.25x speed, because the native control is ugly and the transcript
 * sync needs the clock anyway (brief §12.1).
 *
 * The waveform is a static amplitude strip decoded from the real audio, with a
 * playhead. If decoding fails or is too slow it is a flat, honest placeholder.
 * Bars are never animated to fake a visualiser: that would be a bad joke in a
 * product about provenance.
 */
export function BriefingPlayer({ briefing, onActiveInsight }: BriefingPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [peaks, setPeaks] = useState<number[] | null>(null);
  const [audioFailed, setAudioFailed] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [currentMs, setCurrentMs] = useState(0);
  const [durationMs, setDurationMs] = useState(briefing.duration_ms);
  const [rate, setRate] = useState<Rate>('1');

  const onActiveRef = useRef(onActiveInsight);
  onActiveRef.current = onActiveInsight;

  // Fetch the audio with our auth (an <audio> tag cannot send a bearer token)
  // and decode peaks from the same bytes.
  useEffect(() => {
    let cancelled = false;
    setSrc(null);
    setPeaks(null);
    setAudioFailed(false);
    void loadAudio(briefing.audio_url).then(async (loaded) => {
      if (cancelled) return;
      setSrc(loaded.src);
      if (loaded.bytes) {
        const decoded = await computePeaks(loaded.bytes, BAR_COUNT);
        if (!cancelled) setPeaks(decoded);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [briefing.audio_url]);

  useEffect(() => {
    if (audioRef.current) audioRef.current.playbackRate = Number(rate);
  }, [rate, src]);

  const active = useMemo(
    () => briefing.transcript.find((s) => currentMs >= s.start_ms && currentMs < s.end_ms) ?? null,
    [briefing.transcript, currentMs],
  );
  const activeInsight = active?.insight_id ?? null;

  useEffect(() => {
    onActiveRef.current(activeInsight);
  }, [activeInsight]);
  useEffect(() => () => onActiveRef.current(null), []);

  const audio = () => audioRef.current;

  const syncFromElement = () => {
    const el = audio();
    if (el) setCurrentMs(Math.round(el.currentTime * 1000));
  };

  const toggle = () => {
    const el = audio();
    if (!el) return;
    if (el.paused) void el.play().catch(() => setPlaying(false));
    else el.pause();
  };

  const seek = (ms: number, thenPlay = false) => {
    const el = audio();
    if (!el) return;
    const clamped = Math.max(0, Math.min(ms, durationMs));
    el.currentTime = clamped / 1000;
    setCurrentMs(clamped);
    if (thenPlay && el.paused) void el.play().catch(() => setPlaying(false));
  };

  const onWaveformClick = (event: MouseEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width > 0) seek(((event.clientX - rect.left) / rect.width) * durationMs);
  };

  const progress = durationMs > 0 ? Math.min(1, currentMs / durationMs) : 0;
  const bars = peaks ?? Array.from({ length: BAR_COUNT }, () => 0.3);

  return (
    <div className="flex flex-col gap-4">
      <div className="panel flex flex-col gap-3 p-4">
        {/* The audio element is hidden; the transport below drives it. */}
        {/* eslint-disable-next-line jsx-a11y/media-has-caption -- the full transcript is always shown on the page */}
        <audio
          ref={audioRef}
          src={src ?? undefined}
          preload="auto"
          autoPlay
          onPlay={() => {
            setPlaying(true);
            if (audioRef.current) claimAudioFocus(audioRef.current);
          }}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onTimeUpdate={syncFromElement}
          onSeeked={syncFromElement}
          onLoadedMetadata={() => {
            const d = audio()?.duration;
            if (d && Number.isFinite(d)) setDurationMs(Math.round(d * 1000));
          }}
          onError={() => setAudioFailed(true)}
        />

        <div
          role="presentation"
          onClick={onWaveformClick}
          className="relative flex h-14 cursor-pointer items-center gap-[2px]"
        >
          {bars.map((peak, index) => (
            <span
              key={index}
              aria-hidden="true"
              className={cn(
                'flex-1 rounded-[1px]',
                (index + 0.5) / BAR_COUNT <= progress ? 'bg-ink-50' : 'bg-ink-500',
                !peaks && 'opacity-50',
              )}
              style={{ height: `${Math.max(8, peak * 100)}%` }}
            />
          ))}
          <span
            aria-hidden="true"
            className="absolute inset-y-0 w-0.5 bg-ink-50"
            style={{ left: `${progress * 100}%` }}
          />
        </div>
        {!peaks && src ? (
          <p className="text-body-sm text-ink-200">
            Waveform unavailable for this audio. The transport still works.
          </p>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          <IconButton
            label={playing ? 'Pause briefing' : 'Play briefing'}
            onClick={toggle}
            disabled={!src || audioFailed}
            className="bg-ink-500/40"
          >
            {playing ? <PauseIcon /> : <PlayIcon />}
          </IconButton>
          <span className="nums text-body-sm text-ink-50">
            {formatClock(currentMs)}{' '}
            <span className="text-ink-200">of {formatClock(durationMs)}</span>
          </span>
          <div className="range min-w-32 flex-1">
            <div className="range__track" />
            <div className="range__fill" style={{ left: 0, width: `${progress * 100}%` }} />
            <input
              type="range"
              aria-label="Seek"
              aria-valuetext={`${formatClock(currentMs)} of ${formatClock(durationMs)}`}
              min={0}
              max={Math.max(durationMs, 1)}
              step={100}
              value={Math.min(currentMs, Math.max(durationMs, 1))}
              onChange={(event) => seek(Number(event.target.value))}
            />
          </div>
          <SegmentedControl
            ariaLabel="Playback speed"
            size="sm"
            options={RATES}
            value={rate}
            onChange={setRate}
          />
        </div>

        {audioFailed ? (
          <p role="alert" className="text-body-sm text-ink-50">
            The audio could not be played. The transcript below is complete.
          </p>
        ) : null}
      </div>

      <TranscriptSync
        segments={briefing.transcript}
        activeId={active?.segment_id ?? null}
        playing={playing}
        onSeek={(ms) => seek(ms, true)}
      />
    </div>
  );
}
