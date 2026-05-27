import { useState } from "react";
import { api, ApiError, deleteAccount, legalDoc } from "@/lib/api";
import type { LegalDoc } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { ConfirmModal } from "@/components/ConfirmModal";
import { useNotify } from "@/stores/toast";
import { useAuth } from "@/stores/auth";

export function Privacy() {
  const notify = useNotify();
  const { me, signOut } = useAuth();
  const isDefaultUser = !me || me.is_default;

  // ── Data export / wipe ───────────────────────────────────────────────
  const [bundle, setBundle] = useState<unknown | null>(null);
  const [exportBusy, setExportBusy] = useState(false);
  const [wipingData, setWipingData] = useState(false);

  // ── Account deletion ─────────────────────────────────────────────────
  const [deletingAccount, setDeletingAccount] = useState(false);
  const [deleteTyped, setDeleteTyped] = useState("");
  const DELETE_PHRASE = "DELETE MY ACCOUNT";

  // ── Legal docs ───────────────────────────────────────────────────────
  const [legalShown, setLegalShown] = useState<"privacy" | "terms" | null>(null);
  const [legalDoc_, setLegalDoc_] = useState<LegalDoc | null>(null);
  const [legalBusy, setLegalBusy] = useState(false);

  async function doExport() {
    try {
      setExportBusy(true);
      setBundle(await api("/privacy/export"));
      notify.success("Bundle exported. Save the JSON below.");
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Export failed.");
    } finally {
      setExportBusy(false);
    }
  }

  async function doWipeData() {
    await api("/privacy/data", { method: "DELETE" });
    setBundle(null);
    notify.success("All your data has been deleted.");
  }

  function download() {
    if (!bundle) return;
    const blob = new Blob([JSON.stringify(bundle, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `aptiro-privacy-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function doDeleteAccount() {
    try {
      await deleteAccount(deleteTyped.trim());
      notify.success("Account and all data permanently deleted.");
      signOut();
    } catch (e) {
      notify.error(e instanceof ApiError ? e.message : "Deletion failed.");
    }
  }

  async function showLegal(doc: "privacy" | "terms") {
    if (legalShown === doc) {
      setLegalShown(null);
      return;
    }
    setLegalBusy(true);
    try {
      const d = await legalDoc(doc);
      setLegalDoc_(d);
      setLegalShown(doc);
    } catch {
      notify.error("Failed to load document.");
    } finally {
      setLegalBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Privacy"
        sub="Your data is scoped to your account (or to your local install if auth is off). Export everything as a single JSON bundle, wipe it, or permanently delete your account."
      />

      {/* ── Data export / wipe ─────────────────────────────────────────── */}
      <Card title="Your Data">
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" loading={exportBusy} onClick={doExport}>
            Export my data (JSON)
          </Button>
          {bundle ? (
            <Button variant="secondary" onClick={download}>
              Download as file
            </Button>
          ) : null}
          <Button
            variant="danger"
            onClick={() => setWipingData(true)}
          >
            Delete all my data
          </Button>
        </div>

        <p className="mt-3 text-[12px] text-sub leading-relaxed">
          The audit trail is intentionally excluded from the export so it stays
          tamper-resistant. Account credentials are never included in the export.
        </p>

        {bundle !== null && (
          <pre className="mt-4 text-[11px] bg-panel2 border border-line rounded-md p-3 overflow-auto max-h-96 font-mono">
            {JSON.stringify(bundle, null, 2)}
          </pre>
        )}
      </Card>

      {/* ── Account deletion ───────────────────────────────────────────── */}
      {!isDefaultUser && (
        <Card title="Delete Account">
          <p className="text-[12.5px] text-sub leading-relaxed mb-3">
            Permanently removes your account and{" "}
            <span className="text-ink font-medium">all owned data</span> — every
            source, claim, strategy, job, package, application, research finding,
            and notification. This cannot be undone and cannot be reversed.
          </p>

          {deletingAccount ? (
            <div className="space-y-3">
              <div>
                <Label htmlFor="del-confirm">
                  Type{" "}
                  <code className="bg-panel2 px-1 rounded text-[11px] text-danger">
                    {DELETE_PHRASE}
                  </code>{" "}
                  to confirm
                </Label>
                <Input
                  id="del-confirm"
                  placeholder={DELETE_PHRASE}
                  value={deleteTyped}
                  onChange={(e) => setDeleteTyped(e.target.value)}
                  className="mt-1 font-mono"
                />
              </div>
              <div className="flex gap-2">
                <Button
                  variant="danger"
                  disabled={deleteTyped.trim() !== DELETE_PHRASE}
                  onClick={doDeleteAccount}
                >
                  Permanently delete my account
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setDeletingAccount(false);
                    setDeleteTyped("");
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <Button
              variant="danger"
              onClick={() => setDeletingAccount(true)}
            >
              Delete my account
            </Button>
          )}
        </Card>
      )}

      {/* ── Legal docs ────────────────────────────────────────────────── */}
      <Card title="Legal">
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            size="sm"
            loading={legalBusy && legalShown !== "privacy"}
            onClick={() => showLegal("privacy")}
          >
            {legalShown === "privacy" ? "Hide" : "Privacy Policy"}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            loading={legalBusy && legalShown !== "terms"}
            onClick={() => showLegal("terms")}
          >
            {legalShown === "terms" ? "Hide" : "Terms of Service"}
          </Button>
        </div>

        {legalShown && legalDoc_ && (
          <div className="mt-4 prose prose-sm max-w-none text-ink/90 text-[12.5px] leading-relaxed bg-panel2 border border-line rounded-md p-4 overflow-auto max-h-[32rem]">
            <pre className="whitespace-pre-wrap font-sans">{legalDoc_.content}</pre>
            <p className="text-sub text-[11px] mt-3">
              Last updated: {legalDoc_.last_updated}
            </p>
          </div>
        )}
      </Card>

      {/* ── Modals ────────────────────────────────────────────────────── */}
      <ConfirmModal
        open={wipingData}
        onClose={() => setWipingData(false)}
        destructive
        title="Delete all your data?"
        confirmLabel="Yes, delete everything"
        body={
          <>
            This permanently removes{" "}
            <span className="text-ink">every</span> source, claim, strategy,
            job, package, application, and apply session you own. Your account
            remains. This cannot be undone.
          </>
        }
        onConfirm={doWipeData}
      />
    </div>
  );
}
