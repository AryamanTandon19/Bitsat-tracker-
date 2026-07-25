# Put VisionGuard online for free (Oracle Cloud)

This hosts the **real** app (real clip upload + AI analysis) on a free Oracle
"Always Free" server, so investors can use it from a link like `http://140.x.x.x`.

> Heads-up: this is the most technical of the hosting options. Take it one
> phase at a time. If you get stuck, the free tunnel (`cloudflared`) is an
> easier fallback.

Login for the live site: **YC / 11012235** (or admin / password1101).

---

## Phase 1 — Make an Oracle Cloud account (~15 min)
1. Go to **oracle.com/cloud/free** → **Start for free**.
2. Sign up: email, country **India**, verify phone, and add a **card** for
   identity (Always Free is not charged; a tiny temporary hold may appear).
3. Choose a **Home Region** near you (e.g. **India Central (Hyderabad)** or
   **India West (Mumbai)**). ⚠️ You can't change this later.

## Phase 2 — Create the free server (~10 min)
1. Console menu (☰) → **Compute → Instances → Create instance**.
2. **Name:** `visionguard`.
3. **Image and shape → Edit:**
   - Shape → **Ampere** → `VM.Standard.A1.Flex` → set **4 OCPUs** and **24 GB**
     (all free).
   - Image → **Canonical Ubuntu 22.04**.
   - ⚠️ If it says **"Out of host capacity"**, switch the Availability Domain
     (AD-1 / AD-2 / AD-3) and retry, or try again in a few hours. This is the
     #1 snag with Oracle's free ARM servers.
4. **Add SSH keys → Save private key** → download the `.key` file. Keep it safe;
   you log in with it.
5. Leave networking default (**Assign public IPv4 = yes**).
6. **Create.** After ~1 minute, copy the **Public IP address**.

## Phase 3 — Open the firewall for the web (~3 min)
1. On the instance page, under **Primary VNIC**, click the **Subnet** link.
2. Open the **Default Security List** → **Add Ingress Rules**:
   - **Source CIDR:** `0.0.0.0/0`
   - **IP Protocol:** TCP
   - **Destination Port Range:** `80`
   - **Add.**

## Phase 4 — Connect and deploy (~20 min, mostly waiting)
Connect to the server (pick one):
- **Windows:** open **PowerShell**, then:
  `ssh -i C:\path\to\your.key ubuntu@YOUR_PUBLIC_IP`
- **Mac:** open **Terminal**, then: `chmod 400 your.key` and
  `ssh -i your.key ubuntu@YOUR_PUBLIC_IP`

Once you see the server prompt, paste this **one line** and press Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/AryamanTandon19/Bitsat-tracker-/claude/society-ai-watchdog-demo-hxshu9/deploy/oracle-setup.sh | bash
```

It installs everything, builds the app (10–20 min the first time), and starts
it. When it finishes it prints your link:

```
VisionGuard is LIVE.  Open:  http://YOUR_PUBLIC_IP
```

Open that in a browser, sign in, go to **Forensic Lab**, upload a CCTV clip →
real AI analysis. Share the link with investors.

---

## Handy commands (run on the server)
- See logs / progress: `sudo docker logs -f visionguard`
- Restart: `sudo docker restart visionguard`
- Update after new code is pushed: re-run the one-line command above.

## Notes
- The repo must be **public** for the one-line command to download the code.
  Keep it public during the demo, or ask for the private-repo (token) variant.
- First clip analysis downloads the YOLO model once (a few seconds).
- Analysis runs on CPU, so a clip takes a minute or two — that's expected.
