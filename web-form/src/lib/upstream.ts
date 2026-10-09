import type { NextRequest } from 'next/server';

// Server-side helpers for the route handlers that forward to the API.

// Read at runtime from the container env, unlike NEXT_PUBLIC_* values, which
// `next build` freezes into the bundle (and, for rewrites, into the
// standalone server config). Inside compose/k8s the browser-facing URL is
// not where the API is.
export function apiBaseUrl(): string {
  return process.env.API_INTERNAL_URL || 'http://localhost:8000';
}

// The client address for the API's rate limiter. client-ip.js has already
// overwritten X-Forwarded-For with the TCP peer, so this is never a value
// the browser chose; the API trusts it only from this container
// (TRUSTED_PROXIES).
export function clientAddressHeaders(request: NextRequest): Record<string, string> {
  const ip = request.headers.get('x-forwarded-for');
  return ip ? { 'X-Forwarded-For': ip } : {};
}

// Read at most `limit` bytes of the body; null when it is larger.
export async function readCapped(request: NextRequest, limit: number): Promise<string | null> {
  const declared = Number(request.headers.get('content-length'));
  if (declared > limit) return null;
  if (!request.body) return '';
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > limit) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const body = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder().decode(body);
}
