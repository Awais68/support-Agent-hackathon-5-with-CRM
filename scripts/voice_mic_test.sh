#!/usr/bin/env bash
# Record from the default microphone, send the clip through the voice channel,
# and play the agent's spoken reply back through the speakers.
#
# Usage: ./scripts/voice_mic_test.sh [seconds]   (default: 8)
set -euo pipefail

SECONDS_TO_RECORD="${1:-8}"
API_URL="${API_URL:-http://localhost:8000}"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

PYTHON="$(dirname "$0")/../.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3

echo "🎙  Speak now — recording ${SECONDS_TO_RECORD}s..."
arecord -f cd -d "$SECONDS_TO_RECORD" -t wav "$WORK_DIR/in.wav" >/dev/null 2>&1
echo "📤 Uploading to $API_URL/webhooks/voice/message ..."

"$PYTHON" - "$WORK_DIR" <<'PY'
import base64, json, sys
work = sys.argv[1]
audio = base64.b64encode(open(f"{work}/in.wav", "rb").read()).decode()
json.dump(
    {
        "audio_base64": audio,
        "filename": "mic.wav",
        "content_type": "audio/wav",
        "name": "Mic Test",
        "email": "mic.test@example.com",
    },
    open(f"{work}/req.json", "w"),
)
PY

curl -s -m 180 -X POST "$API_URL/webhooks/voice/message" \
    -H "Content-Type: application/json" \
    --data-binary "@$WORK_DIR/req.json" > "$WORK_DIR/res.json"

"$PYTHON" - "$WORK_DIR" <<'PY'
import base64, json, sys
work = sys.argv[1]
res = json.load(open(f"{work}/res.json"))
if "detail" in res:
    print("❌ API error:", res["detail"])
    raise SystemExit(1)
print(f"\n🗣  Heard      ({res['language']}, confidence {res['confidence']:.2f}):")
print(f"   {res['transcript']}")
if res.get("translated_to_english") and res["translated_to_english"] != res["transcript"]:
    print(f"🌐 English   : {res['translated_to_english']}")
if res.get("needs_clarification"):
    print(f"❓ Unclear   : {res['clarification_message']}")
print(f"\n🤖 Reply     : {res['agent_response']}")
print(f"🎫 Ticket    : {res.get('ticket_number')}")
if res.get("audio_base64"):
    open(f"{work}/reply.mp3", "wb").write(base64.b64decode(res["audio_base64"]))
    print("🔊 Playing spoken reply...")
else:
    print("(no TTS audio returned — text-only mode)")
PY

[ -f "$WORK_DIR/reply.mp3" ] && ffplay -nodisp -autoexit -loglevel quiet "$WORK_DIR/reply.mp3"
