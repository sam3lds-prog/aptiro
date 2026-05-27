import { NavLink } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { cn } from "@/lib/cn";
import { useAuth } from "@/stores/auth";
import { api } from "@/lib/api";
import type { NotifInboxOut } from "@/lib/types";

const NAV: { to: string; label: string; section?: string; badge?: string }[] = [
  { to: "/",               label: "Dashboard" },
  { to: "/vault",          label: "Profile Vault" },
  { to: "/strategy",       label: "Strategy" },
  // Jobs + Match Inbox
  { to: "/matches",        label: "Match Inbox",    section: "Jobs" },
  { to: "/jobs",           label: "Jobs" },
  { to: "/saved-searches", label: "Saved Searches" },
  // Packages + Tracker
  { to: "/packages",       label: "Packages",       section: "Apply" },
  { to: "/tracker",        label: "Tracker" },
  { to: "/apply",          label: "Apply" },
  // Other
  { to: "/research",       label: "Research",       section: "Other" },
  { to: "/notifications",  label: "Notifications" },
  { to: "/activity",       label: "Activity" },
  { to: "/privacy",        label: "Privacy" },
];

export function Nav() {
  const me = useAuth((s) => s.me);
  const signOut = useAuth((s) => s.signOut);
  const showLogout = !!me && !me.is_default;

  // Poll unread count for the badge — low frequency, silent on error.
  const inboxQ = useQuery<NotifInboxOut>({
    queryKey: ["notif-inbox-count"],
    queryFn: () => api<NotifInboxOut>("/notifications/inbox"),
    refetchInterval: 60_000,
    retry: false,
    // Don't throw — the badge is non-critical UI
    throwOnError: false,
  });
  const unread = inboxQ.data?.unread_count ?? 0;

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
          const isNotifications = to === "/notifications";

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
                    "flex items-center justify-between px-3 py-2 rounded-md text-[13px] transition-colors",
                    isActive
                      ? "bg-accent text-white font-medium"
                      : "text-sub hover:text-ink hover:bg-panel2"
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <span>{label}</span>
                    {isNotifications && unread > 0 && (
                      <span
                        className={cn(
                          "text-[10px] font-bold px-1.5 py-0.5 rounded-full leading-none",
                          isActive
                            ? "bg-white/20 text-white"
                            : "bg-accent text-white"
                        )}
                      >
                        {unread > 99 ? "99+" : unread}
                      </span>
                    )}
                  </>
                )}
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
