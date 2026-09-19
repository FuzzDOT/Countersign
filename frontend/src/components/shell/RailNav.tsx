import { NavLink } from "react-router-dom";
import { useRailState } from "../../hooks/useRailState";

type RailItem = {
  label: string;
  path: string;
  badge?: number | undefined;
};
export function RailNav({ escalateCount }: { escalateCount?: number | undefined }) 
{  const { expanded, toggle } = useRailState();

  const items: RailItem[] = [
    { label: "Feed", path: "/feed", badge: escalateCount },
    { label: "Graph", path: "/graph" },
    { label: "Evidence", path: "/evidence" },
    { label: "Voice", path: "/voice" },
    { label: "Ingest", path: "/ingest" },
    { label: "Settings", path: "/settings" },
  ];

  return (
    <nav
      className={`flex flex-col h-full bg-ink-900 text-ink-50 shrink-0
                  transition-[width] duration-quick ease-out
                  ${expanded ? "w-[var(--rail-expanded)]" : "w-[var(--rail-collapsed)]"}`}
    >
      <button
        onClick={toggle}
        aria-label={expanded ? "Collapse navigation" : "Expand navigation"}
        className="p-3 text-ink-200 hover:text-verify self-end"
      >
        {expanded ? "«" : "»"}
      </button>

      <ul className="flex flex-col gap-1">
        {items.map((item) => (
          <li key={item.path}>
            <NavLink
              to={item.path}
              className={({ isActive }) =>
                `flex items-center gap-3 px-4 py-3 relative
                 ${isActive ? "text-verify" : "text-ink-200 hover:text-ink-50"}`
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span className="absolute left-0 top-0 bottom-0 w-[3px] bg-verify" />
                  )}
                  <span className="truncate">{expanded ? item.label : item.label[0]}</span>
                  {item.badge != null && item.badge > 0 && (
                    <span className="ml-auto text-micro font-medium rounded-full px-2 bg-stamp-red text-ink-50">
                      {item.badge}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}