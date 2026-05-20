import { create } from "zustand";

export type ToastKind = "info" | "success" | "warn" | "error";

export interface Toast {
  id: string;
  kind: ToastKind;
  message: string;
}

interface ToastState {
  items: Toast[];
  push: (message: string, kind?: ToastKind) => string;
  dismiss: (id: string) => void;
}

let nextId = 1;

export const useToast = create<ToastState>((set, get) => ({
  items: [],
  push: (message, kind = "info") => {
    const id = `t${nextId++}`;
    const t: Toast = { id, kind, message };
    set({ items: [...get().items, t] });
    setTimeout(() => get().dismiss(id), kind === "error" ? 6500 : 4200);
    return id;
  },
  dismiss: (id) => set({ items: get().items.filter((t) => t.id !== id) }),
}));

/** Convenience hook handle for pages. */
export function useNotify() {
  const push = useToast((s) => s.push);
  return {
    notify: (message: string) => push(message, "info"),
    success: (message: string) => push(message, "success"),
    warn: (message: string) => push(message, "warn"),
    error: (message: string) => push(message, "error"),
  };
}
