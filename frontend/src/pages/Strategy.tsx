import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type {
  Strategy as StrategyT,
  StrategyListItem,
  StrategyPreview,
  StrategyUpsertBody,
  WorkMode,
  Aggressiveness,
} from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { LoadingBlock, EmptyState } from "@/components/ui/feedback";
import { ConfirmModal } from "@/components/ConfirmModal";
import { useNotify } from "@/stores/toast";
import { cn } from "@/lib/cn";

// ----- constants ------------------------------------------------------

const WORK_MODES: WorkMode[] = ["any", "remote", "hybrid", "onsite"];

const AGG_OPTIONS: { value: Aggressiveness; label: string; help: string }[] = [
  {
    value: "conservative",
    label: "Conservative",
    help: "Strong matches only. Higher seniority and role-alignment expectations.",
  },
  {
    value: "balanced",
    label: "Balanced",
    help: "Default. Even weighting across signals.",
  },
  {
    value: "opportunistic",
    label: "Opportunistic",
    help: "Wider net. More credit to transferable evidence and stretch roles.",
  },
];

/** Display order + human-readable labels for the nine scoring
 *  components. Keys match DEFAULT_WEIGHTS in backend/app.py. */
const WEIGHT_FIELDS: { key: string; label: string; hint: string }[] = [
  { key: "role_alignment",      label: "Role alignment",      hint: "How closely the job title matches your target roles." },
  { key: "core_skills",         label: "Core skills",         hint: "Skill overlap between the job and your approved evidence." },
  { key: "seniority_alignment", label: "Seniority",           hint: "Your role history vs the job's seniority signal." },
  { key: "leadership_scope",    label: "Leadership scope",    hint: "Leadership signal in your claims vs the job." },
  { key: "ai_technical",        label: "AI / technical",      hint: "AI/technical overlap, especially for AI-heavy roles." },
  { key: "domain",              label: "Domain",              hint: "Industry/domain overlap (healthcare, SaaS, etc.)." },
  { key: "evidence_strength",   label: "Evidence strength",   hint: "How well-backed your relevant claims are." },
  { key: "preferences",         label: "Preferences",         hint: "Work mode + salary fit against your prefs." },
  { key: "strategy_boost",      label: "Strategy boost",      hint: "Include-list / targeting-note signal in the job." },
];

const DEFAULT_WEIGHTS: Record<string, number> = {
  role_alignment: 15, seniority_alignment: 10, core_skills: 20,
  domain: 10, leadership_scope: 10, ai_technical: 10,
  evidence_strength: 10, preferences: 10, strategy_boost: 5,
};

const arrToText = (v?: string[]) => (v ? v.join(", ") : "");
const textToArr = (v: string) =>
  v.split(",").map((x) => x.trim()).filter(Boolean);

// ----- component ------------------------------------------------------

