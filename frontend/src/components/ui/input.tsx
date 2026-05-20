import { forwardRef, InputHTMLAttributes, TextareaHTMLAttributes, SelectHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

const baseField =
  "w-full bg-panel2 border border-line text-ink placeholder:text-sub/60 " +
  "rounded-lg px-3 py-2 text-[13px] outline-none transition-colors " +
  "focus:border-accent/70 focus:bg-panel disabled:opacity-50";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...rest }, ref) => (
    <input ref={ref} className={cn(baseField, className)} {...rest} />
  )
);
Input.displayName = "Input";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, rows = 4, ...rest }, ref) => (
    <textarea ref={ref} rows={rows} className={cn(baseField, "resize-y", className)} {...rest} />
  )
);
Textarea.displayName = "Textarea";

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...rest }, ref) => (
    <select ref={ref} className={cn(baseField, "appearance-none pr-8", className)} {...rest}>
      {children}
    </select>
  )
);
Select.displayName = "Select";

export function Label({ children, className, ...rest }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label className={cn("eyebrow block mb-1.5 mt-3", className)} {...rest}>
      {children}
    </label>
  );
}
