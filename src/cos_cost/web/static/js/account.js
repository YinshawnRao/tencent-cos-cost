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

  const panel = document.getElementById("settings-panel");
  const openBtn = document.getElementById("open-settings");
  const closeBtn = document.getElementById("close-settings");
  const savedLine = document.getElementById("settings-saved");
  const progress = document.getElementById("settings-progress");
  const errBox = document.getElementById("settings-error");
  const settings = data.settings || {};

  function applyStatus(status) {
    if (!status) return;
    if (savedLine) {
      if (status.secret_id_masked) {
        savedLine.hidden = false;
        savedLine.textContent = `已保存 ${status.secret_id_masked} · ${status.mode === "live" ? "live" : "mock"}`;
      } else {
        savedLine.hidden = true;
      }
    }
    if (errBox) {
      const msg = status.last_collect_error || "";
      errBox.hidden = !msg;
      errBox.textContent = msg;
    }
  }
  applyStatus(settings);
  if (panel && (settings.mode === "mock" || settings.last_collect_error)) {
    panel.hidden = false;
  }
  if (openBtn && panel) openBtn.addEventListener("click", () => { panel.hidden = false; });
  if (closeBtn && panel) closeBtn.addEventListener("click", () => { panel.hidden = true; });

  function errorText(payload, fallback) {
    if (!payload) return fallback;
    if (typeof payload.last_collect_error === "string" && payload.last_collect_error) {
      return payload.last_collect_error;
    }
    if (typeof payload.detail === "string" && payload.detail) return payload.detail;
    if (Array.isArray(payload.detail) && payload.detail.length) {
      const first = payload.detail[0];
      if (typeof first === "string") return first;
      if (first && first.msg) return String(first.msg);
    }
    return fallback;
  }

  const settingsForm = document.getElementById("settings-form");
  if (settingsForm) {
    settingsForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const body = {
        secret_id: (document.getElementById("settings-secret-id") || {}).value || "",
        secret_key: (document.getElementById("settings-secret-key") || {}).value || "",
        token: (document.getElementById("settings-token") || {}).value || "",
        month: (document.getElementById("settings-month") || {}).value || data.month,
        model_api_key: (document.getElementById("settings-model-key") || {}).value || "",
      };
      if (progress) { progress.hidden = false; progress.textContent = "正在拉取账单 / 监控 / 配置…"; }
      if (errBox) { errBox.hidden = true; }
      try {
        const resp = await fetch("/api/settings/credentials", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const status = await resp.json();
        applyStatus(status);
        if (!resp.ok) {
          if (errBox) {
            errBox.hidden = false;
            errBox.textContent = errorText(status, "保存失败");
          }
          return;
        }
        if (status.last_collect_error) {
          if (errBox) {
            errBox.hidden = false;
            errBox.textContent = status.last_collect_error;
          }
          return;
        }
        const month = status.month || body.month || data.month;
        window.location.href = `/?month=${encodeURIComponent(month)}`;
      } catch (err) {
        if (errBox) {
          errBox.hidden = false;
          errBox.textContent = "保存失败，请检查网络或 CAM。";
        }
      } finally {
        if (progress) progress.hidden = true;
        const keyInput = document.getElementById("settings-secret-key");
        if (keyInput) keyInput.value = "";
        const tok = document.getElementById("settings-token");
        if (tok) tok.value = "";
        const mk = document.getElementById("settings-model-key");
        if (mk) mk.value = "";
      }
    });
  }

  async function backToMock() {
    if (progress) { progress.hidden = false; progress.textContent = "正在改回 mock…"; }
    try {
      await fetch("/api/settings/mock", { method: "POST" });
      window.location.href = `/?month=${encodeURIComponent(data.month)}`;
    } catch (err) {
      if (errBox) {
        errBox.hidden = false;
        errBox.textContent = "切换 mock 失败";
      }
    }
  }
  const mockBtn = document.getElementById("settings-mock");
  const clearBtn = document.getElementById("settings-clear");
  if (mockBtn) mockBtn.addEventListener("click", backToMock);
  if (clearBtn) clearBtn.addEventListener("click", backToMock);
})();
