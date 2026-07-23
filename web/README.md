# VisionGuard — online, password-protected demo site

A tiny static site (login page + gated demo viewer) that anyone can open on the
internet but only your invited users can enter. Auth + users + videos are all
managed in **Supabase**; the site is hosted free on Cloudflare Pages / Vercel /
Netlify.

```
web/
  login.html   sign-in page (Supabase Auth)
  app.html     gated viewer — analyzed demo videos + Claude verdicts + incidents
  config.js    YOU fill this: Supabase keys + your demo list
```

> Why not host the real dashboard online? The detection app needs GPU/torch and
> can't run on Supabase/Vercel. So you analyze on your machine, upload the
> finished annotated videos, and this gated site plays them. Clean for investors.

---

## Step 1 — Create the Supabase project (5 min)
1. Go to supabase.com → **New project**. Pick a name + a strong DB password.
2. Open **Project Settings → API**. Copy:
   - **Project URL** → `SUPABASE_URL`
   - **anon public** key → `SUPABASE_ANON_KEY`
   (The anon key is meant to be public — it's safe in the browser.)

## Step 2 — Turn on invite-only login (you control the passwords)
1. **Authentication → Providers → Email:** make sure it's **enabled**.
2. To keep it invite-only, **turn OFF "Allow new users to sign up"** (same page)
   — then the only people who can log in are the ones **you** add.
3. **Authentication → Users → Add user** → enter the investor's **email + a
   password** you choose. That's how you "manage passwords via Supabase." Add
   one user per person; delete them to revoke access anytime.

## Step 3 — Upload your demo videos
1. **Storage → New bucket** → name it `demos`. For the simple setup make it
   **Public**.
2. Upload each **annotated** video (the `annotated.mp4` from a Forensic Lab run).
3. Click a file → **Copy URL** → that's the `video_url` for `config.js`.

## Step 4 — Fill in `config.js`
Open `web/config.js` and paste your `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and one
`DEMOS` entry per video (title, verdict, severity, video_url, incidents).

## Step 5 — Put it online (pick one, all free)
- **Cloudflare Pages / Netlify (easiest):** drag-and-drop the whole `web/`
  folder into their dashboard → you get a public URL.
- **Vercel:** `vercel` on the `web/` folder, or connect this GitHub repo and set
  the root to `web/`.
- Share the URL. Anyone can open it; only your invited users get past login.

Done — a password-protected demo on the internet, users managed in Supabase.

---

## Optional — truly lock the videos (not just the page)
The simple setup above gates the **page**; a determined person could still open
a public video URL directly. To gate the **files** too:
1. Make the `demos` bucket **Private**.
2. Add a Storage **RLS policy** allowing `authenticated` users to read it.
3. In `app.html`, instead of a public `video_url`, generate a temporary link
   after login:
   ```js
   const { data } = await supabase.storage.from("demos")
     .createSignedUrl("theft_night.mp4", 3600); // valid 1 hour
   // use data.signedUrl as the <video src>
   ```
Ask and I'll wire this signed-URL version for you.

## Notes
- The site loads the Supabase JS client from a CDN (`esm.sh`) — fine on
  Cloudflare/Vercel/Netlify (no CSP restrictions there).
- Password reset / magic-link login can be enabled in Supabase → Authentication
  if you prefer emailed links over passwords.
- This is separate from the local dashboard's HTTP Basic auth; that still guards
  the live app on your machine.
