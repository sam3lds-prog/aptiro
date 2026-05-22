import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { SavedSearch, SavedSearchRunResult } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { LoadingBlock, EmptyState } from "@/components/ui/feedback";
import { useNotify } from "@/stores/toast";

const WORK_MODES = [
  { value: "", label: "Any" },
  { value: "remote", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "Onsite" },
];

const PROVIDERS = [
  { value: "", label: "Default (env)" },
  { value: "remotive", label: "Remotive" },
  { value: "greenhouse", label: "Greenhouse" },
  { value: "lever", label: "Lever" },
  { value: "ashby", label: "Ashby" },
  { value: "mock", label: "Mock (offline)" },
];

const FREQUENCIES = [
  { value: "manual", label: "Manual only" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
];

function formatRelative(iso?: string | null): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 2) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return d.toLocaleDateString();
}

// ── component ─────────────────────────────────────────────────────────────────
export function SavedSearches() {
  const qc = useQueryClient();
  const notify = useNotify();

  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState({
    name: "",
    query: "",
    provider: "",
    min_salary: "",
    max_salary: "",
    work_mode: "",
    location_filter: "",
    frequency: "manual",
  });
  const [runResult, setRunResult] = useState<SavedSearchRunResult | null>(null);

  const searchesQ = useQuery<SavedSearch[]>({
    queryKey: ["saved-searches"],
    queryFn: () => api<SavedSearch[]>("/saved-searches"),
  });

  const createM = useMutation({
    mutationFn: () =>
      api<SavedSearch>("/saved-searches", {
        method: "POST",
        body: {
          name: form.name.trim(),
          query: form.query.trim(),
          provider: form.provider || undefined,
          min_salary: form.min_salary ? parseInt(form.min_salary) : undefined,
          max_salary: form.max_salary ? parseInt(form.max_salary) : undefined,
          work_mode: form.work_mode || undefined,
          location_filter: form.location_filter.trim() || undefined,
          frequency: form.frequency,
        },
      }),
    onSuccess: () => {
      notify.success("Saved search created.");
      setShowNew(false);
      setForm({
        name: "", query: "", provider: "", min_salary: "", max_salary: "",
        work_mode: "", location_filter: "", frequency: "manual",
      });
      qc.invalidateQueries({ queryKey: ["saved-searches"] });
    },
    onError: (e) =>
      notify.error(e instanceof ApiError ? e.message : "Create failed."),
  });

  const deleteM = useMutation({
    mutationFn: (id: string) =>
      api(`/saved-searches/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      notify.success("Saved search deleted.");
      qc.invalidateQueries({ queryKey: ["saved-searches"] });
    },
    onError: (e) =>
      notify.error(e instanceof ApiError ? e.message : "Delete failed."),
  });

  const runM = useMutation({
    mutationFn: (id: string) =>
      api<SavedSearchRunResult>(`/saved-searches/${id}/run`, {
        method: "POST",
      }),
    onSuccess: (result) => {
      setRunResult(result);
      notify.success(
        `${result.jobs_created} new job(s) imported, ${result.jobs_skipped_dupes} duplicates skipped.`
      );
      qc.invalidateQueries({ queryKey: ["saved-searches"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["matches"] });
    },
    onError: (e) =>
      notify.error(e instanceof ApiError ? e.message : "Run failed."),
  });

  if (searchesQ.isLoading) return <LoadingBlock />;

  const searches = searchesQ.data ?? [];

  return (
    <div>
      <PageHeader
        title="Saved Searches"
        sub="Create and run recurring job searches across providers. Jobs are merged into your inbox with cross-provider deduplication."
        actions={
          <Button onClick={() => setShowNew((v) => !v)} variant="secondary" size="sm">
            {showNew ? "Cancel" : "+ New search"}
          </Button>
        }
      />

      {/* Last run result */}
      {runResult && (
        <div className="mb-4 p-3 rounded-lg bg-green-50 border border-green-200 text-[13px]">
          <span className="font-medium">{runResult.search_name}</span> via{" "}
          {runResult.provider_used} · {runResult.jobs_fetched} fetched ·{" "}
          <span className="text-green-700 font-medium">
            {runResult.jobs_created} new
          </span>{" "}
          · {runResult.jobs_skipped_dupes} dupes
          <button
            className="ml-3 text-sub hover:text-ink"
            onClick={() => setRunResult(null)}
          >
            ×
          </button>
        </div>
      )}

      {/* New search form */}
      {showNew && (
        <Card title="New saved search" className="mb-5">
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <Label htmlFor="ss-name">Name *</Label>
              <Input
                id="ss-name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="e.g., Healthcare PM Remote"
              />
            </div>
            <div>
              <Label htmlFor="ss-query">Keywords</Label>
              <Input
                id="ss-query"
                value={form.query}
                onChange={(e) => setForm({ ...form, query: e.target.value })}
                placeholder="e.g., product manager healthcare AI"
              />
            </div>
            <div>
              <Label htmlFor="ss-provider">Provider</Label>
              <Select
                id="ss-provider"
                value={form.provider}
                onChange={(e) => setForm({ ...form, provider: e.target.value })}
              >
                {PROVIDERS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="ss-wm">Work mode</Label>
              <Select
                id="ss-wm"
                value={form.work_mode}
                onChange={(e) => setForm({ ...form, work_mode: e.target.value })}
              >
                {WORK_MODES.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="ss-min">Min salary ($)</Label>
              <Input
                id="ss-min"
                type="number"
                value={form.min_salary}
                onChange={(e) => setForm({ ...form, min_salary: e.target.value })}
                placeholder="150000"
              />
            </div>
            <div>
              <Label htmlFor="ss-max">Max salary ($)</Label>
              <Input
                id="ss-max"
                type="number"
                value={form.max_salary}
                onChange={(e) => setForm({ ...form, max_salary: e.target.value })}
                placeholder="250000"
              />
            </div>
            <div>
              <Label htmlFor="ss-loc">Location filter</Label>
              <Input
                id="ss-loc"
                value={form.location_filter}
                onChange={(e) =>
                  setForm({ ...form, location_filter: e.target.value })
                }
                placeholder="e.g., United States"
              />
            </div>
            <div>
              <Label htmlFor="ss-freq">Run frequency</Label>
              <Select
                id="ss-freq"
                value={form.frequency}
                onChange={(e) => setForm({ ...form, frequency: e.target.value })}
              >
                {FREQUENCIES.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          <div className="flex justify-end mt-3">
            <Button
              loading={createM.isPending}
              disabled={!form.name.trim()}
              onClick={() => createM.mutate()}
            >
              Create search
            </Button>
          </div>
        </Card>
      )}

      {/* Searches list */}
      {searches.length === 0 ? (
        <EmptyState
          title="No saved searches"
          body="Create a saved search above to automatically pull in matching jobs from Remotive, Greenhouse, or other providers."
        />
      ) : (
        <div className="space-y-3">
          {searches.map((ss) => (
            <SavedSearchRow
              key={ss.id}
              search={ss}
              onRun={() => runM.mutate(ss.id)}
              running={runM.isPending && runM.variables === ss.id}
              onDelete={() => deleteM.mutate(ss.id)}
              deleting={deleteM.isPending && deleteM.variables === ss.id}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── SavedSearchRow ──────────────────────────────────────────────────────────
function SavedSearchRow({
  search,
  onRun,
  running,
  onDelete,
  deleting,
}: {
  search: SavedSearch;
  onRun: () => void;
  running: boolean;
  onDelete: () => void;
  deleting: boolean;
}) {
  return (
    <Card compact className="p-3 flex items-start justify-between gap-3">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap mb-1">
          <span className="font-display text-[14px] font-semibold">
            {search.name}
          </span>
          <Badge tone={search.is_active ? "success" : "muted"}>
            {search.frequency}
          </Badge>
          {search.provider && (
            <span className="text-[11px] capitalize text-sub px-1.5 py-0.5 bg-panel2 rounded">
              {search.provider}
            </span>
          )}
        </div>

        <div className="text-[12.5px] text-sub space-y-0.5">
          {search.query && (
            <div>
              <span className="text-ink/70">Query:</span> {search.query}
            </div>
          )}
          <div className="flex flex-wrap gap-3">
            {search.work_mode && (
              <span className="capitalize">
                <span className="text-ink/70">Mode:</span> {search.work_mode}
              </span>
            )}
            {search.min_salary && (
              <span>
                <span className="text-ink/70">Min:</span> $
                {Math.round(search.min_salary / 1000)}k
              </span>
            )}
            {search.location_filter && (
              <span>
                <span className="text-ink/70">Location:</span>{" "}
                {search.location_filter}
              </span>
            )}
            <span>
              <span className="text-ink/70">Last run:</span>{" "}
              {formatRelative(search.last_run_at)}
            </span>
          </div>
        </div>
      </div>

      <div className="flex gap-1.5 shrink-0">
        <Button size="sm" variant="primary" loading={running} onClick={onRun}>
          Run now
        </Button>
        <Button
          size="sm"
          variant="ghost"
          loading={deleting}
          onClick={onDelete}
        >
          Delete
        </Button>
      </div>
    </Card>
  );
}
