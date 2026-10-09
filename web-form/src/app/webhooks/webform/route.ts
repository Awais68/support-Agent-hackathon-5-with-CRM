import { NextRequest, NextResponse } from 'next/server';

import { apiBaseUrl, clientAddressHeaders, readCapped } from '@/lib/upstream';

// The support form posts here (same origin) and this route forwards to the
// API's public /webhooks/webform. It replaces a next.config rewrite whose
// destination came from NEXT_PUBLIC_API_URL, frozen at build time to
// http://localhost:8000, which inside the container is not the API. No API
// key is attached (AUDIT S7). The client address is forwarded so the API
// rate-limits each browser, not the whole form (AUDIT S8).
export const dynamic = 'force-dynamic';

// Same default as the API's PUBLIC_MAX_BODY_BYTES; a form post is ~2 KB.
const MAX_BODY_BYTES = Number(process.env.PUBLIC_MAX_BODY_BYTES) || 64 * 1024;

export async function POST(request: NextRequest) {
  const body = await readCapped(request, MAX_BODY_BYTES);
  if (body === null) {
    return NextResponse.json({ message: 'Request body too large' }, { status: 413 });
  }

  try {
    const response = await fetch(`${apiBaseUrl()}/webhooks/webform`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...clientAddressHeaders(request) },
      body,
      cache: 'no-store',
      redirect: 'error',
    });
    const data = await response.json().catch(() => ({}));
    const headers: Record<string, string> = {};
    const retryAfter = response.headers.get('retry-after');
    if (retryAfter) headers['Retry-After'] = retryAfter;
    return NextResponse.json(data, { status: response.status, headers });
  } catch (error) {
    console.error('Error forwarding web form:', error);
    return NextResponse.json({ message: 'Failed to submit support form' }, { status: 502 });
  }
}
