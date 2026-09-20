import type { Config } from 'tailwindcss';

/**
 * Tokens mirror src/styles/tokens.css — the CSS custom properties are the
 * source of truth so non-Tailwind contexts (SVG, D3, canvas) read the same
 * values. Do not hardcode a hex anywhere in a component.
 *
 * See frontend brief §2 for the discipline rules:
 *  - stamp-* means routing severity and nothing else
 *  - verify means interactive/confirmed and nothing else
 *  - confidence and vacuity are never encoded in colour
 */
/**
 * A theme colour that reads a CSS custom property AND supports Tailwind's
 * opacity modifier (`border-ink-500/40`, `bg-ink-700/60`). A bare `var(--x)`
 * string cannot be parsed by Tailwind 3, so the `/40` utilities would silently
 * not exist; `color-mix` gives the same result while the token stays the single
 * source of truth.
 */
/**
 * Tailwind accepts a function here at runtime, but its published types only
 * model `string | RecursiveKeyValuePair`. Rather than reach for `any` (which
 * trips no-unsafe-assignment on every call site), the function is declared
 * with its true signature and cast once, here, to the shape the theme
 * expects. One documented cast beats a dozen suppressions.
 */
function token(name: string): string {
  const fn = ({ opacityValue }: { opacityValue?: string | undefined }): string =>
    opacityValue === undefined
      ? `var(--${name})`
      : `color-mix(in srgb, var(--${name}) ${Number(opacityValue) * 100}%, transparent)`;
  return fn as unknown as string;
}

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          900: token('ink-900'),
          700: token('ink-700'),
          500: token('ink-500'),
          200: token('ink-200'),
          50: token('ink-050'),
        },
        paper: {
          DEFAULT: token('paper'),
          text: token('paper-text'),
          rule: token('paper-rule'),
        },
        stamp: {
          red: token('stamp-red'),
          amber: token('stamp-amber'),
          slate: token('stamp-slate'),
        },
        verify: token('verify'),
        ivory: {
          DEFAULT: token('ivory'),
          text: token('ivory-text'),
          rule: token('ivory-rule'),
        },
        sage: {
          DEFAULT: token('sage'),
          text: token('sage-text'),
        },
      },
      fontFamily: {
        sans: ['Instrument Sans', 'system-ui', 'sans-serif'],
        // Editorial display serif. Literata is already self-hosted and has a
        // real optical-size axis, so it doubles as the display face rather
        // than pulling in a fourth font file for the hero alone.
        display: ['Literata', 'Georgia', 'serif'],
        reader: ['Literata', 'Georgia', 'serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        micro: ['0.64rem', { lineHeight: '1.45', letterSpacing: '0.01em' }],
        'body-sm': ['0.8rem', { lineHeight: '1.5' }],
        body: ['1rem', { lineHeight: '1.55' }],
        h3: ['1.25rem', { lineHeight: '1.35', fontWeight: '600' }],
        h2: ['1.563rem', { lineHeight: '1.25', letterSpacing: '-0.005em', fontWeight: '600' }],
        h1: ['1.953rem', { lineHeight: '1.15', letterSpacing: '-0.01em', fontWeight: '600' }],
        'display-2': ['2.441rem', { lineHeight: '1.1', letterSpacing: '-0.015em', fontWeight: '600' }],
        'display-1': ['3.052rem', { lineHeight: '1.05', letterSpacing: '-0.02em', fontWeight: '600' }],
        'reader-sm': ['0.95rem', { lineHeight: '1.65' }],
        'reader-body': ['1.125rem', { lineHeight: '1.7' }],
      },
      borderRadius: {
        input: '9999px',
        panel: '20px',
      },
      boxShadow: {
        // Overlays only, never inline cards.
        overlay: '0 24px 48px -12px rgba(0, 0, 0, 0.6)',
        // The soft violet glow behind the primary CTA and the hero card.
        glow: '0 0 40px -8px color-mix(in srgb, var(--verify) 45%, transparent)',
      },
      transitionDuration: {
        instant: '120ms',
        quick: '220ms',
        move: '420ms',
        story: '900ms',
      },
      transitionTimingFunction: {
        out: 'cubic-bezier(0.16, 1, 0.3, 1)',
        inout: 'cubic-bezier(0.65, 0, 0.35, 1)',
      },
      maxWidth: {
        reader: '66ch',
        prose: '72ch',
      },
    },
  },
  plugins: [],
} satisfies Config;
