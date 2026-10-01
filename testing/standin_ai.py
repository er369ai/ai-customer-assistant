"""Runs one assistant folder on a throwaway copy, with a stand-in for Claude, so every button can be
clicked without an API key. Answers are visibly fake ([TEST]); this tests the app, not the AI.

    <folder>/.venv/bin/python standin_ai.py <folder> <copy-dir> <port>

Stand-in rules:
- a message containing a phone number  -> calls save_lead (like the real AI would), then confirms
- a message containing "#fail"         -> raises the error Anthropic gives for a wrong API key
- anything else                        -> "[TEST] You wrote: ..."
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

folder, copy, port = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
(copy / "data").mkdir(parents=True, exist_ok=True)
if not (copy / "business.json").exists():
    shutil.copy(folder / "business.json", copy / "business.json")
os.environ["ASSISTANT_DATA"] = str(copy / "data")
os.environ["ASSISTANT_BUSINESS"] = str(copy / "business.json")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, str(folder))

import anthropic  # noqa: E402
import httpx2     # noqa: E402
import assistant as A  # noqa: E402
import app  # noqa: E402


def text(t):
    return SimpleNamespace(type="text", text=t)


def resp(content, stop="end_turn"):
    return SimpleNamespace(content=content, stop_reason=stop, usage=SimpleNamespace(
        input_tokens=40, output_tokens=120, cache_creation_input_tokens=0, cache_read_input_tokens=2600))


class StandIn:
    def create(self, **kw):
        last = kw["messages"][-1]["content"]
        if isinstance(last[0], dict) and last[0].get("type") == "tool_result":
            return resp([text("[TEST] Kaydettim, yetkilimiz sizi arayıp onaylayacak. / Saved, the owner will call you to confirm.")])
        said = last[-1]["text"]
        if "#fail" in said:
            req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.AuthenticationError("invalid x-api-key", response=httpx2.Response(401, request=req), body=None)
        phone = re.search(r"\+?\d[\d ]{8,}\d", said)
        if phone:
            name = (re.match(r"\s*([^\d,]+?)[, ]", said) or [None, "Test Customer"])[1].strip()
            lead = {"customer_name": name, "phone": phone.group(0), "request": "test_drive",
                    "item": "A12 BMW X1 sDrive18i", "preferred_time": "Cumartesi 11:00", "language": "tr",
                    "summary_tr": f"{name} X1 için test sürüşü istiyor (TEST).",
                    "summary_en": f"{name} wants a test drive of the X1 (TEST)."}
            return resp([SimpleNamespace(type="tool_use", id="toolu_test", name="save_lead", input=lead)], "tool_use")
        return resp([text(f"[TEST] You wrote: {said[:120]}")])


FAKE = SimpleNamespace(beta=SimpleNamespace(messages=StandIn()),
                       models=SimpleNamespace(retrieve=lambda m: SimpleNamespace(id=m)))
A._make_client = lambda key: FAKE
print(f"stand-in assistant on http://localhost:{port}", flush=True)
app.ThreadingHTTPServer(("127.0.0.1", port), app.Handler).serve_forever()
