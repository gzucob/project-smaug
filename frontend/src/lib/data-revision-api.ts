/** Same-origin revision probe for the silent background refresh component. */
export async function fetchClientDataRevision(signal: AbortSignal): Promise<string | null> {
  const response = await fetch("/api/data-revision", {
    cache: "no-store",
    signal,
  });
  if (!response.ok) return null;

  const { revision } = (await response.json()) as { revision: string | null };
  return revision;
}
