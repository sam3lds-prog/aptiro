import { useEffect, useState } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
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
// Phase 5
import { SavedSearches } from "@/pages/SavedSearches";

type Boot = "loading" | "ready" | "auth";

export default function App() {
  const [boot, setBoot] = useState<Boot>("loading");
  const { setAuth, setMe } = useAuth();
  const notify = useNotify();

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (boot === "loading") {
    return (
      <div className="min-h-screen flex items-center justify-center text-sub text-sm">
        Loading…
      </div>
    );
  }

  if (boot === "auth") {
    return (
      <Auth
        onAuthed={(token, me) => {
          setAuth(token, me);
          setBoot("ready");
        }}
      />
    );
  }

  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="/vault" element={<Vault />} />
        <Route path="/strategy" element={<Strategy />} />
        <Route path="/jobs" element={<Jobs />} />
        <Route path="/matches" element={<Matches />} />
        <Route path="/packages" element={<Packages />} />
        <Route path="/packages/:packageId" element={<Packages />} />
        <Route path="/tracker" element={<Tracker />} />
        <Route path="/apply" element={<Apply />} />
        <Route path="/activity" element={<Activity />} />
        <Route path="/privacy" element={<Privacy />} />
        {/* Phase 5 */}
        <Route path="/saved-searches" element={<SavedSearches />} />
        <Route path="/dash" element={<Navigate to="/" replace />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
