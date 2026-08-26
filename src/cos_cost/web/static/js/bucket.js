(function () {
  const data = COSUI.readBootstrap();
  COSUI.bindM3Buttons();

  const c7 = data.c7;
  if (c7 && !c7.empty) {
    COSUI.renderLines(document.getElementById("chart-c7"), c7, { area: true, stack: true });
  } else {
    COSUI.emptyChart(document.getElementById("chart-c7"), c7 && c7.message);
  }
  COSUI.renderC3(document.getElementById("chart-c8"), data.c8);
  const c6 = data.c6;
  if (c6 && !c6.empty) {
    COSUI.renderLines(
      document.getElementById("chart-c6"),
      { dates: c6.dates, series: [{ label: "容量 MB", values: c6.capacity_mb, color: "#2563EB" }] }
    );
  } else {
    COSUI.emptyChart(document.getElementById("chart-c6"), c6 && c6.message);
  }
  COSUI.renderLines(document.getElementById("chart-c9"), data.c9);
  COSUI.renderLines(document.getElementById("chart-c10"), data.c10);

  const drawer = document.getElementById("drawer");
  const cards = data.opportunities || [];
  function openCard(index) {
    const card = cards[index];
    if (!card || !drawer) return;
    document.getElementById("drawer-title").textContent = `${card.rule_id} · ${card.title}`;
    const netEl = document.getElementById("drawer-net");
    if (netEl) {
      const net = card.net_saving == null ? "—" : `¥ ${Number(card.net_saving).toLocaleString()}`;
      netEl.textContent = `${net} · 置信度 ${card.confidence}`;
    }
    const whyEl = document.getElementById("drawer-why");
    if (whyEl) whyEl.textContent = card.why || "";
    document.getElementById("drawer-evidence").textContent = JSON.stringify(card.evidence || {}, null, 2);
    document.getElementById("drawer-formula").textContent = card.formula || "";
    document.getElementById("drawer-action").textContent = card.action || "";
    document.getElementById("drawer-draft").textContent = card.action_draft || "";
    document.getElementById("drawer-warning").textContent = card.warning || "";
    drawer.hidden = false;
    drawer.dataset.draft = card.action_draft || "";
  }
  document.querySelectorAll("[data-open]").forEach((el) => {
    el.addEventListener("click", () => openCard(Number(el.getAttribute("data-open"))));
  });
  const close = document.getElementById("drawer-close");
  if (close) close.addEventListener("click", () => { drawer.hidden = true; });
  const copy = document.getElementById("copy-draft");
  if (copy) {
    copy.addEventListener("click", async () => {
      const text = drawer.dataset.draft || "";
      try {
        await navigator.clipboard.writeText(text);
        COSUI.toast("已复制草稿，不会应用到桶。");
      } catch (err) {
        COSUI.toast("复制失败，请手动选择草稿文本。");
      }
    });
  }
  const copyAll = document.getElementById("copy-all-drafts");
  if (copyAll) {
    copyAll.addEventListener("click", async () => {
      const text = (cards[0] && cards[0].action_draft) || "";
      try {
        await navigator.clipboard.writeText(text);
        COSUI.toast("已复制草稿，不会应用到桶。");
      } catch (err) {
        COSUI.toast("复制失败，请打开机会抽屉手动复制。");
      }
    });
  }
})();
