"""Checks the assistant end to end with a fake Claude: no API key, no cost, no real data touched.

    .venv/bin/python selftest.py

Runs on a temporary copy of business.json and a temporary data folder.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="assistant-selftest-"))
shutil.copy(HERE / "business.json", TMP / "business.json")
os.environ["ASSISTANT_DATA"] = str(TMP / "data")
os.environ["ASSISTANT_BUSINESS"] = str(TMP / "business.json")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, str(HERE))

import anthropic  # noqa: E402
import httpx2     # noqa: E402  (the HTTP library anthropic 1.x is built on; used to fake an error)
import assistant as A  # noqa: E402
import app  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "FAIL  ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        failures.append(name)


def raises(fn, exc, contains=""):
    try:
        fn()
    except exc as e:
        return contains in str(e)
    except Exception:
        return False
    return False


# ── fake Claude ──────────────────────────────────────────────────────────────

def text(t):
    return SimpleNamespace(type="text", text=t)


def usage(i=100, o=50, cw=3000, cr=0):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_creation_input_tokens=cw, cache_read_input_tokens=cr)


def resp(content, stop="end_turn"):
    return SimpleNamespace(content=content, stop_reason=stop, usage=usage())


class FakeMessages:
    def __init__(self):
        self.script, self.calls = [], []

    def create(self, **kw):
        self.calls.append(dict(kw, messages=list(kw["messages"])))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


FAKE = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()),
                       models=SimpleNamespace(retrieve=lambda m: SimpleNamespace(id=m)))
A._make_client = lambda key: FAKE


def script(*items):
    FAKE.beta.messages.script = list(items)
    FAKE.beta.messages.calls = []


def api_error(cls, status):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("fake", response=httpx2.Response(status, request=req), body=None)


# ── setup ────────────────────────────────────────────────────────────────────
print("setup")
b = A.load_business()
miss = A.missing_setup(b)
check("missing setup lists owner and API key", any("WhatsApp" in m for m in miss) and "Claude API key" in miss, miss)
check("chat refuses before setup", raises(lambda: A.reply(A.new_chat(), "Merhaba"), A.SetupError))

payload = {"business": dict(b["business"], owner_name="Mehmet", owner_whatsapp="0533 123"), "items": b["items"]}
check("bad WhatsApp number rejected", raises(lambda: A.save_business(payload), A.UserError, "country code"))
payload["business"]["owner_whatsapp"] = "+90 533 123 45 67"
payload["items"] = b["items"] + [{"nonsense": "x"}, {k["key"]: "" for k in b["item_fields"]}]
saved = A.save_business(payload)
check("number normalised to digits", saved["business"]["owner_whatsapp"] == "905331234567", saved["business"]["owner_whatsapp"])
for raw, want in (("0533 123 45 67", "905331234567"), ("533 123 45 67", "905331234567"),
                  ("0090 533 123 45 67", "905331234567"), ("+7 912 345 67 89", "79123456789")):
    check(f"WhatsApp '{raw}' -> {want}", A.wa_digits(raw) == want, A.wa_digits(raw))
check("empty and unknown rows dropped", len(saved["items"]) == len(b["items"]), len(saved["items"]))
check("unknown model rejected", raises(lambda: A.save_business(dict(payload, model="gpt-x")), A.UserError, "Unknown model"))

check("non-Claude key rejected", raises(lambda: A.save_key("hello"), A.UserError, "sk-ant-"))
A.save_key("sk-ant-test-0000-abcd")
check("key file is private (600)", (A.SECRET.stat().st_mode & 0o777) == 0o600, oct(A.SECRET.stat().st_mode))
check("setup complete after key", A.missing_setup(A.load_business()) == [], A.missing_setup(A.load_business()))
pub = json.dumps(A.public_business())
check("browser never gets the key", "sk-ant-test" not in pub and A.key_status()["hint"] == "…abcd")
check("key test is free (models.retrieve)", A.test_key()["ok"])

# ── prompt ───────────────────────────────────────────────────────────────────
print("prompt")
b = A.load_business()
p1, p2 = A.system_prompt(b), A.system_prompt(b)
first_key = b["item_fields"][0]["key"]
check("prompt is byte-identical every time (cacheable)", p1 == p2)
check("every listing is in the prompt", all(it[first_key] in p1 for it in b["items"]))
check("placeholders filled", "{{" not in p1 and "Mehmet" in p1)
check("owner's WhatsApp kept out of the prompt", "905331234567" not in p1)

# ── replies ──────────────────────────────────────────────────────────────────
print("replies")
cid = A.new_chat()
script(resp([SimpleNamespace(type="thinking", thinking="", signature="s"), text("Merhaba! Elimizde A01 var.")]))
r = A.reply(cid, "Merhaba, otomatik araba var mı?")
call = FAKE.beta.messages.calls[0]
check("answer returned", r["reply"] == "Merhaba! Elimizde A01 var.", r)
check("uses the chosen model", call["model"] == b["model"], call["model"])
check("refusal fallback on", call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"])
check("low effort, caching on", call["output_config"] == {"effort": "low"} and call["cache_control"] == {"type": "ephemeral"})
check("system prompt sent", call["system"][0]["text"] == A.system_prompt(b))
check("save_lead tool is strict", call["tools"][0]["name"] == "save_lead" and call["tools"][0]["strict"] is True)
first = call["messages"][0]["content"]
check("clock note added before the customer's text", first[0]["text"].startswith("[system: local time") and first[1]["text"] == "Merhaba, otomatik araba var mı?")
chat = A._chats[cid]
check("thinking block kept in history unchanged", [blk.type for blk in chat.messages[1]["content"]] == ["thinking", "text"])
check("cost computed", abs(r["usd"] - (100 * 4 + 50 * 20 + 3000 * 5) / 1e6) < 1e-6, r["usd"])

lead_in = {"customer_name": "Ali", "phone": "05331112233", "request": "test_drive", "item": "A12 BMW X1",
           "preferred_time": "Cumartesi 11:00", "language": "tr", "summary_tr": "Ali X1 için test sürüşü istiyor.",
           "summary_en": "Ali wants to test drive the X1."}
script(resp([text("Kaydediyorum."), SimpleNamespace(type="tool_use", id="tu_1", name="save_lead", input=lead_in)], "tool_use"),
       resp([text("Tamam Ali, Mehmet Bey sizi arayacak.")]))
r = A.reply(cid, "Ali, 05331112233, cumartesi 11 uygun")
second = FAKE.beta.messages.calls[1]["messages"][-1]["content"][0]
check("tool result sent back with matching id", second["type"] == "tool_result" and second["tool_use_id"] == "tu_1", second)
check("lead returned with the reply", r["leads"] and r["leads"][0]["customer_name"] == "Ali" and "arayacak" in r["reply"], r)
leads = A.list_leads()
check("lead stored for the Leads page", leads and leads[0]["item"] == "A12 BMW X1" and leads[0]["status"] == "new", leads)
A.set_lead_status(leads[0]["id"], "done")
check("lead can be marked done", A.list_leads()[0]["status"] == "done")

from datetime import datetime, timedelta  # noqa: E402
fresh = A.list_leads()[0]
check("lead that was just saved is not overdue", fresh["age_hours"] < 0.1 and not fresh["overdue"], fresh)
old_t = (datetime.now(A.TZ) - timedelta(hours=3)).isoformat(timespec="seconds")
A._append(A.LEADS, dict(lead_in, id="oldlead00001", t=old_t, chat=cid, customer_name="Veli"))
veli = next(x for x in A.list_leads() if x["id"] == "oldlead00001")
check("lead waiting 3 h with a 2 h limit is overdue", veli["overdue"] and veli["age_hours"] >= 3, veli)
A.set_lead_status("oldlead00001", "done")
check("marking it done stops the reminder", not next(x for x in A.list_leads() if x["id"] == "oldlead00001")["overdue"])
bb = A.load_business()
for bad in ("abc", "0", "100"):
    check(f"remind hours '{bad}' rejected",
          raises(lambda: A.save_business({"business": dict(bb["business"], remind_hours=bad), "items": bb["items"]}), A.UserError, "hours"))
A.save_business({"business": dict(bb["business"], remind_hours="1,5"), "items": bb["items"]})
check("remind hours accepts 1,5", A.load_business()["business"]["remind_hours"] == "1.5" and A.remind_hours(A.load_business()) == 1.5)
check("remind setting kept out of the prompt", "Remind after" not in A.system_prompt(A.load_business()))

n = len(chat.messages)
script(resp([], "refusal"))
r = A.reply(cid, "something the model declines")
check("declined turn rolled back, owner offered", len(chat.messages) == n and r["reply"] == A.DECLINED)

script(api_error(anthropic.AuthenticationError, 401))
check("bad key explained in plain words", raises(lambda: A.reply(cid, "hello"), A.ServiceError, "rejected the API key"))
check("failed turn rolled back", len(chat.messages) == n)
script(api_error(anthropic.BadRequestError, 400))
check("400 (e.g. no credit) explained", raises(lambda: A.reply(cid, "hello"), A.ServiceError, "refused the request"))

check("over-long message rejected", raises(lambda: A.reply(cid, "x" * (A.MAX_CHARS + 1)), A.UserError, "too long"))
check("unknown chat rejected", raises(lambda: A.reply("ffffffffffff", "hi"), A.UserError, "expired"))
check("chat log id can't escape the folder", raises(lambda: A.chat_log("../secret"), A.UserError))
check("chat log written", [x["who"] for x in A.chat_log(cid)][:2] == ["customer", "assistant"])

fb = [SimpleNamespace(type="thinking"), SimpleNamespace(type="fallback"), SimpleNamespace(type="text")]
check("after a fallback, declined model's thinking not echoed", [x.type for x in A._echoable(fb)] == ["fallback", "text"])
check("no fallback: everything echoed", len(A._echoable(fb[:1] + fb[2:])) == 2)
s = A.usage_stats()
check("cost page totals", s["calls"] >= 4 and s["chats"] >= 1 and s["usd_total"] > 0, s)

# ── the real SDK, against a simulated Anthropic server ───────────────────────
print("sdk")
wire, canned = [], []


def mock(req):
    wire.append((req.headers.get("anthropic-beta"), json.loads(req.content)))
    return httpx2.Response(200, json=canned.pop(0))


def msg(content, stop):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": b["model"], "content": content,
            "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


canned += [msg([{"type": "thinking", "thinking": "", "signature": "sig1"},
                {"type": "tool_use", "id": "toolu_1", "name": "save_lead", "input": lead_in}], "tool_use"),
           msg([{"type": "text", "text": "Tamam."}], "end_turn")]
A._make_client = lambda key: anthropic.Anthropic(
    api_key=key, max_retries=0, http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(mock)))
A._clients.clear()
r = A.reply(A.new_chat(), "Merhaba")
beta, body = wire[1]
check("SDK accepts every parameter we send", r["reply"] == "Tamam." and len(r["leads"]) == 1, r)
check("beta header and fallbacks on the wire", beta == "server-side-fallback-2026-07-01" and body["fallbacks"] == "default")
check("thinking + tool call echoed byte-for-byte",
      body["messages"][1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig1"}
      and body["messages"][2]["content"][0]["tool_use_id"] == "toolu_1", body["messages"][1:])
A._make_client = lambda key: FAKE
A._clients.clear()

# ── HTTP ─────────────────────────────────────────────────────────────────────
print("http")
srv = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"


def http(path, body=None, headers=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


code, page = http("/")
check("page served", code == 200 and b"<title>" in page, code)
code, body = http("/api/business")
check("business API has no key", code == 200 and b"sk-ant" not in body, code)
new_id = json.loads(http("/api/chat/new", {})[1])["id"]
script(resp([text("Hello from the gallery.")]))
code, body = http("/api/chat", {"id": new_id, "text": "Hi"})
check("chat over HTTP", code == 200 and json.loads(body)["reply"] == "Hello from the gallery.", body)
check("other websites blocked (Origin)", http("/api/business", headers={"Origin": "https://evil.example"})[0] == 403)
check("other hostnames blocked (Host)", http("/api/business", headers={"Host": "evil.example"})[0] == 403)
check("bad JSON is a 400", http("/api/chat", b"{nope")[0] == 400)
check("malformed setup data is a 400", http("/api/business", {"business": "x", "items": []})[0] == 400)
check("unknown route 404", http("/api/nothing")[0] == 404)
srv.shutdown()

shutil.rmtree(TMP, ignore_errors=True)
print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
