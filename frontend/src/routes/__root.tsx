import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Outlet,
  Link,
  createRootRouteWithContext,
  useRouter,
  HeadContent,
  Scripts,
} from "@tanstack/react-router";
import { useEffect, type ReactNode } from "react";

import appCss from "../styles.css?url";
import { reportLovableError } from "../lib/lovable-error-reporting";
import { AppShell } from "../components/AppShell";

function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="glass max-w-md p-8 text-center">
        <div className="label-hud text-cyan">404 · signal lost</div>
        <h1 className="mt-2 text-3xl font-bold text-text">Off-camera</h1>
        <p className="mt-2 text-sm text-muted">This view is not in the console layout.</p>
        <div className="mt-6">
          <Link to="/" className="inline-flex items-center justify-center rounded-lg bg-cyan px-4 py-2 text-sm font-semibold text-white hover:bg-cyan-dim">
            Back to Live Monitoring
          </Link>
        </div>
      </div>
    </div>
  );
}

function ErrorComponent({ error, reset }: { error: Error; reset: () => void }) {
  console.error(error);
  const router = useRouter();
  useEffect(() => {
    reportLovableError(error, { boundary: "tanstack_root_error_component" });
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="glass max-w-md p-8 text-center">
        <h1 className="text-xl font-semibold text-text">This view failed to load</h1>
        <p className="mt-2 text-sm text-muted">A component threw an error. Retry, or return to the wall.</p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <button
            onClick={() => { router.invalidate(); reset(); }}
            className="inline-flex items-center rounded-lg bg-cyan px-4 py-2 text-sm font-semibold text-white hover:bg-cyan-dim"
          >
            Try again
          </button>
          <a href="/" className="inline-flex items-center rounded-lg bg-[color:var(--color-surface-2)] px-4 py-2 text-sm text-text hover:bg-black/[0.04]">
            Go home
          </a>
        </div>
      </div>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "Overview · VisionGuard" },
      { name: "description", content: "A calm, at-a-glance view of your society's cameras and recent events." },
      { name: "theme-color", content: "#f5f3ee" },
      { property: "og:title", content: "Overview · VisionGuard" },
      { property: "og:description", content: "A calm, at-a-glance view of your society's cameras and recent events." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
      { name: "twitter:title", content: "Overview · VisionGuard" },
      { name: "twitter:description", content: "A calm, at-a-glance view of your society's cameras and recent events." },
      { property: "og:image", content: "https://pub-bb2e103a32db4e198524a2e9ed8f35b4.r2.dev/e4d98886-c178-4bd3-b6eb-35519f52d8da/id-preview-beeb3c4b--fd81f4df-222b-48dc-ae50-307f1383821a.lovable.app-1784882399232.png" },
      { name: "twitter:image", content: "https://pub-bb2e103a32db4e198524a2e9ed8f35b4.r2.dev/e4d98886-c178-4bd3-b6eb-35519f52d8da/id-preview-beeb3c4b--fd81f4df-222b-48dc-ae50-307f1383821a.lovable.app-1784882399232.png" },
    ],
    links: [
      { rel: "stylesheet", href: appCss },
      { rel: "icon", href: "/favicon.ico", type: "image/x-icon" },
      { rel: "preconnect", href: "https://fonts.googleapis.com" },
      { rel: "preconnect", href: "https://fonts.gstatic.com", crossOrigin: "anonymous" },
      { rel: "stylesheet", href: "https://fonts.googleapis.com/css2?family=Outfit:wght@500;600;700;800&family=Figtree:wght@400;500;600;700&display=swap" },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootShell({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();
  return (
    <QueryClientProvider client={queryClient}>
      <AppShell>
        <Outlet />
      </AppShell>
    </QueryClientProvider>
  );
}
