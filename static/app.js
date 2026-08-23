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
    return;
  }
  if (data.temp === null || data.temp === undefined) return;

  // A stale reading must stop *looking* like an answer. The card is the whole
  // interface — on 2026-08-22 the poller wedged and it showed a confident "hot"
  // for 31 hours while the water was actually 29°, because the only hint that
  // anything was wrong was a timestamp hidden behind a 600ms press-and-hold.
  // So staleness greys the card out and forces the badge into view: unmissable
  // at a glance, and it says nothing about the temperature it no longer knows.
  document.body.classList.toggle("stale", !!data.stale);
  badge.classList.toggle("pinned", !!data.stale);

  const category = categoryFor(data.temp);
  if (category !== currentCategory) {
    currentCategory = category;
    card.src = `/static/${category}.png?v=${window.ASSET_V}`;
    for (const c of ["cold", "medium", "hot"]) {
      document.body.classList.toggle(c, c === category);
    }
  }

  const updated = data.updated_at
    ? new Date(data.updated_at).toLocaleString("sv-SE", {
        hour: "2-digit",
        minute: "2-digit",
        ...(agoDays(data) >= 1 ? { weekday: "short" } : {}),
      })
    : "";
  badge.textContent = data.stale
    ? `Ingen kontakt · senast ${updated}`
    : `${data.temp.toFixed(1)}${data.unit} · uppdaterad ${updated}`;
}

/** Whole days since the reading, so a day-old one shows its weekday too. */
function agoDays(data) {
  return typeof data.age_seconds === "number" ? data.age_seconds / 86400 : 0;
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
