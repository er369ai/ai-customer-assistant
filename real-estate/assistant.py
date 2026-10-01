"""Everything the assistant does: business info, the prompt Claude reads, the reply loop, leads, costs.

Business-specific content lives in business.json (edited from the Setup page) and prompt.md (how this
kind of business talks to its customers). This file is identical in every folder: fix it in one,
copy it to the others, run selftest.py in each.
"""
import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("ASSISTANT_DATA") or HERE / "data")
BUSINESS = Path(os.environ.get("ASSISTANT_BUSINESS") or HERE / "business.json")
PROMPT = HERE / "prompt.md"
SECRET = DATA / "secret.json"          # the API key; chmod 600, never sent to the browser
LEADS = DATA / "leads.jsonl"
LEAD_STATUS = DATA / "lead_status.json"
USAGE = DATA / "usage.jsonl"
CHATS = DATA / "chats"

TZ = ZoneInfo("Asia/Nicosia")
DEFAULT_MODEL = "claude-opus-5-5"
# $ per million tokens: input, output, cache write (5 min), cache read
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
}
MODEL_LABELS = {
    "claude-opus-5-5": "Claude Opus 5.5: best answers",
    "claude-sonnet-5-5": "Claude Sonnet 5.5: about half the cost",
}
MAX_CHARS = 1500       # one customer message
MAX_TURNS = 30         # customer messages per chat; history is never trimmed, so it is capped instead
MAX_CHATS = 200        # chats kept in memory
MAX_ITEMS = 300
MAX_TOOL_ROUNDS = 4
REMIND_HOURS = 2.0     # used when business.json has no valid remind_hours
CHANNEL = "web demo; the customer's phone number is not visible"
DECLINED = ("Bu konuda burada yardımcı olamıyorum, ama sorunuzu işletme sahibine iletebilirim. / "
            "I can't help with that here, but I can pass your question to the owner.")

LEAD_FIELDS = ["customer_name", "phone", "request", "item", "preferred_time", "language",
               "summary_tr", "summary_en"]
TOOLS = [{
    "name": "save_lead",
    "description": (
        "Save a customer's request for the owner: a visit or test drive, a reservation, a price "
        "offer, a callback, or a question the business info doesn't answer. Call it once you have "
        "the customer's name and a way to reach them. The owner sees it on the Leads page and "
        "contacts the customer."),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": LEAD_FIELDS,
        "properties": {
            "customer_name": {"type": "string"},
            "phone": {"type": "string",
                      "description": "Phone or WhatsApp number as the customer gave it; empty string if not given."},
            "request": {"type": "string",
                        "enum": ["visit", "test_drive", "reservation", "offer", "callback", "question"]},
            "item": {"type": "string",
                     "description": "Listing code(s) and name the request is about; empty string if none."},
            "preferred_time": {"type": "string",
                               "description": "When the customer wants it, in their words; empty string if not given."},
            "language": {"type": "string", "enum": ["tr", "en", "ru", "fa", "other"]},
            "summary_tr": {"type": "string", "description": "One or two sentences for the owner, in Turkish."},
            "summary_en": {"type": "string", "description": "The same summary in English."},
        },
    },
}]


class UserError(Exception):
    """Bad input; the message is shown as is."""


class SetupError(Exception):
    """The Setup page isn't finished."""


class ServiceError(Exception):
    """The Anthropic call failed; the message is already in plain language."""


_lock = threading.Lock()
_chats = {}
_clients = {}


# ── business info ────────────────────────────────────────────────────────────

def load_business():
    return json.loads(BUSINESS.read_text(encoding="utf-8"))


