import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { InAppNotification, NotifInboxOut, NotificationPreference } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { LoadingBlock, EmptyState } from "@/components/ui/feedback";
import { useNotify } from "@/stores/toast";

// ── Helpers ────────────────────────────────────────────────────────────────

function fmtDate(iso: string) {
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;
  const diffD = Math.floor(diffH / 24);
  if (diffD < 7) return `${diffD}d ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function kindLabel(kind: string) {
  if (kind === "daily_digest") return "Daily Digest";
  if (kind === "weekly_digest") return "Weekly Digest";
  if (kind === "match_threshold_alert") return "Match Alert";
  if (kind === "followup_reminder") return "Follow-up";
  if (kind === "package_ready") return "Package Ready";
  if (kind === "integrity_alert") return "Integrity Alert";
  return kind.replace(/_/g, " ");
}

function kindTone(kind: string): "blue" | "green" | "orange" | "neutral" {
  if (kind === "match_threshold_alert") return "green";
  if (kind === "integrity_alert") return "orange";
  if (kind === "followup_reminder") return "blue";
  return "neutral";
}

// ── Inbox panel ───────────────────────────────────────────────────────────

function InboxPanel() {
  const qc = useQueryClient();
  const notify = useNotify();

  const inboxQ = useQuery<NotifInboxOut>({
    queryKey: ["notif-inbox"],
    queryFn: () => api<NotifInboxOut>("/notifications/inbox"),
    refetchInterval: 30_000,
  });

  const markRead = useMutation({
    mutationFn: (id: string) =>
      api(`/notifications/inbox/${id}/read`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notif-inbox"] }),
  });

  const markAllRead = useMutation({
    mutationFn: () =>
      api("/notifications/inbox/read-all", { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notif-inbox"] });
      notify.success("All notifications marked as read.");
    },
  });

  const del = useMutation({
    mutationFn: (id: string) =>
      api(`/notifications/inbox/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notif-inbox"] }),
    onError: (e) =>
      notify.error(e instanceof ApiError ? e.message : "Delete failed."),
  });

  const sendDigest = useMutation({
    mutationFn: () => api("/notifications/send/digest", { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notif-inbox"] });
      notify.success("Digest delivered to your in-app inbox.");
    },
    onError: (e) =>
      notify.error(e instanceof ApiError ? e.message : "Send failed."),
  });

  if (inboxQ.isPending) return <LoadingBlock />;
  if (inboxQ.isError)
    return (
      <EmptyState
        title="Couldn't load inbox"
        body={inboxQ.error instanceof Error ? inboxQ.error.message : "Try again."}
      />
    );

  const { items, unread_count } = inboxQ.data!;

  return (
    <div className="flex flex-col gap-4">
      {/* Header row */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-[15px]">In-App Inbox</span>
          {unread_count > 0 && (
            <Badge tone="blue">{unread_count} unread</Badge>
          )}
        </div>
        <div className="flex gap-2">
          {unread_count > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => markAllRead.mutate()}
              disabled={markAllRead.isPending}
            >
              Mark all read
            </Button>
          )}
          <Button
            variant="secondary"
            size="sm"
            onClick={() => sendDigest.mutate()}
            disabled={sendDigest.isPending}
          >
            {sendDigest.isPending ? "Sending…" : "Send digest now"}
          </Button>
        </div>
      </div>

      {/* List */}
      {items.length === 0 ? (
        <EmptyState
          title="No notifications yet"
          body="Trigger a digest or match alert to see notifications here."
        />
      ) : (
        <div className="flex flex-col gap-2">
          {items.map((n: InAppNotification) => (
            <div
              key={n.id}
              className={`rounded-lg border px-4 py-3 transition-colors ${
                n.is_read
                  ? "border-line bg-panel/40 opacity-70"
                  : "border-accent/30 bg-panel"
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <Badge tone={kindTone(n.kind)} className="text-[10px]">
                      {kindLabel(n.kind)}
                    </Badge>
                    {!n.is_read && (
                      <span className="w-2 h-2 rounded-full bg-accent shrink-0" />
                    )}
                    <span className="text-[11px] text-sub ml-auto shrink-0">
                      {fmtDate(n.created_at)}
                    </span>
                  </div>
                  <div className="font-medium text-[13px] truncate">{n.subject}</div>
                  <div className="text-sub text-[12px] mt-1 line-clamp-2 whitespace-pre-line">
                    {n.body}
                  </div>
                </div>
                <div className="flex flex-col gap-1 shrink-0 mt-0.5">
                  {!n.is_read && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => markRead.mutate(n.id)}
                      disabled={markRead.isPending}
                      className="text-[11px] h-7 px-2"
                    >
                      Mark read
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => del.mutate(n.id)}
                    disabled={del.isPending}
                    className="text-[11px] h-7 px-2 text-red-400 hover:text-red-300"
                  >
                    Delete
                  </Button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Toggle ────────────────────────────────────────────────────────────────

function Toggle({
  checked,
  onChange,
  label,
  sub,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  sub?: string;
  disabled?: boolean;
}) {
  return (
    <label
      className={`flex items-start gap-3 cursor-pointer ${
        disabled ? "opacity-50 pointer-events-none" : ""
      }`}
    >
      <div className="relative mt-0.5 shrink-0">
        <input
          type="checkbox"
          className="sr-only"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          disabled={disabled}
        />
        <div
          className={`w-9 h-5 rounded-full transition-colors ${
            checked ? "bg-accent" : "bg-panel2 border border-line"
          }`}
        />
        <div
          className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
            checked ? "translate-x-4" : ""
          }`}
        />
      </div>
      <div>
        <div className="text-[13px] font-medium">{label}</div>
        {sub && <div className="text-[11px] text-sub mt-0.5">{sub}</div>}
      </div>
    </label>
  );
}

// ── Preferences panel ─────────────────────────────────────────────────────

function PrefsPanel() {
  const qc = useQueryClient();
  const notify = useNotify();

  const prefsQ = useQuery<NotificationPreference>({
    queryKey: ["notif-prefs"],
    queryFn: () => api<NotificationPreference>("/notifications/preferences"),
  });

  const [localEmail, setLocalEmail] = useState("");
  const [emailDirty, setEmailDirty] = useState(false);
  const [localPhone, setLocalPhone] = useState("");
  const [phoneDirty, setPhoneDirty] = useState(false);
  const [thresholdVal, setThresholdVal] = useState(0);
  const [thresholdDirty, setThresholdDirty] = useState(false);

  const update = useMutation({
    mutationFn: (patch: Partial<NotificationPreference>) =>
      api("/notifications/preferences", { method: "PUT", body: patch }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notif-prefs"] });
      setEmailDirty(false);
      setPhoneDirty(false);
      setThresholdDirty(false);
      notify.success("Preferences saved.");
    },
    onError: (e) =>
      notify.error(e instanceof ApiError ? e.message : "Save failed."),
  });

  if (prefsQ.isPending) return <LoadingBlock />;
  if (prefsQ.isError)
    return (
      <EmptyState
        title="Couldn't load preferences"
        body={prefsQ.error instanceof Error ? prefsQ.error.message : "Try again."}
      />
    );

  const p = prefsQ.data!;
  const emailAddr = emailDirty ? localEmail : p.email_address;
  const phone = phoneDirty ? localPhone : p.sms_phone;
  const threshold = thresholdDirty ? thresholdVal : p.match_alert_threshold;

  function toggleField(field: keyof NotificationPreference) {
    const current = p[field] as boolean;
    update.mutate({ [field]: !current });
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Status banner */}
      {!p.smtp_configured && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-[12px] text-amber-300">
          <strong>Email server not configured.</strong> Set{" "}
          <code className="font-mono text-[11px]">APTIRO_SMTP_HOST</code>,{" "}
          <code className="font-mono text-[11px]">APTIRO_SMTP_USER</code>, and{" "}
          <code className="font-mono text-[11px]">APTIRO_SMTP_PASS</code> to
          enable outbound email. Nothing will be sent until configured.
        </div>
      )}

      {/* In-app */}
      <Card title="In-App Notifications">
        <div className="flex flex-col gap-4 pt-1">
          <Toggle
            checked={p.in_app_enabled}
            onChange={() => toggleField("in_app_enabled")}
            label="In-app notification center"
            sub="Always recommended — notifications are saved in your inbox above."
          />
        </div>
      </Card>

      {/* Email */}
      <Card title="Email Notifications">
        <div className="flex flex-col gap-4 pt-1">
          <Toggle
            checked={p.email_enabled}
            onChange={() => toggleField("email_enabled")}
            label="Enable email notifications"
            sub={
              p.smtp_configured
                ? "SMTP configured — emails will be sent when enabled."
                : "Requires APTIRO_SMTP_HOST/USER/PASS to be set first."
            }
            disabled={!p.smtp_configured}
          />

          {p.email_enabled && p.smtp_configured && (
            <>
              <div>
                <label className="block text-[12px] text-sub mb-1.5">
                  Email address
                </label>
                <div className="flex gap-2">
                  <input
                    className="flex-1 bg-panel2 border border-line rounded-md px-3 py-2 text-[13px] focus:outline-none focus:border-accent"
                    type="email"
                    placeholder="you@example.com"
                    value={emailAddr}
                    onChange={(e) => {
                      setLocalEmail(e.target.value);
                      setEmailDirty(true);
                    }}
                  />
                  {emailDirty && (
                    <Button
                      size="sm"
                      onClick={() => update.mutate({ email_address: emailAddr })}
                      disabled={update.isPending}
                    >
                      Save
                    </Button>
                  )}
                </div>
              </div>
              <div className="flex flex-col gap-3 pl-1">
                <Toggle
                  checked={p.email_daily_digest}
                  onChange={() => toggleField("email_daily_digest")}
                  label="Daily digest"
                  sub="Top matches and application status every day."
                />
                <Toggle
                  checked={p.email_weekly_digest}
                  onChange={() => toggleField("email_weekly_digest")}
                  label="Weekly digest"
                  sub="A once-per-week summary of matches and activity."
                />
                <Toggle
                  checked={p.email_match_alerts}
                  onChange={() => toggleField("email_match_alerts")}
                  label="Match threshold alerts"
                  sub="Email when a new job scores above your alert threshold."
                />
                <Toggle
                  checked={p.email_followup_reminders}
                  onChange={() => toggleField("email_followup_reminders")}
                  label="Follow-up reminders"
                  sub="Reminders to follow up on submitted applications."
                />
              </div>
            </>
          )}
        </div>
      </Card>

      {/* Match threshold */}
      <Card title="Match Alert Threshold">
        <div className="flex flex-col gap-3 pt-1">
          <p className="text-[12px] text-sub">
            Receive an alert when a job scores at or above this threshold. Set to{" "}
            <strong className="text-ink">0</strong> to disable alerts.
          </p>
          <div className="flex items-center gap-3">
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={threshold}
              onChange={(e) => {
                setThresholdVal(Number(e.target.value));
                setThresholdDirty(true);
              }}
              className="flex-1 accent-accent"
            />
            <span className="font-mono text-[14px] font-semibold w-10 text-center">
              {threshold}
            </span>
            {thresholdDirty && (
              <Button
                size="sm"
                onClick={() => update.mutate({ match_alert_threshold: threshold })}
                disabled={update.isPending}
              >
                Save
              </Button>
            )}
          </div>
          {threshold > 0 && (
            <p className="text-[11px] text-sub">
              Alert when score ≥ <strong>{threshold}</strong>/100
            </p>
          )}
        </div>
      </Card>

      {/* SMS */}
      <Card title="SMS Notifications">
        <div className="flex flex-col gap-4 pt-1">
          {!p.twilio_configured && (
            <div className="rounded-md border border-line bg-panel2 px-3 py-2.5 text-[11px] text-sub">
              SMS requires{" "}
              <code className="font-mono">APTIRO_TWILIO_SID</code>,{" "}
              <code className="font-mono">APTIRO_TWILIO_TOKEN</code>, and{" "}
              <code className="font-mono">APTIRO_TWILIO_FROM</code> to be set.
            </div>
          )}
          <Toggle
            checked={p.sms_enabled}
            onChange={() => toggleField("sms_enabled")}
            label="Enable SMS notifications"
            sub="Explicit opt-in only. Never sent unsolicited."
            disabled={!p.twilio_configured}
          />
          {p.sms_enabled && p.twilio_configured && (
            <div>
              <label className="block text-[12px] text-sub mb-1.5">
                Phone number (E.164, e.g.{" "}
                <code className="font-mono text-[11px]">+12125550100</code>)
              </label>
              <div className="flex gap-2">
                <input
                  className="flex-1 bg-panel2 border border-line rounded-md px-3 py-2 text-[13px] font-mono focus:outline-none focus:border-accent"
                  type="tel"
                  placeholder="+12125550100"
                  value={phone}
                  onChange={(e) => {
                    setLocalPhone(e.target.value);
                    setPhoneDirty(true);
                  }}
                />
                {phoneDirty && (
                  <Button
                    size="sm"
                    onClick={() => update.mutate({ sms_phone: phone })}
                    disabled={update.isPending}
                  >
                    Save
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────

export function Notifications() {
  const [tab, setTab] = useState<"inbox" | "prefs">("inbox");

  return (
    <div>
      <PageHeader
        title="Notifications"
        sub="In-app inbox and delivery preferences. Nothing is sent until you configure and enable a channel."
      />

      {/* Tab bar */}
      <div className="flex gap-1 mb-6 border-b border-line">
        {(["inbox", "prefs"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2.5 text-[13px] font-medium border-b-2 transition-colors -mb-px ${
              tab === t
                ? "border-accent text-ink"
                : "border-transparent text-sub hover:text-ink"
            }`}
          >
            {t === "inbox" ? "Inbox" : "Preferences"}
          </button>
        ))}
      </div>

      {tab === "inbox" ? <InboxPanel /> : <PrefsPanel />}
    </div>
  );
}
