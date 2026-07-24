import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { login } from "@/lib/auth";
import { Button } from "@/components/ui-prims";

export const Route = createFileRoute("/login")({
  head: () => ({
    meta: [
      { title: "Sign in · VisionGuard" },
      { name: "description", content: "Private investor preview — please sign in." },
    ],
  }),
  component: LoginView,
});

function LoginView() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (login(username, password)) {
      navigate({ to: "/" });
    } else {
      setError("Invalid username or password.");
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={onSubmit} className="glass w-full max-w-sm p-8">
        <div className="font-display text-2xl font-extrabold tracking-tight text-text">VisionGuard</div>
        <div className="text-[11px] text-muted-soft" style={{ letterSpacing: "0.06em" }}>society watch</div>
        <p className="mt-4 text-sm text-muted">Private investor preview — please sign in.</p>

        <label htmlFor="username" className="label-hud mt-6 mb-1.5 block">Username</label>
        <input
          id="username"
          value={username}
          autoComplete="username"
          onChange={(e) => setUsername(e.target.value)}
          className="w-full rounded-lg bg-[color:var(--color-surface)] px-3 py-2 text-sm text-text ring-1 ring-inset ring-[color:var(--color-border-strong)] focus:outline-none focus:ring-2 focus:ring-[color:var(--color-cyan)]"
        />

        <label htmlFor="password" className="label-hud mt-4 mb-1.5 block">Password</label>
        <input
          id="password"
          type="password"
          value={password}
          autoComplete="current-password"
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-lg bg-[color:var(--color-surface)] px-3 py-2 text-sm text-text ring-1 ring-inset ring-[color:var(--color-border-strong)] focus:outline-none focus:ring-2 focus:ring-[color:var(--color-cyan)]"
        />

        {error && <p className="mt-3 text-sm" style={{ color: "var(--color-red-deep)" }}>{error}</p>}

        <Button type="submit" variant="grad" className="mt-6 w-full">Sign in</Button>
        <p className="mt-4 text-center text-xs text-muted-soft">Access is by invitation.</p>
      </form>
    </div>
  );
}
