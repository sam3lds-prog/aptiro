import { NavLink } from "react-router-dom";
import { cn } from "@/lib/cn";
import { useAuth } from "@/stores/auth";

const NAV: { to: string; label: string; eyebrow?: boolean }[] = [
  { to: "/", label: "Dashboard" },
  { to: "/vault", label: "Profile Vault" },
  { to: "/strategy", label: "Strategy" },
  { to: "/jobs", label: "Jobs" },
  { to: "/matches", label: "Matches" },
  { to: "/packages", label: "Packages" },
  { to: "/tracker", label: "Tracker" },
  { to: "/apply", label: "Apply" },
  { to: "/activity", label: "Activity" },
  { to: "/privacy", label: "Privacy" },
];

export function Nav() {
  const me = useAuth((s) => s.me);
  const signOut = useAuth((s) => s.signOut);
  const showLogout = !!me && !me.is_default;

  return (
    <aside className="w-56 shrink-0 border-r border-line bg-panel/80 backdrop-blur-sm sticky top-0 h-screen flex flex-col">
      <div className="px-5 pt-6 pb-5 border-b border-line">
        <div className="font-display text-[26px] font-bold tracking-tight leading-none">
          Aptiro
        </div>
        <div className="text-[10.5px] text-sub mt-1.5 tracking-wide uppercase">
          evidence-backed applications
        </div>
      </div>

      <nav className="flex-1 px-2.5 py-3 space-y-0.5 overflow-y-auto">
        {NAV.map(({ to, label }) => (
          <NavLink
            key={to}
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
        ))}
      </nav>

      {showLogout && (
        <div className="px-3 py-3 border-t border-line">
          <button
            onClick={signOut}
            className="w-full text-left px-3 py-2 rounded-md text-[12px] text-sub hover:text-ink hover:bg-panel2"
            title={me!.email}
          >
            Log out
            <div className="text-[10.5px] text-sub/80 mt-0.5 truncate">{me!.email}</div>
          </button>
        </div>
      )}
    </aside>
  );
}
