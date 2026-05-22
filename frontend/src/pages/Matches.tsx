import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import type { Match, MatchFilter } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { LoadingBlock, EmptyState } from "@/components/ui/feedback";
import { useNotify } from "@/stores/toast";

// ── helpers ─────────────────────────────────────────────────────────────────
function scoreTone(score: number): "success" | "warning" | "muted" {
  return score >= 70 ? "success" : score >= 50 ? "warning" : "muted";
}

function scoreLabel(score: number): string {
  if (score >= 70) return "Strong match";
  if (score >= 50) return "Moderate match";
  if (score >= 40) return "Stretch";
  return "Weak";
}

const FILTERS: { key: MatchFilter; label: string }[] = [
  { key: "all",           label: "All" },
  { key: "strong",        label: "Strong (70+)" },
  { key: "moderate",      label: "Moderate (50-69)" },
  { key: "stretch",       label: "Stretch (40-49)" },
  { key: "remote",        label: "Remote" },
  { key: "above_target",  label: "Above target salary" },
  { key: "new_this_week", label: "New this week" },
  { key: "missing_req",   label: "Has gaps" },
  { key: "stale",         label: "Stale" },
];

function applyFilter(matches: Match[], filter: MatchFilter): Match[] {
  const weekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  switch (filter) {
    case "strong":        return matches.filter((m) => m.score >= 70);
    case "moderate":      return matches.filter((m) => m.score >= 50 && m.score < 70);
    case "stretch":       return matches.filter((m) => m.score >= 40 && m.score < 50);
    case "remote":        return matches.filter((m) => m.job.work_mode === "remote");
    case "above_target":  return matches.filter((m) => (m.job.salary_min ?? 0) >= 150000);
    case "new_this_week": return matches.filter(
      (m) => new Date(m.job.imported_at).getTime() > weekAgo
    );
    case "missing_req":   return matches.filter((m) => m.missing_requirements.length > 0);
    case "stale":         return matches.filter((m) => !!m.job.is_stale);
    default:              return matches;
  }
}

