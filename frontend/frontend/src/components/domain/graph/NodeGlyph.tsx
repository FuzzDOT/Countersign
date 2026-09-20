import { shapeFor } from '@/lib/graphEncoding';

interface NodeGlyphProps {
  entityType: string;
  r: number;
  fillOpacity: number;
  hasFlags: boolean;
  selected: boolean;
}

/**
 * One node's shape, centred on (0,0): ORG square, PERSON circle, ACCOUNT_REF
 * diamond, anything else a small circle. Flags draw a hairline outer ring in
 * ink-200. Selection is a --verify ring, which is an interactive state.
 */
export function NodeGlyph({ entityType, r, fillOpacity, hasFlags, selected }: NodeGlyphProps) {
  const kind = shapeFor(entityType);
  const common = {
    fill: 'var(--ink-050)',
    fillOpacity,
    stroke: selected ? 'var(--verify)' : 'var(--ink-900)',
    strokeWidth: selected ? 3 : 1,
  };
  return (
    <>
      {hasFlags ? (
        <circle r={r * 1.25 + 4} fill="none" stroke="var(--ink-200)" strokeWidth={1} />
      ) : null}
      {kind === 'square' ? (
        <rect x={-r * 0.9} y={-r * 0.9} width={r * 1.8} height={r * 1.8} {...common} />
      ) : kind === 'diamond' ? (
        <polygon points={`0,${-r * 1.2} ${r * 1.2},0 0,${r * 1.2} ${-r * 1.2},0`} {...common} />
      ) : (
        <circle r={r} {...common} />
      )}
    </>
  );
}
