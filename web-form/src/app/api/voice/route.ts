import { NextRequest, NextResponse } from 'next/server';

import { apiBaseUrl, clientAddressHeaders, readCapped } from '@/lib/upstream';

// The browser posts voice messages here and this route forwards them to the
// public, rate-limited /webhooks/voice/message. No API key is attached (and
// the web form is not given one): a key here let anyone spend STT/LLM budget
// on the operator's credentials (AUDIT S7). The body is capped before it is
// buffered; the backend enforces the same cap.
export const dynamic = 'force-dynamic';

const MAX_AUDIO_BYTES = Number(process.env.VOICE_MAX_AUDIO_BYTES) || 5 * 1024 * 1024;
const MAX_BODY_BYTES = Math.ceil(MAX_AUDIO_BYTES / 3) * 4 + 4 * 1024;

const tooLarge = () =>
  NextResponse.json({ message: 'Voice message too large' }, { status: 413 });

export async function POST(request: NextRequest) {
  const body = await readCapped(request, MAX_BODY_BYTES);
  if (body === null) {
    return tooLarge();
  }

  try {
    const response = await fetch(`${apiBaseUrl()}/webhooks/voice/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...clientAddressHeaders(request) },
      body,
      cache: 'no-store',
      redirect: 'error',
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
