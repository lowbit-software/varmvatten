const symbol = document.getElementById("symbol");
const card = document.getElementById("card");
const badge = document.getElementById("badge");

let currentCategory = null;

function categoryFor(temp) {
  if (temp < 30) return "cold";
  if (temp <= 45) return "medium";
  return "hot";
}

async function fetchTemp() {
  let data;
  try {
    const res = await fetch("/api/temperature");
    data = await res.json();
  } catch {
    // Could not reach our own server — a page-side blip, not a statement about
    // the water. Keep the last frame rather than inventing a new one.
    return;
  }

  // A reading we cannot stand behind must stop *looking* like an answer. The
  // card is the whole interface — on 2026-08-22 the poller wedged and it showed
  // a confident "hot" for 31 hours while the water was actually 29°, because the
  // only hint was a timestamp hidden behind a 600ms press-and-hold.
  //
  // "No reading at all" counts, and is handled BEFORE anything else: the page
  // ships as `body class="hot stale"`, so a restart before the first poll — or
  // one during a myUplink outage, which is when the watchdog restarts us — shows
  // a dead grey card instead of a confident red one. Never return early past
  // this: that is precisely how a blank state renders as "hot".
  const known = data.temp !== null && data.temp !== undefined;
  const stale = !known || !!data.stale;
  document.body.classList.toggle("stale", stale);
  badge.classList.toggle("pinned", stale);

  if (known) {
    const category = categoryFor(data.temp);
    if (category !== currentCategory) {
      currentCategory = category;
      card.src = `/static/${category}.png?v=${window.ASSET_V}`;
      for (const c of ["cold", "medium", "hot"]) {
        document.body.classList.toggle(c, c === category);
      }
    }
  }

  // A day-old reading gets its weekday, so "senast 14:26" cannot be misread as
  // this afternoon — which is exactly how the outage stayed invisible.
  const olderThanADay = (data.age_seconds ?? 0) >= 86400;
  const updated = data.updated_at
    ? new Date(data.updated_at).toLocaleString("sv-SE", {
        hour: "2-digit",
        minute: "2-digit",
        ...(olderThanADay ? { weekday: "short" } : {}),
      })
    : "";

  if (!known) {
    badge.textContent = "Ingen kontakt";
  } else if (stale) {
    badge.textContent = `Ingen kontakt · senast ${updated}`;
  } else {
    badge.textContent = `${data.temp.toFixed(1)}${data.unit} · uppdaterad ${updated}`;
  }
}

fetchTemp();
setInterval(fetchTemp, 30000);

// Hidden "easter-egg" reveal: press and HOLD the card for ~600ms to show the
// exact temperature; release to hide it. Deliberately not tied to tap/hover
// so it never triggers accidentally and always dismisses itself on release.
// Pointer Events unify mouse, trackpad and touch.
const HOLD_MS = 600;
let holdTimer = null;

function endHold() {
  clearTimeout(holdTimer);
  badge.classList.remove("visible");
}

symbol.addEventListener("pointerdown", () => {
  holdTimer = setTimeout(() => badge.classList.add("visible"), HOLD_MS);
});
symbol.addEventListener("pointerup", endHold);
symbol.addEventListener("pointerleave", endHold);
symbol.addEventListener("pointercancel", endHold);

// Suppress iOS Safari's long-press "Save Image" / callout menu on the card.
symbol.addEventListener("contextmenu", (e) => e.preventDefault());
