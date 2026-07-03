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

  const category = categoryFor(data.temp);
  if (category !== currentCategory) {
    currentCategory = category;
    card.src = `/static/${category}.png?v=${window.ASSET_V}`;
    for (const c of ["cold", "medium", "hot"]) {
      document.body.classList.toggle(c, c === category);
    }
  }

  const updated = data.updated_at
    ? new Date(data.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "";
  badge.textContent = `${data.temp.toFixed(1)}${data.unit} · uppdaterad ${updated}`;
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
