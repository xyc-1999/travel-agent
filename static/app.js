(() => {
  const $ = (selector) => document.querySelector(selector);

  const state = {
    sessionId: null,
    analysis: null,
    itinerary: null,
    meta: null,
    budgetBasis: null,
    pace: null,
    interests: new Set(),
  };

  const STAGES = [
    { agent: "requirement", label: "需求理解", code: "需" },
    { agent: "research", label: "资料检索", code: "查" },
    { agent: "weather", label: "天气适配", code: "天" },
    { agent: "verification", label: "地点核验", code: "核" },
    { agent: "route", label: "路线编排", code: "路" },
    { agent: "budget", label: "预算计算", code: "预" },
  ];

  const views = {
    empty: $("#empty-state"),
    clarify: $("#clarify-view"),
    progress: $("#progress-view"),
    error: $("#error-view"),
    result: $("#result-view"),
  };

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));

  const money = (value) => `${Math.round(Number(value) || 0).toLocaleString("zh-CN")} 元`;

  const cityOptions = (kind) => {
    const meta = state.meta || {};
    if (kind === "origin") return meta.city_index || [];
    return meta.destination_cities || meta.supported_cities || [];
  };

  // 自绘城市下拉：替代原生 datalist，支持固定高度滚动、拖动滚动条与键盘选择
  const enhanceCityInput = (input) => {
    if (!input || input.dataset.cityReady) return;
    input.dataset.cityReady = "1";
    input.removeAttribute("list");
    const kind = input.dataset.cityPicker || "destination";
    const menu = document.createElement("div");
    menu.className = "city-menu";
    menu.hidden = true;
    document.body.appendChild(menu);

    let items = [];
    let activeIndex = -1;

    const placeMenu = () => {
      const rect = input.getBoundingClientRect();
      const below = window.innerHeight - rect.bottom - 10;
      const above = rect.top - 10;
      const openUp = below < 160 && above > below;
      const maxHeight = Math.min(240, Math.max(96, openUp ? above : below));
      menu.style.left = `${rect.left}px`;
      menu.style.width = `${rect.width}px`;
      menu.style.maxHeight = `${maxHeight}px`;
      if (openUp) {
        menu.style.top = "auto";
        menu.style.bottom = `${window.innerHeight - rect.top + 4}px`;
      } else {
        menu.style.top = `${rect.bottom + 4}px`;
        menu.style.bottom = "auto";
      }
    };

    const close = () => {
      menu.hidden = true;
      activeIndex = -1;
    };

    const open = () => {
      if (input.disabled) return;
      const query = input.value.trim();
      const options = cityOptions(kind);
      items = options.filter((city) => !query || city.includes(query)).slice(0, 80);
      if (!items.length) {
        close();
        return;
      }
      menu.innerHTML = items
        .map(
          (city, index) =>
            `<button type="button" class="city-option" data-index="${index}">${esc(city)}</button>`
        )
        .join("");
      menu.hidden = false;
      activeIndex = -1;
      placeMenu();
    };

    const choose = (city) => {
      if (!city) return;
      input.value = city;
      close();
      input.dispatchEvent(new Event("change", { bubbles: true }));
    };

    const move = (delta) => {
      if (!items.length) return;
      activeIndex = (activeIndex + delta + items.length) % items.length;
      menu.querySelectorAll(".city-option").forEach((button, index) => {
        button.classList.toggle("active", index === activeIndex);
      });
      menu.querySelector(`[data-index="${activeIndex}"]`)?.scrollIntoView({ block: "nearest" });
    };

    input.addEventListener("focus", open);
    input.addEventListener("input", open);
    input.addEventListener("keydown", (event) => {
      if (menu.hidden) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        move(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        move(-1);
      } else if (event.key === "Enter" && activeIndex >= 0) {
        event.preventDefault();
        choose(items[activeIndex]);
      } else if (event.key === "Escape") {
        close();
      }
    });
    menu.addEventListener("mousedown", (event) => {
      const button = event.target.closest(".city-option");
      if (!button) return;
      event.preventDefault();
      choose(items[Number(button.dataset.index)]);
    });
    document.addEventListener("pointerdown", (event) => {
      if (!menu.hidden && event.target !== input && !menu.contains(event.target)) close();
    });
    window.addEventListener("resize", () => {
      if (!menu.hidden) placeMenu();
    });
    document.addEventListener(
      "scroll",
      () => {
        if (!menu.hidden) placeMenu();
      },
      true
    );
  };

  const enhanceCityInputs = (root = document) => {
    root.querySelectorAll("[data-city-picker]").forEach(enhanceCityInput);
  };

  const showView = (name) => {
    Object.values(views).forEach((node) => { node.hidden = true; });
    if (views[name]) views[name].hidden = false;
  };

  const toast = (message) => {
    const node = $("#toast");
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { node.hidden = true; }, 3200);
  };

  const setLoading = (loading) => {
    const button = $("#submit-btn");
    button.disabled = loading;
    button.querySelector(".spinner").hidden = !loading;
    button.querySelector(".btn-label").textContent = loading ? "处理中…" : "开始规划";
  };

  const splitList = (value) =>
    String(value || "")
      .split(/[，,、;；]/)
      .map((item) => item.trim())
      .filter(Boolean);

  const readForm = () => {
    const amountText = $("#budget-amount").value.trim();
    const amount = amountText ? Number(amountText) : null;
    return {
      raw_text: $("#raw-text").value.trim() || null,
      origin: $("#origin").value.trim() || null,
      destination: $("#destination").value || null,
      start_date: $("#start-date").value || null,
      end_date: $("#end-date").value || null,
      days: $("#days").value ? Number($("#days").value) : null,
      travelers: $("#travelers").value ? Number($("#travelers").value) : null,
      budget: {
        amount,
        basis: amount !== null && state.budgetBasis ? state.budgetBasis : null,
        skip: amount === null && state.budgetBasis === "",
      },
      interests: Array.from(state.interests),
      intercity_mode: $("#intercity-mode").value || null,
      local_transport: $("#local-transport").value || null,
      lodging: $("#lodging").value || null,
      pace: state.pace,
      must_visit: splitList($("#must-visit").value),
      avoid: splitList($("#avoid").value),
      notes: $("#notes").value.trim() || null,
      demo_weather: $("#demo-weather").value || "auto",
    };
  };

  const loadMeta = async () => {
    try {
      const response = await fetch("/api/meta");
      state.meta = await response.json();
      const providers = state.meta.providers || {};
      const pill = $("#mode-pill");
      const liveCount = [providers.amap, providers.qweather, providers.llm].filter(Boolean).length;
      if (liveCount >= 2) {
        pill.className = "pill pill-live";
        pill.textContent = "实时/混合数据源已连接";
      } else {
        pill.className = "pill pill-demo";
        pill.textContent = "演示模式 · 示例数据已标注";
      }
    } catch (error) {
      $("#mode-pill").textContent = "服务状态未知";
    }
  };

  const resetViews = () => {
    $("#clarify-form").innerHTML = "";
    $("#agent-timeline").innerHTML = "";
    $("#live-log").textContent = "";
    $("#progress-bar").style.width = "4%";
  };

  const ask = async (payload) => {
    setLoading(true);
    try {
      const response = await fetch("/api/plan/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error((await response.json()).detail || "需求分析失败");
      const analysis = await response.json();
      state.sessionId = analysis.session_id;
      state.analysis = analysis;
      if (analysis.status === "need_clarification") {
        renderClarify(analysis);
      } else {
        startGeneration();
      }
    } catch (error) {
      showError(error.message);
    } finally {
      setLoading(false);
    }
  };

  const renderClarify = async (analysis) => {
    resetViews();
    showView("clarify");
    $("#clarify-summary").textContent = analysis.summary;
    const form = $("#clarify-form");
    form.innerHTML = "";

    analysis.questions.forEach((question) => {
      const card = document.createElement("div");
      card.className = "question-card";
      const hint = question.hint ? `<p class="hint">${esc(question.hint)}</p>` : "";
      let control = "";

      if (question.id === "date_range") {
        const start = analysis.draft.start_date || "";
        const end = analysis.draft.end_date || "";
        control = `
          <div class="field">
            <label>出发与返回日期</label>
            <div style="display:flex;gap:8px;">
              <input type="date" name="${esc(question.id)}__start" value="${esc(start)}" />
              <input type="date" name="${esc(question.id)}__end" value="${esc(end)}" />
            </div>
          </div>`;
      } else if (question.id === "origin") {
        control = `<div class="field"><input name="${esc(question.id)}" data-city-picker="origin" autocomplete="off" placeholder="搜索或输入城市，如：北京" /></div>`;
      } else if (question.options && question.options.length) {
        const isBudget = question.id === "budget_amount";
        const customBudget = isBudget
          ? `
          <div class="field budget-custom" hidden>
            <label>自定义预算</label>
            <div class="budget-custom-row">
              <select name="budget_custom_basis" data-custom-role="basis">
                <option value="total">总预算</option>
                <option value="per_person">人均预算</option>
              </select>
              <input type="number" name="budget_custom_amount" data-custom-role="amount" min="1" step="100" placeholder="输入金额（元）" />
            </div>
          </div>`
          : "";
        control = `
          <div class="field">
            <select name="${esc(question.id)}" ${isBudget ? "data-budget-select" : ""}>
              <option value="">请选择</option>
              ${question.options.map((option) => `<option value="${esc(option.value)}">${esc(option.label)}</option>`).join("")}
            </select>
          </div>${customBudget}`;
      } else {
        const type = question.type === "number" ? "number" : "text";
        control = `<div class="field"><input type="${type}" name="${esc(question.id)}" placeholder="请输入" /></div>`;
      }

      card.innerHTML = `<h3>${esc(question.prompt)}</h3>${hint}${control}`;
      form.appendChild(card);
    });

    form.querySelectorAll("[data-budget-select]").forEach((select) => {
      const row = select.closest(".question-card")?.querySelector(".budget-custom");
      if (!row) return;
      const sync = () => {
        row.hidden = select.value !== "custom";
        if (!row.hidden) row.querySelector("[data-custom-role='amount']")?.focus();
      };
      select.addEventListener("change", sync);
      sync();
    });

    const box = $("#assumption-box");
    if (analysis.assumptions && analysis.assumptions.length) {
      box.hidden = false;
      box.innerHTML = `<strong>当前已标注的默认与假设</strong><ul>${analysis.assumptions.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`;
    } else {
      box.hidden = true;
    }
    enhanceCityInputs(form);
  };

  const collectAnswers = () => {
    const answers = {};
    const form = $("#clarify-form");
    form.querySelectorAll("input, select").forEach((input) => {
      const name = input.name;
      if (!name || input.dataset.customRole) return;
      const value = input.value.trim();
      if (!value) return;
      if (name.endsWith("__start")) {
        const base = name.replace("__start", "");
        const end = form.querySelector(`[name="${base}__end"]`)?.value || "";
        if (end) answers[base] = `${value}|${end}`;
        else answers[base] = value;
      } else if (name.endsWith("__end")) {
        return;
      } else {
        answers[name] = value;
      }
    });

    const budgetSelect = form.querySelector("[data-budget-select]");
    if (budgetSelect && budgetSelect.value === "custom") {
      const amountInput = form.querySelector('[data-custom-role="amount"]');
      const basisSelect = form.querySelector('[data-custom-role="basis"]');
      const amount = Number(amountInput?.value);
      if (amount > 0) {
        answers["budget_amount"] = `${basisSelect?.value || "total"}|${amount}`;
      } else {
        delete answers["budget_amount"];
      }
    }
    return answers;
  };

  const submitClarify = async () => {
    const answers = collectAnswers();
    if (!Object.keys(answers).length) {
      toast("请至少填写一项，或直接修改左侧表单");
      return;
    }
    setLoading(true);
    try {
      const response = await fetch("/api/plan/clarify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: state.sessionId, answers }),
      });
      if (!response.ok) throw new Error((await response.json()).detail || "澄清失败");
      const analysis = await response.json();
      state.analysis = analysis;
      if (analysis.status === "need_clarification") {
        renderClarify(analysis);
      } else {
        startGeneration();
      }
    } catch (error) {
      showError(error.message);
    } finally {
      setLoading(false);
    }
  };

  const renderTimeline = () => {
    const container = $("#agent-timeline");
    container.innerHTML = STAGES.map(
      (stage) => `
        <div class="agent-row" data-agent="${stage.agent}">
          <div class="agent-badge">${stage.code}</div>
          <div class="agent-content">
            <div class="agent-title">${stage.label}<span class="agent-status">等待中</span></div>
            <div class="agent-summary">尚未执行</div>
            <ul class="agent-details"></ul>
          </div>
        </div>`
    ).join("");
  };

  const updateAgent = (agent, status, summary, details, duration) => {
    const row = $(`[data-agent="${agent}"]`);
    if (!row) return;
    row.className = `agent-row ${status}`;
    row.querySelector(".agent-status").textContent =
      status === "running" ? "执行中" : status === "done" ? `已完成 · ${duration || 0} ms` :
      status === "warning" ? `完成（有提示） · ${duration || 0} ms` : "失败";
    row.querySelector(".agent-summary").textContent = summary || "";
    const list = row.querySelector(".agent-details");
    list.innerHTML = (details || []).map((item) => `<li>${esc(item)}</li>`).join("");
  };

  const log = (message) => {
    const node = $("#live-log");
    node.textContent += `${new Date().toLocaleTimeString("zh-CN", { hour12: false })}  ${message}\n`;
    node.scrollTop = node.scrollHeight;
  };

  const startGeneration = async () => {
    resetViews();
    showView("progress");
    renderTimeline();
    log("已提交生成请求，等待 Agent 事件…");

    try {
      const response = await fetch("/api/plan/generate/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: state.sessionId }),
      });
      if (!response.ok || !response.body) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "流式接口不可用");
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          handleEventBlock(block);
          boundary = buffer.indexOf("\n\n");
        }
      }
      if (state.itinerary) {
        $("#progress-bar").style.width = "100%";
        setTimeout(() => renderResult(state.itinerary), 260);
      }
    } catch (error) {
      showError(error.message);
    }
  };

  const handleEventBlock = (block) => {
    let eventName = "message";
    const dataLines = [];
    block.split(/\r?\n/).forEach((line) => {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    });
    if (!dataLines.length) return;
    let data;
    try {
      data = JSON.parse(dataLines.join("\n"));
    } catch (error) {
      return;
    }

    if (eventName === "agent_start") {
      updateAgent(data.agent, "running", "执行中…", [], 0);
      const percent = (data.index / data.total) * 100;
      $("#progress-bar").style.width = `${Math.max(4, percent - 8)}%`;
      log(`▶ ${data.label} 开始`);
    } else if (eventName === "agent_done") {
      updateAgent(data.agent, data.status || "done", data.summary, data.details, data.duration_ms);
      const stage = STAGES.find((item) => item.agent === data.agent);
      log(`✓ ${stage ? stage.label : data.agent}：${data.summary || ""}`);
      if (data.details && data.details.length) {
        data.details.slice(0, 2).forEach((item) => log(`   · ${item}`));
      }
    } else if (eventName === "result") {
      state.itinerary = data;
      $("#progress-bar").style.width = "100%";
      log("✓ 行程已生成，可导出 Word 与 PDF");
      renderResult(data);
    } else if (eventName === "error") {
      showError(data.message || "生成失败", data.details);
    }
  };

  const showError = (message, details) => {
    resetViews();
    showView("error");
    $("#error-message").textContent = message + (details && details.length ? `（${details.join("；")}）` : "");
    log(`✗ ${message}`);
  };

  const weatherChip = (weather) => {
    const bad = !weather.outdoor_ok;
    const demo = String(weather.source || "").includes("demo");
    return `
      <div class="weather-chip ${bad ? "bad" : ""} ${demo ? "demo" : ""}">
        <strong>${esc(weather.date)} · ${esc(weather.condition)}</strong>
        ${esc(weather.low)}~${esc(weather.high)}℃ · 降水 ${esc(weather.rain_probability)}%
        <br />${esc(weather.source)}
      </div>`;
  };

  const routeSketch = (day) => {
    const points = day.items.filter((item) => item.lat !== null && item.lng !== null && item.kind !== "hotel");
    if (points.length < 2) return "";
    const lats = points.map((item) => Number(item.lat));
    const lngs = points.map((item) => Number(item.lng));
    const minLat = Math.min(...lats), maxLat = Math.max(...lats);
    const minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
    const pad = 24;
    const width = 320, height = 128;
    const xs = points.map((item) => {
      const ratio = maxLng === minLng ? 0.5 : (Number(item.lng) - minLng) / (maxLng - minLng);
      return pad + ratio * (width - pad * 2);
    });
    const ys = points.map((item) => {
      const ratio = maxLat === minLat ? 0.5 : (Number(item.lat) - minLat) / (maxLat - minLat);
      return height - pad - ratio * (height - pad * 2);
    });
    const coords = xs.map((x, index) => `${x.toFixed(1)},${ys[index].toFixed(1)}`).join(" ");
    const circles = xs.map((x, index) => `
      <circle cx="${x.toFixed(1)}" cy="${ys[index].toFixed(1)}" r="9" fill="#fffdfa" stroke="#234f4a" stroke-width="1.6" />
      <text x="${x.toFixed(1)}" y="${(ys[index] + 3.5).toFixed(1)}" text-anchor="middle" font-size="9" fill="#234f4a">${index + 1}</text>`).join("");
    return `
      <div class="route-sketch">
        <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="当天路线示意">
          <polyline points="${coords}" fill="none" stroke="#8fb7ab" stroke-width="2" stroke-dasharray="5 4" />
          ${circles}
        </svg>
        <div class="legend">${points.map((item, index) => `${index + 1}. ${esc(item.name)}`).join("　")}</div>
      </div>`;
  };

  const renderDay = (day) => {
    const items = day.items.map((item) => {
      const tags = (item.tags || []).slice(0, 3)
        .map((tag) => `<span class="ti-tag">${esc(tag)}</span>`)
        .join("");
      const must = item.must_visit ? `<span class="ti-tag must">必去</span>` : "";
      const indoor = item.indoor ? `<span class="ti-tag">室内</span>` : "";
      const verify = item.verification_level
        ? `<span class="ti-tag ${item.verification_level === "live_poi" ? "" : "warn"}">${item.verification_level === "live_poi" ? "实时POI" : "示例库核验"}</span>`
        : "";
      const costKnown = item.price_known === false
        ? `<span class="ti-tag warn">价格未获取</span>`
        : "";
      const hoursKnown = item.hours_known === false
        ? `<span class="ti-tag warn">时间未获取</span>`
        : "";
      return `
        <div class="timeline-item ${item.kind}">
          <div class="ti-time">${esc(item.start_time)}<br />${esc(item.end_time)}</div>
          <div class="ti-main">
            <h5>${esc(item.name)}</h5>
            ${item.note ? `<p>${esc(item.note)}</p>` : ""}
            <div class="ti-tags">${must}${tags}${indoor}${verify}${costKnown}${hoursKnown}</div>
          </div>
          <div class="ti-cost">${item.cost ? money(item.cost) : ""}</div>
        </div>`;
    }).join("");

    const legs = (day.legs || []).filter((leg) => leg.distance_km > 0);
    const notes = (day.notes || []).map((note) => `<p>· ${esc(note)}</p>`).join("");

    return `
      <article class="day-card">
        <div class="day-head">
          <div class="day-title">
            <span class="day-no">DAY ${day.day_index}</span>
            <h4>${esc(day.theme)}</h4>
            <span class="day-date">${esc(day.date)}</span>
          </div>
          <div class="day-weather ${day.weather.outdoor_ok ? "" : "bad"}">
            ${esc(day.weather.condition)} · ${esc(day.weather.low)}~${esc(day.weather.high)}℃ · 降水 ${esc(day.weather.rain_probability)}%
          </div>
        </div>
        <div class="day-body">
          ${routeSketch(day)}
          <div class="timeline">${items}</div>
          ${legs.length ? `<div class="day-foot">${legs.map((leg) => `<p>交通：${esc(leg.mode)} ${esc(leg.from_place)} → ${esc(leg.to_place)}，约 ${esc(leg.minutes)} 分钟 / ${esc(leg.distance_km)} km</p>`).join("")}</div>` : ""}
          ${notes ? `<div class="day-foot">${notes}</div>` : ""}
        </div>
      </article>`;
  };

  const renderBudget = (itinerary) => {
    const budget = itinerary.budget;
    const partial = budget.completeness === "partial";
    const statusText = (partial ? "已知费用口径 · " : "") + (budget.budget_status === "ok"
      ? `预算内 · 剩余 ${money(budget.remaining || 0)}${budget.limit_total ? ` · 预算 ${money(budget.limit_total)}` : ""}`
      : budget.budget_status === "over_budget"
        ? `超出预算 ${money(budget.over_budget_amount)}${budget.limit_total ? ` · 预算 ${money(budget.limit_total)}` : ""} · ${budget.within_tolerance ? `${money(budget.tolerance_amount)}容差内` : `超过 ${money(budget.tolerance_amount)}容差`}`
        : "未设置预算上限 · 仅做估算");
    const statusClass = budget.budget_status === "over_budget" ? "over" : budget.budget_status === "ok" ? "" : "empty";
    const adjustments = (budget.adjustments || []).length
      ? `<div class="budget-adjust"><h4>预算适配记录</h4><ul class="note-list">${budget.adjustments.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>`
      : "";
    const bars = budget.lines.filter((line) => line.amount > 0).map((line) => {
      const ratio = budget.total ? Math.max(2, (line.amount / budget.total) * 100) : 0;
      return `
        <div class="bar-row">
          <span>${esc(line.label)}</span>
          <div class="bar-track"><div class="bar-fill ${budget.budget_status === "over_budget" ? "warn" : ""}" style="width:${ratio.toFixed(1)}%"></div></div>
          <span class="bar-value">${money(line.amount)}</span>
        </div>`;
    }).join("");
    const rows = budget.lines.map((line) => `
      <tr><td>${esc(line.label)}</td><td>${money(line.amount)}</td><td>${esc(line.detail)}</td></tr>`).join("");
    const suggestions = budget.suggestions.length
      ? `<ul class="note-list">${budget.suggestions.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`
      : "";
    const totalLabel = partial ? "已知费用合计" : "合计";
    const completeness = partial
      ? `<div class="budget-partial">数据完整度：部分价格未获取（${esc((budget.unknown_prices || []).join("、"))}）。合计为已获取价格之和，不含未获取费用；0 元不代表免费，请按实际报价核实。</div>`
      : `<div class="budget-partial ok">数据完整度：本单所有费用项均来自已获取的价格数据。</div>`;
    return `
      <div class="section">
        <h3>预算明细</h3>
        <div class="budget-status ${statusClass}">${esc(statusText)}</div>
        ${completeness}
        ${adjustments}
        <div class="budget-bars">${bars}</div>
        <table class="budget-table">
          <thead><tr><th>项目</th><th>金额</th><th>说明</th></tr></thead>
          <tbody>${rows}
            <tr class="total"><td>${totalLabel}</td><td>${money(budget.total)}</td><td>人均 ${money(budget.per_person)}</td></tr>
          </tbody>
        </table>
        ${suggestions}
      </div>`;
  };

  const renderSources = (itinerary) => {
    const cards = itinerary.sources.map((source) => `
      <div class="source-card">
        <h5>${esc(source.title)}<span class="source-level ${["live_poi", "forecast", "llm"].includes(source.level) ? "live" : ""}">${esc(source.level)}</span></h5>
        <p>${esc(source.detail)}</p>
      </div>`).join("");
    return `
      <div class="section">
        <h3>数据来源与核验边界</h3>
        <div class="source-grid">${cards}</div>
      </div>`;
  };

  const renderResult = (itinerary) => {
    showView("result");
    const container = $("#result-view");
    const budgetNote = itinerary.budget.budget_status === "over_budget"
      ? `预算 ${money(itinerary.budget.limit_total)} · 超出 ${money(itinerary.budget.over_budget_amount)}`
      : itinerary.budget.budget_status === "ok"
        ? `预算 ${money(itinerary.budget.limit_total)} · 剩余 ${money(itinerary.budget.remaining || 0)}`
        : "未设预算";
    const weather = itinerary.days.map((day) => weatherChip(day.weather)).join("");
    const tips = itinerary.tips.length
      ? `<div class="section"><h3>出行提示</h3><div class="tips-box">${itinerary.tips.map((tip) => `<div class="tip-card">${esc(tip)}</div>`).join("")}</div></div>`
      : "";
    const warnings = itinerary.warnings.length
      ? `<div class="section"><h3>风险与待确认</h3><ul class="note-list">${itinerary.warnings.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>`
      : "";
    const assumptions = itinerary.assumptions.length
      ? `<div class="section"><h3>假设与默认值</h3><div class="assumption-list">${itinerary.assumptions.map((item) => `<div class="assumption-item">${esc(item)}</div>`).join("")}</div></div>`
      : "";

    container.innerHTML = `
      <div class="result-head">
        <div>
          <h2>${esc(itinerary.destination)} · ${esc(itinerary.days_count)} 天行程</h2>
          <p>${esc(itinerary.summary)}</p>
        </div>
        <div class="result-actions">
          <button type="button" class="export-trigger" data-export-open data-trip="${esc(itinerary.trip_id)}">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>
            导出行程
          </button>
        </div>
      </div>

      ${itinerary.demo_mode ? `<div class="demo-banner"><strong>演示模式</strong> · ${esc(itinerary.data_mode_label)}。地点、天气、价格中的示例部分不代表实时查询结果，请以官方信息为准。</div>` : ""}

      <div class="stat-grid">
        <div class="stat"><span>行程天数</span><strong>${esc(itinerary.days_count)}<small> 天</small></strong></div>
        <div class="stat"><span>出行人数</span><strong>${esc(itinerary.travelers)}<small> 人</small></strong></div>
        <div class="stat ${itinerary.budget.budget_status === "over_budget" ? "over" : ""}"><span>${itinerary.budget.completeness === "partial" ? "已知费用合计" : "估算总花费"}</span><strong>${Math.round(itinerary.budget.total).toLocaleString("zh-CN")}<small> 元</small></strong><small>${esc(budgetNote)}${itinerary.budget.completeness === "partial" ? " · 未含未获取价格" : ""}</small></div>
        <div class="stat"><span>人均估算</span><strong>${Math.round(itinerary.budget.per_person).toLocaleString("zh-CN")}<small> 元</small></strong></div>
      </div>

      <div class="weather-strip">${weather}</div>

      <div class="section">
        <h3>每日行程</h3>
        ${itinerary.days.map(renderDay).join("")}
      </div>

      ${renderBudget(itinerary)}
      ${tips}
      ${warnings}
      ${renderSources(itinerary)}
      ${assumptions}

      <div class="result-footer">
        <span>生成时间：${esc(itinerary.created_at.replace("T", " ").slice(0, 16))} · 行程编号 ${esc(itinerary.trip_id)}</span>
        <span>${esc(itinerary.data_mode_label)}</span>
      </div>`;
    container.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const exportTrip = async (tripId, format) => {
    toast(`正在生成 ${format.toUpperCase()} 文件…`);
    try {
      const response = await fetch(
        `/api/trips/${encodeURIComponent(tripId)}/export?format=${encodeURIComponent(format)}`
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const match =
        disposition.match(/filename\*=UTF-8''([^;]+)/i) ||
        disposition.match(/filename="?([^";]+)"?/i);
      const filename = match ? decodeURIComponent(match[1]) : `itinerary.${format}`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast(`已开始下载 ${filename}`);
    } catch (error) {
      toast(`导出失败：${error.message}`);
    }
  };

  const syncDaysFromDates = () => {
    const startValue = $("#start-date").value;
    const endValue = $("#end-date").value;
    const daysInput = $("#days");
    if (!startValue || !endValue) return;
    const startDate = new Date(`${startValue}T00:00:00`);
    const endDate = new Date(`${endValue}T00:00:00`);
    const diff =
      Math.round((endDate.getTime() - startDate.getTime()) / 86400000) + 1;
    daysInput.value = Number.isFinite(diff) && diff > 0 ? String(diff) : "";
  };

  const syncEndDateFromDays = () => {
    const startValue = $("#start-date").value;
    const daysValue = Number($("#days").value);
    if (!startValue || !Number.isFinite(daysValue) || daysValue < 1) return;
    const end = new Date(`${startValue}T00:00:00`);
    end.setDate(end.getDate() + daysValue - 1);
    $("#end-date").value = `${end.getFullYear()}-${String(end.getMonth() + 1).padStart(2, "0")}-${String(end.getDate()).padStart(2, "0")}`;
  };

  // 事件绑定
  $("#start-date").addEventListener("change", () => {
    if ($("#end-date").value) syncDaysFromDates();
    else syncEndDateFromDays();
  });
  $("#end-date").addEventListener("change", syncDaysFromDates);
  $("#days").addEventListener("input", syncEndDateFromDays);
  $("#trip-form").addEventListener("submit", (event) => {
    event.preventDefault();
    ask(readForm());
  });

  $("#clarify-submit").addEventListener("click", submitClarify);

  $("#reset-btn").addEventListener("click", () => window.location.reload());

  $("#example-chips").addEventListener("click", (event) => {
    const button = event.target.closest("[data-example]");
    if (!button) return;
    $("#raw-text").value = button.dataset.example;
    $("#raw-text").focus();
  });

  $("#interest-chips").addEventListener("click", (event) => {
    const button = event.target.closest("[data-value]");
    if (!button) return;
    const value = button.dataset.value;
    if (state.interests.has(value)) {
      state.interests.delete(value);
      button.classList.remove("active");
    } else {
      state.interests.add(value);
      button.classList.add("active");
    }
  });

  $("#budget-basis").addEventListener("click", (event) => {
    const button = event.target.closest("[data-value]");
    if (!button) return;
    state.budgetBasis = button.dataset.value;
    $("#budget-basis").querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
    const amountInput = $("#budget-amount");
    const skipBudget = state.budgetBasis === "";
    amountInput.disabled = skipBudget;
    amountInput.placeholder = state.budgetBasis === "per_person" ? "如：3000" : state.budgetBasis === "total" ? "如：8000" : "不设预算，无需填写金额";
    if (skipBudget) amountInput.value = "";
  });

  $("#pace").addEventListener("click", (event) => {
    const button = event.target.closest("[data-value]");
    if (!button) return;
    state.pace = button.dataset.value;
    $("#pace").querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  });

  const exportDialog = $("#export-dialog");

  $("#result-view").addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-export-open]");
    if (!trigger) return;
    exportDialog.dataset.trip = trigger.dataset.trip;
    exportDialog.showModal();
  });

  exportDialog.addEventListener("click", (event) => {
    if (event.target.closest("[data-close]") || event.target === exportDialog) {
      exportDialog.close();
      return;
    }
    const option = event.target.closest("[data-format]");
    if (!option) return;
    const tripId = exportDialog.dataset.trip;
    exportDialog.close();
    exportTrip(tripId, option.dataset.format);
  });

  resetViews();
  enhanceCityInputs(document);
  loadMeta();
})();
