import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { AuditEvent } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { LoadingBlock, EmptyState } from "@/components/ui/feedback";
import { Badge } from "@/components/ui/badge";

function statusTone(s: number): "green" | "blue" | "orange" | "red" | "neutral" {
  if (s >= 500) return "red";
  if (s >= 400) return "orange";
  if (s >= 300) return "blue";
  if (s >= 200) return "green";
  return "neutral";
}

export function Activity() {
  const q = useQuery<AuditEvent[]>({
    queryKey: ["audit"],
    queryFn: () => api<AuditEvent[]>("/audit?limit=200"),
  });

  return (
    <div>
      <PageHeader
        title="Activity"
        sub="Append-only audit trail of every change you made, written by the server (not the UI), with a request id for correlation. Intentionally excluded from the privacy export so the trail stays tamper-resistant."
      />

      {q.isLoading && <LoadingBlock lines={6} />}
      {!q.isLoading && !q.data?.length && (
        <EmptyState
          title="No activity yet"
          body="Mutations will show up here as you use the app."
        />
      )}

      <Card compact>
        <div className="divide-y divide-line/50">
          {(q.data || []).map((e) => (
            <div
              key={e.id}
              className="flex items-center justify-between gap-3 py-2 text-[12.5px]"
            >
              <div className="flex items-center gap-2 min-w-0">
                <Badge tone={statusTone(e.status)}>{e.status}</Badge>
                <span className="text-ink/90 font-mono text-[12px]">{e.method}</span>
                <span className="text-sub truncate font-mono text-[12px]">{e.path}</span>
              </div>
              <div className="flex items-center gap-2 text-sub shrink-0">
                <span>{e.duration_ms}ms</span>
                <span>·</span>
                <span>{new Date(e.at).toLocaleString()}</span>
                <span className="font-mono text-[11px] text-sub/80">{e.request_id}</span>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
