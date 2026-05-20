import { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

type Tone = "neutral" | "blue" | "purple" | "green" | "orange" | "red" | "ink";

const TONES: Record<Tone, string> = {
  neutral: "bg-panel2 text-sub border-line",
  blue: "bg-prov-blue/15 text-prov-blue border-prov-blue/30",
  purple: "bg-prov-purple/15 text-prov-purple border-prov-purple/30",
  green: "bg-prov-green/15 text-prov-green border-prov-green/30",
  orange: "bg-prov-orange/15 text-prov-orange border-prov-orange/40",
  red: "bg-prov-red/15 text-prov-red border-prov-red/35",
  ink: "bg-ink/10 text-ink border-ink/20",
};

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
}

export function Badge({ tone = "neutral", className, children, ...rest }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-medium leading-tight",
        TONES[tone],
        className
      )}
      {...rest}
    >
      {children}
    </span>
  );
}
