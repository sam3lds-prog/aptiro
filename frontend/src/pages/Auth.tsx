import { FormEvent, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { AuthSuccess, Me } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";

interface AuthProps {
  onAuthed: (token: string, me: Me) => void;
}

export function Auth({ onAuthed }: AuthProps) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!email.includes("@")) return setError("Enter a valid email.");
    if (pw.length < 8) return setError("Password must be at least 8 characters.");

    setBusy(true);
    try {
      const path = mode === "login" ? "/auth/login" : "/auth/register";
      const body: Record<string, unknown> = { email, password: pw };
      if (mode === "register") body.name = name || undefined;
      const r = await api<AuthSuccess>(path, { method: "POST", body });
      onAuthed(r.token, {
        id: r.id,
        email: r.email,
        name: r.name,
        is_default: false,
        auth_enabled: true,
      });
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Sign-in failed.";
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen grain-bg flex items-center justify-center px-6 py-10 bg-bg">
      <form onSubmit={submit} className="w-full max-w-sm relative">
        <div className="text-center mb-7">
          <div className="font-display text-4xl font-bold tracking-tight mb-1.5">Aptiro</div>
          <div className="text-[12px] text-sub tracking-wider uppercase">
            evidence-backed applications
          </div>
        </div>

        <div className="bg-panel border border-line rounded-xl2 p-6 shadow-soft">
          <div className="grid grid-cols-2 gap-2 mb-4">
            <Button
              type="button"
              variant={mode === "login" ? "primary" : "secondary"}
              onClick={() => setMode("login")}
            >
              Log in
            </Button>
            <Button
              type="button"
              variant={mode === "register" ? "primary" : "secondary"}
              onClick={() => setMode("register")}
            >
              Register
            </Button>
          </div>

          {mode === "register" && (
            <>
              <Label htmlFor="name">Name</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Your name"
                autoComplete="name"
              />
            </>
          )}

          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            autoComplete="email"
            required
          />

          <Label htmlFor="pw">Password</Label>
          <Input
            id="pw"
            type="password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            placeholder="8+ characters"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            required
          />

          {error && (
            <div className="mt-3 text-[12.5px] text-prov-red bg-prov-red/10 border border-prov-red/30 rounded-md p-2.5">
              {error}
            </div>
          )}

          <Button type="submit" loading={busy} className="w-full mt-5">
            {mode === "login" ? "Log in" : "Create account"}
          </Button>
        </div>

        <p className="text-[12px] text-sub text-center mt-5 leading-relaxed">
          Your data is private to your account. Aptiro never submits anything for you.
        </p>
      </form>
    </div>
  );
}
