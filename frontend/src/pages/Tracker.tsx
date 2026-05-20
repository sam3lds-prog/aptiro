import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, downloadUrl } from "@/lib/api";
import type { Application } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { LoadingBlock, EmptyState } from "@/components/ui/feedback";
import { useNotify } from "@/stores/toast";

const NEXT_BY_STATUS: Record<string, string[]> = {
  drafted: ["exported", "withdrawn"],
  exported: ["submitted_by_user", "withdrawn"],
  submitted_by_user: ["interviewing", "rejected", "withdrawn"],
  interviewing: ["offer", "rejected", "withdrawn"],
  offer: ["rejected", "withdrawn"],
  rejected: [],
  withdrawn: [],
};

function statusTone(s: string): "neutral" | "green" | "blue" | "orange" | "red" | "ink" {
  if (s === "offer" || s === "submitted_by_user") return "green";
  if (s === "interviewing") return "orange";
  if (s === "rejected") return "red";
  if (s === "exported") return "blue";
  if (s === "withdrawn") return "neutral";
  return "neutral";
}

export function Tracker() {
  const qc = useQueryClient();
  const notify = useNotify();
  const appsQ = useQuery<Application[]>({
    queryKey: ["applications"],
    queryFn: () => api<Application[]>("/applications"),
  });

  const [open, setOpen] = useState<string | null>(null);
  const [snap, setSnap] = useState<unknown | null>(null);

  async function transition(id: string, to: string) {
    try {
      await api(`/applications/${id}/transition`, { method: "POST", body: { to } });
      notify.success(`Status → ${to} (recorded in history)`);
      qc.invalidateQueries({ queryKey: ["applications"] });
      if (open === id) viewSnap(id);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        notify.warn("That transition isn't allowed from the current status.");
      } else {
        notify.error(e instanceof Error ? e.message : "Transition failed.");
      }
    }
  }

  async function viewSnap(id: string) {
    setOpen(id);
    try {
      setSnap(await api(`/applications/${id}/snapshot`));
    } catch {
      setSnap(null);
    }
  }

  async function reminderDone(id: string, rid: string) {
    try {
      await api(`/applications/${id}/reminders/${rid}/done`, { method: "POST" });
      notify.success("Reminder marked done.");
      qc.invalidateQueries({ queryKey: ["applications"] });
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Update failed.");
    }
  }

  const apps = appsQ.data ?? [];

  return (
    <div>
      <PageHeader
        title="Application Tracker"
        sub={
          <>
            Track applications you submit yourself. Aptiro{" "}
            <span className="text-ink font-semibold">never submits anything for you</span> —
            “submitted” is a status you set after applying on the employer’s own site.
          </>
        }
        actions={
          <a href={downloadUrl("/applications/export.csv")} target="_blank" rel="noreferrer">
            <Button variant="secondary" size="sm">Export CSV</Button>
          </a>
        }
      />

      {appsQ.isLoading && <LoadingBlock lines={5} />}
      {!appsQ.isLoading && !apps.length && (
        <EmptyState
          title="No applications yet"
          body="Build a package on the Packages page and then track it here once you've applied."
        />
      )}

      <div className="grid gap-3">
        {apps.map((a) => (
          <Card key={a.id} compact>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="font-display text-[16px] font-semibold leading-tight">
                  {a.job_title}
                </div>
                <div className="text-[12.5px] text-sub mt-0.5">{a.company}</div>
                <div className="flex items-center gap-2 mt-1.5">
                  <Badge tone={statusTone(a.status)}>{a.status.replace(/_/g, " ")}</Badge>
                  {a.snapshot_sha && (
                    <span className="text-[11px] text-sub font-mono">
                      snapshot {a.snapshot_sha.slice(0, 10)}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5 justify-end shrink-0">
                {NEXT_BY_STATUS[a.status]?.map((s) => (
                  <Button key={s} size="sm" variant="secondary" onClick={() => transition(a.id, s)}>
                    → {s.replace(/_/g, " ")}
                  </Button>
                ))}
                {a.snapshot_sha && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => (open === a.id ? setOpen(null) : viewSnap(a.id))}
                  >
                    {open === a.id ? "Hide snapshot" : "View snapshot"}
                  </Button>
                )}
              </div>
            </div>

            {!!a.reminders?.length && (
              <div className="mt-3 border-t border-line pt-3 space-y-1.5">
                <div className="eyebrow">Follow-up reminders</div>
                {a.reminders.map((r) => (
                  <div key={r.id} className="flex items-center justify-between gap-2 text-[12.5px]">
                    <span className={r.done ? "text-sub line-through" : "text-ink/90"}>
                      d+{r.offset_days} · {r.message}
                    </span>
                    {!r.done && (
                      <Button size="sm" variant="ghost" onClick={() => reminderDone(a.id, r.id)}>
                        Mark done
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            )}

            {open === a.id && snap !== null && (
              <pre className="mt-3 text-[11px] bg-panel2 border border-line rounded-md p-3 overflow-auto max-h-72">
                {JSON.stringify(snap, null, 2)}
              </pre>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}
