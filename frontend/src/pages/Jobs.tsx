import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { Job } from "@/lib/types";
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
  const jobsQ = useQuery<Job[]>({ queryKey: ["jobs"], queryFn: () => api<Job[]>("/jobs") });

  const [jd, setJd] = useState("");
  const [url, setUrl] = useState("");

  const importJdM = useMutation({
    mutationFn: () =>
      api<Job & { deduplicated?: boolean }>("/jobs", {
        method: "POST",
        body: { description_text: jd },
      }),
    onSuccess: (r) => {
      setJd("");
      notify.success(
        r.deduplicated ? "Already imported — showing the existing job." : "Job imported & normalized."
      );
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["matches"] });
    },
    onError: (e) => notify.error(e instanceof ApiError ? e.message : "Import failed."),
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
        r.deduplicated ? "Already imported — showing the existing job." : "Fetched & imported from URL."
      );
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["matches"] });
    },
    onError: (e) => notify.error(e instanceof ApiError ? e.message : "URL import failed."),
  });

  const fetchMockM = useMutation({
    mutationFn: () =>
      api("/job-sources/fetch", { method: "POST", body: { provider: "remotive" } }),
    onSuccess: () => {
      notify.success("Fetched sample jobs (mock provider).");
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["matches"] });
    },
    onError: (e) => notify.error(e instanceof ApiError ? e.message : "Fetch failed."),
  });

  async function archive(id: string) {
    try {
      await api(`/jobs/${id}/archive`, { method: "POST" });
      notify.success("Job archived.");
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["matches"] });
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Archive failed.");
    }
  }

  const jobs = jobsQ.data ?? [];

  return (
    <div>
      <PageHeader
        title="Jobs"
        sub="Paste a description, or import from a public posting URL you supply. No browser automation, no auth-walled scraping, no CAPTCHA bypass."
        actions={
          <Button
            variant="secondary"
            loading={fetchMockM.isPending}
            onClick={() => fetchMockM.mutate()}
          >
            Fetch mock provider
          </Button>
        }
      />

      <div className="grid gap-4 md:grid-cols-2 mb-5">
        <Card title="Paste a job description">
          <Textarea
            rows={6}
            value={jd}
            onChange={(e) => setJd(e.target.value)}
            placeholder="Paste the full posting text here. Title, requirements, and structured signals are parsed automatically."
          />
          <div className="flex justify-end mt-2">
            <Button
              loading={importJdM.isPending}
              disabled={jd.trim().length < 20}
              onClick={() => importJdM.mutate()}
            >
              Import job
            </Button>
          </div>
        </Card>

        <Card title="Import from a public posting URL">
          <Label htmlFor="url">URL</Label>
          <Input
            id="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://…"
          />
          <p className="text-[11.5px] text-sub mt-1.5">
            One public URL only. LinkedIn / Indeed / Glassdoor and auth-walled
            hosts are refused on purpose.
          </p>
          <div className="flex justify-end mt-2">
            <Button
              loading={importUrlM.isPending}
              disabled={!/^https?:\/\//i.test(url.trim())}
              onClick={() => importUrlM.mutate()}
            >
              Fetch & import
            </Button>
          </div>
        </Card>
      </div>

      <div className="eyebrow mb-2">Imported jobs ({jobs.length})</div>
      {jobsQ.isLoading && <LoadingBlock lines={5} />}
      {!jobsQ.isLoading && !jobs.length && (
        <EmptyState
          title="No jobs yet"
          body="Paste a description above, import a public URL, or fetch the mock provider to seed sample postings."
        />
      )}
      <div className="grid gap-3">
        {jobs.map((j) => (
          <Card key={j.id} compact>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="font-display text-[17px] font-semibold leading-tight">
                  {j.title}
                </div>
                <div className="text-[13px] text-sub mt-0.5">
                  {j.company}
                  {j.location ? ` · ${j.location}` : ""} · {j.work_mode}
                  {j.salary_min ? ` · $${j.salary_min.toLocaleString()}+` : ""}
                </div>
                <div className="flex flex-wrap gap-1.5 mt-2">
                  <Badge>{j.source}</Badge>
                  {(j.requirements || []).slice(0, 5).map((r, i) => (
                    <Badge key={i} tone="neutral">
                      {r.length > 60 ? r.slice(0, 57) + "…" : r}
                    </Badge>
                  ))}
                </div>
              </div>
              <div className="flex flex-col items-end gap-2 shrink-0">
                <Button size="sm" variant="ghost" onClick={() => archive(j.id)}>
                  Archive
                </Button>
                {j.source_url && (
                  <a
                    href={j.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[11.5px] text-accent hover:underline"
                  >
                    open source ↗
                  </a>
                )}
              </div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