def save_business(payload):
    """Validate what the Setup page sent and write business.json. Returns the saved data."""
    b = load_business()
    biz_in = payload.get("business")
    items_in = payload.get("items")
    if not isinstance(biz_in, dict) or not isinstance(items_in, list):
        raise UserError("Malformed setup data.")

    biz = {}
    for f in b["business_fields"]:
        v = str(biz_in.get(f["key"], "")).strip()
        limit = 4000 if f.get("long") else 300
        if len(v) > limit:
            raise UserError(f"{f['label']}: too long (max {limit} characters).")
        if f.get("phone") and v:
            v = wa_digits(v)
            if not 10 <= len(v) <= 15:
                raise UserError(f"{f['label']}: write the full number with country code, e.g. 905331234567.")
        if f.get("number") and v:
            try:
                n = float(v.replace(",", "."))
            except ValueError:
                n = -1
            if not 0.25 <= n <= 72:
                raise UserError(f"{f['label']}: a number of hours between 0.25 and 72.")
            v = f"{n:g}"
        biz[f["key"]] = v

    if len(items_in) > MAX_ITEMS:
        raise UserError(f"Too many rows (max {MAX_ITEMS}).")
    keys = [f["key"] for f in b["item_fields"]]
    items = []
    for row in items_in:
        if not isinstance(row, dict):
            raise UserError("Malformed table row.")
        item = {k: str(row.get(k, "")).strip()[:300] for k in keys}
        if any(item.values()):
            items.append(item)

    model = payload.get("model") or b.get("model") or DEFAULT_MODEL
    if model not in PRICES:
        raise UserError(f"Unknown model: {model}")

    b.update(business=biz, items=items, model=model)
    if BUSINESS.exists():
        BUSINESS.with_suffix(".json.bak").write_text(BUSINESS.read_text(encoding="utf-8"), encoding="utf-8")
    _write_json(BUSINESS, b)
    return b


def wa_digits(phone):
    """A number as WhatsApp wants it: digits only, with country code. Local Turkish/TRNC style
    (0533 123 45 67) becomes 905331234567; 00-prefixed international loses the 00."""
    d = re.sub(r"\D", "", phone or "")
    if d.startswith("00"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = "90" + d[1:]
    elif len(d) == 10 and d.startswith("5"):
        d = "90" + d
    return d


def missing_setup(b):
    """Labels of what the owner still has to fill in before the assistant can answer."""
    miss = [f["label"] for f in b["business_fields"]
            if f.get("required") and not str(b["business"].get(f["key"], "")).strip()]
    if not b.get("items"):
        miss.append(b["item_label"])
    if not api_key():
        miss.append("Claude API key")
    return miss


def public_business():
    """What the browser gets: everything except the key itself."""
    b = load_business()
    return {
        "kind_label": b["kind_label"],
        "business": b["business"],
        "business_fields": b["business_fields"],
        "item_label": b["item_label"],
        "item_fields": b["item_fields"],
        "items": b["items"],
        "examples": b.get("examples", []),
        "model": b.get("model") or DEFAULT_MODEL,
        "models": [{"id": m, "label": MODEL_LABELS[m]} for m in PRICES],
        "setup": {"missing": missing_setup(b), "key": key_status()},
    }


def system_prompt(b):
    """The text Claude reads before every chat. Same business.json gives byte-identical output, which
    is what lets prompt caching reuse it; never put the time or anything per-chat in here."""
    biz = b["business"]
    text = PROMPT.read_text(encoding="utf-8").strip()
    for k, v in biz.items():
        text = text.replace("{{%s}}" % k, v)

    lines = [text, "", "## Business info"]
    for f in b["business_fields"]:
        v = biz.get(f["key"], "").strip()
        if v and not f.get("private") and not f.get("long"):
            lines.append(f"- {f['label']}: {v}")
    for f in b["business_fields"]:
        v = biz.get(f["key"], "").strip()
        if v and f.get("long") and not f.get("private"):
            lines += ["", f"## {f['label']}", v]

    lines += ["", f"## {b['item_label']} ({len(b['items'])})"]
    for it in b["items"]:
        lines.append("- " + " | ".join(f"{f['label']}: {it[f['key']]}"
                                       for f in b["item_fields"] if it.get(f["key"])))
    return "\n".join(lines) + "\n"


# ── API key ──────────────────────────────────────────────────────────────────

def api_key():
    env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env:
        return env
    try:
        return json.loads(SECRET.read_text()).get("api_key", "")
    except (OSError, ValueError):
        return ""


def key_status():
    env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    key = api_key()
    return {"set": bool(key), "source": "env" if env else ("file" if key else None),
            "hint": f"…{key[-4:]}" if key else ""}


def save_key(key):
    key = (key or "").strip()
    if key and not key.startswith("sk-ant-"):
        raise UserError("That doesn't look like a Claude API key. They start with sk-ant-")
    if not key:
        SECRET.unlink(missing_ok=True)
        return
    DATA.mkdir(parents=True, exist_ok=True)
    fd = os.open(SECRET, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"api_key": key}, f)
    os.chmod(SECRET, 0o600)


def test_key():
    """Free check: looks the model up instead of sending a message."""
    model = load_business().get("model") or DEFAULT_MODEL
    try:
        _client().models.retrieve(model)
    except anthropic.APIError as e:
        raise ServiceError(_explain(e)) from e
    return {"ok": True, "model": model}


def _make_client(key):
    return anthropic.Anthropic(api_key=key, timeout=90.0, max_retries=2)


