import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

export function NotFound() {
  return (
    <div className="min-h-[60vh] flex flex-col items-center justify-center text-center px-6">
      <div className="font-display text-6xl font-bold tracking-tight text-ink/80">404</div>
      <div className="text-sub text-[14px] mt-2 mb-5 max-w-md">
        That page isn't part of Aptiro. Use the sidebar, or head back to the
        Dashboard.
      </div>
      <Link to="/">
        <Button>Back to Dashboard</Button>
      </Link>
    </div>
  );
}
