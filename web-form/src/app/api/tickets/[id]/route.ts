import { NextRequest, NextResponse } from 'next/server';

// Route handlers and their fetches are cached by default in the App Router,
// so the tracking page kept serving the pre-agent snapshot of the ticket even
// after the worker had written the reply.
export const dynamic = 'force-dynamic';

// Only the public, per-ticket endpoint is reachable from here, and no API key
// is ever attached: the customer proves access with the tracking token from
// the submission (?t=) or the email the ticket was filed under (?email=).
// The id param arrives URL-decoded, so a strict whitelist is what stops `..%2F`
// and friends from rewriting the upstream path.
const TICKET_NUMBER_RE = /^TKT-\d{8}-[A-Z0-9]{4,12}$/;
const TOKEN_RE = /^[0-9a-f]{32}$/;
const MAX_EMAIL_LENGTH = 320;

const notFound = () =>
  NextResponse.json({ message: 'Ticket not found' }, { status: 404 });

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id: ticketNumber } = await params;
  if (!TICKET_NUMBER_RE.test(ticketNumber)) {
    return NextResponse.json({ message: 'Invalid ticket number' }, { status: 400 });
  }

  const token = request.nextUrl.searchParams.get('t');
  const email = request.nextUrl.searchParams.get('email');
  const upstreamParams = new URLSearchParams();
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (token && TOKEN_RE.test(token)) {
    headers['X-Tracking-Token'] = token;
  } else if (email && email.length <= MAX_EMAIL_LENGTH) {
    upstreamParams.set('email', email);
  } else {
    return notFound();
  }

  // Server-side call: inside compose the browser-facing URL (localhost) is
  // not the API, so prefer the internal service URL when it is set.
  const apiUrl =
    process.env.API_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    'http://localhost:8000';
  const query = upstreamParams.toString();
  const upstream =
    `${apiUrl}/public/tickets/${encodeURIComponent(ticketNumber)}` +
    (query ? `?${query}` : '');

  try {
    const response = await fetch(upstream, {
      method: 'GET',
      headers,
      cache: 'no-store',
      redirect: 'error',
    });

    if (response.status === 404) {
      return notFound();
    }
    if (!response.ok) {
      return NextResponse.json(
        { message: 'Failed to fetch ticket status' },
        { status: response.status === 429 ? 429 : 502 }
      );
    }

    return NextResponse.json(await response.json());
  } catch (error) {
    console.error('Error fetching ticket:', error);
    return NextResponse.json(
      { message: 'Failed to fetch ticket status' },
      { status: 500 }
    );
  }
}
