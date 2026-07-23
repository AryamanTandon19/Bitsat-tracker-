// ============================================================================
//  VisionGuard demo site — configuration
//  Fill these in from your Supabase project, then commit/deploy.
//  The ANON key is SAFE to expose publicly (it's the browser key; your data is
//  protected by Row-Level Security + Auth, not by hiding this key).
// ============================================================================
window.VISIONGUARD_CONFIG = {
  // Supabase → Project Settings → API
  SUPABASE_URL: "https://YOUR-PROJECT-REF.supabase.co",
  SUPABASE_ANON_KEY: "YOUR-ANON-PUBLIC-KEY",

  // The demos shown after login. Upload each annotated video to Supabase
  // Storage (bucket "demos") and paste its URL here. Use a PUBLIC bucket URL
  // for the simple setup, or a private bucket + signed URLs (see README).
  DEMOS: [
    {
      title: "Night vehicle break-in (real 452x342 CCTV)",
      verdict: "HIGH — car driven away after tampering, likely theft",
      severity: "HIGH",
      video_url: "https://YOUR-PROJECT-REF.supabase.co/storage/v1/object/public/demos/theft_night.mp4",
      incidents: [
        { severity: "HIGH", span: "11–60s", culprit: "#7",
          summary: "Vehicle drove away 48s after suspicious activity at the window" },
      ],
    },
    // add more { ... } demo entries here
  ],
};