// ── component ────────────────────────────────────────────────────────────────
export function Matches() {
  const qc = useQueryClient();
  const notify = useNotify();
  const [filter, setFilter] = useState<MatchFilter>("all");
  const [refreshing, setRefreshing] = useState(false);

  const matchesQ = useQuery<Match[]>({
    queryKey: ["matches"],
    queryFn: () => api<Match[]>("/matches"),
  });

  const archiveM = useMutation({
    mutationFn: (jobId: string) =>
      api(`/jobs/${jobId}/archive`, { method: "POST" }),
    onSuccess: () => {
      notify.success("Job archived.");
      qc.invalidateQueries({ queryKey: ["matches"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e) =>
      notify.error(e instanceof ApiError ? e.message : "Archive failed."),
  });

  async function fetchMore() {
    setRefreshing(true);
    try {
      await api("/job-sources/fetch", {
        method: "POST",
        body: { provider: "remotive", limit: 20 },
      });
      notify.success("Fetched latest jobs.");
      qc.invalidateQueries({ queryKey: ["matches"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Fetch failed.");
    } finally {
      setRefreshing(false);
    }
  }

  if (matchesQ.isLoading) return <LoadingBlock />;

  const all = matchesQ.data ?? [];
  const filtered = applyFilter(all, filter);

  return (
    <div>
      <PageHeader
        title="Match Inbox"
        sub="Jobs ranked by fit. Strong (70+) are ready to apply; Moderate (50-69) may need positioning; Stretch (40-49) require framing."
        actions={
          <Button variant="secondary" size="sm" loading={refreshing} onClick={fetchMore}>
            Fetch latest jobs
          </Button>
        }
      />

      {/* Filters */}
      <div className="mb-5 flex flex-wrap gap-1.5">
        {FILTERS.map(({ key, label }) => {
          const count = applyFilter(all, key).length;
          const isActive = filter === key;
          return (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={
                "px-3 py-1.5 rounded-md border text-[12.5px] transition " +
                (isActive
                  ? "border-accent bg-accent/10 text-accent font-semibold"
                  : "border-line text-sub hover:text-ink hover:border-line/60")
              }
            >
              {label}
              {isActive && count > 0 && (
                <span className="ml-1.5 text-[11px] opacity-75">({count})</span>
              )}
            </button>
          );
        })}
      </div>

      {/* Empty state */}
      {filtered.length === 0 && (
        <EmptyState
          title={
            all.length === 0
              ? "No jobs yet"
              : `No matches for "${FILTERS.find((f) => f.key === filter)?.label}"`
          }
          body={
            all.length === 0
              ? "Paste a job description on the Jobs page, or click Fetch latest jobs to pull in remote opportunities."
              : "Try a different filter, or fetch more jobs."
          }
        />
      )}

      {/* Match cards */}
      <div className="space-y-3">
        {filtered.map((match) => (
          <MatchCard
            key={match.job.id}
            match={match}
            onArchive={() => archiveM.mutate(match.job.id)}
            archiving={archiveM.isPending && archiveM.variables === match.job.id}
          />
        ))}
      </div>
    </div>
  );
}

// ── MatchCard ─────────────────────────────────────────────────────────────────
function MatchCard({
  match,
  onArchive,
  archiving,
}: {
  match: Match;
  onArchive: () => void;
  archiving: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const job = match.job;

  const topStrengths = match.components
    .filter((c) => c.earned > 0)
    .sort((a, b) => b.earned - a.earned)
    .slice(0, 3);

  return (
    <Card className="p-0 overflow-hidden">
      {/* Score stripe */}
      <div
        className={
          "h-1 w-full " +
          (match.score >= 70
            ? "bg-green-500"
            : match.score >= 50
            ? "bg-yellow-500"
            : "bg-red-400")
        }
      />

      <div className="p-4">
        <div className="flex items-start justify-between gap-3">
          {/* Left: job info */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <span className="font-display text-[15px] font-semibold truncate">
                {job.title}
              </span>
              <Badge tone={scoreTone(match.score)}>
                {match.score}/100 &middot; {scoreLabel(match.score)}
              </Badge>
              {job.is_stale && <Badge tone="muted">Stale</Badge>}
              {job.provider_source && job.provider_source !== "manual_import" && (
                <span className="text-[11px] text-sub capitalize px-1.5 py-0.5 bg-panel2 rounded">
                  {job.provider_source}
                </span>
              )}
            </div>

            <div className="text-[12.5px] text-sub mb-2">
              {job.company}
              {job.location ? ` \u00b7 ${job.location}` : ""}
              {" \u00b7 "}
              <span className="capitalize">{job.work_mode}</span>
              {job.salary_min ? (
                <span>
                  {" \u00b7 "}${Math.round(job.salary_min / 1000)}k
                  {job.salary_max
                    ? `\u2013$${Math.round(job.salary_max / 1000)}k`
                    : "+"}
                </span>
              ) : null}
            </div>

            {/* Summary */}
            <p className="text-[12.5px] text-ink/80 italic leading-relaxed mb-2">
              {match.summary}
            </p>

            {/* Top strengths */}
            {topStrengths.length > 0 && (
              <div className="text-[12px] text-sub flex flex-wrap gap-1.5">
                {topStrengths.map((c) => (
                  <span
                    key={c.key}
                    className="px-2 py-0.5 bg-green-50 text-green-700 rounded-full border border-green-200"
                  >
                    {c.label} +{c.earned}
                  </span>
                ))}
              </div>
            )}

            {/* Gaps */}
            {match.missing_requirements.length > 0 && (
              <div className="mt-1.5 text-[12px] text-red-600">
                Gap:{" "}
                {match.missing_requirements.slice(0, 2).join(" \u00b7 ")}
                {match.missing_requirements.length > 2 &&
                  ` (+${match.missing_requirements.length - 2} more)`}
              </div>
            )}
          </div>

          {/* Right: actions */}
          <div className="flex flex-col gap-1.5 shrink-0">
            <Link to={`/packages?job=${job.id}`}>
              <Button size="sm" variant="primary">
                Build Package
              </Button>
            </Link>
            <button
              onClick={() => setExpanded((v) => !v)}
              className="text-[12px] text-sub hover:text-ink text-center py-1"
            >
              {expanded ? "Less \u25b2" : "Details \u25bc"}
            </button>
            <Button
              size="sm"
              variant="ghost"
              loading={archiving}
              onClick={onArchive}
            >
              Archive
            </Button>
          </div>
        </div>

        {/* Expanded: score breakdown */}
        {expanded && (
          <div className="mt-3 pt-3 border-t border-line">
            <div className="text-[12px] font-medium mb-2">Score breakdown</div>
            <div className="space-y-1.5">
              {match.components.map((c) => (
                <div key={c.key} className="flex items-center gap-3">
                  <span className="text-[12px] text-sub w-40 truncate" title={c.label}>
                    {c.label}
                  </span>
                  <div className="flex-1 h-1.5 bg-line rounded-full overflow-hidden">
                    <div
                      className="h-full bg-accent rounded-full"
                      style={{
                        width: `${Math.round((c.earned / Math.max(c.weight, 1)) * 100)}%`,
                      }}
                    />
                  </div>
                  <span className="text-[11.5px] text-sub w-16 text-right">
                    {c.earned}/{c.weight}
                  </span>
                </div>
              ))}
            </div>

            {/* Must-have requirements if available */}
            {match.structured_requirements &&
              (match.structured_requirements as Record<string, unknown>).must_have &&
              ((match.structured_requirements as Record<string, unknown[]>).must_have as string[]).length > 0 && (
                <div className="mt-3">
                  <div className="text-[12px] font-medium mb-1.5">
                    Must-have requirements
                  </div>
                  <ul className="space-y-0.5">
                    {((match.structured_requirements as Record<string, unknown[]>).must_have as string[])
                      .slice(0, 5)
                      .map((req, i) => (
                        <li key={i} className="text-[12px] text-sub flex gap-1.5">
                          <span className="text-green-600 shrink-0">&middot;</span>
                          {req}
                        </li>
                      ))}
                  </ul>
                </div>
              )}

            {/* Matched skills */}
            {match.matched_skills.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {match.matched_skills.map((s) => (
                  <span
                    key={s}
                    className="text-[11px] px-2 py-0.5 bg-accent/10 text-accent rounded-full"
                  >
                    {s}
                  </span>
                ))}
              </div>
            )}

            {/* Source link */}
            {job.source_url && (
              <a
                href={job.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 text-[12px] text-accent hover:underline block"
              >
                View original posting &rarr;
              </a>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
