// Clicks through the assistant like a first-time user, in a real (headless) Chromium.
// node browser_test.js <port> <copy-dir> <kind: car|estate> <shots-dir>
// First start a stand-in copy: python standin_ai.py <folder> <copy-dir> <port>  (needs Playwright, found in ~/.npm/_npx)
const { chromium } = require("/home/er369ai/.npm/_npx/9833c18b2d85bc59/node_modules/playwright-core");
const fs = require("fs");
const path = require("path");

const [port, copy, kind, shots] = process.argv.slice(2);
const base = `http://localhost:${port}`;
const exe = fs.readdirSync(process.env.HOME + "/.cache/ms-playwright").filter(d => d.startsWith("chromium_headless_shell"))
  .map(d => `${process.env.HOME}/.cache/ms-playwright/${d}/chrome-headless-shell-linux64/chrome-headless-shell`)[0];
fs.mkdirSync(shots, { recursive: true });
let n = 0, fails = 0;
const ok = (name, cond, detail = "") => { console.log((cond ? "  ok  " : "FAIL  ") + name + (cond ? "" : "  -> " + detail)); if (!cond) fails++; };
const shot = async (page, name) => page.screenshot({ path: path.join(shots, `${String(++n).padStart(2, "0")}-${kind}-${name}.png`) });
const leadLine = (name, hoursAgo) => JSON.stringify({
  id: "old" + Math.random().toString(16).slice(2, 11), t: new Date(Date.now() - hoursAgo * 3600e3).toISOString(),
  chat: "000000000000", customer_name: name, phone: "+90 542 000 00 00", request: "callback", item: kind === "car" ? "A06 Nissan Qashqai" : "E101 Gönyeli 2+1",
  preferred_time: "", language: "tr", summary_tr: name + " geri aranmak istiyor.", summary_en: name + " wants a call back." }) + "\n";

