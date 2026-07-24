// Demo-grade, client-side auth for the investor preview.
// NOTE: these credentials live in the client bundle — fine for a gated preview,
// but replace with real auth (e.g. Supabase) before any production use.
const USERS: Record<string, string> = {
  admin: "password1101",
  YC: "11012235",
};

const KEY = "vg_auth_user";

/** Try to sign in. Returns true on success and persists the session. */
export function login(username: string, password: string): boolean {
  const u = username.trim();
  if (USERS[u] && USERS[u] === password) {
    if (typeof window !== "undefined") window.localStorage.setItem(KEY, u);
    return true;
  }
  return false;
}

export function logout(): void {
  if (typeof window !== "undefined") window.localStorage.removeItem(KEY);
}

/** The signed-in username, or null. SSR-safe (returns null on the server). */
export function currentUser(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(KEY);
}

export function isAuthed(): boolean {
  return currentUser() !== null;
}
