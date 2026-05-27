/**
 * Research.tsx — Public Research Module (Upgrade Phase 6)
 *
 * Three-pane layout:
 *   Left   — Profile seeds: which approved claims drive the queries
 *   Center — Findings feed: all findings with filter + approval actions
 *   Right  — Finding detail: source, full text, classification, approval
 *
 * Safety principle mirrored from Truth Vault:
 *   Approved findings suggest framing. They NEVER become claims or bullets
 *   automatically. The UI makes this unmistakably clear at every point.
 *
 * FIXES in this version:
 *   1. runResearch + patchFinding: removed JSON.stringify() — api() already
 *      serialises the body. Double-encoding caused FastAPI 422 errors.
 *   2. All <select> elements: added style={{ colorScheme:"dark" }} so native
 *      <option> elements render readable text in dark-themed browsers.
 *      (CSS custom properties don't propagate into native option lists.)
 *   3. Added "Results/query:" label next to the limitPerQuery select so users
 *      know it controls the next Run Research call, not a live filter.
 *   4. Added "Filter:" label + "Clear filters" shortcut for better UX.
 *   5. Empty state now distinguishes "no findings at all" from "nothing
 *      matches the current filter".
 */

import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type {
  ResearchFinding,
  ResearchUsageClass,
  ResearchApprovalStatus,
  GenerateQueriesOut,
  RunResearchOut,
} from "../lib/types";
import { useNotify } from "../stores/toast";
import { cn } from "../lib/cn";

// ─── shared primitives (inline — matching existing page style) ────────────

function PageHeader({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between mb-6">
      <div>
        <h1 className="text-2xl font-display font-semibold text-ink">
          {title}
        </h1>
        {subtitle && (
          <p className="mt-1 text-sm text-ink-muted">{subtitle}</p>
        )}
      </div>
      {children && <div className="flex gap-2">{children}</div>}
    </div>
  );
}

function Card({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-xl border border-surface-2 bg-surface-1 p-4",
        className
      )}
    >
      {children}
    </div>
  );
}

function Button({
  children,
  variant = "primary",
  size = "sm",
  loading = false,
  disabled = false,
  onClick,
  className,
}: {
  children: React.ReactNode;
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  loading?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  className?: string;
}) {
  const base =
    "inline-flex items-center gap-1.5 font-medium rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-brand/50 disabled:opacity-50 disabled:cursor-not-allowed";
  const sizes = { sm: "text-xs px-3 py-1.5", md: "text-sm px-4 py-2" };
  const variants = {
    primary: "bg-brand text-white hover:bg-brand-dark",
    secondary:
      "bg-surface-2 text-ink border border-surface-3 hover:bg-surface-3",
    ghost: "text-ink-muted hover:text-ink hover:bg-surface-2",
    danger: "bg-red-600 text-white hover:bg-red-700",
  };
  return (
    <button
      className={cn(base, sizes[size], variants[variant], className)}
      disabled={disabled || loading}
      onClick={onClick}
    >
      {loading && (
        <svg
          className="w-3.5 h-3.5 animate-spin"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
          />
        </svg>
      )}
      {children}
    </button>
  );
}

function Badge({
  children,
  color = "neutral",
}: {
  children: React.ReactNode;
  color?: "neutral" | "green" | "yellow" | "red" | "blue" | "purple";
}) {
  const colors = {
    neutral: "bg-surface-2 text-ink-muted",
    green: "bg-green-500/15 text-green-400",
    yellow: "bg-yellow-500/15 text-yellow-400",
    red: "bg-red-500/15 text-red-400",
    blue: "bg-blue-500/15 text-blue-400",
    purple: "bg-purple-500/15 text-purple-400",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center text-xs font-medium px-2 py-0.5 rounded-full",
        colors[color]
      )}
    >
      {children}
    </span>
  );
}

function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn("animate-pulse rounded bg-surface-2", className)}
    />
  );
}

// ─── domain helpers ───────────────────────────────────────────────────────

const USAGE_CLASS_META: Record<
  ResearchUsageClass,
  { label: string; color: "blue" | "green" | "purple" | "red"; desc: string }
