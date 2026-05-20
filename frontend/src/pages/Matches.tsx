import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Match } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { LoadingBlock, EmptyState } from "@/components/ui/feedback";

function bandTone(score: number): "green" | "blue" | "orange" | "red" {
  if (score >= 75) return "green";
  if (score >= 50) return "blue";
  if (score >= 25) return "orange";
  return "red";
}

export function Matches() {
  const matchesQ = useQuery<Match[]>({
    queryKey: ["matches"],
    queryFn: () => api<Match[]>("/matches"),
  });

  const [open, setOpen] = useState<Match | null>(null);

  async function loadDetail(jobId: string) {
    const m = await api<Match>(`/matches/${jobId}`);
    setOpen(m);
  }

  const matches = matchesQ.data ?? [];

  return (
    <div>
      <PageHeader
        title="Job Matches"
        sub='Each job scored 0–100 against your strategy and approved evidence. Ranking is by score alone; open one for the full "why this score" with the exact evidence behind every point.'
      />

      {matchesQ.isLoading && <LoadingBlock lines={6} />}
      {!matchesQ.isLoading && !matches.length && (
        <EmptyState
          title="No matches yet"
          body="Add evidence, set a strategy, and import jobs to see scored matches here."
        />
      )}

      <div className="grid gap-3">
        {matches.map((m) => {
          const j = m.job;
          const expanded = open && open.job.id === j.id;
          return (
            <Card key={j.id} compact>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-display text-[17px] font-semibold leading-tight">
                    {j.title}
                  </div>
                  <div className="text-[13px] text-sub mt-0.5">
                    {j.company}
                    {j.location ? ` · ${j.location}` : ""} · {j.work_mode}
                  </div>
                  {m.semantic && (
                    <div className="text-[11.5px] text-sub mt-1.5">
                      Secondary semantic signal ({m.semantic.provider}):{" "}
                      {Math.round(m.semantic.similarity * 100)}% — {m.semantic.agreement}.{" "}
                      <span className="italic">Does not affect the score or ranking.</span>
                    </div>
                  )}
                </div>
                <div className="flex flex-col items-end gap-2 shrink-0">
                  <Badge tone={bandTone(m.score)} className="text-[13px] px-2.5 py-1">
                    {m.score}/100
                  </Badge>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => (expanded ? setOpen(null) : loadDetail(j.id))}
                  >
                    {expanded ? "Hide" : "Why this score?"}
                  </Button>
                </div>
              </div>

              {expanded && open && (
                <div className="mt-4 pt-4 border-t border-line">
                  <div className="text-[12.5px] text-sub mb-3">{open.summary}</div>
                  <div className="space-y-3">
                    {open.components.map((c, i) => {
                      const frac = c.weight ? c.earned / c.weight : 0;
                      return (
                        <div key={i}>
                          <div className="flex items-baseline justify-between text-[13px]">
                            <span className="text-ink/90">{c.label}</span>
                            <span className="text-sub font-mono text-[12px]">
                              {c.earned}/{c.weight}
                            </span>
                          </div>
                          <div className="h-1.5 bg-panel2 rounded overflow-hidden mt-1">
                            <div
                              className="h-full bg-accent transition-[width]"
                              style={{
                                width: `${Math.max(0, Math.min(1, frac)) * 100}%`,
                              }}
                            />
                          </div>
                          <div className="text-[11.5px] text-sub mt-1">{c.detail}</div>
                          {(c.evidence || []).length > 0 && (
                            <div className="mt-1.5 pl-2.5 border-l-2 border-line space-y-0.5">
                              {(c.evidence || []).map((e, k) => (
                                <div key={k} className="text-[11.5px] text-sub italic">
                                  ↳ “{e.snippet}”
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  {!!open.matched_skills.length && (
                    <div className="flex flex-wrap gap-1.5 mt-3">
                      {open.matched_skills.map((s, i) => (
                        <Badge key={i} tone="green">
                          ✓ {s}
                        </Badge>
                      ))}
                    </div>
                  )}

                  {!!open.missing_requirements.length && (
                    <div className="mt-3 text-[12px] text-sub bg-panel2 border border-line rounded-md p-2.5">
                      <span className="text-ink">Gaps:</span>{" "}
                      {open.missing_requirements.join("; ")}
                    </div>
                  )}
                </div>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
