(() => {
  const cart = new Map(); // id -> { id, name, price, emoji, qty }

  const cartLinesEl = document.getElementById("cart-lines");
  const cartEmptyEl = document.getElementById("cart-empty");
  const cartCountEl = document.getElementById("cart-count");
  const cartTotalEl = document.getElementById("cart-total");
  const placeOrderBtn = document.getElementById("place-order-btn");
  const phoneInput = document.getElementById("cart-phone");

  const overlay = document.getElementById("confirm-overlay");
  const confirmNumber = document.getElementById("confirm-number");
  const confirmSms = document.getElementById("confirm-sms");
  const confirmClose = document.getElementById("confirm-close-btn");

  function fmt(n) {
    return "$" + n.toFixed(2);
  }

  function render() {
    cartLinesEl.innerHTML = "";
    let count = 0;
    let total = 0;

    if (cart.size === 0) {
      cartEmptyEl.style.display = "block";
    } else {
      cartEmptyEl.style.display = "none";
      cart.forEach((line) => {
        count += line.qty;
        total += line.qty * line.price;

        const row = document.createElement("div");
        row.className = "cart-line";
        row.innerHTML = `
          <span class="cart-line-emoji">${line.emoji}</span>
          <span class="cart-line-info">
            <span class="cart-line-name">${line.name}</span><br>
            <span class="cart-line-price">${fmt(line.price)} each</span>
          </span>
          <span class="cart-line-qty">
            <button class="qty-btn" data-action="dec" data-id="${line.id}">−</button>
            ${line.qty}
            <button class="qty-btn" data-action="inc" data-id="${line.id}">+</button>
          </span>
        `;
        cartLinesEl.appendChild(row);
      });
    }

    cartCountEl.textContent = count;
    cartTotalEl.textContent = fmt(total);
    placeOrderBtn.disabled = count === 0;

    document.querySelectorAll(".menu-card").forEach((card) => {
      card.classList.toggle("in-cart", cart.has(card.dataset.id));
    });
  }

  document.getElementById("menu-grid").addEventListener("click", (e) => {
    const card = e.target.closest(".menu-card");
    if (!card) return;
    const id = card.dataset.id;
    const existing = cart.get(id);
    if (existing) {
      existing.qty += 1;
    } else {
      cart.set(id, {
        id,
        name: card.dataset.name,
        price: parseFloat(card.dataset.price),
        emoji: card.dataset.emoji,
        qty: 1,
      });
    }
    render();
  });

  cartLinesEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".qty-btn");
    if (!btn) return;
    const id = btn.dataset.id;
    const line = cart.get(id);
    if (!line) return;
    if (btn.dataset.action === "inc") {
      line.qty += 1;
    } else {
      line.qty -= 1;
      if (line.qty <= 0) cart.delete(id);
    }
    render();
  });

  placeOrderBtn.addEventListener("click", async () => {
    if (cart.size === 0) return;
    placeOrderBtn.disabled = true;
    placeOrderBtn.textContent = "Placing order…";

    const items = Array.from(cart.values()).map((l) => ({ id: l.id, qty: l.qty }));
    const phone = phoneInput.value.trim();

    try {
      const res = await fetch("/api/order", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items, phone }),
      });
      const data = await res.json();

      if (!res.ok) {
        alert(data.error || "Something went wrong placing the order.");
        placeOrderBtn.disabled = false;
        placeOrderBtn.textContent = "Place order";
        return;
      }

      confirmNumber.textContent = data.queue_number;
      if (phone) {
        if (data.sms && data.sms.ok) {
          confirmSms.textContent = "Receipt texted to " + phone;
        } else if (data.sms) {
          confirmSms.textContent = "Order placed — text receipt couldn't be sent.";
        } else {
          confirmSms.textContent = "";
        }
      } else {
        confirmSms.textContent = "";
      }
      overlay.hidden = false;

      cart.clear();
      phoneInput.value = "";
      render();
    } catch (err) {
      alert("Network error placing order. Please try again.");
    }
    placeOrderBtn.disabled = false;
    placeOrderBtn.textContent = "Place order";
  });

  confirmClose.addEventListener("click", () => {
    overlay.hidden = true;
  });

  render();
})();