def _client():
    key = api_key()
    if not key:
        raise SetupError("Add your Claude API key on the Setup page first.")
    with _lock:
        if key not in _clients:
            _clients.clear()
            _clients[key] = _make_client(key)
        return _clients[key]


def _explain(e):
    # Most specific first: timeout is a kind of connection error, and the status errors share a base.
    if isinstance(e, anthropic.AuthenticationError):
        return "Anthropic rejected the API key. Check it on the Setup page."
    if isinstance(e, anthropic.PermissionDeniedError):
        return f"This API key isn't allowed to do that: {e.message}"
    if isinstance(e, anthropic.NotFoundError):
        return f"Model not found: {e.message}"
    if isinstance(e, anthropic.RateLimitError):
        return "Too many requests right now. Wait a minute and try again."
    if isinstance(e, anthropic.BadRequestError):
        return f"Anthropic refused the request: {e.message}"   # e.g. "credit balance is too low"
    if isinstance(e, anthropic.APIStatusError):
        return f"Anthropic is having a problem ({e.status_code}). Try again shortly."
    if isinstance(e, anthropic.APITimeoutError):
        return "Anthropic took too long to answer. Try again."
    if isinstance(e, anthropic.APIConnectionError):
        return "Can't reach Anthropic. Check the internet connection."
    return str(e)


# ── chats ────────────────────────────────────────────────────────────────────

class _Chat:
    def __init__(self, cid):
        self.id = cid
        self.messages = []
        self.turns = 0
        self.lock = threading.Lock()


def new_chat():
    cid = uuid.uuid4().hex[:12]
    with _lock:
        _chats[cid] = _Chat(cid)
        while len(_chats) > MAX_CHATS:
            _chats.pop(next(iter(_chats)))
    return cid


def _get_chat(cid):
    with _lock:
        chat = _chats.get(cid)
    if chat is None:
        raise UserError("This chat has expired (the app was restarted). Start a new chat.")
    return chat


def reply(cid, text):
    """One customer message in, the assistant's answer out (plus any leads it saved)."""
    text = (text or "").strip()
    if not text:
        raise UserError("Write a message first.")
    if len(text) > MAX_CHARS:
        raise UserError(f"Message too long (max {MAX_CHARS} characters).")
    b = load_business()
    missing = missing_setup(b)
    if missing:
        raise SetupError("Finish the Setup page first: " + ", ".join(missing))
    chat = _get_chat(cid)

    with chat.lock:
        if chat.turns >= MAX_TURNS:
            raise UserError("This chat is very long. Start a new chat.")
        start = len(chat.messages)
        now = datetime.now(TZ).strftime("%A %d %B %Y, %H:%M")
        chat.messages.append({"role": "user", "content": [
            {"type": "text", "text": f"[system: local time {now} in {b['business'].get('city', '')}; "
                                     f"channel: {CHANNEL}]"},
            {"type": "text", "text": text},
        ]})
        try:
            answer, leads, usd = _run(chat, b)
        except BaseException:
            del chat.messages[start:]      # the turn never finished; the next one starts clean
            raise
        if answer is None:                 # declined by the model: drop the turn, offer the owner
            del chat.messages[start:]
            answer = DECLINED
        else:
            chat.turns += 1

    _log_chat(cid, "customer", text)
    _log_chat(cid, "assistant", answer)
    return {"reply": answer, "leads": leads, "usd": round(usd, 4)}


def _run(chat, b):
    """Call Claude until it answers in text, running save_lead whenever it asks.
    Returns (text or None if declined, leads saved, dollars spent)."""
    model = b.get("model") or DEFAULT_MODEL
    system = [{"type": "text", "text": system_prompt(b)}]
    leads, usd = [], 0.0
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            resp = _client().beta.messages.create(
                model=model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",                 # a declined request is retried on another model
                output_config={"effort": "low"},     # chat: short answers, low thinking cost
                cache_control={"type": "ephemeral"},  # caches prompt + history between turns
                system=system,
                tools=TOOLS,
                messages=chat.messages,
            )
        except anthropic.APIError as e:
            raise ServiceError(_explain(e)) from e
        usd += _record_usage(chat.id, model, resp.usage)

        if resp.stop_reason == "refusal":
            return None, leads, usd
        content = _echoable(resp.content)
        chat.messages.append({"role": "assistant", "content": content})
        if resp.stop_reason == "tool_use":
            results = [_run_tool(chat.id, blk, leads) for blk in content if blk.type == "tool_use"]
            chat.messages.append({"role": "user", "content": results})
            continue
        text = "\n".join(blk.text for blk in content if blk.type == "text").strip()
        return text or "…", leads, usd
    return "Talebinizi işletme sahibine ilettim. / I've passed your request to the owner.", leads, usd


