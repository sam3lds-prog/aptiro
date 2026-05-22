import { NavLink } from "react-router-dom";
import { cn } from "@/lib/cn";
import { useAuth } from "@/stores/auth";

const NAV: { to: string; label: string; section?: string }[] = [
  { to: "/",               label: "Dashboard" },
  { to: "/vault",          label: "Profile Vault" },
  { to: "/strategy",       label: "Strategy" },
  // Phase 5: Jobs + Match Inbox + Saved Searches grouped together
  { to: "/matches",        label: "Match Inbox",    section: "Jobs" },
  { to: "/jobs",           label: "Jobs" },
  { to: "/saved-searches", label: "Saved Searches" },
  // Packages + Tracker
  { to: "/packages",       label: "Packages",       section: "Apply" },
  { to: "/tracker",        label: "Tracker" },
  { to: "/apply",          label: "Apply" },
  // Other
  { to: "/activity",       label: "Activity",       section: "Other" },
  { to: "/privacy",        label: "Privacy" },
];

export function Nav() {
  const me = useAuth((s) => s.me);
  const signOut = useAuth((s) => s.signOut);
  const showLogout = !!me && !me.is_default;

  let lastSection = "";

  return (
    <aside className="w-56 shrink-0 border-r border-line bg-panel/80 backdrop-blur-sm sticky top-0 h-screen flex flex-col">
      {/* Brand */}
      <div className="px-5 pt-6 pb-5 border-b border-line">
        <div className="font-display text-[26px] font-bold tracking-tight leading-none">
          Aptiro
        </div>
        <div className="text-[10.5px] text-sub mt-1.5 tracking-wide uppercase">
          evidence-backed applications
        </div>
      </div>

      {/* Nav links */}
      <nav className="flex-1 px-2.5 py-3 space-y-0.5 overflow-y-auto">
        {NAV.map(({ to, label, section }) => {
          const showSection = section && section !== lastSection;
          if (showSection) lastSection = section!;
          return (
            <div key={to}>
              {showSection && (
                <div className="eyebrow px-3 pt-3 pb-1">{section}</div>
              )}
              <NavLink
                to={to}
                end={to === "/"}
                className={({ isActive }) =>
                  cn(
                    "block px-3 py-2 rounded-md text-[13px] transition-colors",
                    isActive
                      ? "bg-accent text-white font-medium"
                      : "text-sub hover:text-ink hover:bg-panel2"
                  )
                }
              >
                {label}
              </NavLink>
            </div>
          );
        })}
      </nav>

      {/* Logout */}
      {showLogout && (
        <div className="px-3 py-3 border-t border-line">
          <button
            onClick={signOut}
            className="w-full text-left px-3 py-2 rounded-md text-[12px] text-sub hover:text-ink hover:bg-panel2"
            title={me!.email}
          >
            Log out
            <div className="text-[10.5px] text-sub/80 mt-0.5 truncate">
              {me!.email}
            </div>
          </button>
        </div>
      )}
    </aside>
  );
}
