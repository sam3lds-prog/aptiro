import { ProvenanceColor } from "@/lib/types";
import { cn } from "@/lib/cn";

const DOT: Record<ProvenanceColor, string> = {
  blue: "bg-prov-blue",
  purple: "bg-prov-purple",
  green: "bg-prov-green",
  orange: "bg-prov-orange",
  red: "bg-prov-red",
};

const LABEL: Record<ProvenanceColor, string> = {
  blue: "grounded résumé truth",
  purple: "profile-derived",
  green: "public context",
  orange: "AI-suggested",
  red: "unsupported — never exported",
};

export function ProvenanceBadge({
  color,
  label,
  showLabel = true,
  className,
}: {
  color: ProvenanceColor;
  label?: string;
  showLabel?: boolean;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <span className={cn("inline-block h-2 w-2 rounded-full", DOT[color])} aria-hidden />
      {showLabel && (
        <span className="text-[11.5px] text-sub leading-none">{label || LABEL[color]}</span>
      )}
    </span>
  );
}
