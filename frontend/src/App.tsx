import { useEffect, useState } from "react";
import { Routes, Route } from "react-router-dom";
import { api } from "@/lib/api";
import { useAuth } from "@/stores/auth";
import { useNotify } from "@/stores/toast";
import type { Health, Me } from "@/lib/types";
import { AppLayout } from "@/layouts/AppLayout";
import { Dashboard } from "@/pages/Dashboard";
import { Vault } from "@/pages/Vault";
import { Strategy } from "@/pages/Strategy";
import { Jobs } from "@/pages/Jobs";
import { Matches } from "@/pages/Matches";
import { Packages } from "@/pages/Packages";
import { Tracker } from "@/pages/Tracker";
import { Apply } from "@/pages/Apply";
import { Activity } from "@/pages/Activity";
import { Privacy } from "@/pages/Privacy";
import { Auth } from "@/pages/Auth";
import { NotFound } from "@/pages/NotFound";
// Upgrade Phase 5
import { SavedSearches } from "@/pages/SavedSearches";
// Upgrade Phase 6
import Research from "@/pages/Research";
// Upgrade Phase 7
import { Notifications } from "@/pages/Notifications";

type Boot = "loading" | "ready" | "auth";

export default function App() {
  const [boot, setBoot] = useState<Boot>("loading");
  const { setMe } = useAuth();
  // When Auth page calls setMe() on success, this re-renders and we
  // transition to "ready" via the effect below.
  const me = useAuth((s) => s.me);
  const notify = useNotify();

  useEffect(() => {
    if (boot === "auth" && me && !me.is_default) {
      setBoot("ready");
    }
  }, [me, boot]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const h = await api<Health>("/health");
        const authOn = !!h.auth?.enabled;
        if (!authOn) {
          if (!cancelled) setBoot("ready");
          return;
        }
        // Auth is on — check if an existing token is valid.
        try {
          const m = await api<Me>("/auth/me");
          if (cancelled) return;
          if (m && !m.is_default) {
            setMe(m);
            setBoot("ready");
          } else {
            setBoot("auth");
          }
        } catch {
          if (!cancelled) setBoot("auth");
        }
      } catch {
        if (!cancelled) setBoot("ready");
        notify.error(
          "Couldn't reach the Aptiro API. Is the backend running on :8000?"
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (boot === "loading") {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="font-display text-2xl font-bold tracking-tight opacity-60 animate-pulse">
          Aptiro
        </div>
      </div>
    );
  }

  if (boot === "auth") {
    return <Auth onAuthed={(token, m) => { setMe(m); }} />;
  }

  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/vault" element={<Vault />} />
        <Route path="/strategy" element={<Strategy />} />
        <Route path="/jobs" element={<Jobs />} />
        <Route path="/matches" element={<Matches />} />
        <Route path="/saved-searches" element={<SavedSearches />} />
        <Route path="/packages" element={<Packages />} />
        <Route path="/tracker" element={<Tracker />} />
        <Route path="/apply" element={<Apply />} />
        <Route path="/research" element={<Research />} />
        {/* Upgrade Phase 7 */}
        <Route path="/notifications" element={<Notifications />} />
        <Route path="/activity" element={<Activity />} />
        <Route path="/privacy" element={<Privacy />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
