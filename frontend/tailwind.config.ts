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
const token =
  (name: string) =>
  ({ opacityValue }: { opacityValue?: string | undefined }) =>
    opacityValue === undefined
      ? `var(--${name})`
      : `color-mix(in srgb, var(--${name}) ${Number(opacityValue) * 100}%, transparent)`;

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
   colors: {
  ink: {
    900: 'rgb(var(--ink-900-rgb) / <alpha-value>)',
    700: 'rgb(var(--ink-700-rgb) / <alpha-value>)',
    500: 'rgb(var(--ink-500-rgb) / <alpha-value>)',
    200: 'rgb(var(--ink-200-rgb) / <alpha-value>)',
    50: 'rgb(var(--ink-050-rgb) / <alpha-value>)',
  },
  paper: {
    DEFAULT: 'var(--paper)',
    text: 'var(--paper-text)',
    rule: 'var(--paper-rule)',
  },
  stamp: {
    red: 'var(--stamp-red)',
    amber: 'var(--stamp-amber)',
    slate: 'var(--stamp-slate)',
  },
  verify: 'rgb(var(--verify-rgb) / <alpha-value>)',
},
      fontFamily: {
        sans: ['Instrument Sans', 'system-ui', 'sans-serif'],
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
        input: '2px',
        panel: '6px',
      },
      boxShadow: {
        // The only shadow. Overlays only, never inline cards.
        overlay: '0 24px 48px -12px rgba(6, 14, 22, 0.55)',
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
