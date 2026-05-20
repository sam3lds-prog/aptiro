import { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

interface CardProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  /** Optional title row shown above the body content. */
  title?: ReactNode;
  /** Optional content rendered on the right of the title row. */
  actions?: ReactNode;
  /** Compact mode (less padding). */
  compact?: boolean;
}

export function Card({ title, actions, compact, className, children, ...rest }: CardProps) {
  return (
    <div
      className={cn(
        "rounded-xl2 border border-line bg-panel relative",
        compact ? "p-3.5" : "p-5",
        className
      )}
      {...rest}
    >
      {(title || actions) && (
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="text-[15px] font-semibold text-ink leading-tight">{title}</div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </div>
  );
}
