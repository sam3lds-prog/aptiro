import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { ApplySession, PackageListItem } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, Label } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { LoadingBlock, EmptyState } from "@/components/ui/feedback";
import { useNotify } from "@/stores/toast";

export function Apply() {
  const notify = useNotify();
  const pkgsQ = useQuery<PackageListItem[]>({
    queryKey: ["packages"],
    queryFn: () => api<PackageListItem[]>("/packages"),
  });
  const sessionsQ = useQuery<ApplySession[]>({
    queryKey: ["apply-sessions"],
    queryFn: () => api<ApplySession[]>("/apply"),
  });

  const [pid, setPid] = useState<string>("");
  const [sess, setSess] = useState<ApplySession | null>(null);

  useEffect(() => {
    if (pkgsQ.data && pkgsQ.data.length && !pid) setPid(pkgsQ.data[0].id);
  }, [pkgsQ.data, pid]);

  async function start() {
    if (!pid) return notify.warn("Pick a package first.");
    try {
      const s = await api<ApplySession>("/apply", {
        method: "POST",
        body: { package_id: pid },
      });
      setSess(s);
      notify.success("Apply session started — no automation will run.");
      sessionsQ.refetch();
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Start failed.");
    }
  }

  async function advance(action: string, confirm?: boolean) {
    if (!sess) return;
    try {
      const s = await api<ApplySession>(`/apply/${sess.id}/advance`, {
        method: "POST",
        body: { action, confirm: !!confirm },
      });
      setSess(s);
      notify.success(`Advanced → ${s.state}`);
      sessionsQ.refetch();
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Advance failed.");
    }
  }

  async function load(id: string) {
    try {
      setSess(await api<ApplySession>(`/apply/${id}`));
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Load failed.");
    }
  }

  const pkgs = pkgsQ.data ?? [];
  const sessions = sessionsQ.data ?? [];

  return (
    <div>
      <PageHeader
        title="Apply (scaffolding)"
        sub={
          <>
            Plan and pause before any handoff. No browser automation, no CAPTCHA
            handling, <span className="text-ink font-semibold">nothing is ever submitted externally</span>,
            and a human must explicitly confirm any simulated handoff.
          </>
        }
      />

      <Card title="Start a session" className="mb-5">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[280px]">
            <Label>Package</Label>
            <Select value={pid} onChange={(e) => setPid(e.target.value)}>
              {!pkgs.length && <option value="">No packages yet</option>}
              {pkgs.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title} — {p.company}
                </option>
              ))}
            </Select>
          </div>
          <Button disabled={!pid} onClick={start}>
            Start session
          </Button>
        </div>
      </Card>

      {sess && (
        <Card title={`${sess.job_title} — ${sess.company}`} className="mb-5">
          <div className="flex items-center gap-2 mb-3">
            <Badge tone="blue">{sess.state.replace(/_/g, " ")}</Badge>
            {sess.requires_handoff && <Badge tone="orange">handoff required</Badge>}
          </div>

          {!!sess.guardrails?.length && (
            <div className="text-[12.5px] bg-panel2 border border-line rounded-md p-2.5 mb-3 space-y-1">
              {sess.guardrails.map((g, i) => (
                <div key={i} className="text-sub">
                  • {g}
                </div>
              ))}
            </div>
          )}

          {!!sess.plan?.length && (
            <div className="mb-3 space-y-1.5">
              <div className="eyebrow">Field plan</div>
              {sess.plan.map((p) => (
                <div
                  key={p.step}
                  className="flex items-baseline justify-between gap-3 text-[12.5px] border-b border-line/40 py-1"
                >
                  <span className="text-ink/90">
                    {p.step}. {p.field}
                  </span>
                  <span className="text-sub">
                    {p.value} {p.needs_user && <Badge tone="orange">user fills</Badge>}
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="flex flex-wrap gap-1.5">
            {sess.allowed_actions.map((a) => (
              <Button
                key={a}
                size="sm"
                variant={a === "confirm" ? "primary" : "secondary"}
                onClick={() => advance(a, a === "confirm" ? true : undefined)}
              >
                {a.replace(/_/g, " ")}
              </Button>
            ))}
            {!sess.allowed_actions.length && (
              <span className="text-[12.5px] text-sub">Terminal state.</span>
            )}
          </div>

          {!!sess.history?.length && (
            <details className="mt-4">
              <summary className="cursor-pointer text-[12px] text-sub hover:text-ink">
                History ({sess.history.length})
              </summary>
              <div className="mt-2 space-y-1.5">
                {sess.history.map((h, i) => (
                  <div key={i} className="text-[11.5px] text-sub border-l-2 border-line pl-2">
                    <span className="text-ink/80">{h.state}</span> — {h.note}
                  </div>
                ))}
              </div>
            </details>
          )}
        </Card>
      )}

      <div className="eyebrow mb-2">All sessions ({sessions.length})</div>
      {sessionsQ.isLoading && <LoadingBlock lines={3} />}
      {!sessionsQ.isLoading && !sessions.length && (
        <EmptyState
          title="No apply sessions yet"
          body="Start one above. Each session is a planned handoff — never an automated submission."
        />
      )}
      <div className="grid gap-2">
        {sessions.map((s) => (
          <button
            key={s.id}
            onClick={() => load(s.id)}
            className="text-left bg-panel border border-line rounded-md px-3 py-2 hover:border-sub/60 transition-colors"
          >
            <div className="flex items-center justify-between gap-3">
              <div className="text-[13px] text-ink/90 min-w-0 truncate">
                {s.job_title} — {s.company}
              </div>
              <Badge tone="blue">{s.state.replace(/_/g, " ")}</Badge>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
