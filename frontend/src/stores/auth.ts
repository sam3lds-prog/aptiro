import { create } from "zustand";
import type { Me } from "@/lib/types";

const TOKEN_KEY = "aptiro_token";

interface AuthState {
  token: string | null;
  me: Me | null;
  setAuth: (token: string | null, me: Me | null) => void;
  setMe: (me: Me | null) => void;
  signOut: () => void;
}

function loadToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function saveToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore: private mode / disabled storage */
  }
}

export const useAuth = create<AuthState>((set) => ({
  token: loadToken(),
  me: null,
  setAuth: (token, me) => {
    saveToken(token);
    set({ token, me });
  },
  setMe: (me) => set({ me }),
  signOut: () => {
    saveToken(null);
    set({ token: null, me: null });
  },
}));

/** Plain accessor for the API client to avoid React-only hooks. */
export function getToken(): string | null {
  return useAuth.getState().token;
}