> = {
  background_context: {
    label: "Background",
    color: "blue",
    desc: "General industry context — informs tone, not a direct claim.",
  },
  claim_support: {
    label: "Claim Support",
    color: "green",
    desc: "Public evidence corroborating an existing approved claim.",
  },
  framing_only: {
    label: "Framing Suggestion",
    color: "purple",
    desc: "Suggested angle you may adapt — you write the actual claim.",
  },
  not_usable: {
    label: "Not Usable",
    color: "red",
    desc: "Cannot be safely used. Cannot be approved.",
  },
};

const APPROVAL_META: Record<
  ResearchApprovalStatus,
  { label: string; color: "neutral" | "green" | "red" }
> = {
  pending: { label: "Pending", color: "neutral" },
  approved: { label: "Approved", color: "green" },
  rejected: { label: "Rejected", color: "red" },
};

// ─── API calls ────────────────────────────────────────────────────────────

const fetchGenerateQueries = () =>
  api<GenerateQueriesOut>("/research/generate-queries");

const fetchFindings = (approvalFilter: string, usageFilter: string) => {
  const params = new URLSearchParams();
  if (approvalFilter) params.set("approval_status", approvalFilter);
  if (usageFilter) params.set("usage_class", usageFilter);
  const qs = params.toString();
  return api<ResearchFinding[]>(`/research/findings${qs ? "?" + qs : ""}`);
};

// FIX 1: body is now a plain object — api() calls JSON.stringify() itself.
// Previously: body: JSON.stringify({...}) → api() would stringify again →
// FastAPI received a double-encoded string → 422 Unprocessable Entity.
const runResearch = (limitPerQuery: number) =>
  api<RunResearchOut>("/research/profile-contributions", {
    method: "POST",
    body: { limit_per_query: limitPerQuery },
  });

const patchFinding = (
  id: string,
  body: { usage_class?: ResearchUsageClass; approval_status?: ResearchApprovalStatus }
) =>
  api<ResearchFinding>(`/research/findings/${id}`, {
    method: "PATCH",
    body, // FIX 1: was JSON.stringify(body)
  });

const deleteFinding = (id: string) =>
  api<void>(`/research/findings/${id}`, { method: "DELETE" });

// ─── sub-components ───────────────────────────────────────────────────────

