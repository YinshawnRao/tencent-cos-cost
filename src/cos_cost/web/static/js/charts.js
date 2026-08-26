function readBootstrap() {
  const el = document.getElementById("bootstrap");
  return el ? JSON.parse(el.textContent) : {};
}

function toast(text) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.hidden = false;
  el.textContent = text;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.hidden = true;
  }, 2400);
}

function bindExportButtons(month) {
  document.querySelectorAll("[data-export]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const kind = btn.getAttribute("data-export");
      const stamp = month || "";
      window.location.href = `/export/${kind}?month=${encodeURIComponent(stamp)}`;
    });
  });
}

function bindM3Buttons() {
  const data = readBootstrap();
  bindExportButtons(data && data.month);
}

function emptyChart(dom, message) {
  const chart = echarts.init(dom);
  chart.setOption({
    title: {
      text: message || "暂无数据",
      left: "center",
      top: "middle",
      textStyle: { color: "#94a3b8", fontSize: 13, fontWeight: 400 },
    },
  });
  return chart;
}

function renderC1(el, trend) {
  if (!trend || !trend.months) return emptyChart(el, "无趋势数据");
  const stacks = (trend.stacks || []).filter((s) =>
    (s.values || []).some((v) => v != null && v > 0)
  );
  if (!stacks.length && !(trend.payable || []).some((v) => v != null)) {
    return emptyChart(el, trend.note || "无趋势数据");
  }
  const chart = echarts.init(el);
  chart.setOption({
    tooltip: { trigger: "axis" },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 48, right: 16, top: 36, bottom: 28 },
    xAxis: { type: "category", data: trend.months },
    yAxis: { type: "value", name: "¥" },
    series: [
      ...stacks.map((s) => ({
        name: s.label,
        type: "bar",
        stack: "bill",
        data: s.values,
        itemStyle: { color: s.color },
      })),
      {
        name: "应付",
        type: "line",
        data: trend.payable,
        itemStyle: { color: "#111827" },
        lineStyle: { width: 2 },
      },
    ],
  });
  return chart;
}

function renderC2(el, items, month) {
  if (!items || !items.length) return emptyChart(el, "无桶费用");
  const chart = echarts.init(el);
  chart.setOption({
    tooltip: {
      formatter: (p) => `${p.name}<br/>${p.data.payable_text || ""}`,
    },
    series: [
      {
        type: "treemap",
        roam: false,
        breadcrumb: { show: false },
        nodeClick: false,
        data: items.map((it) => ({
          name: it.short || it.name,
          value: it.value,
          bucket: it.name,
          payable_text: it.payable_text,
          itemStyle: {
            color: it.mom != null && it.mom > 0 ? "#fecaca" : "#bfdbfe",
            borderColor: "#fff",
          },
        })),
        label: { formatter: "{b}" },
      },
    ],
  });
  chart.on("click", (ev) => {
    const bucket = ev.data && ev.data.bucket;
    if (bucket) window.location.href = `/b/${encodeURIComponent(bucket)}?month=${month}`;
  });
  return chart;
}

function renderC3(el, composition) {
  const items = (composition && composition.items) || [];
  if (!items.length) return emptyChart(el, "无计费项拆分");
  const chart = echarts.init(el);
  chart.setOption({
    tooltip: { trigger: "item" },
    series: [
      {
        type: "pie",
        radius: ["46%", "72%"],
        data: items.map((it) => ({
          name: it.label,
          value: it.value,
          itemStyle: { color: it.color },
        })),
        label: { formatter: "{b} {d}%" },
      },
    ],
  });
  return chart;
}

function renderC4(el, classes) {
  if (!classes || !classes.length || classes.every((c) => c.pct == null)) {
    return emptyChart(el, "无监控容量");
  }
  const chart = echarts.init(el);
  chart.setOption({
    grid: { left: 48, right: 36, top: 8, bottom: 16 },
    xAxis: { type: "value", max: 100, axisLabel: { formatter: "{value}%" } },
    yAxis: { type: "category", data: classes.map((c) => c.label).reverse() },
    series: [
      {
        type: "bar",
        data: classes
          .map((c) => ({
            value: c.pct || 0,
            itemStyle: { color: c.color },
          }))
          .reverse(),
        label: { show: true, position: "right", formatter: (p) => `${Math.round(p.value)}%` },
      },
    ],
  });
  return chart;
}

function renderLines(el, pack, extra) {
  if (!pack || pack.empty) return emptyChart(el, (pack && pack.message) || "暂无数据");
  const chart = echarts.init(el);
  const series = (pack.series || []).map((s) => ({
    name: s.label,
    type: "line",
    data: s.values,
    itemStyle: { color: s.color },
    lineStyle: s.dashed ? { type: "dashed", color: s.color } : { color: s.color },
    areaStyle: extra && extra.area ? { opacity: 0.15 } : undefined,
    stack: extra && extra.stack ? "st" : undefined,
  }));
  if (extra && extra.markLine != null) {
    series.push({
      name: extra.markName || "参考",
      type: "line",
      markLine: {
        symbol: "none",
        data: [{ yAxis: extra.markLine, name: extra.markName }],
        lineStyle: { type: "dashed", color: "#94a3b8" },
        label: { formatter: extra.markName },
      },
    });
  }
  chart.setOption({
    tooltip: { trigger: "axis" },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 44, right: 12, top: 32, bottom: 24 },
    xAxis: { type: "category", data: pack.dates || [], axisLabel: { hideOverlap: true } },
    yAxis: { type: "value" },
    series,
  });
  return chart;
}

window.COSUI = {
  readBootstrap,
  toast,
  bindM3Buttons,
  bindExportButtons,
  renderC1,
  renderC2,
  renderC3,
  renderC4,
  renderLines,
  emptyChart,
};
