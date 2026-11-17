/* Small progressive enhancements. No dependencies. */

// Open / close confirmation dialogs.
document.querySelectorAll("[data-open-dialog]").forEach((btn) => {
  btn.addEventListener("click", () => document.getElementById(btn.dataset.openDialog)?.showModal());
});
document.querySelectorAll("[data-close-dialog]").forEach((btn) => {
  btn.addEventListener("click", () => btn.closest("dialog").close());
});

// Ask before a destructive button submits.
document.querySelectorAll("[data-confirm]").forEach((btn) => {
  btn.addEventListener("click", (e) => { if (!confirm(btn.dataset.confirm)) e.preventDefault(); });
});

// Submit the surrounding form as soon as a filter changes.
document.querySelectorAll("[data-autosubmit]").forEach((el) => {
  el.addEventListener("change", () => el.form.submit());
});

// Collection switcher: navigate when a collection is chosen.
document.querySelectorAll("[data-nav]").forEach((el) => {
  el.addEventListener("change", () => { window.location.href = el.value; });
});

// Add card: live preview of the chosen photo, and "Take photo" opens the phone camera.
const photoInput = document.getElementById("photo");
const previewImg = document.getElementById("preview-img");
if (photoInput && previewImg) {
  photoInput.addEventListener("change", () => {
    const file = photoInput.files[0];
    if (!file) return;
    previewImg.src = URL.createObjectURL(file);
    previewImg.hidden = false;
  });
}
const cameraBtn = document.getElementById("camera-btn");
const cameraInput = document.getElementById("camera");
if (cameraBtn && cameraInput && photoInput) {
  cameraBtn.addEventListener("click", () => cameraInput.click());
  cameraInput.addEventListener("change", () => {
    if (!cameraInput.files.length) return;
    photoInput.files = cameraInput.files;
    photoInput.dispatchEvent(new Event("change"));
  });
}

// Confirm screen: the variant list follows the chosen card; the artwork follows the choice.
const variantSelect = document.getElementById("variant");
const candidateRadios = document.querySelectorAll("input[name=card_id][data-variants]");
if (variantSelect && candidateRadios.length) {
  const wanted = variantSelect.dataset.default;
  const fillVariants = (radio, keep) => {
    const list = JSON.parse(radio.dataset.variants);
    const current = keep ? variantSelect.value : wanted;
    variantSelect.replaceChildren(...list.map((v) => new Option(v, v, false, v === current)));
    if (!list.includes(current)) variantSelect.selectedIndex = 0;
  };
  const art = document.getElementById("cand-art");
  candidateRadios.forEach((radio) => radio.addEventListener("change", () => {
    fillVariants(radio, true);
    if (art) art.src = radio.dataset.art;
  }));
  fillVariants(document.querySelector("input[name=card_id]:checked") || candidateRadios[0], false);
}

// Gallery view: thumbnail size slider + show/hide details (remembered per browser).
const sizeInput = document.getElementById("tile-size");
const detailsInput = document.getElementById("show-details");
const store = {
  get(key) { try { return localStorage.getItem(key); } catch (e) { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* storage blocked */ } },
};
if (sizeInput) {
  const saved = store.get("pokemon.tileSize");
  if (saved) sizeInput.value = saved;
  sizeInput.addEventListener("input", () => {
    document.documentElement.style.setProperty("--tile-min", sizeInput.value + "px");
    store.set("pokemon.tileSize", sizeInput.value);
  });
}
if (detailsInput) {
  detailsInput.checked = store.get("pokemon.showDetails") !== "0";
  detailsInput.addEventListener("change", () => {
    document.documentElement.classList.toggle("hide-details", !detailsInput.checked);
    store.set("pokemon.showDetails", detailsInput.checked ? "1" : "0");
  });
}

