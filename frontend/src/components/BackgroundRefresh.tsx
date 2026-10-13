"use client";

import { useEffect, useTransition } from "react";
import { usePathname, useRouter } from "next/navigation";

/** Read fresh saved data without exposing the worker's progress or failures. */
export function BackgroundRefresh({
  initialRevision,
}: {
  initialRevision: string | null;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    let disposed = false;
    let inFlight = false;
    let lastCheck = 0;
    let deferred: number | undefined;
    let controller: AbortController | undefined;
    const canRefresh = () =>
      document.visibilityState === "visible" &&
      !document.querySelector('[role="dialog"], [role="listbox"], [aria-busy="true"]') &&
      !document.activeElement?.matches(
        'input, textarea, [contenteditable="true"]',
      );

    const refreshWhenIdle = () => {
      if (disposed) return;
      if (!canRefresh()) {
        deferred = window.setTimeout(refreshWhenIdle, 1000);
        return;
      }
      startTransition(() => router.refresh());
    };

    const check = async () => {
      if (
        disposed ||
        inFlight ||
        isPending ||
        deferred !== undefined ||
        document.visibilityState !== "visible" ||
        Date.now() - lastCheck < 15000
      )
        return;
      inFlight = true;
      lastCheck = Date.now();
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller?.abort(), 3000);
      try {
        const response = await fetch("/api/data-revision", {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok || disposed) return;
        const { revision } = (await response.json()) as {
          revision: string | null;
        };
        if (revision && revision !== initialRevision) refreshWhenIdle();
      } catch {
        // Keep the saved screen on transient failures; a later probe retries.
      } finally {
        window.clearTimeout(timeout);
        inFlight = false;
      }
    };
    const timer = window.setInterval(check, 60000);
    document.addEventListener("visibilitychange", check);
    // A cached route may predate a committed change; verify it on navigation.
    void check();
    return () => {
      disposed = true;
      controller?.abort();
      window.clearInterval(timer);
      window.clearTimeout(deferred);
      document.removeEventListener("visibilitychange", check);
    };
  }, [router, pathname, initialRevision, isPending]);

  return null;
}
