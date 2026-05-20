import { Outlet } from "react-router-dom";
import { Nav } from "./Nav";

export function AppLayout() {
  return (
    <div className="grain-bg flex min-h-screen text-ink relative">
      <Nav />
      <main className="flex-1 min-w-0 px-8 py-7 max-w-6xl">
        <Outlet />
      </main>
    </div>
  );
}