// Expansion download: poll progress, reload when finished.
const syncBanner = document.querySelector("[data-sync-url]");
if (syncBanner) {
  const text = syncBanner.querySelector("[data-sync-text]");
  const bar = syncBanner.querySelector("[data-sync-bar]");
  const poll = async () => {
    try {
      const data = await (await fetch(syncBanner.dataset.syncUrl, { headers: { Accept: "application/json" } })).json();
      if (data.status !== "running") { window.location.reload(); return; }
      const pct = data.total ? Math.round((data.done * 100) / data.total) : 0;
      text.textContent = data.total ? `${data.done} of ${data.total} (${pct}%)` : "starting";
      bar.style.width = pct + "%";
    } catch (e) { /* keep trying */ }
    setTimeout(poll, 2000);
  };
  poll();
}

// Expansion view: click a grey card to add one copy (default finish, Near Mint) without leaving the page.
const toast = (message, isError) => {
  let box = document.getElementById("toast");
  if (!box) {
    box = document.createElement("div");
    box.id = "toast";
    box.setAttribute("role", "status");
    document.body.append(box);
  }
  box.textContent = message;
  box.className = "toast" + (isError ? " toast-error" : "");
  clearTimeout(box.timer);
  box.timer = setTimeout(() => { box.className = "toast toast-hidden"; }, 3500);
};
const csrfToken = () => document.querySelector('meta[name="csrf-token"]')?.content || "";
const postJson = async (url) => {
  const resp = await fetch(url, { method: "POST", headers: { "X-CSRFToken": csrfToken(), "X-Requested-With": "fetch", Accept: "application/json" } });
  if (!resp.ok) throw new Error(resp.status === 403 ? "You can't change this collection." : "That didn't work. Try again.");
  return resp.json();
};

// Progress bar on the expansion page: one more collected item (a card, or a finish in master-set mode).
const bumpProgress = () => {
  const panel = document.querySelector("[data-collected]");
  if (!panel) return;
  const have = Number(panel.dataset.have) + 1, total = Number(panel.dataset.total);
  const pct = (total ? Math.round((have * 100) / total) : 0) + "%";
  panel.dataset.have = have;
  panel.querySelector("[data-have-text]").textContent = have;
  panel.querySelector("[data-pct-text]").textContent = pct;
  const bar = panel.querySelector(".progress > span");
  if (bar) bar.style.width = pct;
};
const isMaster = () => document.querySelector("[data-collected]")?.dataset.master === "1";

// Finish pills: N / H / R ... A filled pill is owned. For editors a dashed one adds that finish.
const FINISH_SHORT = { "Normal": "N", "Holofoil": "H", "Reverse Holofoil": "R", "1st Edition": "1st", "1st Edition Holofoil": "1H", "Promo": "P" };
const FINISH_ORDER = Object.keys(FINISH_SHORT);
const finishRank = (v) => (FINISH_ORDER.includes(v) ? FINISH_ORDER.indexOf(v) : FINISH_ORDER.length);
const quickAddUrl = (base, variant) => base + (variant ? "?variant=" + encodeURIComponent(variant) : "");
const setCheck = (tile, total) => {
  let badge = tile.querySelector(".check-badge");
  if (!badge) {
    badge = document.createElement("span");
    badge.className = "check-badge";
    tile.querySelector(".card-frame")?.append(badge);
  }
  badge.textContent = total > 1 ? total : "\u2713";
};