def _echoable(content):
    """Blocks to send back on the next turn: all of them, unchanged, except after a mid-answer
    fallback, where the declined model's thinking and tool calls before the last `fallback`
    block must be left out."""
    cuts = [i for i, blk in enumerate(content) if blk.type == "fallback"]
    if not cuts:
        return list(content)
    drop = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}
    return [blk for i, blk in enumerate(content) if i >= cuts[-1] or blk.type not in drop]


def _run_tool(cid, blk, leads):
    if blk.name != "save_lead":
        return {"type": "tool_result", "tool_use_id": blk.id, "is_error": True,
                "content": f"Unknown tool: {blk.name}"}
    inp = blk.input if isinstance(blk.input, dict) else {}
    lead = {k: str(inp.get(k, "")).strip()[:500] for k in LEAD_FIELDS}
    lead.update(id=uuid.uuid4().hex[:12], t=datetime.now(TZ).isoformat(timespec="seconds"), chat=cid)
    _append(LEADS, lead)
    leads.append(lead)
    return {"type": "tool_result", "tool_use_id": blk.id,
            "content": "Saved. The owner will see it and contact the customer. Tell the customer so, "
                       "without saying any time is confirmed."}


# ── leads, costs, logs ───────────────────────────────────────────────────────

def remind_hours(b):
    """Hours a lead may wait before the app nags the owner about it."""
    try:
        return max(0.25, float(str(b["business"].get("remind_hours", "")).replace(",", ".")))
    except ValueError:
        return REMIND_HOURS


def list_leads():
    """Newest first. Each lead says how long it has waited and whether the owner is late calling."""
    limit = remind_hours(load_business())
    status = _read_json(LEAD_STATUS, {})
    now = datetime.now(TZ)
    rows = _read_jsonl(LEADS)
    for r in rows:
        r["status"] = status.get(r.get("id"), "new")
        try:
            age = (now - datetime.fromisoformat(r["t"])).total_seconds() / 3600
        except (KeyError, TypeError, ValueError):
            age = 0.0
        r["age_hours"] = round(age, 1)
        r["overdue"] = r["status"] == "new" and age >= limit
    return list(reversed(rows))


def set_lead_status(lead_id, status):
    if status not in ("new", "done"):
        raise UserError("Status must be new or done.")
    with _lock:
        s = _read_json(LEAD_STATUS, {})
        s[str(lead_id)] = status
        _write_json(LEAD_STATUS, s)


def chat_log(cid):
    if not re.fullmatch(r"[0-9a-f]{12}", cid or ""):
        raise UserError("Bad chat id.")
    return _read_jsonl(CHATS / f"{cid}.jsonl")


def usage_stats():
    rows = _read_jsonl(USAGE)
    today = datetime.now(TZ).date().isoformat()
    total = sum(r.get("usd", 0) for r in rows)
    chats = {r.get("chat") for r in rows}
    return {
        "calls": len(rows),
        "chats": len(chats),
        "usd_total": round(total, 4),
        "usd_today": round(sum(r.get("usd", 0) for r in rows if r.get("t", "").startswith(today)), 4),
        "usd_per_chat": round(total / len(chats), 4) if chats else 0,
        "cache_read": sum(r.get("cache_read", 0) for r in rows),
    }


def _record_usage(cid, model, u):
    p_in, p_out, p_cw, p_cr = PRICES.get(model, PRICES[DEFAULT_MODEL])
    i = getattr(u, "input_tokens", 0) or 0
    o = getattr(u, "output_tokens", 0) or 0
    cw = getattr(u, "cache_creation_input_tokens", 0) or 0
    cr = getattr(u, "cache_read_input_tokens", 0) or 0
    usd = (i * p_in + o * p_out + cw * p_cw + cr * p_cr) / 1e6
    _append(USAGE, {"t": datetime.now(TZ).isoformat(timespec="seconds"), "chat": cid, "model": model,
                    "in": i, "out": o, "cache_write": cw, "cache_read": cr, "usd": round(usd, 6)})
    return usd


def _log_chat(cid, who, text):
    _append(CHATS / f"{cid}.jsonl", {"t": datetime.now(TZ).isoformat(timespec="seconds"), "who": who, "text": text})


def _append(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue       # a half-written line after a crash; skip it
    return out


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
