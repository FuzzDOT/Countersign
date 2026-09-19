import '@testing-library/jest-dom/vitest';

// jsdom has no matchMedia; several components branch on prefers-reduced-motion.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// jsdom has no ResizeObserver; recharts and the graph canvas both need it.
global.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
