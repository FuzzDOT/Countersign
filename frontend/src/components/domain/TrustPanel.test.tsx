import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TrustPanel } from './TrustPanel';

describe('TrustPanel', () => {
  it('says "not yet measured" for a null fragility instead of showing 0', () => {
    render(
      <TrustPanel trust={{ confidence: 0.81, vacuity: 0.62, dissonance: 0.1, fragility: null }} />,
    );
    expect(screen.getByText('not yet measured')).toBeInTheDocument();
    expect(screen.queryByText('0.00')).not.toBeInTheDocument();
  });

  it('shows a real zero as a number, distinct from not measured', () => {
    render(
      <TrustPanel trust={{ confidence: 0.81, vacuity: 0.62, dissonance: 0.1, fragility: 0 }} />,
    );
    expect(screen.queryByText('not yet measured')).not.toBeInTheDocument();
    expect(screen.getByText('0.00')).toBeInTheDocument();
  });
});
