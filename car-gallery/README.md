# AI assistant: Car gallery

Answers a business's customers by itself, day and night, in Turkish, English, Russian and Persian.
It knows only what the owner types into Setup (business info + cars), never invents prices or stock,
and saves serious customers (visit, call, offer) on the **Leads** page for the owner to call back.

## Start
    ./start.sh          → opens http://localhost:8770

First run installs the Claude library (about a minute). Stop it with Ctrl+C.

## First-time setup (the page asks for these)
1. **Claude API key**: console.anthropic.com → API Keys. Stored only in `data/secret.json`.
2. **Owner's name and WhatsApp**: the name is what the AI tells customers; the number is never shown to them.
3. Check the business info and the cars table, then **Save all**.

Then use the **Chat** tab as if you were a customer.

## Tabs
| Tab | What it is |
|---|---|
| Sohbet · Chat | Customer view. A demo of what WhatsApp customers will get. |
| Kurulum · Setup | Everything the AI knows. Change a price here and the AI uses it on the next message. |
| Talepler · Leads | Customers who want a visit, a call or to make an offer, with a summary and the whole chat. If one waits longer than the "Remind after" hours (default 2), a red reminder shows on every tab, in the browser tab title, and as a desktop alert if you turn alerts on. |
| Maliyet · Cost | What every chat actually cost, so you can set a monthly price. |

## Files
| File | Job |
|---|---|
| `business.json` | The business: info, cars, model. Written by the Setup page. |
| `prompt.md` | How the AI behaves for this kind of business. Plain English, edit freely. |
| `assistant.py` | The engine: prompt, Claude calls, leads, costs. **Same file in every folder.** |
| `app.py` | Local web server (this computer only). |
| `ui.html` | The page. **Same file in every folder.** |
| `selftest.py` | 62 checks with a fake Claude: no key, no cost. Run after any change. |
| `data/` | API key, leads, chat logs, costs. Never share or commit it. |

## Checking it still works
    .venv/bin/python selftest.py      → must end with ALL PASS

## Not built yet
The connection to a real WhatsApp number. That needs the business's own WhatsApp Business
account (Meta's official Cloud API). Build it after a business says yes to the demo, not before.
