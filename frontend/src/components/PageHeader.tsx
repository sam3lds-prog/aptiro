import { ReactNode } from "react";

interface PageHeaderProps {
  title: string;
  sub?: ReactNode;
  actions?: ReactNode;
}

export function PageHeader({ title, sub, actions }: PageHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-6 mb-6">
      <div>
        <h1 className="font-display text-[28px] font-bold tracking-tight leading-tight mb-1">
          {title}
        </h1>
        {sub && <p className="text-[13px] text-sub max-w-2xl leading-relaxed">{sub}</p>}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  );
}