export function Strategy() {
  const qc = useQueryClient();
  const notify = useNotify();

  const listQ = useQuery<StrategyListItem[]>({
    queryKey: ["strategies"],
    queryFn: () => api<StrategyListItem[]>("/strategies"),
  });

  const [selectedId, setSelectedId] = useState<string | null>(null);

  // When the list loads, default-select the active strategy.
  useEffect(() => {
    if (!selectedId && listQ.data && listQ.data.length) {
      const active = listQ.data.find((s) => s.is_active) ?? listQ.data[0];
      setSelectedId(active.id);
    }
  }, [listQ.data, selectedId]);

  const strategies = listQ.data ?? [];
  const noStrategies = !listQ.isLoading && strategies.length === 0;

  async function seedPresets() {
    try {
      const r = await api<{ created: StrategyListItem[]; skipped_existing: string[] }>(
        "/strategies/seed-presets",
        { method: "POST" }
      );
      qc.invalidateQueries({ queryKey: ["strategies"] });
      if (r.created.length) {
        notify.success(
          `Added ${r.created.length} preset${r.created.length === 1 ? "" : "s"}.${
            r.skipped_existing.length ? ` Skipped ${r.skipped_existing.length} you already had.` : ""
          }`
        );
      } else {
        notify.warn("All six presets already on your account.");
      }
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Could not seed presets.");
    }
  }

  async function createBlank() {
    try {
      const created = await api<StrategyT>("/strategies", {
        method: "POST",
        body: {
          name: "New strategy",
          target_roles: [],
          region: null,
          work_mode: "any" as WorkMode,
          salary_min: null,
          salary_max: null,
          aggressiveness: "balanced",
          weights: { ...DEFAULT_WEIGHTS },
          include_companies: [],
          exclude_companies: [],
          targeting_notes: "",
          score_threshold: 50,
        } as StrategyUpsertBody,
      });
      await qc.invalidateQueries({ queryKey: ["strategies"] });
      setSelectedId(created.id);
      notify.success("New strategy created.");
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Could not create strategy.");
    }
  }

  return (
    <div>
      <PageHeader
        title="Strategy"
        sub="Targeting + explainable scoring weights. The active strategy drives Matches and Packages; preview alternatives without saving."
        actions={
          <div className="flex gap-2">
            <Button variant="ghost" onClick={seedPresets}>Seed presets</Button>
            <Button onClick={createBlank}>+ New</Button>
          </div>
        }
      />

      {listQ.isLoading && (
        <Card><LoadingBlock lines={8} /></Card>
      )}

      {noStrategies && (
        <EmptyState
          title="No strategies yet"
          body="Aptiro ships six tuned presets: AI PM, Healthcare AI PM, Senior Product Leadership, Nonprofit / Mission Tech, Enterprise SaaS PM, and Adjacent Stretch. Add them in one click, or start blank."
        action={
            <div className="flex gap-2">
              <Button onClick={seedPresets}>Seed all six presets</Button>
              <Button variant="ghost" onClick={createBlank}>Start blank</Button>
            </div>
          }
        />
      )}

      {!listQ.isLoading && strategies.length > 0 && (
        <div className="grid gap-5 md:grid-cols-[260px_1fr]">
          <StrategyList
            items={strategies}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
          {selectedId && (
            <StrategyEditor
              key={selectedId}
              strategyId={selectedId}
              onDeleted={() => {
                setSelectedId(null);
                qc.invalidateQueries({ queryKey: ["strategies"] });
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

// ----- list rail -----------------------------------------------------

function StrategyList({
  items, selectedId, onSelect,
}: {
  items: StrategyListItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <Card compact>
      <div className="space-y-1">
        {items.map((s) => {
          const selected = s.id === selectedId;
          return (
            <button
              key={s.id}
              onClick={() => onSelect(s.id)}
              className={cn(
                "w-full text-left px-3 py-2.5 rounded-md transition-colors",
                "border border-transparent",
                selected
                  ? "bg-panel2 border-line"
                  : "hover:bg-panel2/60"
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-[13.5px] leading-tight truncate">
                  {s.name || "Untitled strategy"}
                </span>
                {s.is_active && (
                  <Badge tone="green" className="text-[10.5px] px-1.5 py-0.5 shrink-0">
                    active
                  </Badge>
                )}
              </div>
              <div className="text-[11.5px] text-sub mt-1 flex gap-2">
                <span className="capitalize">{s.aggressiveness}</span>
                <span aria-hidden>·</span>
                <span>≥ {s.score_threshold}/100</span>
                <span aria-hidden>·</span>
                <span>{s.work_mode}</span>
              </div>
            </button>
          );
        })}
      </div>
    </Card>
  );
}

// ----- editor + live preview -----------------------------------------

function StrategyEditor({
  strategyId, onDeleted,
}: {
  strategyId: string;
  onDeleted: () => void;
}) {
  const qc = useQueryClient();
  const notify = useNotify();

  const q = useQuery<StrategyT>({
    queryKey: ["strategy", strategyId],
    queryFn: () => api<StrategyT>(`/strategies/${strategyId}`),
  });

  const [draft, setDraft] = useState<StrategyT | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    if (q.data) {
      setDraft(q.data);
    }
  }, [q.data]);

  // Debounced live preview — fires when sliders / fields settle.
  const [preview, setPreview] = useState<StrategyPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const previewTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!draft) return;
    if (previewTimer.current) window.clearTimeout(previewTimer.current);
    previewTimer.current = window.setTimeout(async () => {
      try {
        setPreviewing(true);
        const body: StrategyUpsertBody = {
          name: draft.name,
          target_roles: draft.target_roles,
          region: draft.region,
          work_mode: draft.work_mode,
          salary_min: draft.salary_min,
          salary_max: draft.salary_max,
          aggressiveness: draft.aggressiveness,
          weights: draft.weights,
          include_companies: draft.include_companies,
          exclude_companies: draft.exclude_companies,
          targeting_notes: draft.targeting_notes,
          score_threshold: draft.score_threshold,
        };
        const out = await api<StrategyPreview>("/strategies/preview", {
          method: "POST",
          body,
        });
        setPreview(out);
      } catch {
        // Preview failures are silent — the editor still works.
      } finally {
        setPreviewing(false);
      }
    }, 250) as unknown as number;
    return () => {
      if (previewTimer.current) window.clearTimeout(previewTimer.current);
    };
    // The dependencies below are the actual draft-defining values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    draft?.name, draft?.aggressiveness, draft?.score_threshold,
    draft?.work_mode, draft?.salary_min, draft?.salary_max,
    JSON.stringify(draft?.weights),
    JSON.stringify(draft?.target_roles),
    JSON.stringify(draft?.include_companies),
    JSON.stringify(draft?.exclude_companies),
    draft?.targeting_notes,
  ]);

  if (q.isLoading || !draft) {
    return <Card><LoadingBlock lines={10} /></Card>;
  }

  const set = <K extends keyof StrategyT>(k: K, v: StrategyT[K]) =>
    setDraft({ ...draft, [k]: v });

  const wsum = Object.values(draft.weights || {})
    .reduce((a, b) => a + (Number(b) || 0), 0);

  async function save() {
    try {
      setBusy(true);
      const body: StrategyUpsertBody = {
        name: draft!.name,
        target_roles: draft!.target_roles,
        region: draft!.region,
        work_mode: draft!.work_mode,
        salary_min: draft!.salary_min,
        salary_max: draft!.salary_max,
        aggressiveness: draft!.aggressiveness,
        weights: draft!.weights,
        include_companies: draft!.include_companies,
        exclude_companies: draft!.exclude_companies,
        targeting_notes: draft!.targeting_notes,
        score_threshold: draft!.score_threshold,
      };
      const r = await api<StrategyT>(`/strategies/${draft!.id}`, {
        method: "PUT",
        body,
      });
      setDraft(r);
      await qc.invalidateQueries({ queryKey: ["strategies"] });
      await qc.invalidateQueries({ queryKey: ["matches"] });
      await qc.invalidateQueries({ queryKey: ["strategy"] });
      notify.success("Strategy saved.");
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Save failed.");
    } finally {
      setBusy(false);
    }
  }

  async function activate() {
    try {
      setBusy(true);
      await api<StrategyT>(`/strategies/${draft!.id}/activate`, {
        method: "POST",
      });
      await qc.invalidateQueries({ queryKey: ["strategies"] });
      await qc.invalidateQueries({ queryKey: ["matches"] });
      await qc.invalidateQueries({ queryKey: ["strategy"] });
      await qc.invalidateQueries({ queryKey: ["strategy", draft!.id] });
      notify.success(`"${draft!.name}" is now your active strategy.`);
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Activate failed.");
    } finally {
      setBusy(false);
    }
  }

  async function duplicate() {
    try {
      setBusy(true);
      const body: StrategyUpsertBody = {
        name: `${draft!.name} (copy)`,
        target_roles: draft!.target_roles,
        region: draft!.region,
        work_mode: draft!.work_mode,
        salary_min: draft!.salary_min,
        salary_max: draft!.salary_max,
        aggressiveness: draft!.aggressiveness,
        weights: draft!.weights,
        include_companies: draft!.include_companies,
        exclude_companies: draft!.exclude_companies,
        targeting_notes: draft!.targeting_notes,
        score_threshold: draft!.score_threshold,
      };
      const created = await api<StrategyT>("/strategies", {
        method: "POST",
        body,
      });
      await qc.invalidateQueries({ queryKey: ["strategies"] });
      notify.success(`Cloned to "${created.name}".`);
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Duplicate failed.");
    } finally {
      setBusy(false);
    }
  }

  async function doDelete() {
    try {
      await api(`/strategies/${draft!.id}`, { method: "DELETE" });
      setConfirmDelete(false);
      notify.success(`"${draft!.name}" deleted.`);
      onDeleted();
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Delete failed.");
      setConfirmDelete(false);
    }
  }

  return (
    <div className="space-y-5">
      {/* header row */}
      <Card compact>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex-1 min-w-[200px]">
            <Label>Name</Label>
            <Input
              value={draft.name || ""}
              onChange={(e) => set("name", e.target.value)}
              placeholder="e.g. AI PM"
            />
          </div>
          <div className="flex items-center gap-2 self-end">
            {draft.is_active ? (
              <Badge tone="green" className="px-2.5 py-1">Active strategy</Badge>
            ) : (
              <Button variant="secondary" onClick={activate} loading={busy}>
                Make active
              </Button>
            )}
            <Button variant="ghost" onClick={duplicate} loading={busy}>
              Duplicate
            </Button>
            <Button
              variant="danger"
              onClick={() => setConfirmDelete(true)}
              disabled={busy}
            >
              Delete
            </Button>
            <Button onClick={save} loading={busy}>Save</Button>
          </div>
        </div>
      </Card>

      <div className="grid gap-5 md:grid-cols-[1fr_320px]">
        {/* editor column */}
        <div className="space-y-5">
          <Card title="Approach">
            <div className="grid gap-2 md:grid-cols-3">
              {AGG_OPTIONS.map((opt) => {
                const selected = draft.aggressiveness === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => set("aggressiveness", opt.value)}
                    className={cn(
                      "text-left px-3 py-2.5 rounded-md border transition-colors",
                      selected
                        ? "border-accent bg-accent/10"
                        : "border-line hover:bg-panel2/60"
                    )}
                  >
                    <div className="font-medium text-[13.5px]">{opt.label}</div>
                    <div className="text-[11.5px] text-sub mt-1 leading-snug">
                      {opt.help}
                    </div>
                  </button>
                );
              })}
            </div>

            <div className="mt-5">
              <div className="flex items-center justify-between mb-1.5">
                <Label className="mb-0">Score threshold</Label>
                <span className="text-[12px] text-sub font-mono">
                  ≥ {draft.score_threshold}/100
                </span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                step={5}
                value={draft.score_threshold}
                onChange={(e) =>
                  set("score_threshold", Number(e.target.value) || 0)
                }
                className="w-full accent-accent"
              />
              <p className="text-[11.5px] text-sub mt-1">
                Jobs scoring below this don't surface in the Match Inbox
                "above threshold" filter. 0 = no floor.
              </p>
            </div>
          </Card>

          <Card
            title={
              <span>
                Scoring weights{" "}
                <span className="text-sub text-[12px] font-normal">
                  (sum {wsum})
                </span>
              </span>
            }
          >
            <div className="space-y-4">
              {WEIGHT_FIELDS.map((f) => {
                const v = draft.weights[f.key] ?? 0;
                return (
                  <div key={f.key}>
                    <div className="flex items-center justify-between mb-1">
                      <Label className="mb-0">{f.label}</Label>
                      <span className="text-[12px] text-sub font-mono">
                        {v}
                      </span>
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={30}
                      step={1}
                      value={v}
                      onChange={(e) =>
                        set("weights", {
                          ...draft.weights,
                          [f.key]: Number(e.target.value) || 0,
                        })
                      }
                      className="w-full accent-accent"
                    />
                    <div className="text-[11px] text-sub mt-0.5">{f.hint}</div>
                  </div>
                );
              })}
            </div>
            <p className="text-[11.5px] text-sub mt-4 border-t border-line pt-3">
              Weights are summed and a job's earned points are computed
              proportionally — exact sum doesn't matter, the relative
              ratios do. Defaults sum to 100 for legibility.
            </p>
          </Card>

          <Card title="Targeting">
            <div className="grid gap-3 md:grid-cols-3">
              <div>
                <Label>Work mode</Label>
                <Select
                  value={draft.work_mode}
                  onChange={(e) => set("work_mode", e.target.value as WorkMode)}
                >
                  {WORK_MODES.map((x) => (
                    <option key={x} value={x}>{x}</option>
                  ))}
                </Select>
              </div>
              <div>
                <Label>Salary min</Label>
                <Input
                  type="number"
                  value={draft.salary_min ?? ""}
                  onChange={(e) =>
                    set("salary_min",
                        e.target.value ? Number(e.target.value) : null)
                  }
                />
              </div>
              <div>
                <Label>Salary max</Label>
                <Input
                  type="number"
                  value={draft.salary_max ?? ""}
                  onChange={(e) =>
                    set("salary_max",
                        e.target.value ? Number(e.target.value) : null)
                  }
                />
              </div>
            </div>

            <Label>Target roles (comma-separated)</Label>
            <Input
              value={arrToText(draft.target_roles)}
              onChange={(e) => set("target_roles", textToArr(e.target.value))}
              placeholder="AI Product Manager, Senior PM, Healthcare AI PM"
            />

            <Label>Region</Label>
            <Input
              value={draft.region || ""}
              onChange={(e) => set("region", e.target.value)}
              placeholder="Remote / US-Mountain / EU"
            />

            <Label>Include / priority companies</Label>
            <Input
              value={arrToText(draft.include_companies)}
              onChange={(e) =>
                set("include_companies", textToArr(e.target.value))
              }
              placeholder="Anthropic, FamilySearch, Khan Academy"
            />

            <Label>Exclude companies</Label>
            <Input
              value={arrToText(draft.exclude_companies)}
              onChange={(e) =>
                set("exclude_companies", textToArr(e.target.value))
              }
            />

            <Label>Targeting notes</Label>
            <Textarea
              rows={3}
              value={draft.targeting_notes || ""}
              onChange={(e) => set("targeting_notes", e.target.value)}
              placeholder="LLM, agentic, applied ML, healthcare AI…"
            />
          </Card>
        </div>

        {/* preview column */}
        <PreviewPanel preview={preview} previewing={previewing} />
      </div>

      <ConfirmModal
        open={confirmDelete}
        title={`Delete "${draft.name}"?`}
        body={
          draft.is_active
            ? "This is your active strategy. Deleting it will promote another strategy to active automatically. This can't be undone."
            : "This can't be undone. Matches and packages built with this strategy are not affected."
        }
        confirmLabel="Delete strategy"
        destructive
        onConfirm={doDelete}
        onClose={() => setConfirmDelete(false)}
      />
    </div>
  );
}

// ----- preview side panel --------------------------------------------

function PreviewPanel({
  preview, previewing,
}: {
  preview: StrategyPreview | null;
  previewing: boolean;
}) {
  // Heuristic explainer line — generated client-side from the preview
  // payload. The server's `summary` field is the canonical sentence;
  // this UI adds a friendly framing around the numbers.
  const why = useMemo(() => {
    if (!preview) return null;
    const c = preview.current;
    const a = preview.active;
    if (c.jobs_considered === 0) {
      return "Import some jobs to see how this strategy would rank them.";
    }
    if (!a) {
      return "This is the active strategy — these are the counts your Match Inbox shows today.";
    }
    const dStrong = c.strong - a.strong;
    const dAbove = c.above_threshold - a.above_threshold;
    if (dStrong === 0 && dAbove === 0) {
      return "Same volume as the active strategy on these jobs — the ranking order may still differ.";
    }
    const parts: string[] = [];
    if (dStrong > 0) parts.push(`${dStrong} more strong match${dStrong === 1 ? "" : "es"}`);
    if (dStrong < 0) parts.push(`${-dStrong} fewer strong match${-dStrong === 1 ? "" : "es"}`);
    if (dAbove > 0) parts.push(`${dAbove} more above your threshold`);
    if (dAbove < 0) parts.push(`${-dAbove} fewer above your threshold`);
    return `Compared to active: ${parts.join(", ")}.`;
  }, [preview]);

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          Live preview
          {previewing && (
            <span className="text-[11px] text-sub font-normal">updating…</span>
          )}
        </span>
      }
    >
      {!preview && (
        <div className="text-[12.5px] text-sub">
          Adjust any slider or field — preview runs the existing scorer over
          your imported jobs without saving anything.
        </div>
      )}

      {preview && (
        <>
          <div className="grid grid-cols-2 gap-3 mb-4">
            <Stat label="Jobs considered" value={preview.current.jobs_considered} />
            <Stat
              label={`Above ≥ ${preview.current.score_threshold}/100`}
              value={preview.current.above_threshold}
              tone="green"
            />
            <Stat label="Strong (≥75)" value={preview.current.strong} tone="green" />
            <Stat label="Moderate (50–74)" value={preview.current.moderate} tone="blue" />
            <Stat label="Weak (<50)" value={preview.current.weak} tone="orange" />
            <Stat label="Excluded" value={preview.current.excluded} tone="red" />
          </div>

          <div className="text-[11.5px] text-sub mb-3">
            Top score on these jobs: <b className="text-ink">{preview.current.top_score}/100</b>
            {" · "}
            Average: <b className="text-ink">{preview.current.avg_score.toFixed(1)}</b>
          </div>

          {why && (
            <div className="text-[12.5px] leading-snug border-t border-line pt-3 mb-3">
              {why}
            </div>
          )}

          {preview.current.threshold_passing_titles.length > 0 && (
            <div>
              <div className="text-[11.5px] text-sub mb-1.5">
                Top {preview.current.threshold_passing_titles.length} above
                threshold:
              </div>
              <ul className="space-y-1">
                {preview.current.threshold_passing_titles.map((t, i) => (
                  <li key={i} className="text-[12.5px] truncate">
                    · {t}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

function Stat({
  label, value, tone,
}: {
  label: string;
  value: number;
  tone?: "green" | "blue" | "orange" | "red";
}) {
  const toneClass =
    tone === "green" ? "text-green" :
    tone === "blue" ? "text-accent" :
    tone === "orange" ? "text-orange" :
    tone === "red" ? "text-red" : "text-ink";
  return (
    <div className="rounded-md bg-panel2/60 border border-line px-3 py-2">
      <div className="text-[10.5px] text-sub uppercase tracking-wide">
        {label}
      </div>
      <div className={cn("font-display font-semibold text-[20px] leading-none mt-1", toneClass)}>
        {value}
      </div>
    </div>
  );
}
