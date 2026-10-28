// Prices an estimate with the real AWS Pricing Calculator and optionally saves it as a public link.
//
// Input:  { estimate: <calculator estimate JSON, costs may be 0/missing>, save: boolean }
// Output: { services: { key: { name, monthly, upfront, configSummary } }, total: {monthly, upfront},
//           url?: string, error?: string }
//
// How: open calculator.aws in headless Chromium, put the estimate into the session storage key the
// calculator uses for the working estimate, open #/estimate, wait until the calculator has recalculated
// every service, read the result back, then POST it to the calculator's own save endpoint.
import puppeteer from "puppeteer-core";

const CALC = "https://calculator.aws/";
const KEY = "awspc-root-estimate-v1";
const SAVE_URL = "https://dnd5zrqcec4or.cloudfront.net/Prod/v2/saveAs";
const LINK = "https://calculator.aws/#/estimate?id=";

async function launch() {
  if (process.env.CHROME_PATH) {
    return puppeteer.launch({ executablePath: process.env.CHROME_PATH, headless: true, args: ["--no-sandbox"] });
  }
  const chromium = (await import("@sparticuz/chromium")).default;
  return puppeteer.launch({
    args: chromium.args, executablePath: await chromium.executablePath(), headless: "shell",
    defaultViewport: { width: 1280, height: 900 },
  });
}

function strip(estimate) {
  // Zero the costs so we can tell when the calculator has recalculated them.
  const e = JSON.parse(JSON.stringify(estimate));
  for (const s of Object.values(e.services || {})) {
    s.serviceCost = { monthly: 0, upfront: 0 };
    delete s.oldServiceCost; delete s.oldConfigSummary;
    for (const x of s.subServices || []) { x.serviceCost = { monthly: 0, upfront: 0 }; delete x.oldServiceCost; }
  }
  e.totalCost = { monthly: 0, upfront: 0 };
  e.groupSubtotal = { monthly: 0, upfront: 0 };
  return e;
}

const TABS = 4;            // parallel calculator tabs (each tab has its own session storage)
const PER_TAB_MIN = 3;     // don't split small estimates

// Split the services across tabs, price each part, and merge. Saved estimates store the computed costs,
// so the merged estimate is equivalent to one priced in a single tab.
async function price(estimate, timeoutMs) {
  const keys = Object.keys(estimate.services || {});
  const tabs = Math.max(1, Math.min(TABS, Math.floor(keys.length / PER_TAB_MIN)));
  const parts = Array.from({ length: tabs }, (_, i) => keys.filter((_, j) => j % tabs === i));
  const browser = await launch();
  try {
    const priced = await Promise.all(parts.map((ks) => priceInTab(browser, {
      ...estimate, services: Object.fromEntries(ks.map((k) => [k, estimate.services[k]])),
    }, timeoutMs)));
    const merged = { ...priced[0], services: {} };
    for (const p of priced) Object.assign(merged.services, p.services);
    const total = { monthly: 0, upfront: 0 };
    for (const s of Object.values(merged.services)) {
      total.monthly += s.serviceCost?.monthly || 0;
      total.upfront += s.serviceCost?.upfront || 0;
    }
    total.monthly = Math.round(total.monthly * 100) / 100;
    total.upfront = Math.round(total.upfront * 100) / 100;
    merged.totalCost = total;
    merged.groupSubtotal = { ...total };
    merged.name = estimate.name;
    return merged;
  } finally {
    await browser.close();
  }
}

async function priceInTab(browser, estimate, timeoutMs) {
  {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    // Skip analytics, chat and media: faster and keeps the session quiet.
    page.on("request", (r) => {
      const u = r.url();
      if (/shortbread|omtrdc|demdex|everesttech|panorama|d2c\.aws|mrc-sunrise|awsstatic\.com\/s_code|\.(png|jpg|gif|svg|woff2?|mp3)(\?|$)/.test(u)) r.abort();
      else r.continue();
    });
    await page.goto(CALC, { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.evaluate((k, v) => sessionStorage.setItem(k, v), KEY, JSON.stringify({ estimate: strip(estimate) }));
    await page.evaluate(() => { location.hash = "#/estimate"; });

    const n = Object.keys(estimate.services || {}).length;
    const deadline = Date.now() + timeoutMs;
    let last = "", stableSince = 0, snap = null;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 750));
      snap = await page.evaluate((k) => {
        const rows = [...document.querySelectorAll("tr")].filter((r) => /USD/.test(r.innerText)).length;
        const raw = sessionStorage.getItem(k) || "";
        // The calculator writes each service's configSummary when it prices it (we send them empty).
        let summarized = 0;
        try { summarized = Object.values(JSON.parse(raw).estimate.services || {}).filter((s) => s.configSummary).length; } catch {}
        return { rows, summarized, raw };
      }, KEY);
      if (snap.raw === last) {
        if (!stableSince) stableSince = Date.now();
        const quiet = Date.now() - stableSince;
        // Done when every service is priced and nothing changed for 2.5 s. The table can paginate and free
        // services may never show a summary, so also accept 10 s with no change once pricing has started.
        if ((snap.rows >= n || snap.summarized >= n) && quiet > 2500) break;
        if ((snap.rows > 0 || snap.summarized > 0) && quiet > 10000) break;
      } else {
        stableSince = 0;
        last = snap.raw;
      }
    }
    const priced = JSON.parse(snap.raw || "{}").estimate;
    if (!priced) throw new Error("The calculator did not load the estimate.");
    await page.close();
    return priced;
  }
}

async function save(estimate) {
  const e = JSON.parse(JSON.stringify(estimate));
  for (const s of Object.values(e.services || {})) { delete s.oldServiceCost; delete s.oldConfigSummary; }
  const r = await fetch(SAVE_URL, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(e) });
  const resp = await r.json();
  const body = typeof resp.body === "string" ? JSON.parse(resp.body) : resp;
  if (!body.savedKey) throw new Error("The calculator did not return a link.");
  return LINK + body.savedKey;
}

export async function handler(event) {
  try {
    const estimate = event.estimate;
    if (!estimate || !estimate.services) throw new Error("No estimate given.");
    const priced = await price(estimate, Math.min(event.timeoutMs || 60000, 280000));
    const services = {};
    for (const [k, s] of Object.entries(priced.services || {})) {
      services[k] = { name: s.serviceName, monthly: s.serviceCost?.monthly ?? 0, upfront: s.serviceCost?.upfront ?? 0,
                      configSummary: s.configSummary || "" };
    }
    const out = { services, total: priced.totalCost || { monthly: 0, upfront: 0 } };
    if (event.save) {
      if (event.name) priced.name = event.name;
      out.url = await save(priced);
    }
    return out;
  } catch (err) {
    return { error: String(err && err.message || err) };
  }
}
