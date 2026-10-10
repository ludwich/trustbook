import type { Locality } from "@/api/types";

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]", "host.docker.internal"]);

/** Same rule as the backend: only these hosts count as local; LAN addresses are remote. */
export function localityOfUrl(url: string): { locality: Locality; host: string } | null {
  try {
    const u = new URL(url.trim());
    const host = u.hostname.toLowerCase();
    return { locality: LOCAL_HOSTS.has(host) ? "local" : "remote", host: u.host };
  } catch {
    return null;
  }
}
