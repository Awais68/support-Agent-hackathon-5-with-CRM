import { NextRequest, NextResponse } from 'next/server';

// The backend's /webhooks/voice/message requires X-API-Key, which must never
// reach the browser. The browser posts here and this server route adds it.
export const dynamic = 'force-dynamic';

export async function POST(request: NextRequest) {
  const apiUrl =
    process.env.API_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    'http://localhost:8000';
  const apiKey = process.env.API_KEY_SECRET;

  if (!apiKey) {
    return NextResponse.json(
      { message: 'Server configuration error' },
      { status: 500 }
    );
  }

  try {
    const response = await fetch(`${apiUrl}/webhooks/voice/message`, {
      method: 'POST',
      headers: {
        'X-API-Key': apiKey,
        'Content-Type': 'application/json',
      },
      body: await request.text(),
      cache: 'no-store',
    });
    const data = await response.json().catch(() => ({}));
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error('Error forwarding voice message:', error);
    return NextResponse.json(
      { message: 'Failed to submit voice message' },
      { status: 502 }
    );
  }
}
