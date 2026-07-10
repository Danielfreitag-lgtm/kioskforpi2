(() => {
  const toastEl = document.getElementById("admin-toast");
  let toastTimer = null;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toastEl.hidden = true), 2600);
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Request failed");
    return data;
  }

  // ---- Tabs --------------------------------------------------------------
  const tabs = document.querySelectorAll(".admin-tab");
  const panels = {
    queue: document.getElementById("panel-queue"),
    menu: document.getElementById("panel-menu"),
    orders: document.getElementById("panel-orders"),
    sms: document.getElementById("panel-sms"),
  };
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      Object.entries(panels).forEach(([key, el]) => (el.hidden = key !== tab.dataset.tab));
      if (tab.dataset.tab === "menu") loadMenu();
      if (tab.dataset.tab === "orders") loadOrders();
      if (tab.dataset.tab === "sms") loadSettings();
    });
  });

  // ---- Queue ---------------------------------------------------------------
  const nowServingDisplay = document.getElementById("admin-now-serving");
  const waitingListEl = document.getElementById("admin-waiting-list");

  async function loadQueue() {
    const data = await api("/api/queue-status");
    nowServingDisplay.textContent = data.now_serving || "--";
    waitingListEl.innerHTML = "";
    if (data.waiting && data.waiting.length) {
      data.waiting.forEach((n) => {
        const chip = document.createElement("span");
        chip.className = "waiting-chip";
        chip.textContent = n;
        waitingListEl.appendChild(chip);
      });
    } else {
      waitingListEl.innerHTML = '<p class="empty-state">No one waiting.</p>';
    }
  }

  document.getElementById("call-next-btn").addEventListener("click", async () => {
    try {
      const data = await api("/api/admin/queue/next", { method: "POST" });
      if (data.sms && data.sms.ok) {
        toast(`Now serving #${data.now_serving} — text sent`);
      } else if (data.sms && !data.sms.ok) {
        toast(`Now serving #${data.now_serving} — text failed: ${data.sms.error}`);
      } else {
        toast("Now serving #" + data.now_serving);
      }
      loadQueue();
    } catch (err) {
      toast(err.message);
    }
  });

  document.getElementById("reset-queue-btn").addEventListener("click", async () => {
    if (!confirm("Reset the entire queue and clear all orders? This can't be undone.")) return;
    await api("/api/admin/queue/reset", { method: "POST" });
    toast("Queue reset.");
    loadQueue();
  });

  // ---- Menu CRUD -----------------------------------------------------------
  const menuForm = document.getElementById("menu-form");
  const emojiInput = document.getElementById("item-emoji");
  const nameInput = document.getElementById("item-name");
  const priceInput = document.getElementById("item-price");
  const menuListEl = document.getElementById("admin-menu-list");

  document.getElementById("emoji-picker").addEventListener("click", (e) => {
    const span = e.target.closest("span[data-emoji]");
    if (!span) return;
    emojiInput.value = span.dataset.emoji;
  });

  menuForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/admin/menu", {
        method: "POST",
        body: JSON.stringify({
          emoji: emojiInput.value.trim() || "🍽️",
          name: nameInput.value.trim(),
          price: parseFloat(priceInput.value),
        }),
      });
      menuForm.reset();
      toast("Item added.");
      loadMenu();
    } catch (err) {
      toast(err.message);
    }
  });

  async function loadMenu() {
    const items = await api("/api/admin/menu");
    menuListEl.innerHTML = "";
    if (!items.length) {
      menuListEl.innerHTML = '<p class="empty-state">No menu items yet — add one above.</p>';
      return;
    }
    items.forEach((item) => {
      const row = document.createElement("div");
      row.className = "admin-menu-row";
      row.innerHTML = `
        <span class="row-emoji">${item.emoji}</span>
        <span class="row-name">${item.name}</span>
        <span class="row-price">$${item.price.toFixed(2)}</span>
        <button class="icon-btn" title="Edit" data-action="edit" data-id="${item.id}">✎</button>
        <button class="icon-btn danger" title="Delete" data-action="delete" data-id="${item.id}">✕</button>
      `;
      menuListEl.appendChild(row);
    });
  }

  menuListEl.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const id = btn.dataset.id;

    if (btn.dataset.action === "delete") {
      if (!confirm("Remove this item from the menu?")) return;
      await api(`/api/admin/menu/${id}`, { method: "DELETE" });
      toast("Item removed.");
      loadMenu();
    }

    if (btn.dataset.action === "edit") {
      const row = btn.closest(".admin-menu-row");
      const currentName = row.querySelector(".row-name").textContent;
      const currentPrice = row.querySelector(".row-price").textContent.replace("$", "");
      const currentEmoji = row.querySelector(".row-emoji").textContent;

      const newEmoji = prompt("Emoji:", currentEmoji) ?? currentEmoji;
      const newName = prompt("Name:", currentName) ?? currentName;
      const newPrice = prompt("Price:", currentPrice) ?? currentPrice;

      try {
        await api(`/api/admin/menu/${id}`, {
          method: "PUT",
          body: JSON.stringify({ emoji: newEmoji, name: newName, price: parseFloat(newPrice) }),
        });
        toast("Item updated.");
        loadMenu();
      } catch (err) {
        toast(err.message);
      }
    }
  });

  // ---- Orders ---------------------------------------------------------------
  const ordersTableEl = document.getElementById("orders-table");

  async function loadOrders() {
    const orders = await api("/api/admin/orders");
    ordersTableEl.innerHTML = "";
    if (!orders.length) {
      ordersTableEl.innerHTML = '<p class="empty-state">No orders yet.</p>';
      return;
    }
    orders.forEach((o) => {
      const itemsSummary = o.items.map((i) => `${i.qty}x ${i.emoji}`).join(" ");
      const readyStatus = o.ready_sms_status || "not_sent";
      const ownerStatus = o.owner_sms_status || "not_sent";
      const row = document.createElement("div");
      row.className = "order-row";
      row.innerHTML = `
        <span class="order-num">#${o.queue_number}</span>
        <span class="order-items">${itemsSummary}</span>
        <span class="order-total">$${o.total.toFixed(2)}</span>
        <span class="order-status ${o.status}">${o.status}</span>
        <span class="sms-badge sms-badge-cell ${ownerStatus}" title="Owner notification when order was placed">owner: ${ownerStatus.replace("_", " ")}</span>
        <span class="sms-badge sms-badge-cell ${readyStatus}" title="Text sent when number was called">ready: ${readyStatus.replace("_", " ")}</span>
      `;
      ordersTableEl.appendChild(row);
    });
  }

  // ---- SMS settings -----------------------------------------------------------
  const smsForm = document.getElementById("sms-form");
  const businessNameInput = document.getElementById("setting-business-name");
  const sidInput = document.getElementById("setting-sid");
  const tokenInput = document.getElementById("setting-token");
  const fromInput = document.getElementById("setting-from");
  const enabledInput = document.getElementById("setting-enabled");
  const tokenHint = document.getElementById("token-hint");

  async function loadSettings() {
    const data = await api("/api/admin/settings");
    businessNameInput.value = data.business_name || "";
    sidInput.value = data.twilio_account_sid || "";
    fromInput.value = data.twilio_from_number || "";
    enabledInput.checked = data.sms_enabled === "1";
    tokenInput.value = "";
    tokenHint.textContent = data.twilio_auth_token_set
      ? "A token is already saved. Leave blank to keep it."
      : "No token saved yet.";
  }

  smsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/admin/settings", {
        method: "POST",
        body: JSON.stringify({
          business_name: businessNameInput.value,
          twilio_account_sid: sidInput.value,
          twilio_auth_token: tokenInput.value,
          twilio_from_number: fromInput.value,
          sms_enabled: enabledInput.checked,
        }),
      });
      toast("Settings saved.");
      loadSettings();
    } catch (err) {
      toast(err.message);
    }
  });

  document.getElementById("send-test-btn").addEventListener("click", async () => {
    const phone = document.getElementById("test-phone").value.trim();
    const resultEl = document.getElementById("sms-test-result");
    if (!phone) {
      resultEl.textContent = "Enter a phone number first.";
      return;
    }
    resultEl.textContent = "Sending…";
    try {
      const data = await api("/api/admin/sms-test", {
        method: "POST",
        body: JSON.stringify({ phone }),
      });
      resultEl.textContent = data.ok ? "Test sent! Check the phone." : "Failed: " + data.error;
    } catch (err) {
      resultEl.textContent = "Failed: " + err.message;
    }
  });

  // ---- Init ------------------------------------------------------------------
  loadQueue();
  setInterval(loadQueue, 5000);
})();