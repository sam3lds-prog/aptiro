import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { Claim, Source, SourceType } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { LoadingBlock, EmptyState } from "@/components/ui/feedback";
import { ProvenanceBadge } from "@/components/ProvenanceBadge";
import { ConfirmModal } from "@/components/ConfirmModal";
import { useNotify } from "@/stores/toast";
import { getToken } from "@/stores/auth";

const SOURCE_TYPES: { id: SourceType; label: string }[] = [
  { id: "resume", label: "Resume" },
  { id: "linkedin", label: "LinkedIn (pasted)" },
  { id: "portfolio", label: "Portfolio" },
  { id: "public_article", label: "Public article" },
  { id: "manual_note", label: "Manual note" },
];

export function Vault() {
  const qc = useQueryClient();
  const notify = useNotify();
  const sourcesQ = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api<Source[]>("/sources"),
  });
  const claimsQ = useQuery<Claim[]>({
    queryKey: ["claims"],
    queryFn: () => api<Claim[]>("/claims"),
  });

  const [text, setText] = useState("");
  const [stype, setStype] = useState<SourceType>("resume");
  const [sel, setSel] = useState<string>("all");
  const [edit, setEdit] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [delTarget, setDelTarget] = useState<Source | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refreshAll = () => {
    qc.invalidateQueries({ queryKey: ["sources"] });
    qc.invalidateQueries({ queryKey: ["claims"] });
    qc.invalidateQueries({ queryKey: ["onboarding"] });
  };

  const pasteM = useMutation({
    mutationFn: async () =>
      api<unknown>("/sources", {
        method: "POST",
        body: {
          source_type: stype,
          label: `${stype} (pasted)`,
          raw_text: text,
        },
      }),
    onSuccess: () => {
      setText("");
      notify.success("Source added — claims extracted with provenance.");
      refreshAll();
    },
    onError: (e) => notify.error(e instanceof Error ? e.message : "Paste failed."),
  });

  async function uploadFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    try {
      const fd = new FormData();
      fd.append("file", f);
      const headers: Record<string, string> = {};
      const tok = getToken();
      if (tok) headers["Authorization"] = `Bearer ${tok}`;
      const base =
        ((window as unknown as { APTIRO_API?: string }).APTIRO_API || "") +
        "/api/sources/upload?source_type=" +
        encodeURIComponent(stype);
      const r = await fetch(base, { method: "POST", body: fd, headers });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      notify.success(`Uploaded ${f.name} — ${d.claim_count} claim(s) extracted.`);
      refreshAll();
    } catch (err) {
      notify.error(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function setStatus(c: Claim, status: Claim["approval_status"]) {
    try {
      await api(`/claims/${c.id}`, { method: "PATCH", body: { approval_status: status } });
      refreshAll();
    } catch (err) {
      notify.error(err instanceof ApiError ? err.message : "Update failed.");
    }
  }

  async function saveEdit(c: Claim) {
    try {
      await api(`/claims/${c.id}`, { method: "PATCH", body: { claim_text: draft } });
      setEdit(null);
      notify.success("Claim text updated.");
      refreshAll();
    } catch (err) {
      notify.error(err instanceof ApiError ? err.message : "Save failed.");
    }
  }

  async function deleteSource(s: Source) {
    await api(`/sources/${s.id}`, { method: "DELETE" });
    notify.success("Source deleted (claims + evidence cascaded).");
    refreshAll();
  }

  const sources = sourcesQ.data ?? [];
  const claims = claimsQ.data ?? [];
  const shown = sel === "all" ? claims : claims.filter((c) => c.source_id === sel);
  const loading = sourcesQ.isLoading || claimsQ.isLoading;

  return (
    <div>
      <PageHeader
        title="Profile Vault"
        sub="Upload or paste a résumé/profile. Every extracted claim keeps its source snippet, section, confidence, and an explicit approval gate. Nothing is fabricated; nothing is auto-submitted."
      />

      <Card title="Add a source" className="mb-5">
        <div className="grid gap-3 md:grid-cols-[1fr_2fr]">
          <div>
            <Label htmlFor="stype">Type</Label>
            <Select id="stype" value={stype} onChange={(e) => setStype(e.target.value as SourceType)}>
              {SOURCE_TYPES.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </Select>

            <Label>Upload file</Label>
            <input
              ref={fileRef}
              type="file"
              onChange={uploadFile}
              accept=".pdf,.docx,.txt,.md"
              className="block w-full text-[12.5px] text-sub file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:bg-panel2 file:text-ink file:cursor-pointer file:hover:bg-line/60"
            />
            <p className="text-[11.5px] text-sub mt-1.5">PDF, DOCX, TXT, or Markdown (≤10 MB).</p>
          </div>

          <div>
            <Label htmlFor="paste">Or paste text</Label>
            <Textarea
              id="paste"
              value={text}
              rows={6}
              onChange={(e) => setText(e.target.value)}
              placeholder="Paste résumé, LinkedIn 'About', portfolio content, or a public article…"
            />
            <div className="flex justify-end mt-2">
              <Button
                loading={pasteM.isPending}
                disabled={text.trim().length < 20}
                onClick={() => pasteM.mutate()}
              >
                Add source
              </Button>
            </div>
          </div>
        </div>
      </Card>

      {/* Sources list */}
      <div className="grid gap-4 md:grid-cols-[260px_1fr]">
        <div className="space-y-2">
          <div className="eyebrow px-1">Sources</div>
          <button
            onClick={() => setSel("all")}
            className={
              "block w-full text-left px-3 py-2 rounded-md text-[13px] border " +
              (sel === "all"
                ? "border-accent/40 bg-accent/10 text-ink"
                : "border-line bg-panel text-sub hover:text-ink hover:border-sub/40")
            }
          >
            All claims <span className="text-sub">({claims.length})</span>
          </button>
          {sources.map((s) => (
            <button
              key={s.id}
              onClick={() => setSel(s.id)}
              className={
                "block w-full text-left px-3 py-2 rounded-md text-[13px] border " +
                (sel === s.id
                  ? "border-accent/40 bg-accent/10 text-ink"
                  : "border-line bg-panel text-sub hover:text-ink hover:border-sub/40")
              }
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate">
                  {s.filename || s.label}
                </span>
                <Badge>{s.claim_count}</Badge>
              </div>
              <div className="text-[11px] text-sub mt-0.5 flex items-center gap-2">
                <span>{s.source_type}</span>
                {s.parse_meta?.format && <span>· {s.parse_meta.format}</span>}
                {s.parse_meta?.pages ? <span>· {s.parse_meta.pages}p</span> : null}
              </div>
            </button>
          ))}
          {!sources.length && !sourcesQ.isLoading && (
            <p className="text-[12px] text-sub px-1">No sources yet.</p>
          )}
        </div>

        <div>
          {loading && <LoadingBlock lines={6} />}
          {!loading && !shown.length && (
            <EmptyState
              title="No claims yet"
              body="Upload a résumé or paste profile text on the left. Aptiro will extract claims with full provenance."
            />
          )}
          <div className="space-y-3">
            {shown.map((c) => (
              <Card key={c.id} compact>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    {edit === c.id ? (
                      <Textarea
                        value={draft}
                        rows={3}
                        onChange={(e) => setDraft(e.target.value)}
                        className="mb-2"
                      />
                    ) : (
                      <div className="text-[13.5px] text-ink leading-relaxed">{c.claim_text}</div>
                    )}
                    <div className="flex flex-wrap items-center gap-2 mt-2">
                      <ProvenanceBadge color={c.provenance_color} />
                      <Badge tone={statusTone(c.approval_status)}>{c.approval_status}</Badge>
                      <span className="text-[11px] text-sub">conf {Math.round(c.confidence * 100)}%</span>
                      {c.company && <Badge>{c.company}</Badge>}
                      {c.role && <Badge>{c.role}</Badge>}
                      {(c.metrics || []).map((m, i) => (
                        <Badge key={`m${i}`} tone="ink">
                          📊 {m}
                        </Badge>
                      ))}
                      {(c.skills || []).slice(0, 6).map((s, i) => (
                        <Badge key={`s${i}`}>{s}</Badge>
                      ))}
                    </div>
                    {(c.source_refs || []).slice(0, 2).map((r, i) => (
                      <div
                        key={i}
                        className="mt-2 text-[12px] text-sub italic bg-panel2 border border-line rounded-md px-2.5 py-1.5"
                      >
                        “{r.snippet}”
                        <span className="not-italic ml-2 text-sub/80">
                          — {r.section}
                          {r.page ? ` · p${r.page}` : ""}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="flex flex-wrap gap-1.5 mt-3">
                  {edit === c.id ? (
                    <>
                      <Button size="sm" onClick={() => saveEdit(c)}>
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
                        disabled={c.approval_status === "approved"}
                        onClick={() => setStatus(c, "approved")}
                      >
                        Approve
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => {
                          setEdit(c.id);
                          setDraft(c.claim_text);
                        }}
                      >
                        Edit
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setStatus(c, "rejected")}>
                        Reject
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setStatus(c, "do_not_use")}>
                        Do-not-use
                      </Button>
                    </>
                  )}
                </div>
              </Card>
            ))}
          </div>

          {sel !== "all" && (
            <div className="mt-4 flex justify-end">
              <Button
                size="sm"
                variant="danger"
                onClick={() => {
                  const s = sources.find((x) => x.id === sel);
                  if (s) setDelTarget(s);
                }}
              >
                Delete source
              </Button>
            </div>
          )}
        </div>
      </div>

      <ConfirmModal
        open={!!delTarget}
        onClose={() => setDelTarget(null)}
        destructive
        title="Delete this source?"
        body={
          <>
            This will permanently remove the source and{" "}
            <span className="text-ink">cascade delete</span> every claim and
            evidence row it produced. This cannot be undone.
          </>
        }
        confirmLabel="Delete source"
        onConfirm={async () => {
          if (delTarget) await deleteSource(delTarget);
        }}
      />
    </div>
  );
}

function statusTone(s: Claim["approval_status"]): "neutral" | "green" | "red" | "orange" {
  if (s === "approved") return "green";
  if (s === "rejected" || s === "do_not_use") return "red";
return "neutral";
}
