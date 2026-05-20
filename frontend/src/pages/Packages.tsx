import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, downloadUrl } from "@/lib/api";
import type {
  Job, PackageBullet, PackageDetail, PackageListItem, ExportPreview,
} from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, Textarea, Label } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { LoadingBlock, EmptyState } from "@/components/ui/feedback";
import { ProvenanceBadge } from "@/components/ProvenanceBadge";
import { useNotify } from "@/stores/toast";

const SECTIONS: [string, string][] = [
  ["summary", "Summary"],
  ["experience", "Experience"],
  ["skills", "Skills"],
  ["cover_letter", "Cover letter"],
];

const FORMATS = ["md", "html", "docx", "pdf"] as const;
const ARTIFACTS = ["resume", "cover_letter", "both"] as const;

export function Packages() {
  const qc = useQueryClient();
  const notify = useNotify();
  const jobsQ = useQuery<Job[]>({ queryKey: ["jobs"], queryFn: () => api<Job[]>("/jobs") });
  const listQ = useQuery<PackageListItem[]>({
    queryKey: ["packages"],
    queryFn: () => api<PackageListItem[]>("/packages"),
  });

  const [jid, setJid] = useState<string>("");
  const [sel, setSel] = useState<string | null>(null);
  const [pkg, setPkg] = useState<PackageDetail | null>(null);
  const [edit, setEdit] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState<{ ready?: boolean; steps?: number; flags?: string[] } | null>(null);

  const [exFmt, setExFmt] = useState<(typeof FORMATS)[number]>("md");
  const [exArt, setExArt] = useState<(typeof ARTIFACTS)[number]>("both");
  const [exInc, setExInc] = useState(false);
  const [preview, setPreview] = useState<ExportPreview | null>(null);

  useEffect(() => {
    if (jobsQ.data && jobsQ.data.length && !jid) setJid(jobsQ.data[0].id);
  }, [jobsQ.data, jid]);

  async function openPkg(id: string) {
    setSel(id);
    setRun(null);
    setPreview(null);
    try {
      setPkg(await api<PackageDetail>(`/packages/${id}`));
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Load failed.");
    }
  }

  async function build() {
    if (!jid) {
      notify.warn("Pick a job first.");
      return;
    }
    setBusy(true);
    try {
      const p = await api<PackageDetail>("/packages", { method: "POST", body: { job_id: jid } });
      notify.success("Package built — review each bullet's provenance.");
      qc.invalidateQueries({ queryKey: ["packages"] });
      await openPkg(p.id);
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Build failed.");
    } finally {
      setBusy(false);
    }
  }

  async function patchBullet(b: PackageBullet, body: Record<string, unknown>) {
    if (!sel) return;
    try {
      await api(`/packages/${sel}/bullets/${b.id}`, { method: "PATCH", body });
      setEdit(null);
      setPkg(await api<PackageDetail>(`/packages/${sel}`));
      setPreview(null);
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Update failed.");
    }
  }

  async function aiSuggest(b: PackageBullet) {
    if (!sel) return;
    try {
      const r = await api<{ grounded: boolean; suggestion?: string; provider?: string; violations?: string[] }>(
        `/packages/${sel}/bullets/${b.id}/ai-rewrite`,
        { method: "POST", body: {} }
      );
      if (!r.grounded) {
        notify.warn(
          `AI suggestion BLOCKED by the grounding gate: ${
            r.violations?.[0] || "introduces unsupported facts"
          } — not applied.`
        );
        return;
      }
      setEdit(b.id);
      setDraft(r.suggestion || "");
      notify.success(
        `AI suggestion is grounded in your evidence (${r.provider}). Review and Save to apply.`
      );
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "AI suggest failed.");
    }
  }

  async function aiCover() {
    if (!sel) return;
    try {
      const r = await api<{ grounded: boolean; provider?: string; violations?: string[] }>(
        `/packages/${sel}/ai-cover-letter?apply=true`,
        { method: "POST" }
      );
      if (r.grounded) {
        notify.success(`Cover letter drafted from your accepted bullets (${r.provider}).`);
      } else {
        notify.warn(
          `Cover-letter draft BLOCKED by the grounding gate: ${r.violations?.[0] || "unsupported"} — not saved.`
        );
      }
      setPkg(await api<PackageDetail>(`/packages/${sel}`));
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Cover letter failed.");
    }
  }

  async function orchestrate() {
    if (!sel) return;
    setBusy(true);
    try {
      const r = await api<{ ready?: boolean; steps?: number; flags?: string[] }>(
        `/packages/${sel}/orchestrate`,
        { method: "POST" }
      );
      setRun(r);
      setPkg(await api<PackageDetail>(`/packages/${sel}`));
      notify.success(r.ready ? "Council: READY to export" : "Council: review the flags");
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Council failed.");
    } finally {
      setBusy(false);
    }
  }

  async function loadPreview() {
    if (!sel) return;
    try {
      const p = await api<ExportPreview>(
        `/packages/${sel}/export/preview${exInc ? "?include_unsupported=true" : ""}`
      );
      setPreview(p);
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Preview failed.");
    }
  }

  function doExport() {
    if (!sel) return;
    const url = downloadUrl(`/packages/${sel}/export`, {
      format: exFmt,
      artifact: exArt,
      include_unsupported: exInc,
    });
    window.open(url, "_blank");
    notify.success(
      `Exporting ${exArt} as ${exFmt.toUpperCase()}${
        exInc ? " (unsupported INCLUDED — your override)" : " — unsupported/rejected excluded"
      }`
    );
  }

  const jobs = jobsQ.data ?? [];
  const list = listQ.data ?? [];

  return (
    <div>
      <PageHeader
        title="Package Workspace"
        sub="Every bullet is colour-coded by provenance and traces back to an approved claim. Templated connectives are honestly marked AI-suggested (orange). Export excludes rejected / unsupported content by default."
      />

      <Card title="Build a package from a job" className="mb-5">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[280px]">
            <Label>Job</Label>
            <Select value={jid} onChange={(e) => setJid(e.target.value)}>
              {!jobs.length && <option value="">No jobs imported yet</option>}
              {jobs.map((j) => (
                <option key={j.id} value={j.id}>
                  {j.title} — {j.company}
                </option>
              ))}
            </Select>
          </div>
          <Button loading={busy} disabled={!jid} onClick={build}>
            Build package
          </Button>
        </div>
        {!!list.length && (
          <div className="flex flex-wrap gap-1.5 mt-4">
            {list.map((p) => (
              <button
                key={p.id}
                onClick={() => openPkg(p.id)}
                className={
                  "px-2.5 py-1 text-[12px] rounded-md border transition-colors " +
                  (sel === p.id
                    ? "border-accent/40 bg-accent/15 text-ink"
                    : "border-line bg-panel2 text-sub hover:text-ink")
                }
              >
                {p.title}{" "}
                <span className="text-sub">({p.score_snapshot})</span>
              </button>
            ))}
          </div>
        )}
      </Card>

      {jobsQ.isLoading && <LoadingBlock lines={4} />}
      {!jobsQ.isLoading && !jobs.length && (
        <EmptyState
          title="No jobs to build from"
          body="Import a job on the Jobs page first."
        />
      )}

      {pkg && (
        <div className="space-y-4">
          <Card>
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="font-display text-[18px] font-semibold">
                  {pkg.title} — {pkg.company}
                </div>
                <div className="text-[12.5px] text-sub mt-0.5">{pkg.status}</div>
              </div>
              <Badge tone="blue" className="text-[12.5px] px-2.5 py-1">
                fit {pkg.score_snapshot}/100
              </Badge>
            </div>
            <div className="flex flex-wrap gap-1.5 mt-4">
              <Button size="sm" variant="secondary" loading={busy} onClick={orchestrate}>
                Run agent council (13 steps)
              </Button>
              <Button size="sm" variant="secondary" onClick={aiCover}>
                ✦ AI cover letter
              </Button>
            </div>
            {run && (
              <div className="mt-3 text-[12.5px] bg-panel2 border border-line rounded-md p-2.5 text-sub">
                Council {run.ready ? "READY" : "NOT YET READY"} · {run.steps ?? 13} steps
                {!!run.flags?.length && <> · flags: {run.flags.join("; ")}</>}
              </div>
            )}
          </Card>

          {SECTIONS.map(([key, label]) => {
            const bullets = (pkg.bullets || [])
              .filter((b) => b.section === key)
              .sort((a, b) => a.order_index - b.order_index);
            if (!bullets.length) return null;
            return (
              <Card key={key} title={label}>
                <div className="space-y-2.5">
                  {bullets.map((b) => (
                    <div
                      key={b.id}
                      className="border-l-2 pl-3 py-1.5"
                      style={{
                        borderColor: `rgb(var(--prov-${b.provenance_color}))`,
                      }}
                    >
                      {edit === b.id ? (
                        <Textarea
                          rows={3}
                          value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                        />
                      ) : (
                        <div className="text-[13.5px] text-ink leading-relaxed">
                          {b.current_text}
                        </div>
                      )}
                      <div className="flex flex-wrap items-center gap-2 mt-1.5">
                        <ProvenanceBadge color={b.provenance_color} />
                        <Badge tone={bulletStatusTone(b.status)}>{b.status}</Badge>
                        {(b.flagged || []).map((f, i) => (
                          <Badge key={i} tone="orange">
                            ⚠ {f}
                          </Badge>
                        ))}
                      </div>
                      <div className="flex flex-wrap gap-1.5 mt-1.5">
                        {edit === b.id ? (
                          <>
                            <Button
                              size="sm"
                              onClick={() => patchBullet(b, { current_text: draft, status: "accepted" })}
                            >
                              Save
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setEdit(null)}>
                              Cancel
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button
                              size="sm"
                              disabled={b.status === "accepted"}
                              onClick={() => patchBullet(b, { status: "accepted" })}
                            >
                              Accept
                            </Button>
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => {
                                setEdit(b.id);
                                setDraft(b.current_text);
                              }}
                            >
                              Rewrite
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => patchBullet(b, { status: "rejected" })}>
                              Reject
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => patchBullet(b, { status: "locked" })}>
                              Lock
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => aiSuggest(b)}>
                              ✦ AI suggest
                            </Button>
                          </>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </Card>
            );
          })}

          {/* Export gate */}
          <Card title="Export">
            <div className="grid gap-3 md:grid-cols-4">
              <div>
                <Label>Format</Label>
                <Select value={exFmt} onChange={(e) => setExFmt(e.target.value as typeof exFmt)}>
                  {FORMATS.map((f) => (
                    <option key={f} value={f}>
                      {f.toUpperCase()}
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <Label>Artifact</Label>
                <Select value={exArt} onChange={(e) => setExArt(e.target.value as typeof exArt)}>
                  {ARTIFACTS.map((a) => (
                    <option key={a} value={a}>
                      {a.replace("_", " ")}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="flex items-end">
                <label className="flex items-center gap-2 text-[12.5px] text-sub">
                  <input
                    type="checkbox"
                    checked={exInc}
                    onChange={(e) => setExInc(e.target.checked)}
                  />
                  Include unsupported (override)
                </label>
              </div>
              <div className="flex items-end gap-2">
                <Button variant="secondary" onClick={loadPreview}>
                  Preview gate
                </Button>
                <Button onClick={doExport}>Export</Button>
              </div>
            </div>

            {preview && (
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <div className="bg-panel2 border border-line rounded-md p-3">
                  <div className="eyebrow mb-1.5">Included</div>
                  {Object.entries(preview.sections).map(([sec, items]) => (
                    <div key={sec} className="mb-2">
                      <div className="text-[12px] text-sub uppercase tracking-wide">{sec}</div>
                      {items.map((it, i) => (
                        <div
                          key={i}
                          className="text-[12.5px] text-ink/90 border-l-2 pl-2 py-0.5 mt-0.5"
                          style={{ borderColor: `rgb(var(--prov-${it.color}))` }}
                        >
                          {it.text}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
                <div className="bg-panel2 border border-line rounded-md p-3">
                  <div className="eyebrow mb-1.5">Excluded by the gate</div>
                  {!preview.excluded.length && (
                    <div className="text-[12.5px] text-sub">Nothing excluded — clean export.</div>
                  )}
                  {preview.excluded.map((x, i) => (
                    <div key={i} className="mb-2">
                      <div className="text-[12.5px] text-ink/80">{x.text}</div>
                      <div className="text-[11px] text-prov-red mt-0.5">
                        {x.reasons.join(" · ")}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}

function bulletStatusTone(s: PackageBullet["status"]): "neutral" | "green" | "red" | "orange" | "ink" {
  if (s === "accepted") return "green";
  if (s === "rejected") return "red";
  if (s === "rewritten") return "orange";
  if (s === "locked") return "ink";
  return "neutral";
}
