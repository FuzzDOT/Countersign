import { API_BASE } from '@/api/client';
import { api } from '@/api/endpoints';

/**
 * Audio loading for the voice console.
 *
 * `<audio src>` cannot send an Authorization header, so we fetch the file
 * ourselves (bearer token, refresh handling and all) and hand the element a
 * blob URL. That also gives us the bytes to decode a real waveform from, and
 * it means a preloaded fallback briefing is genuinely in memory before the
 * conference wifi dies (brief §12.3). If the fetch fails we fall back to
 * pointing the element at the URL directly, which works when it is signed.
 */

export interface LoadedAudio {
  src: string;
  /** The raw bytes, when we managed to fetch them. Null means "use src as a plain URL". */
  bytes: ArrayBuffer | null;
}

const cache = new Map<string, Promise<LoadedAudio>>();

/** `audio_url` is root-relative; if the API lives on another origin, anchor it there. */
export function resolveAudioUrl(audioUrl: string): string {
  if (/^https?:\/\//i.test(audioUrl)) return audioUrl;
  if (/^https?:\/\//i.test(API_BASE)) return `${new URL(API_BASE).origin}${audioUrl}`;
  return audioUrl;
}

export function loadAudio(audioUrl: string): Promise<LoadedAudio> {
  const cached = cache.get(audioUrl);
  if (cached) return cached;

  const url = resolveAudioUrl(audioUrl);
  const pending: Promise<LoadedAudio> = api.voice
    .audio(url)
    .then(({ data, contentType }) => ({
      src: URL.createObjectURL(new Blob([data], { type: contentType || 'audio/mpeg' })),
      bytes: data,
    }))
    .catch(() => {
      cache.delete(audioUrl); // let a later attempt try the authenticated fetch again
      return { src: url, bytes: null };
    });
  cache.set(audioUrl, pending);
  return pending;
}

/** Fire and forget: get the bytes into memory. */
export function preloadAudio(audioUrl: string): void {
  void loadAudio(audioUrl);
}

interface WebkitWindow {
  webkitAudioContext?: typeof AudioContext;
}

/**
 * Amplitude peaks for a static waveform strip. Returns null when decoding is
 * unavailable or too slow, in which case the caller draws an honest flat
 * placeholder. It never animates bars to fake a visualiser.
 */
export async function computePeaks(
  bytes: ArrayBuffer,
  bars: number,
  timeoutMs = 4000,
): Promise<number[] | null> {
  const Ctor = window.AudioContext ?? (window as unknown as WebkitWindow).webkitAudioContext;
  if (!Ctor) return null;
  const context = new Ctor();
  try {
    const decode = context.decodeAudioData(bytes.slice(0));
    const timeout = new Promise<null>((resolve) => {
      window.setTimeout(() => resolve(null), timeoutMs);
    });
    const buffer = await Promise.race([decode, timeout]);
    if (!buffer) return null;

    const channel = buffer.getChannelData(0);
    const size = Math.max(1, Math.floor(channel.length / bars));
    const stride = Math.max(1, Math.floor(size / 200));
    const peaks: number[] = [];
    for (let bar = 0; bar < bars; bar += 1) {
      let sum = 0;
      let count = 0;
      const from = bar * size;
      for (let i = from; i < from + size && i < channel.length; i += stride) {
        sum += Math.abs(channel[i] ?? 0);
        count += 1;
      }
      peaks.push(count > 0 ? sum / count : 0);
    }
    const max = Math.max(...peaks, 1e-6);
    return peaks.map((p) => p / max);
  } catch {
    return null;
  } finally {
    void context.close();
  }
}
