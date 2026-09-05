import { NextRequest, NextResponse } from 'next/server';

// Route handlers and their fetches are cached by default in the App Router,
// so the tracking page kept serving the pre-agent snapshot of the ticket even
// after the worker had written the reply.
export const dynamic = 'force-dynamic';

export async function GET(
  _request: NextRequest,
  { params }: { params: { id: string } }
) {
  const ticketId = params.id;
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const apiKey = process.env.API_KEY_SECRET;

  if (!apiKey) {
    return NextResponse.json(
      { message: 'Server configuration error' },
      { status: 500 }
    );
  }

  try {
    const response = await fetch(`${apiUrl}/tickets/${ticketId}`, {
      method: 'GET',
      headers: {
        'X-API-Key': apiKey,
        'Content-Type': 'application/json',
      },
      cache: 'no-store',
    });

    if (!response.ok) {
      if (response.status === 404) {
        return NextResponse.json(
          { message: 'Ticket not found' },
          { status: 404 }
        );
      }
      const error = await response.json().catch(() => ({}));
      return NextResponse.json(error, { status: response.status });
    }

    const data = await response.json();

    // The API returns messages as { direction, created_at }; the tracking UI
    // reads { sender_type, timestamp }. Without this mapping every message
    // rendered as a customer bubble with an "Invalid Date" stamp.
    const messages = Array.isArray(data.messages)
      ? data.messages.map((m: Record<string, unknown>) => ({
          ...m,
          sender_type:
            m.sender_type ?? (m.direction === 'outbound' ? 'agent' : 'customer'),
          timestamp: m.timestamp ?? m.created_at,
        }))
      : [];

    return NextResponse.json({
      ...data,
      ticket_id: data.ticket_id ?? data.id,
      messages,
    });
  } catch (error) {
    console.error('Error fetching ticket:', error);
    return NextResponse.json(
      { message: 'Failed to fetch ticket status' },
      { status: 500 }
    );
  }
}
