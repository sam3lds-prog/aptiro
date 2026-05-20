import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { Strategy as StrategyT, WorkMode, Aggressiveness } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { LoadingBlock } from "@/components/ui/feedback";
import { useNotify } from "@/stores/toast";

const WORK_MODES: WorkMode[] = ["any", "remote", "hybrid", "onsite"];
const AGG: Aggressiveness[] = ["conservative", "balanced", "aggressive"];

const arrToText = (v?: string[]) => (v ? v.join(", ") : "");
const textToArr = (v: string) =>
  v
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

export function Strategy() {
  const notify = useNotify();
  const q = useQuery<StrategyT>({
    queryKey: ["strategy"],
    queryFn: () => api<StrategyT>("/strategy"),
  });

  const [draft, setDraft] = useState<StrategyT | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (q.data) setDraft(q.data);
  }, [q.data]);

  if (q.isLoading || !draft)
    return (
      <div>
        <PageHeader title="Strategy" />
        <Card>
          <LoadingBlock lines={8} />
        </Card>
      </div>
    );

  const set = <K extends keyof StrategyT>(k: K, v: StrategyT[K]) =>
    setDraft({ ...draft, [k]: v });

  const wsum = Object.values(draft.weights || {}).reduce((a, b) => a + (Number(b) || 0), 0);

  async function save() {
    try {
      setBusy(true);
      const r = await api<StrategyT>("/strategy", {
        method: "PUT",
        body: {
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
        },
      });
      setDraft(r);
      notify.success("Strategy saved.");
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Save failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Strategy"
        sub="Targeting and explainable scoring weights. The matcher uses these and shows the reason for every component."
        actions={<Button onClick={save} loading={busy}>Save strategy</Button>}
      />

      <Card title="Targeting" className="mb-5">
        <div className="grid gap-3 md:grid-cols-3">
          <div>
            <Label>Name</Label>
            <Input value={draft.name || ""} onChange={(e) => set("name", e.target.value)} />
          </div>
          <div>
            <Label>Work mode</Label>
            <Select value={draft.work_mode} onChange={(e) => set("work_mode", e.target.value as WorkMode)}>
              {WORK_MODES.map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Aggressiveness</Label>
            <Select
              value={draft.aggressiveness}
              onChange={(e) => set("aggressiveness", e.target.value as Aggressiveness)}
            >
              {AGG.map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </Select>
          </div>
        </div>

        <Label>Target roles (comma-separated)</Label>
        <Input
          value={arrToText(draft.target_roles)}
          onChange={(e) => set("target_roles", textToArr(e.target.value))}
        />

        <div className="grid gap-3 md:grid-cols-3">
          <div>
            <Label>Region</Label>
            <Input value={draft.region || ""} onChange={(e) => set("region", e.target.value)} />
          </div>
          <div>
            <Label>Salary min</Label>
            <Input
              type="number"
              value={draft.salary_min ?? ""}
              onChange={(e) => set("salary_min", e.target.value ? Number(e.target.value) : null)}
            />
          </div>
          <div>
            <Label>Salary max</Label>
            <Input
              type="number"
              value={draft.salary_max ?? ""}
              onChange={(e) => set("salary_max", e.target.value ? Number(e.target.value) : null)}
            />
          </div>
        </div>

        <Label>Exclude companies (comma-separated)</Label>
        <Input
          value={arrToText(draft.exclude_companies)}
          onChange={(e) => set("exclude_companies", textToArr(e.target.value))}
        />

        <Label>Include / priority companies</Label>
        <Input
          value={arrToText(draft.include_companies)}
          onChange={(e) => set("include_companies", textToArr(e.target.value))}
        />

        <Label>Targeting notes</Label>
        <Textarea
          rows={3}
          value={draft.targeting_notes || ""}
          onChange={(e) => set("targeting_notes", e.target.value)}
        />
      </Card>

      <Card
        title={
          <span>
            Scoring weights{" "}
            <span className="text-sub text-[12px] font-normal">(sum {wsum})</span>
          </span>
        }
      >
        <div className="grid gap-3 md:grid-cols-3">
          {Object.keys(draft.weights || {}).map((k) => (
            <div key={k}>
              <Label>{k.replace(/_/g, " ")}</Label>
              <Input
                type="number"
                value={draft.weights[k] ?? 0}
                onChange={(e) =>
                  set("weights", { ...draft.weights, [k]: Number(e.target.value) || 0 })
                }
              />
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
