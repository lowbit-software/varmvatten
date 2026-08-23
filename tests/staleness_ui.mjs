// The page must never present a reading it cannot stand behind.
//
// This exists because of 2026-08-22, when the poller wedged and the card showed
// a confident "hot" at 58° for 31 hours while the water was 29°. The card IS the
// interface — there is no text to read past it — so "stale" has to be visible in
// the picture, not only in the JSON.
//
// No browser and no dependencies: the logic under test is class toggling and
// badge text, so a stub DOM reaches all of it and runs anywhere node does.
//
//   node tests/staleness_ui.mjs

import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..");

function el() {
  const set = new Set();
  return {
    classList: {
      toggle: (c, on) => (on ? set.add(c) : set.delete(c)),
      contains: (c) => set.has(c),
    },
    _classes: set,
    textContent: "",
    src: "",
    addEventListener() {},
  };
}

/** A page as it ships: asserting nothing, greyed, badge open. */
function freshPage() {
  const body = el();
  for (const c of ["hot", "stale"]) body.classList.toggle(c, true);
  const badge = el();
  badge.classList.toggle("pinned", true);
  badge.textContent = "Hämtar…";
  return { body, badge, card: el(), symbol: el() };
}

/** Run one `fetchTemp()` against `page`, with the API answering `payload`. */
async function render(payload, page = freshPage()) {
  const { body, badge, card, symbol } = page;
  const ctx = {
    document: { body, getElementById: (id) => ({ symbol, card, badge }[id]) },
    window: { ASSET_V: "1" },
    fetch: async () => ({ json: async () => payload }),
    setInterval() {},
    setTimeout() {},
    clearTimeout() {},
  };
  const src = readFileSync(join(REPO, "static", "app.js"), "utf8");
  const fn = new Function(...Object.keys(ctx), `${src}\nreturn fetchTemp();`);
  await fn(...Object.values(ctx));
  return page;
}

let fails = 0;
const check = (label, ok, detail = "") => {
  if (!ok) fails++;
  console.log(`  ${ok ? "ok  " : "FAIL"} ${label}${detail ? " — " + detail : ""}`);
};

const now = () => new Date().toISOString();

console.log("staleness in the page:");

// No reading yet — first boot, or a watchdog restart while myUplink is down.
// The page ships as `hot stale`, so getting this wrong shows a confident red
// card for as long as the outage lasts.
{
  const { body, badge } = await render({ temp: null, unit: "°C", updated_at: null, age_seconds: null, stale: true });
  check("no reading yet keeps the card stale", body.classList.contains("stale"));
  check("…and says so plainly", badge.textContent === "Ingen kontakt", badge.textContent);
  check("…and pins the badge open", badge.classList.contains("pinned"));
}

// A known but old reading — the shape of the 2026-08-22 outage, 31h stale.
{
  const { body, badge } = await render({
    temp: 58, unit: "°C", updated_at: "2026-08-22T12:26:15Z", age_seconds: 111600, stale: true,
  });
  check("a day-old reading is stale", body.classList.contains("stale"));
  check("…does not quote a temperature", !badge.textContent.includes("58"), badge.textContent);
  check(
    "…and dates it by weekday, so it cannot read as this afternoon",
    /\b(mån|tis|ons|tors|fre|lör|sön)/i.test(badge.textContent),
    badge.textContent,
  );
}

// A fresh reading clears all of it and picks the right card.
{
  const { body, badge } = await render({ temp: 29, unit: "°C", updated_at: now(), age_seconds: 12, stale: false });
  check("a fresh reading clears stale", !body.classList.contains("stale"));
  check("…unpins the badge", !badge.classList.contains("pinned"));
  check("…reports the temperature", badge.textContent.startsWith("29.0°C"), badge.textContent);
  check("…and picks the matching card", body.classList.contains("cold") && !body.classList.contains("hot"),
    [...body._classes].join(" "));
}

// The transition the cases above cannot reach. They all start from the shipped
// state, which is already stale — so a bug that only drops the way *back* to
// stale passes every one of them. This is the sequence that actually happens.
{
  const page = freshPage();
  await render({ temp: 29, unit: "°C", updated_at: now(), age_seconds: 5, stale: false }, page);
  check("(setup) the page is showing a fresh reading", !page.body.classList.contains("stale"));

  await render({ temp: null, unit: "°C", updated_at: null, age_seconds: null, stale: true }, page);
  check("losing contact takes a live page back to stale", page.body.classList.contains("stale"));
  check("…re-pins the badge", page.badge.classList.contains("pinned"));
  check("…and stops quoting the last known temperature",
    page.badge.textContent === "Ingen kontakt", page.badge.textContent);
}

console.log(fails ? `\n${fails} check(s) failed` : "\nall checks passed");
process.exit(fails ? 1 : 0);
