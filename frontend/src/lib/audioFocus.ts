let current: HTMLAudioElement | null = null;

/**
 * Only one voice at a time. When a player starts, whatever else was playing
 * (the briefing while an answer starts, or the reverse) is paused, so two
 * voices never talk over each other.
 */
export function claimAudioFocus(element: HTMLAudioElement): void {
  if (current && current !== element) current.pause();
  current = element;
}