(async () => {
  const browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, locale: "tr-TR" });
  // Record desktop alerts instead of showing them.
  await ctx.addInitScript(() => {
    window.__alerts = [];
    window.Notification = class { constructor(t, o) { window.__alerts.push(t + " | " + ((o && o.body) || "")); }
      static get permission() { return "granted"; } static requestPermission() { return Promise.resolve("granted"); } };
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", m => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });  // 4xx/5xx we trigger on purpose are not bugs

  // 1. First open: it asks for what is missing.
  await page.goto(base);
  await page.waitForSelector("#bizForm input");
  const banner = await page.textContent("#banner");
  ok("first open lands on Setup", await page.isVisible("#tab-setup"));
  ok("banner asks for owner name, WhatsApp and API key", /Owner's name|Agent's name/.test(banner) && /WhatsApp/.test(banner) && /API key/.test(banner), banner);
  await shot(page, "first-open-setup");

  // 2. A wrong phone number is refused with a reason.
  await page.fill("#biz-owner_name", "Mehmet Demo");
  await page.fill("#biz-owner_whatsapp", "123");
  await page.click("#saveBiz");
  await page.waitForFunction(() => document.querySelector("#saveMsg").textContent.length > 0);
  ok("bad WhatsApp number refused", /country code/.test(await page.textContent("#saveMsg")), await page.textContent("#saveMsg"));
  await shot(page, "bad-number");

  // 3. Local-style number is accepted and stored with 90.
  await page.fill("#biz-owner_whatsapp", "0533 123 45 67");
  await page.click("#saveBiz");
  await page.waitForFunction(() => /Saved/.test(document.querySelector("#saveMsg").textContent));
  ok("owner saved, number stored as 905331234567", (await page.inputValue("#biz-owner_whatsapp")) === "905331234567", await page.inputValue("#biz-owner_whatsapp"));
  ok("banner now only asks for the API key", /API key/.test(await page.textContent("#banner")) && !/WhatsApp/.test(await page.textContent("#banner")), await page.textContent("#banner"));

  // 4. API key: junk refused, real-looking key saved, never shown back.
  await page.fill("#keyInput", "hello");
  await page.click("#keySave");
  await page.waitForFunction(() => document.querySelector("#keyMsg").textContent.length > 0);
  ok("non-key text refused", /sk-ant-/.test(await page.textContent("#keyMsg")), await page.textContent("#keyMsg"));
  await page.fill("#keyInput", "sk-ant-test-000000-abcd");
  await page.click("#keySave");
  await page.waitForFunction(() => /Saved \(/.test(document.querySelector("#keyStatus").textContent));
  ok("key saved, only last 4 shown", /…abcd/.test(await page.textContent("#keyStatus")) && !(await page.content()).includes("sk-ant-test-000000"));
  ok("setup banner gone", await page.isHidden("#banner"));
  await page.click("#keyTest");
  await page.waitForFunction(() => /Key works|rejected|Can't/.test(document.querySelector("#keyMsg").textContent));
  ok("Test button answers (stand-in)", /Key works/.test(await page.textContent("#keyMsg")), await page.textContent("#keyMsg"));
  await shot(page, "setup-done");

  // 5. Chat: example question.
  await page.click('nav button[data-tab="chat"]');
  const chips = await page.$$("#chips button");
  ok("4 example questions shown", chips.length === 4, chips.length);
  await chips[0].click();
  await page.waitForSelector(".msg-ai:not(.typing) .bubble");
  ok("customer bubble + answer + cost", (await page.$$(".msg-me")).length === 1 && /\[TEST\]/.test(await page.textContent(".msg-ai:not(.typing) .bubble"))
     && /≈ \$/.test(await page.textContent(".msg-ai .meta")));
  // 6. Customer gives name + phone -> AI saves a lead.
  await page.fill("#input", kind === "car" ? "Ali, 0533 111 22 33, cumartesi 11 uygun" : "Ayşe, 0548 222 33 44, yarın 17:00");
  await page.press("#input", "Enter");
  await page.waitForSelector(".msg-note");
  ok("lead-saved note appears in chat", /Lead saved/.test(await page.textContent(".msg-note")), await page.textContent(".msg-note"));
  await page.waitForFunction(() => document.querySelector("#leadCount").textContent === "1");
  ok("Leads badge shows 1", true);
  // 7. Persian goes right-to-left.
  await chips[3].click();
  await page.waitForFunction(() => document.querySelectorAll(".msg-me").length === 3);
  const dir = await page.$$eval(".msg-me .bubble", bs => getComputedStyle(bs[bs.length - 1]).direction);
  ok("Persian message shown right-to-left", dir === "rtl", dir);
  await page.waitForFunction(() => document.querySelectorAll(".msg-ai:not(.typing)").length === 3);
  // 8. A broken key shows a plain-language error, not a crash.
  await page.fill("#input", "#fail");
  await page.press("#input", "Enter");
  await page.waitForSelector(".msg-err");
  ok("bad key explained in the chat", /rejected the API key/.test(await page.textContent(".msg-err")), await page.textContent(".msg-err"));
  await shot(page, "chat");

  // 9. Leads page.
  await page.click('nav button[data-tab="leads"]');
  await page.waitForSelector("#leads .lead");
  const card = await page.textContent("#leads .lead");
  ok("lead card has name, request, summary", /(Ali|Ayşe)/.test(card) && /Test sürüşü|Test drive/.test(card) && /TEST/.test(card), card);
  ok("fresh lead shows minutes waiting, not red", /dk bekliyor/.test(card) && (await page.$$("#leads .lead.late")).length === 0, card);
  const href = await page.getAttribute("#leads .lead a", "href");
  ok("phone links to WhatsApp with 90", href === (kind === "car" ? "https://wa.me/905331112233" : "https://wa.me/905482223344"), href);
  await page.click("#leads .lead >> text=Show chat");
  await page.waitForSelector("#leads .lead .log:not([hidden]) div");
  ok("Show chat opens the conversation", (await page.$$("#leads .lead .log div")).length >= 4);
  ok("no reminder yet", await page.isHidden("#remind"));
  await shot(page, "leads");

  // 10. A customer who has waited 3 hours -> red reminder everywhere.
  fs.appendFileSync(path.join(copy, "data/leads.jsonl"), leadLine("Veli Bekleyen", 3));
  await page.reload();
  await page.waitForSelector("#leads .lead.late");
  const remind = await page.textContent("#remind");
  ok("red banner names the waiting customer", /1 müşteri/.test(remind) && /Veli Bekleyen/.test(remind), remind);
  ok("tab title shows (1)", (await page.title()).startsWith("(1) "), await page.title());
  ok("late card has the clock tag", /⏰ 3 saat bekliyor/.test(await page.textContent("#leads .lead.late")));
  ok("desktop alert fired once", (await page.evaluate(() => window.__alerts)).length === 1, JSON.stringify(await page.evaluate(() => window.__alerts)));
  await shot(page, "reminder");
  await page.click('nav button[data-tab="chat"]');
  ok("banner also shows on the Chat tab", await page.isVisible("#remind"));

  // 11. Another one goes late while the page is open: picked up within a minute, no reload.
  fs.appendFileSync(path.join(copy, "data/leads.jsonl"), leadLine("Olga Late", 5));
  const t0 = Date.now();
  await page.waitForFunction(() => /2 müşteri/.test(document.querySelector("#remind").textContent), null, { timeout: 75000 });
  ok(`second late customer noticed without reload (${Math.round((Date.now() - t0) / 1000)} s)`, true);
  ok("a new alert for the new one only", (await page.evaluate(() => window.__alerts)).length === 2, JSON.stringify(await page.evaluate(() => window.__alerts)));

  // 12. Marking them done clears the reminder.
  await page.click('nav button[data-tab="leads"]');
  for (const who of ["Veli Bekleyen", "Olga Late"]) {
    await page.click(`#leads .lead:has-text("${who}") >> text=Done`);
    await page.waitForSelector(`#leads .lead.done:has-text("${who}")`);
  }
  ok("reminder gone after both are marked done", await page.isHidden("#remind") && !(await page.title()).startsWith("("), await page.title());
  await shot(page, "after-done");

  // 13. Cost page.
  await page.click('nav button[data-tab="cost"]');
  await page.waitForSelector("#cost .stat");
  const cost = await page.textContent("#cost");
  ok("cost page shows totals and a monthly estimate", /Per chat/.test(cost) && /300 chats a month/.test(cost), cost);
  await shot(page, "cost");

  // 14. Change a price in Setup -> saved and used.
  await page.click('nav button[data-tab="setup"]');
  const codeKey = kind === "car" ? "A12" : "E104";
  const row = page.locator("#items tbody tr", { has: page.locator(`input[data-key="kod"]`) }).filter({ has: page.locator(`input[value="${codeKey}"]`) });
  await page.evaluate(code => {
    const tr = [...document.querySelectorAll("#items tbody tr")].find(r => r.querySelector('[data-key="kod"]').value === code);
    tr.querySelector('[data-key="fiyat"]').value = "39000";
  }, codeKey);
  await page.click("#saveBiz");
  await page.waitForFunction(() => /Saved/.test(document.querySelector("#saveMsg").textContent));
  const saved = JSON.parse(fs.readFileSync(path.join(copy, "business.json"), "utf8")).items.find(i => i.kod === codeKey);
  ok(`price of ${codeKey} saved as 39000`, saved && saved.fiyat === "39000", JSON.stringify(saved));

  // 15. Phone-size screen.
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await phone.goto(base + "/#chat");
  await phone.waitForSelector("#chips button");
  const wide = await phone.evaluate(() => document.documentElement.scrollWidth);
  ok("no sideways scrolling on a phone", wide <= 390, wide);
  await shot(phone, "phone");

  ok("no JavaScript errors", errors.length === 0, errors.join(" | "));
  await browser.close();
  console.log(fails ? `${fails} FAILED` : "ALL PASS");
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error("CRASH", e); process.exit(2); });