function ProfileSeedsPanel({
  queriesData,
  queriesLoading,
}: {
  queriesData: GenerateQueriesOut | undefined;
  queriesLoading: boolean;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div>
        <p className="text-sm font-medium text-ink mb-1">Profile Seeds</p>
        <p className="text-xs text-ink-muted leading-relaxed">
          Queries are auto-generated from your approved claims. The more
          claims you approve in Vault, the richer the research.
        </p>
      </div>

      {queriesLoading ? (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      ) : queriesData && queriesData.queries.length > 0 ? (
        <>
          <p className="text-xs text-ink-muted">
            <span className="font-medium text-ink">
              {queriesData.approved_claim_count}
            </span>{" "}
            approved claims →{" "}
            <span className="font-medium text-ink">
              {queriesData.queries.length}
            </span>{" "}
            queries
          </p>
          <div className="space-y-1.5 max-h-80 overflow-y-auto pr-1">
            {queriesData.queries.map((q, i) => (
              <div key={i} className="rounded-lg bg-surface-2 px-3 py-2">
                <p className="text-xs text-ink font-medium line-clamp-2">
                  {q.query}
                </p>
                <p className="text-[11px] text-ink-muted mt-0.5 line-clamp-1">
                  {q.rationale}
                </p>
              </div>
            ))}
          </div>
        </>
      ) : (
        <div className="rounded-lg bg-surface-2 px-3 py-4 text-center">
          {queriesData && queriesData.approved_claim_count > 0 ? (
            <p className="text-xs text-ink-muted">
              <span className="font-medium text-ink">
                {queriesData.approved_claim_count}
              </span>{" "}
              approved claims found.{" "}
              Click <strong>Run Research</strong> — queries will be
              generated from your claim content.
            </p>
          ) : (
            <p className="text-xs text-ink-muted">
              No approved claims yet. Go to{" "}
              <a href="/vault" className="text-brand underline">
                Vault
              </a>{" "}
              and approve claims to generate research queries.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function FindingCard({
  finding,
  selected,
  onSelect,
}: {
  finding: ResearchFinding;
  selected: boolean;
  onSelect: () => void;
}) {
  const usage = USAGE_CLASS_META[finding.usage_class];
  const approval = APPROVAL_META[finding.approval_status];

  return (
    <button
      className={cn(
        "w-full text-left rounded-xl border p-3 transition-colors",
        selected
          ? "border-brand bg-brand/5"
          : "border-surface-2 bg-surface-1 hover:border-surface-3"
      )}
      onClick={onSelect}
    >
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <p className="text-xs font-medium text-ink line-clamp-2 flex-1">
          {finding.source_title ?? "Untitled source"}
        </p>
        <div className="flex gap-1 shrink-0">
          <Badge color={usage.color}>{usage.label}</Badge>
          <Badge color={approval.color}>{approval.label}</Badge>
        </div>
      </div>
      <p className="text-[11px] text-ink-muted line-clamp-2 leading-relaxed">
        {finding.finding_text}
      </p>
      <p className="mt-1.5 text-[11px] text-ink-muted/60 truncate">
        {finding.source_url ?? ""}
      </p>
    </button>
  );
}

function FindingDetail({
  finding,
  onApprove,
  onReject,
  onClassify,
  onDelete,
  approving,
  rejecting,
}: {
  finding: ResearchFinding;
  onApprove: () => void;
  onReject: () => void;
  onClassify: (cls: ResearchUsageClass) => void;
  onDelete: () => void;
  approving: boolean;
  rejecting: boolean;
}) {
  const usage = USAGE_CLASS_META[finding.usage_class];
  const approval = APPROVAL_META[finding.approval_status];
  const isNotUsable = finding.usage_class === "not_usable";
  const isApproved = finding.approval_status === "approved";
  const isRejected = finding.approval_status === "rejected";

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 flex-wrap mb-1">
          <Badge color={usage.color}>{usage.label}</Badge>
          <Badge color={approval.color}>{approval.label}</Badge>
        </div>
        <h3 className="text-sm font-semibold text-ink mt-2">
          {finding.source_title ?? "Research finding"}
        </h3>
        {finding.source_url && (
          <a
            href={finding.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-brand underline break-all"
          >
            {finding.source_url}
          </a>
        )}
      </div>

      {/* Source snippet */}
      {finding.source_snippet && (
        <div>
          <p className="text-[11px] font-semibold text-ink-muted uppercase tracking-wide mb-1">
            Source snippet
          </p>
          <blockquote className="border-l-2 border-brand/40 pl-3 text-xs text-ink-muted italic leading-relaxed">
            {finding.source_snippet}
          </blockquote>
        </div>
      )}

      {/* Full finding */}
      <div>
        <p className="text-[11px] font-semibold text-ink-muted uppercase tracking-wide mb-1">
          Finding
        </p>
        <p className="text-xs text-ink leading-relaxed">
          {finding.finding_text}
        </p>
      </div>

      {/* Suggested framing — clearly labeled NOT a claim */}
      {finding.suggested_framing && (
        <div className="rounded-lg border border-purple-500/20 bg-purple-500/5 p-3">
          <div className="flex items-center gap-1.5 mb-1.5">
            <svg
              className="w-3.5 h-3.5 text-purple-400 shrink-0"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.347.346a2.25 2.25 0 01-3.182 0l-.346-.346z"
              />
            </svg>
            <p className="text-[11px] font-semibold text-purple-400 uppercase tracking-wide">
              Suggested framing — not a claim
            </p>
          </div>
          <p className="text-xs text-ink-muted leading-relaxed">
            {finding.suggested_framing}
          </p>
          <p className="mt-2 text-[11px] text-ink-muted/60 italic">
            This is a suggested angle only. You must write the actual claim
            from your own approved evidence.
          </p>
        </div>
      )}

      {/* Classification selector */}
      <div>
        <p className="text-[11px] font-semibold text-ink-muted uppercase tracking-wide mb-2">
          Usage classification
        </p>
        <div className="grid grid-cols-2 gap-1.5">
          {(
            Object.entries(USAGE_CLASS_META) as [
              ResearchUsageClass,
              (typeof USAGE_CLASS_META)[ResearchUsageClass]
            ][]
          ).map(([cls, meta]) => (
            <button
              key={cls}
              className={cn(
                "text-left rounded-lg border px-2.5 py-2 text-xs transition-colors",
                finding.usage_class === cls
                  ? "border-brand bg-brand/10 text-ink"
                  : "border-surface-2 text-ink-muted hover:border-surface-3 hover:text-ink"
              )}
              onClick={() => onClassify(cls)}
            >
              <p className="font-medium">{meta.label}</p>
              <p className="text-[10px] mt-0.5 leading-tight opacity-70">
                {meta.desc}
              </p>
            </button>
          ))}
        </div>
      </div>

      {/* Approval actions */}
      <div className="flex gap-2 pt-1">
        {!isApproved && (
          <Button
            variant="primary"
            size="sm"
            loading={approving}
            disabled={isNotUsable}
            onClick={onApprove}
            className="flex-1"
          >
            {isNotUsable ? "Cannot approve (not usable)" : "Approve"}
          </Button>
        )}
        {isApproved && (
          <Button
            variant="secondary"
            size="sm"
            loading={rejecting}
            onClick={onReject}
            className="flex-1"
          >
            Revoke approval
          </Button>
        )}
        {!isApproved && !isRejected && (
          <Button
            variant="secondary"
            size="sm"
            loading={rejecting}
            onClick={onReject}
          >
            Reject
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={onDelete}>
          <svg
            className="w-3.5 h-3.5"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
            />
          </svg>
        </Button>
      </div>

      {isNotUsable && (
        <p className="text-[11px] text-red-400 leading-snug">
          ⚠ Findings classified as "Not Usable" cannot be approved. Change
          the classification first if you believe this finding is usable.
        </p>
      )}

      {/* Provenance chain */}
      {finding.prompted_by_claim_ids.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold text-ink-muted uppercase tracking-wide mb-1">
            Generated from {finding.prompted_by_claim_ids.length} profile
            claim{finding.prompted_by_claim_ids.length > 1 ? "s" : ""}
          </p>
          <p className="text-[11px] text-ink-muted/60">
            Query: <span className="italic">{finding.query}</span>
          </p>
        </div>
      )}
    </div>
  );
}

// ─── main page ────────────────────────────────────────────────────────────

export default function Research() {
  const qc = useQueryClient();
  const notify = useNotify();

  const [selected, setSelected] = useState<ResearchFinding | null>(null);
  const [approvalFilter, setApprovalFilter] = useState("");
  const [usageFilter, setUsageFilter] = useState("");
  const [limitPerQuery, setLimitPerQuery] = useState(3);

  // Queries
  const { data: queriesData, isLoading: queriesLoading } = useQuery({
    queryKey: ["research-queries"],
    queryFn: fetchGenerateQueries,
  });

  const {
    data: findings,
    isLoading: findingsLoading,
  } = useQuery({
    queryKey: ["research-findings", approvalFilter, usageFilter],
    queryFn: () => fetchFindings(approvalFilter, usageFilter),
  });

  // Mutations
  const runMutation = useMutation({
    mutationFn: () => runResearch(limitPerQuery),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["research-findings"] });
      qc.invalidateQueries({ queryKey: ["research-queries"] });
      if (data.findings_created > 0) {
        notify.success(
          `Found ${data.findings_created} new findings from ${data.queries_used.length} queries.`
        );
      } else {
        notify.notify("No new findings — all results were already imported.");
      }
    },
    onError: () => notify.error("Research run failed. Please try again."),
  });

  const patchMutation = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body: {
        usage_class?: ResearchUsageClass;
        approval_status?: ResearchApprovalStatus;
      };
    }) => patchFinding(id, body),
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ["research-findings"] });
      setSelected(updated);
    },
    onError: (err: any) =>
      notify.error(err?.body?.detail ?? "Could not update finding."),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteFinding(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["research-findings"] });
      setSelected(null);
      notify.success("Finding deleted.");
    },
    onError: () => notify.error("Could not delete finding."),
  });

  const handleApprove = useCallback(() => {
    if (!selected) return;
    patchMutation.mutate({
      id: selected.id,
      body: { approval_status: "approved" },
    });
  }, [selected, patchMutation]);

  const handleReject = useCallback(() => {
    if (!selected) return;
    patchMutation.mutate({
      id: selected.id,
      body: { approval_status: "rejected" },
    });
  }, [selected, patchMutation]);

  const handleClassify = useCallback(
    (cls: ResearchUsageClass) => {
      if (!selected) return;
      patchMutation.mutate({ id: selected.id, body: { usage_class: cls } });
    },
    [selected, patchMutation]
  );

  const handleDelete = useCallback(() => {
    if (!selected) return;
    deleteMutation.mutate(selected.id);
  }, [selected, deleteMutation]);

  const counts = {
    pending: findings?.filter((f) => f.approval_status === "pending").length ?? 0,
    approved: findings?.filter((f) => f.approval_status === "approved").length ?? 0,
    total: findings?.length ?? 0,
  };

  const hasActiveFilter = !!(approvalFilter || usageFilter);
  const clearFilters = () => { setApprovalFilter(""); setUsageFilter(""); };

  return (
    <div className="flex flex-col h-full p-6 gap-4 overflow-hidden">
      <PageHeader
        title="Public Research"
        subtitle="Find public context about your work. Approve what's safe — it contextualises your story but never invents claims."
      >
        {/* FIX 2 + 3: Added "Results/query:" label and colorScheme:"dark"
            so the dropdown is readable and its purpose is clear. */}
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-ink-muted whitespace-nowrap">
              Results/query:
            </span>
            <div className="relative">
              <select
                className="text-xs bg-surface-2 border border-surface-3 rounded-lg px-2 py-1.5 text-ink focus:outline-none focus:ring-2 focus:ring-brand/50 appearance-none pr-7"
                style={{ colorScheme: "dark" }}
                value={limitPerQuery}
                onChange={(e) => setLimitPerQuery(Number(e.target.value))}
              >
                <option value={1}>1</option>
                <option value={2}>2</option>
                <option value={3}>3</option>
                <option value={5}>5</option>
              </select>
              <svg
                className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-ink-muted"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </div>
          </div>
          <Button
            variant="primary"
            size="sm"
            loading={runMutation.isPending}
            onClick={() => runMutation.mutate()}
          >
            <svg
              className="w-3.5 h-3.5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
              />
            </svg>
            Run Research
          </Button>
        </div>
      </PageHeader>

      {/* Safety banner */}
      <div className="rounded-xl border border-yellow-500/20 bg-yellow-500/5 px-4 py-3 flex items-start gap-3">
        <svg
          className="w-4 h-4 text-yellow-400 shrink-0 mt-0.5"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
          />
        </svg>
        <p className="text-xs text-yellow-300 leading-relaxed">
          <strong>Safety guardrail:</strong> Approved findings can{" "}
          <em>contextualise</em> your story and{" "}
          <em>suggest framing</em>. They are never auto-added to packages or
          claims. Every bullet in an application must trace back to your own
          approved profile evidence.
        </p>
      </div>

      {/* Stats row */}
      {findings && findings.length > 0 && (
        <div className="flex gap-3">
          {[
            { label: "Total", value: counts.total, color: "text-ink" },
            {
              label: "Pending review",
              value: counts.pending,
              color: "text-yellow-400",
            },
            {
              label: "Approved",
              value: counts.approved,
              color: "text-green-400",
            },
          ].map((s) => (
            <div
              key={s.label}
              className="rounded-lg bg-surface-1 border border-surface-2 px-3 py-2"
            >
              <p className={cn("text-lg font-semibold font-mono", s.color)}>
                {s.value}
              </p>
              <p className="text-[11px] text-ink-muted">{s.label}</p>
            </div>
          ))}
        </div>
      )}

      {/* Three-pane layout */}
      <div className="flex gap-4 flex-1 min-h-0">
        {/* Left: profile seeds */}
        <Card className="w-64 shrink-0 overflow-y-auto">
          <ProfileSeedsPanel
            queriesData={queriesData}
            queriesLoading={queriesLoading}
          />
        </Card>

        {/* Center: findings feed */}
        <div className="flex flex-col flex-1 min-w-0 gap-3 overflow-hidden">
          {/* FIX 4: Added "Filter:" label and "Clear filters" shortcut.
              colorScheme:"dark" fixes option text visibility. */}
          <div className="flex gap-2 items-center flex-wrap">
            <span className="text-xs text-ink-muted">Filter:</span>
            <div className="relative">
              <select
                className="text-xs bg-surface-1 border border-surface-2 rounded-lg px-2 py-1.5 text-ink focus:outline-none focus:ring-2 focus:ring-brand/50 appearance-none pr-7"
                style={{ colorScheme: "dark" }}
                value={approvalFilter}
                onChange={(e) => setApprovalFilter(e.target.value)}
              >
                <option value="">All statuses</option>
                <option value="pending">Pending</option>
                <option value="approved">Approved</option>
                <option value="rejected">Rejected</option>
              </select>
              <svg
                className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-ink-muted"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </div>
            <div className="relative">
              <select
                className="text-xs bg-surface-1 border border-surface-2 rounded-lg px-2 py-1.5 text-ink focus:outline-none focus:ring-2 focus:ring-brand/50 appearance-none pr-7"
                style={{ colorScheme: "dark" }}
                value={usageFilter}
                onChange={(e) => setUsageFilter(e.target.value)}
              >
                <option value="">All classifications</option>
                <option value="background_context">Background</option>
                <option value="claim_support">Claim Support</option>
                <option value="framing_only">Framing Suggestion</option>
                <option value="not_usable">Not Usable</option>
              </select>
              <svg
                className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-ink-muted"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </div>
            {hasActiveFilter && (
              <button
                className="text-xs text-brand hover:underline"
                onClick={clearFilters}
              >
                Clear filters
              </button>
            )}
          </div>

          {/* Findings list */}
          <div className="flex-1 overflow-y-auto space-y-2 pr-1">
            {findingsLoading ? (
              <div className="space-y-2">
                {[1, 2, 3, 4].map((i) => (
                  <Skeleton key={i} className="h-20 w-full rounded-xl" />
                ))}
              </div>
            ) : !findings || findings.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-48 text-center">
                <svg
                  className="w-10 h-10 text-ink-muted/30 mb-3"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={1.5}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                  />
                </svg>
                <p className="text-sm text-ink-muted font-medium">
                  {hasActiveFilter
                    ? "No findings match this filter"
                    : "No findings yet"}
                </p>
                <p className="text-xs text-ink-muted/60 mt-1 max-w-xs">
                  {hasActiveFilter ? (
                    <>
                      Try a different filter or{" "}
                      <button
                        className="text-brand underline"
                        onClick={clearFilters}
                      >
                        clear filters
                      </button>
                      .
                    </>
                  ) : (
                    <>
                      Approve claims in Vault, then click{" "}
                      <strong>Run Research</strong> to discover public context
                      about your work.
                    </>
                  )}
                </p>
              </div>
            ) : (
              findings.map((f) => (
                <FindingCard
                  key={f.id}
                  finding={f}
                  selected={selected?.id === f.id}
                  onSelect={() => setSelected(f)}
                />
              ))
            )}
          </div>
        </div>

        {/* Right: detail panel */}
        <Card className="w-80 shrink-0 overflow-y-auto">
          {selected ? (
            <FindingDetail
              finding={selected}
              onApprove={handleApprove}
              onReject={handleReject}
              onClassify={handleClassify}
              onDelete={handleDelete}
              approving={
                patchMutation.isPending &&
                (patchMutation.variables as any)?.body?.approval_status ===
                  "approved"
              }
              rejecting={
                patchMutation.isPending &&
                (patchMutation.variables as any)?.body?.approval_status ===
                  "rejected"
              }
            />
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-center py-12">
              <svg
                className="w-8 h-8 text-ink-muted/30 mb-3"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1.5}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 01-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 011.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46-3.243-8.161-7.5-8.876a9.06 9.06 0 00-1.5-.124H9.375c-.621 0-1.125.504-1.125 1.125v3.5m7.5 10.375H9.375a1.125 1.125 0 01-1.125-1.125v-9.25m12 6.625v-1.875a3.375 3.375 0 00-3.375-3.375h-1.5a1.125 1.125 0 01-1.125-1.125v-1.5a3.375 3.375 0 00-3.375-3.375H9.75"
                />
              </svg>
              <p className="text-sm text-ink-muted">Select a finding</p>
              <p className="text-xs text-ink-muted/60 mt-1">
                Click any finding to classify and review it here.
              </p>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
