import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { Job, JobSources } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Label, Textarea } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { LoadingBlock, EmptyState } from "@/components/ui/feedback";
import { useNotify } from "@/stores/toast";

export function Jobs() {
  const qc = useQueryClient();
  const notify = useNotify();
  const [jd, setJd] = useState("");
  const [url, setUrl] = useState("");
  const [fetchProvider, setFetchProvider] = useState("remotive");

  const jobsQ = useQuery<Job[]>({
    queryKey: ["jobs"],
    queryFn: () => api<Job[]>("/jobs"),
  });

  const sourcesQ = useQuery<JobSources>({
    queryKey: ["job-sources"],
    queryFn: () => api<JobSources>("/job-sources"),
  });

  const importJdM = useMutation({
    mutationFn: () =>
      api<Job & { deduplicated?: boolean }>("/jobs", {
        method: "POST",
        body: { description_text: jd },
      }),
    onSuccess: (r) => {
      setJd("");
      notify.success(
        r.deduplicated
          ? "Already imported — showing the existing job."
          : "Job imported & normalized."
      );
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["matches"] });
    },
    onError: (e) =>
      notify.error(e instanceof ApiError ? e.message : "Import failed."),
  });

  const importUrlM = useMutation({
    mutationFn: () =>
      api<Job & { deduplicated?: boolean }>("/jobs/import-url", {
        method: "POST",
        body: { url: url.trim() },
      }),
    onSuccess: (r) => {
      setUrl("");
      notify.success(
        r.deduplicated
          ? "Already imported — showing the existing job."
          : "Fetched & imported from URL."
      );
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["matches"] });
    },
    onError: (e) =>
      notify.error(e instanceof ApiError ? e.message : "URL import failed."),
  });

  const fetchM = useMutation({
    mutationFn: () =>
      api("/job-sources/fetch", {
        method: "POST",
        body: { provider: fetchProvider, limit: 20 },
      }),
    onSuccess: () => {
      notify.success(`Fetched jobs from ${fetchProvider}.`);
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["matches"] });
    },
    onError: (e) =>
      notify.error(e instanceof ApiError ? e.message : "Fetch failed."),
  });

  const archiveM = useMutation({
    mutationFn: (id: string) =>
      api(`/jobs/${id}/archive`, { method: "POST" }),
    onSuccess: () => {
      notify.success("Job archived.");
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["matches"] });
    },
    onError: (e) =>
      notify.error(e instanceof ApiError ? e.message : "Archive failed."),
  });

  const jobs = jobsQ.data ?? [];
  const availableProviders = sourcesQ.data?.available ?? [];

  return (
    <div>
      <PageHeader
        title="Jobs"
        sub="Paste a description, or import from a public posting URL you supply. No scraping, no CAPTCHA."
      />

      {/* Import panel */}
      <Card title="Add a job" className="mb-5">
        <div className="grid gap-4 md:grid-cols-2">
          {/* Paste JD */}
          <div>
            <Label htmlFor="jd">Paste job description</Label>
            <Textarea
              id="jd"
              value={jd}
              rows={5}
              onChange={(e) => setJd(e.target.value)}
              placeholder="Paste the full job description here…"
            />
            <div className="flex justify-end mt-2">
              <Button
                loading={importJdM.isPending}
                disabled={jd.trim().length < 20}
                onClick={() => importJdM.mutate()}
              >
                Import from paste
              </Button>
            </div>
          </div>

          {/* URL + Fetch */}
          <div className="space-y-3">
            <div>
              <Label htmlFor="url">Import from URL</Label>
              <Input
                id="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://company.com/jobs/..."
              />
              <div className="flex justify-end mt-2">
                <Button
                  variant="secondary"
                  loading={importUrlM.isPending}
                  disabled={!url.trim().startsWith("http")}
                  onClick={() => importUrlM.mutate()}
                >
                  Fetch URL
                </Button>
              </div>
            </div>

            <div>
              <Label htmlFor="provider">Fetch from provider</Label>
              <div className="flex gap-2">
                <select
                  id="provider"
                  value={fetchProvider}
                  onChange={(e) => setFetchProvider(e.target.value)}
                  className="input flex-1"
                >
                  {availableProviders.length > 0
                    ? availableProviders.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.id} {p.mock ? "(mock)" : "(live)"}
                        </option>
                      ))
                    : (
                        <>
                          <option value="remotive">remotive (mock)</option>
                          <option value="greenhouse">greenhouse (mock)</option>
                          <option value="lever">lever (mock)</option>
                          <option value="ashby">ashby (mock)</option>
                        </>
                      )}
                </select>
                <Button
                  variant="secondary"
                  loading={fetchM.isPending}
                  onClick={() => fetchM.mutate()}
                >
                  Fetch
                </Button>
              </div>
              <p className="text-[11.5px] text-sub mt-1">
                Mock is offline + deterministic. Set APTIRO_JOB_PROVIDER=remotive
                in .env for live jobs.
              </p>
            </div>
          </div>
        </div>
      </Card>

      {/* Jobs list */}
      {jobsQ.isLoading ? (
        <LoadingBlock />
      ) : jobs.length === 0 ? (
        <EmptyState
          title="No jobs yet"
          body="Paste a job description above, import a URL, or click Fetch to pull sample jobs."
        />
      ) : (
        <div className="space-y-3">
          {jobs.map((job) => (
            <JobRow
              key={job.id}
              job={job}
              onArchive={() => archiveM.mutate(job.id)}
              archiving={archiveM.isPending && archiveM.variables === job.id}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function JobRow({
  job,
  onArchive,
  archiving,
}: {
  job: Job;
  onArchive: () => void;
  archiving: boolean;
}) {
  return (
    <Card compact className="flex items-start justify-between gap-3 p-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap mb-0.5">
          <span className="font-display text-[14px] font-semibold truncate">
            {job.title}
          </span>
          {job.is_stale && <Badge tone="muted">Stale</Badge>}
          {job.provider_source && job.provider_source !== "manual_import" && (
            <span className="text-[11px] text-sub capitalize px-1.5 py-0.5 bg-panel2 rounded">
              {job.provider_source}
            </span>
          )}
        </div>
        <div className="text-[12.5px] text-sub">
          {job.company}
          {job.location ? ` · ${job.location}` : ""}
          {" · "}
          <span className="capitalize">{job.work_mode}</span>
          {job.salary_min ? (
            <span>
              {" · "}${Math.round(job.salary_min / 1000)}k
              {job.salary_max ? `–$${Math.round(job.salary_max / 1000)}k` : "+"}
            </span>
          ) : null}
        </div>
        {job.requirements.length > 0 && (
          <div className="mt-1 text-[11.5px] text-sub">
            {job.requirements.slice(0, 3).join(" · ")}
            {job.requirements.length > 3 &&
              ` +${job.requirements.length - 3} more`}
          </div>
        )}
      </div>
      <div className="flex flex-col gap-1.5 shrink-0">
        {job.source_url && (
          <a
            href={job.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[12px] text-accent hover:underline"
          >
            Source ↗
          </a>
        )}
        <Button
          size="sm"
          variant="ghost"
          loading={archiving}
          onClick={onArchive}
        >
          Archive
        </Button>
      </div>
    </Card>
  );
}
