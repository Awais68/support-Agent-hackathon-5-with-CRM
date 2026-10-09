"""A stand-in for the DeepSeek chat API, for live runs without a real key.

Serves the OpenAI-compatible ``POST /v1/chat/completions`` that
``chat_provider`` talks to. Each request is logged (model, whether a bearer
key was sent, tools offered) so a live run shows which provider and model
the app actually used.

Replies:
- tools offered, no tool result yet: call ``send_response`` once;
- tools offered, after a tool result: finish with plain text;
- ``response_format`` json_object: a small JSON object;
- otherwise: "General Inquiry" (what classification expects).

Run: python scripts/mock_deepseek.py [port]   (default 8089)
Point the app at it with DEEPSEEK_BASE_URL=http://<host>:<port>/v1.
"""

import json
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MOCK_REPLY = (
    "Thanks for reaching out. To reset your password, use Forgot password on the sign-in page."
)


def _completion(model: str, message: dict) -> dict:
    return {
        "id": f"chatcmpl-mock-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def reply_for(body: dict) -> dict:
    model = body.get("model", "")
    messages = body.get("messages", [])
    if body.get("tools"):
        if any(m.get("role") == "tool" for m in messages):
            return _completion(model, {"role": "assistant", "content": "Reply sent."})
        call = {
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": "send_response",
                "arguments": json.dumps({"response_body": MOCK_REPLY}),
            },
        }
        return _completion(model, {"role": "assistant", "content": None, "tool_calls": [call]})
    if (body.get("response_format") or {}).get("type") == "json_object":
        content = json.dumps({"intent": "general", "reply": MOCK_REPLY})
    else:
        content = "General Inquiry"
    return _completion(model, {"role": "assistant", "content": content})


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": f"mock has no {self.path}"}})
            return
        print(
            json.dumps(
                {
                    "mock": "deepseek",
                    "path": self.path,
                    "model": body.get("model"),
                    "bearer": self.headers.get("Authorization", "").startswith("Bearer "),
                    "tools": [t["function"]["name"] for t in body.get("tools") or []][:3],
                }
            ),
            flush=True,
        )
        self._send(200, reply_for(body))

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8089
    print(f"mock deepseek on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
