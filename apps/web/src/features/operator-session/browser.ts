/** Check the same-origin operator session without exposing private session data. */
export async function hasOperatorSession(signal?: AbortSignal): Promise<boolean> {
  try {
    const timeout = AbortSignal.timeout(3000);
    const requestSignal = signal ? AbortSignal.any([signal, timeout]) : timeout;
    const response = await fetch("/api/operator/session/authorization", {
      cache: "no-store",
      credentials: "same-origin",
      redirect: "error",
      signal: requestSignal,
    });
    if (!response.ok) return false;
    const value: unknown = await response.json();
    return typeof value === "object" && value !== null && !Array.isArray(value) &&
      Object.keys(value).length === 1 && Object.hasOwn(value, "authorized") &&
      (value as {authorized?: unknown}).authorized === true;
  } catch {
    return false;
  }
}
