import { Link, useRouterState, useNavigate } from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";
import { USING_MOCKS } from "@/lib/api";
import { currentUser, isAuthed, logout } from "@/lib/auth";

const NAV = [
  { to: "/", label: "Overview" },
  { to: "/view", label: "Live view" },
  { to: "/lab", label: "Review a clip" },
  { to: "/events", label: "History" },
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const navigate = useNavigate();

  // Client-only auth check. `mounted` avoids an SSR/hydration mismatch and the
  // brief flash of protected content before the redirect runs.
  const [mounted, setMounted] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [user, setUser] = useState<string | null>(null);

  useEffect(() => {
    setMounted(true);
    setAuthed(isAuthed());
    setUser(currentUser());
  }, [pathname]);

  const onLoginPage = pathname === "/login";

  useEffect(() => {
    if (mounted && !authed && !onLoginPage) {
      navigate({ to: "/login" });
    }
  }, [mounted, authed, onLoginPage, navigate]);

  // The login page renders full-screen without the console chrome.
  if (onLoginPage) return <>{children}</>;

  // Until we've confirmed auth on the client, render nothing (prevents a flash
  // of the protected app for signed-out visitors and keeps SSR output stable).
  if (!mounted || !authed) return null;

  const signOut = () => {
    logout();
    setAuthed(false);
    setUser(null);
    navigate({ to: "/login" });
  };

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-border bg-[color-mix(in_oklab,var(--color-bg)_85%,transparent)] backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-3 md:px-8">
          <Link to="/" className="leading-tight">
            <div className="font-display text-lg font-extrabold tracking-tight text-text">VisionGuard</div>
            <div className="text-[10px] text-muted-soft" style={{ letterSpacing: "0.06em" }}>society watch</div>
          </Link>

          <nav className="ml-4 hidden items-center gap-1 md:flex">
            {NAV.map((item) => {
              const active = item.to === "/" ? pathname === "/" : pathname.startsWith(item.to);
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className={`rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors ${
                    active
                      ? "bg-[color-mix(in_oklab,var(--color-cyan)_14%,transparent)] text-text"
                      : "text-muted hover:bg-black/[0.04] hover:text-text"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            {USING_MOCKS && (
              <span className="hidden rounded-full border border-border px-2.5 py-0.5 text-[10px] font-medium text-muted sm:inline-flex">
                Demo mode
              </span>
            )}
            {user && (
              <>
                <span className="hidden text-xs text-muted sm:inline">{user}</span>
                <button
                  onClick={signOut}
                  className="rounded-full px-2.5 py-1 text-xs font-medium text-muted transition-colors hover:bg-black/[0.04] hover:text-text"
                >
                  Sign out
                </button>
              </>
            )}
          </div>
        </div>

        {/* Mobile nav */}
        <nav className="flex gap-1 overflow-x-auto border-t border-border px-4 py-2 scrollbar-thin md:hidden">
          {NAV.map((item) => {
            const active = item.to === "/" ? pathname === "/" : pathname.startsWith(item.to);
            return (
              <Link
                key={item.to}
                to={item.to}
                className={`whitespace-nowrap rounded-full px-3 py-1 text-xs font-medium ${
                  active ? "bg-[color-mix(in_oklab,var(--color-cyan)_14%,transparent)] text-text" : "text-muted"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 md:px-8 md:py-10">{children}</main>

      <footer className="mx-auto max-w-7xl px-4 pb-8 pt-4 text-center text-[11px] text-muted-soft md:px-8">
        On-premise · your footage never leaves your building.
      </footer>
    </div>
  );
}
