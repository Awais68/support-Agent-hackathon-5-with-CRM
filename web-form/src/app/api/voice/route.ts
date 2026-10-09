import { NextRequest, NextResponse } from 'next/server';

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

// Read at most MAX_BODY_BYTES of the (JSON) body; null when it is larger.
async function readCapped(request: NextRequest): Promise<string | null> {
  if (!request.body) return '';
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BODY_BYTES) {
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

export async function POST(request: NextRequest) {
  const declared = Number(request.headers.get('content-length'));
  if (declared > MAX_BODY_BYTES) {
    return tooLarge();
  }
  const body = await readCapped(request);
  if (body === null) {
    return tooLarge();
  }

  const apiUrl =
    process.env.API_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    'http://localhost:8000';

  try {
    const response = await fetch(`${apiUrl}/webhooks/voice/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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