// "+" on a card you don't have: add one copy (default finish, Near Mint) without leaving the page.
document.querySelectorAll("[data-quick-add]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const base = btn.dataset.quickAdd;
      const data = await postJson(base);
      const tile = btn.closest(".tile");
      tile.classList.replace("missing", "owned");
      tile.querySelectorAll(".tile-btn").forEach((b) => b.remove());       // it's owned now: nothing left to press
      setCheck(tile, data.total);
      const strip = document.createElement("span");
      strip.className = "finish-strip";
      [...new Set([...(data.variants || []), data.variant])].sort((a, b) => finishRank(a) - finishRank(b)).forEach((v) => {
        const own = v === data.variant, f = document.createElement(own ? "span" : "button");
        f.className = "finish " + (own ? "has" : "lacks add");
        f.textContent = FINISH_SHORT[v] || v[0];
        if (own) f.title = v + ": you own this finish";
        else {
          f.type = "button";
          f.dataset.addFinish = base;
          f.dataset.variant = v;
          f.title = v + ": not owned yet. Click to add a Near Mint copy.";
          f.setAttribute("aria-label", "Add a " + v + " copy");
        }
        strip.append(f);
      });
      tile.querySelector(".tile-art")?.append(strip);
      bumpProgress();
      toast(`Added ${data.name} (${data.variant}, ${data.condition}). You now have ${data.total}.`);
    } catch (err) {
      btn.disabled = false;
      toast(err.message, true);
    }
  });
});

// A dashed finish pill on a card you own: add a Near Mint copy of that finish.
document.addEventListener("click", async (e) => {
  const pill = e.target.closest("[data-add-finish]");
  if (!pill || pill.disabled) return;
  pill.disabled = true;
  try {
    const data = await postJson(quickAddUrl(pill.dataset.addFinish, pill.dataset.variant));
    pill.classList.replace("lacks", "has");
    pill.classList.remove("add");
    pill.removeAttribute("data-add-finish");
    pill.title = data.variant + ": you own this finish";
    pill.removeAttribute("aria-label");
    setCheck(pill.closest(".tile"), data.total);
    if (isMaster()) bumpProgress();                    // the card was already counted; only master sets count the finish
    toast(`Added ${data.name} (${data.variant}, ${data.condition}). You now have ${data.total}.`);
  } catch (err) {
    pill.disabled = false;
    toast(err.message, true);
  }
});

// Star on a card you don't have: add it to (or take it off) the wanted list.
document.querySelectorAll("[data-want]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const data = await postJson(btn.dataset.want);
      btn.classList.toggle("on", data.wanted);
      btn.setAttribute("aria-pressed", data.wanted ? "true" : "false");
      btn.textContent = data.wanted ? "\u2605" : "\u2606";
      btn.title = data.wanted ? "On your wanted list. Click to remove." : "Add to your wanted list";
      toast(data.wanted ? `${data.name} added to your wanted list.` : `${data.name} removed from your wanted list.`);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });
});

// Card page: left / right arrow keys go to the previous / next card in the expansion.
document.addEventListener("keydown", (e) => {
  if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "")) return;
  const link = document.querySelector(e.key === "ArrowLeft" ? '.card-nav a[rel="prev"]' : e.key === "ArrowRight" ? '.card-nav a[rel="next"]' : "");
  if (link) location.href = link.href;
});

// Card page on a touch screen: swipe left for the next card, right for the previous one.
(() => {
  const prev = document.querySelector('.card-nav a[rel="prev"]'), next = document.querySelector('.card-nav a[rel="next"]');
  if (!prev && !next) return;
  let x0 = 0, y0 = 0, ok = false;
  document.addEventListener("touchstart", (e) => {
    const t = e.touches[0];
    x0 = t.clientX; y0 = t.clientY;
    ok = e.touches.length === 1 && !e.target.closest("input, select, textarea, button, details[open], [data-theme-menu], .menu-body");
  }, { passive: true });
  document.addEventListener("touchend", (e) => {
    if (!ok) return;
    const t = e.changedTouches[0], dx = t.clientX - x0, dy = t.clientY - y0;
    if (Math.abs(dx) < 70 || Math.abs(dy) > Math.abs(dx) * 0.6) return;           // a clear sideways flick only
    const link = dx < 0 ? next : prev;
    if (link) location.href = link.href;
  }, { passive: true });
})();

// Click-to-select for read-only link boxes.
document.querySelectorAll("[data-select-all]").forEach((input) => input.addEventListener("focus", () => input.select()));
