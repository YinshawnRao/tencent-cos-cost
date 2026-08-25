(function () {
  const data = COSUI.readBootstrap();
  COSUI.bindM3Buttons();
  COSUI.renderC1(document.getElementById("chart-c1"), data.trend);
  COSUI.renderC2(document.getElementById("chart-c2"), data.treemap, data.month);
  COSUI.renderC3(document.getElementById("chart-c3"), data.composition);
  COSUI.renderC4(document.getElementById("chart-c4"), data.storage_classes);
  document.querySelectorAll("tr[data-href]").forEach((tr) => {
    tr.addEventListener("click", (ev) => {
      if (ev.target.closest("a")) return;
      window.location.href = tr.getAttribute("data-href");
    });
  });
})();
