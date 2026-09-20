import { useSearchParams } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import { CalibrationTab } from '@/components/domain/evidence/CalibrationTab';
import { FragilityTab } from '@/components/domain/evidence/FragilityTab';
import { RoutingTab } from '@/components/domain/evidence/RoutingTab';
import { InsightSheet } from '@/components/domain/InsightSheet';
import { EmptyState } from '@/components/primitives/EmptyState';
import { TabPanel, Tabs } from '@/components/primitives/Tabs';
import { useSheetControl } from '@/hooks/useSheetControl';

const TABS = [
  { id: 'fragility', label: 'Fragility' },
  { id: 'routing', label: 'Routing' },
  { id: 'calibration', label: 'Calibration' },
] as const;

type TabId = (typeof TABS)[number]['id'];

function parseTab(value: string | null): TabId {
  return TABS.find((t) => t.id === value)?.id ?? 'fragility';
}

/**
 * /app/evidence with the active tab in ?tab=. Clicking a chart point or a table
 * row opens that insight's detail sheet over the dashboard, so the charts are
 * navigable, not decorative.
 */
export default function EvidenceRoute() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const sheet = useSheetControl();
  const tab = parseTab(params.get('tab'));

  if (!can('evals:read')) {
    return (
      <EmptyState title="You do not have access to the evidence dashboards">
        Ask an owner to grant access.
      </EmptyState>
    );
  }

  const setTab = (next: TabId) =>
    setParams(
      (current) => {
        const copy = new URLSearchParams(current);
        copy.set('tab', next);
        copy.delete('insight');
        return copy;
      },
      { replace: true },
    );

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-5 py-6">
        <header>
          <h1 className="text-display-2 text-ink-50">Evidence</h1>
          <p className="mt-1 max-w-prose text-body text-ink-200">
            The measurements behind the claim that you can trust what the feed says.
          </p>
        </header>

        <Tabs tabs={TABS} value={tab} onChange={setTab} ariaLabel="Evidence dashboards" />

        <TabPanel id={tab}>
          {tab === 'fragility' ? (
            <FragilityTab onOpenInsight={(id) => sheet.open(id)} />
          ) : tab === 'routing' ? (
            <RoutingTab onOpenInsight={(id) => sheet.open(id)} />
          ) : (
            <CalibrationTab />
          )}
        </TabPanel>
      </div>

      <InsightSheet insightId={sheet.insightId} onClose={sheet.close} />
    </div>
  );
}
