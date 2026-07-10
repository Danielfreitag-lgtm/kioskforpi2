(() => {
  const nowServingEl = document.getElementById("now-serving");
  const upNextEl = document.getElementById("upnext-numbers");
  let lastServing = null;

  async function poll() {
    try {
      const res = await fetch("/api/queue-status");
      const data = await res.json();

      if (data.now_serving !== lastServing) {
        nowServingEl.textContent = data.now_serving || "--";
        lastServing = data.now_serving;
      }

      upNextEl.innerHTML = "";
      if (data.waiting && data.waiting.length) {
        data.waiting.forEach((n) => {
          const span = document.createElement("span");
          span.textContent = n;
          upNextEl.appendChild(span);
        });
      } else {
        const span = document.createElement("span");
        span.className = "queue-dim";
        span.textContent = "—";
        upNextEl.appendChild(span);
      }
    } catch (err) {
      // Silently retry on next poll — a display screen shouldn't error out loud.
    }
  }

  poll();
  setInterval(poll, 4000);
})();
