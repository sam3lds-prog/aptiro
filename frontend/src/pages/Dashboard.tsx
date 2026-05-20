import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import type { Health, OnboardingStatus } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { LoadingBlock } from "@/components/ui/feedback";
import { ProvenanceBadge } from "@/components/ProvenanceBadge";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export function Dashboard() {
  const onboardingQ = useQuery<OnboardingStatus>({
    queryKey: ["onboarding"],
    queryFn: () => api<OnboardingStatus>("/onboarding"),
  });
  const healthQ = useQuery<Health>({
    queryKey: ["health"],
    queryFn: () => api<Health>("/health"),
  });

  return (
    <div>
      <PageHeader
        title="Dashboard"
        sub="Every claim and every exported line traces back to approved evidence. Nothing is fabricated; nothing is auto-submitted."
      />

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Onboarding checklist */}
        <Card
          className="lg:col-span-2"
          title={
            <div className="flex items-baseline gap-2">
              <span>Getting started</span>
              {onboardingQ.data && (
                <span className="text-sub text-[12px] font-normal">
                  {onboardingQ.data.completed}/{onboardingQ.data.total} done
                </span>
              )}
            </div>
          }
        >
          {onboardingQ.isLoading && <LoadingBlock lines={5} />}
          {onboardingQ.data && (
            <div className="space-y-1.5">
              {onboardingQ.data.steps.map((s) => (
                <div
                  key={s.key}
                  className="flex items-center justify-between py-1.5 border-b border-line/60 last:border-0"
                >
                  <div className="flex items-center gap-2.5 text-[13.5px]">
                    <span
                      className={
                        s.done
                          ? "text-prov-green"
                          : "text-sub/70"
                      }
                      aria-hidden
                    >
                      {s.done ? "●" : "○"}
                    </span>
                    <span className={s.done ? "text-ink" : "text-sub"}>{s.label}</span>
                  </div>
                  <Badge tone={s.done ? "green" : "neutral"}>{s.done ? "done" : "todo"}</Badge>
                </div>
              ))}
              {onboardingQ.data.next_step && (
                <div className="mt-3 text-[12.5px] text-sub bg-panel2 border border-line rounded-md p-2.5">
                  <span className="text-ink/80">Next:</span> {onboardingQ.data.next_step}
                </div>
              )}
            </div>
          )}
        </Card>

        {/* Provenance legend */}
        <Card title="Provenance legend">
          <div className="space-y-2.5">
            <ProvenanceBadge color="blue" label="grounded résumé truth" />
            <ProvenanceBadge color="purple" label="profile-derived" />
            <ProvenanceBadge color="green" label="public context" />
            <ProvenanceBadge color="orange" label="AI-suggested (connective)" />
            <ProvenanceBadge color="red" label="unsupported — never exported" />
          </div>
        </Card>

        {/* Status */}
        <Card title="Status" className="lg:col-span-2">
          {healthQ.isLoading && <LoadingBlock lines={4} />}
          {healthQ.data && (
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-[13px]">
              <Row k="App" v={healthQ.data.app || "Aptiro"} />
              <Row k="Slice" v={healthQ.data.slice || `Phase ${healthQ.data.phase ?? "-"}`} />
              <Row k="AI provider" v={healthQ.data.providers?.ai || "mock"} />
              <Row k="Job provider" v={healthQ.data.providers?.job || "mock"} />
              <Row k="Ingestion" v={(healthQ.data.ingestion_formats || []).join(", ")} />
              <Row k="Export" v={(healthQ.data.export_formats || []).join(", ")} />
              <Row
                k="Auth"
                v={healthQ.data.auth?.enabled ? "enabled" : "off (single-user)"}
              />
              <Row
                k="AI grounding gate"
                v={healthQ.data.ai_assist?.grounding_gate ? "on" : "—"}
              />
            </div>
          )}
        </Card>

        {/* Quick actions */}
        <Card title="Flow">
          <p className="text-[12.5px] text-sub leading-relaxed mb-3">
            Vault → Strategy → Jobs → Matches → Package → Export. Use the
            quick links below.
          </p>
          <div className="flex flex-wrap gap-1.5">
            <Link to="/vault">
              <Button size="sm">Profile Vault</Button>
            </Link>
            <Link to="/packages">
              <Button size="sm" variant="secondary">
                Packages
              </Button>
            </Link>
            <Link to="/matches">
              <Button size="sm" variant="secondary">
                Matches
              </Button>
            </Link>
          </div>
        </Card>
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line/40 py-1.5 last:border-0">
      <span className="text-sub">{k}</span>
      <span className="text-ink/90 font-mono text-[12px] truncate">{v || "—"}</span>
    </div>
  );
}
