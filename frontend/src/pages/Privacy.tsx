import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ConfirmModal";
import { useNotify } from "@/stores/toast";

export function Privacy() {
  const notify = useNotify();
  const [bundle, setBundle] = useState<unknown | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  async function doExport() {
    try {
      setBusy(true);
      setBundle(await api("/privacy/export"));
      notify.success("Bundle exported. Save the JSON below.");
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Export failed.");
    } finally {
      setBusy(false);
    }
  }

  async function doWipe() {
    await api("/privacy/data", { method: "DELETE" });
    setBundle(null);
    notify.success("All your data has been deleted.");
  }

  function download() {
    if (!bundle) return;
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `aptiro-privacy-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div>
      <PageHeader
        title="Privacy"
        sub="Your data is scoped to your account (or to your local install if auth is off). Export everything as a single JSON bundle, or wipe it. The audit trail is intentionally excluded from the export so it stays tamper-resistant."
      />

      <Card>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" loading={busy} onClick={doExport}>
            Export my data (JSON)
          </Button>
          {bundle ? (
            <Button variant="secondary" onClick={download}>
              Download as file
            </Button>
          ) : null}
          <Button variant="danger" onClick={() => setConfirming(true)}>
            Delete all my data
          </Button>
        </div>

        {bundle !== null && (
          <pre className="mt-4 text-[11px] bg-panel2 border border-line rounded-md p-3 overflow-auto max-h-96 font-mono">
            {JSON.stringify(bundle, null, 2)}
          </pre>
        )}
      </Card>

      <ConfirmModal
        open={confirming}
        onClose={() => setConfirming(false)}
        destructive
        title="Delete all your data?"
        confirmLabel="Yes, delete everything"
        body={
          <>
            This permanently removes <span className="text-ink">every</span>{" "}
            source, claim, strategy, job, package, application, and apply
            session you own. This cannot be undone.
          </>
        }
        onConfirm={doWipe}
      />
    </div>
  );
}
