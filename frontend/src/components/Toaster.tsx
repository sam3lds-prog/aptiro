import { useToast } from "@/stores/toast";
import { cn } from "@/lib/cn";

const KIND_STYLES = {
  info: "border-line text-ink",
  success: "border-prov-green/40 text-ink",
  warn: "border-prov-orange/45 text-ink",
  error: "border-prov-red/45 text-ink",
};

export function Toaster() {
  const items = useToast((s) => s.items);
  const dismiss = useToast((s) => s.dismiss);
  return (
    <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2 max-w-sm">
      {items.map((t) => (
        <div
          key={t.id}
          role="status"
          className={cn(
            "bg-panel border rounded-lg px-3.5 py-2.5 shadow-soft text-[13px] flex items-start gap-3",
            KIND_STYLES[t.kind]
          )}
        >
          <div className="flex-1 leading-snug">{t.message}</div>
          <button
            onClick={() => dismiss(t.id)}
            className="text-sub hover:text-ink leading-none px-1"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
