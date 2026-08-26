(function () {
  const data = COSUI.readBootstrap();
  COSUI.bindM3Buttons();
  COSUI.renderC1(document.getElementById("chart-c1"), data.trend);
  COSUI.renderC2(document.getElementById("chart-c2"), data.treemap, data.month);
  COSUI.renderC3(document.getElementById("chart-c3"), data.composition);
  COSUI.renderC4(document.getElementById("chart-c4"), data.storage_classes);
  document.querySelectorAll("[data-href]").forEach((el) => {
    el.addEventListener("click", (ev) => {
      if (ev.target.closest("a")) return;
      window.location.href = el.getAttribute("data-href");
    });
  });
  const form = document.getElementById("ask-form");
  const box = document.getElementById("ask-answer");
  if (form && box) {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const q = (document.getElementById("ask-q") || {}).value || "";
      box.hidden = false;
      box.textContent = "正在根据缓存排行与机会卡作答…";
      try {
        const resp = await fetch("/api/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ q, month: data.month }),
        });
        const payload = await resp.json();
        const nums = (payload.numbers || [])
          .map((n) => `${n.name}=${n.value}`)
          .join(" · ");
        box.textContent = (payload.answer || "") + (nums ? `\n数字：${nums}` : "");
      } catch (err) {
        box.textContent = "提问失败。请稍后重试。";
      }
    });
  }
})();
