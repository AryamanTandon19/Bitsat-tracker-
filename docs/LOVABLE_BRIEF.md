# Lovable change brief — prototype-visionguard

Ready-to-send brief for the Lovable agent (project `fd81f4df-222b-48dc-ae50-307f1383821a`,
https://prototype-visionguard.lovable.app). Written after a full code review of the
project on 2026-07-24. Blocked on workspace credits at the time — send verbatim once
credits are available, or apply by hand via GitHub sync.

---

Three tightly-scoped changes. Keep the existing "Paper & Ink" design system exactly as
it is — same fonts (Figtree body, Outfit display), same layout, same page structure,
same copy, same warm cream background and terracotta accent. Do not rebrand anything.

## 1) Ink goes true black
In `src/styles.css`, shift the warm dark ink to pure black across the theme:
- `--color-text: #1a1614` → `#000000`
- `--color-purple: #2d2a27` → `#000000` and `--color-purple-lt: #57524d` → `#3d3d3d`
- All border/scrollbar/shadow rgba values based on `rgba(26,22,20,…)` → `rgba(0,0,0,…)`
  at the same alphas.
Everything else in the palette (cream bg, surfaces, terracotta, red/amber/green) stays.

## 2) Fix leftover dark-theme styling (nearly invisible on the cream background)
`src/routes/lab.tsx` still uses old dark-glass utilities; sweep `view.tsx`,
`events.tsx`, `index.tsx` for the same patterns:
- Dropzone: `border-white/15`, `hover:border-white/30`, `hover:bg-white/[0.02]` →
  `border-border` / `border-border-strong` + black/[0.03] hover tint; drag-active state
  uses the terracotta accent, not `rgba(0,242,255,.06)`.
- Upload icon circle gradient `rgba(0,242,255,.2), rgba(111,0,190,.2)` → soft
  terracotta tint on white.
- Select/switch containers: `bg-white/[0.03..0.04]`, `ring-white/10` → white surface,
  ring `--color-border-strong`, terracotta focus ring.
- Table headers `bg-white/[0.03]` → `var(--color-surface-2)`; row hover → black/[0.03].
- Progress track `bg-white/5` → `var(--color-surface-2)`; remove the neon glow on the fill.
- Timeline marker strip: `bg-black/40` + `ring-black` dots → light strip (surface-2,
  hairline top border, severity-colored markers with a white ring).
- SectionTitle divider `bg-white/5` → `var(--color-border)`.

## 3) Design polish — professional, minimal, user-friendly (no new decoration)
- Remove remaining neon glow box-shadows; use the soft paper shadow.
- Visible keyboard focus everywhere: consistent terracotta `focus-visible` ring on
  links, nav pills, buttons, incident cards, timeline markers, table jump links.
- `tabular-nums` on all aligned digits (stat values, timestamps, progress %).
- `text-wrap: balance` on h1/h2.
- Respect `prefers-reduced-motion` (disable rise/pulse-dot/scan).
- Keep spacing on the existing scale; no layout changes.

## 4) Login gate
Add `/login` in the same Paper & Ink style: centered white card on cream,
"VisionGuard / society watch" wordmark, username + password, terracotta sign-in
button, "Access is by invitation" footnote. Exactly two hardcoded accounts
(demo-grade, client-side):
- `admin` / `password1101`
- `YC` / `11012235`
On success store the session in localStorage and redirect to `/`. All other routes
redirect unauthenticated visitors to `/login`. Wrong credentials → inline
"Invalid username or password." Header right side shows the signed-in username and a
quiet "Sign out" link. Code comment: demo-grade client-side auth, replace with real
auth later.

Do not modify `src/lib/api.ts` (the backend contract is correct as-is).
