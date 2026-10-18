/**
 * update-all-third-party 前端伴生组件（0.1.0）。
 *
 * 官方插件商店「已安装插件」页的增强注入：
 *  · 工具栏新增「检查第三方更新」「更新全部第三方」两个按钮
 *    （官方「全部更新」按钮旁边；官方按钮仅在有官方更新时才渲染，
 *    我们的按钮始终存在）。
 *  · 第三方插件卡片出现可用更新时，卡片操作区注入单独「更新」按钮。
 *  · 「更新全部第三方」点击后弹确认框：列出全部待更新插件的
 *    「旧版本 → 新版本」，作者不一致的行附警示（老作者 → 新作者，
 *    建议先去插件页面人工确认），可勾选「已确认，强制更新」。
 *  · 执行：逐插件 POST /api/update-all-third-party/apply（串行），
 *    行内实时状态，完成后自动刷新页面。
 *
 * 定位锚点（基于 2.2.2 console bundle 实测）：
 *  · 已安装面板：[role="tabpanel"][id$="-panel-installed"]
 *  · 工具栏动作区：[class*="controlActions"]（CSS module 哈希前缀匹配）
 *  · 插件卡片：article[class*="pluginCard"]，aria-label=插件名
 *  · 卡片操作区：[class*="cardActions"]
 */
(function () {
  "use strict";
  if (window.__qutpInstalled) return; // SPA 重复加载防御
  window.__qutpInstalled = true;

  var TAG = "[update-all-third-party]";
  var SS_KEY = "qutpReport";
  var API_BASE = "/update-all-third-party";

  // ── 基础工具 ─────────────────────────────────────────────

  function getApiUrl(path) {
    try {
      if (window.QwenPaw && window.QwenPaw.host && window.QwenPaw.host.getApiUrl) {
        return window.QwenPaw.host.getApiUrl(path);
      }
    } catch (_) {}
    return window.location.origin + "/api" + path;
  }

  function apiFetch(path, options) {
    options = options || {};
    var headers = Object.assign({}, options.headers || {});
    try {
      var token =
        window.QwenPaw && window.QwenPaw.host && window.QwenPaw.host.getApiToken
          ? window.QwenPaw.host.getApiToken()
          : null;
      if (token) headers.Authorization = "Bearer " + token;
    } catch (_) {}
    return fetch(getApiUrl(path), Object.assign({}, options, { headers: headers }));
  }

  function isStorePage() {
    var path = location.pathname || "";
    if (path !== "/market" && path !== "/settings/marketplace") return false;
    return new URLSearchParams(location.search).get("tab") === "plugins";
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "style") node.style.cssText = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else if (k === "html") node.innerHTML = attrs[k];
        else node.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) {
      if (c) node.appendChild(c);
    });
    return node;
  }

  // ── 主题感知配色 ─────────────────────────────────────────
  // console 暗色主题在 <html> 上标 .dark-mode（ThemeContext）；
  // 语义 CSS 变量（--app-*）随主题/dream-skin 皮肤自动切换，
  // 变量缺失时退回亮色兜底值——弹窗文字永不吃掉背景。

  function isDark() {
    try {
      return document.documentElement.classList.contains("dark-mode");
    } catch (_) {
      return false;
    }
  }

  /** 暗色主题下的 toast 配色 [bg, border, text]（antd dark alert 近似）。 */
  var TOAST_DARK = {
    info: ["#111a2c", "#15325b", "#8ca6ff"],
    ok: ["#0f2013", "#135317", "#6dd47b"],
    warn: ["#2b2111", "#594214", "#ffd666"],
    err: ["#2a1215", "#58181c", "#ff7875"],
  };
  var TOAST_LIGHT = {
    info: ["#e6f4ff", "#1677ff", "#0958d9"],
    ok: ["#f6ffed", "#52c41a", "#389e0d"],
    warn: ["#fffbe6", "#faad14", "#d48806"],
    err: ["#fff2f0", "#ff4d4f", "#cf1322"],
  };

  // ── toast ────────────────────────────────────────────────

  var toastBox = null;
  function toast(text, kind, sticky) {
    if (!toastBox || !document.body.contains(toastBox)) {
      toastBox = el("div", {
        id: "qutp-toast-box",
        style:
          "position:fixed;top:16px;right:16px;z-index:10000;display:flex;" +
          "flex-direction:column;gap:8px;pointer-events:none;max-width:420px;",
      });
      document.body.appendChild(toastBox);
    }
    var c = (isDark() ? TOAST_DARK : TOAST_LIGHT)[kind || "info"];
    var t = el("div", {
      text: text,
      style:
        "background:" + c[0] + ";border:1px solid " + c[1] + ";color:" + c[2] +
        ";padding:8px 12px;border-radius:8px;font-size:13px;line-height:1.5;" +
        "box-shadow:0 4px 12px rgba(0,0,0,.25);white-space:pre-wrap;",
    });
    toastBox.appendChild(t);
    if (!sticky) setTimeout(function () { t.remove(); }, 4500);
    return t;
  }

  // ── 状态（sessionStorage 持久，切页回来不重查）─────────────

  function loadReport() {
    try {
      var raw = sessionStorage.getItem(SS_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) { return null; }
  }
  function saveReport(r) {
    try {
      if (!r) sessionStorage.removeItem(SS_KEY);
      else sessionStorage.setItem(SS_KEY, JSON.stringify(r));
    } catch (_) {}
  }
  function reportToUiState(report) {
    return {
      checked_at: report.checked_at,
      updates: (report.updates || []).map(function (u) {
        return {
          plugin_id: u.plugin_id,
          name: u.name,
          local_version: u.local_version,
          market_version: u.market_version,
          author_mismatch: !!u.author_mismatch,
          local_author: u.local_author,
          market_author: u.market_author,
          market_owner: u.market_owner,
          detail_url: u.detail_url,
        };
      }),
      stats: {
        total: report.total_installed,
        official: report.official_count,
        third: report.third_party_count,
        latest: (report.latest || []).length,
        not_in_market: (report.not_in_market || []).length,
        ambiguous: (report.ambiguous || []).length,
        errors: (report.errors || []).length,
      },
    };
  }

  // ── 检查更新 ─────────────────────────────────────────────

  var checking = false; // 检查进行中（前端互斥信号）
  var applying = false; // 更新进行中（前端互斥信号）

  /** 统一刷新工具栏两按钮 + 卡片按钮的忙碌态。
   *  checking：检查按钮换文案「正在检查更新...」，两按钮均禁用；
   *  applying：更新按钮换文案「正在更新...」，两按钮均禁用，
   *  卡片「更新」按钮全部禁用（防重入）。 */
  function refreshToolbarState() {
    var checkBtn = document.querySelector('[data-qutp-btn="check"]');
    var applyBtn = document.querySelector('[data-qutp-btn="applyAll"]');
    var busy = checking || applying;
    if (checkBtn) {
      if (!checkBtn.getAttribute("data-qutp-label")) {
        checkBtn.setAttribute("data-qutp-label", checkBtn.textContent);
      }
      checkBtn.disabled = busy;
      checkBtn.textContent = checking
        ? "正在检查更新..."
        : checkBtn.getAttribute("data-qutp-label");
      checkBtn.style.opacity = busy ? "0.6" : "";
      checkBtn.style.cursor = busy ? "not-allowed" : "pointer";
    }
    if (applyBtn) {
      if (!applyBtn.getAttribute("data-qutp-label")) {
        applyBtn.setAttribute("data-qutp-label", applyBtn.textContent);
      }
      applyBtn.disabled = busy;
      applyBtn.textContent = applying
        ? "正在更新..."
        : applyBtn.getAttribute("data-qutp-label");
      applyBtn.style.opacity = busy ? "0.6" : "";
      applyBtn.style.cursor = busy ? "not-allowed" : "pointer";
    }
    document
      .querySelectorAll('[data-qutp-card-btn]')
      .forEach(function (b) {
        b.disabled = busy;
        b.style.opacity = busy ? "0.6" : "";
        b.style.cursor = busy ? "not-allowed" : "pointer";
      });
  }

  /** 解析响应；409（后端 busy 幂等提示）转成 err.busy。 */
  function parseResp(res) {
    return res.json().then(function (body) {
      if (res.status === 409) {
        var be = new Error(
          (body && (body.detail || body.message)) || "操作进行中"
        );
        be.busy = true;
        throw be;
      }
      if (!res.ok) {
        var e = new Error(
          (body && (body.detail || body.message)) || "HTTP " + res.status
        );
        throw e;
      }
      return body;
    });
  }

  function runCheck() {
    if (checking || applying) return;
    checking = true;
    refreshToolbarState();
    toast("正在检查第三方插件更新（逐个精确搜索，限速 0.2s/请求）…", "info");
    apiFetch(API_BASE + "/check")
      .then(parseResp)
      .then(function (report) {
        var ui = reportToUiState(report);
        saveReport(ui);
        renderCardButtons();
        var s = ui.stats;
        var line =
          "第三方插件 " + s.third + " 个：" + ui.updates.length + " 个可更新，" +
          s.latest + " 个已是最新";
        if (s.not_in_market) line += "，" + s.not_in_market + " 个不在市场";
        if (s.ambiguous) line += "，" + s.ambiguous + " 个歧义候选";
        if (s.errors) line += "，" + s.errors + " 个检查出错";
        var mismatch = ui.updates.filter(function (u) { return u.author_mismatch; }).length;
        if (mismatch) line += "\n⚠️ " + mismatch + " 个候选作者不一致，更新前请人工确认";
        toast(line, ui.updates.length ? "warn" : "ok");
      })
      .catch(function (e) {
        toast(e.busy ? e.message : "检查失败：" + e.message, e.busy ? "warn" : "err");
      })
      .finally(function () {
        checking = false;
        refreshToolbarState();
      });
  }

  // ── 执行更新（逐插件串行）─────────────────────────────────

  function applyOne(pluginId, confirmMismatch) {
    return apiFetch(API_BASE + "/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        plugin_ids: [pluginId],
        confirm_mismatch: !!confirmMismatch,
      }),
    }).then(parseResp);
  }

  // ── 确认弹窗 ─────────────────────────────────────────────

  function closeModal() {
    var m = document.getElementById("qutp-modal");
    if (m) m.remove();
  }

  function buildModal(title, bodyNode, footNodes, closable) {
    closeModal();
    var box = el("div", {
      style:
        "background:var(--app-surface,#fff);color:var(--app-text,rgba(0,0,0,.88));" +
        "border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,.35);" +
        "width:min(560px,92vw);max-height:80vh;display:flex;flex-direction:column;overflow:hidden;",
    });
    var head = el("div", {
      text: title,
      style:
        "padding:16px 20px;font-size:16px;font-weight:600;" +
        "color:var(--app-text,rgba(0,0,0,.88));" +
        "border-bottom:1px solid var(--app-border-subtle,#f0f0f0);",
    });
    var body = el("div", {
      style:
        "padding:12px 20px;overflow:auto;flex:1;" +
        "color:var(--app-text,rgba(0,0,0,.88));",
    });
    body.appendChild(bodyNode);
    var foot = el("div", {
      style:
        "padding:12px 20px;border-top:1px solid var(--app-border-subtle,#f0f0f0);" +
        "display:flex;gap:8px;justify-content:flex-end;align-items:center;",
    });
    (footNodes || []).forEach(function (n) { foot.appendChild(n); });
    box.appendChild(head);
    box.appendChild(body);
    box.appendChild(foot);
    var mask = el("div", {
      id: "qutp-modal",
      "data-qutp-closable": closable === false ? "0" : "1",
      style:
        "position:fixed;inset:0;z-index:10001;background:rgba(0,0,0,.45);" +
        "display:flex;align-items:center;justify-content:center;",
    });
    mask.appendChild(box);
    mask.addEventListener("click", function (ev) {
      if (ev.target !== mask) return;
      if (mask.getAttribute("data-qutp-closable") === "0") {
        toast("操作进行中，请等待完成…", "warn");
        return;
      }
      closeModal();
    });
    document.body.appendChild(mask);
    return mask;
  }

  function btnPrimary(label, onClick, disabled) {
    var b = el("button", {
      text: label,
      style:
        "background:var(--app-accent,#1677ff);color:#fff;border:none;" +
        "border-radius:6px;padding:6px 16px;font-size:14px;cursor:pointer;" +
        (disabled ? "opacity:.5;cursor:not-allowed;" : ""),
    });
    b.disabled = !!disabled;
    b.addEventListener("click", onClick);
    return b;
  }
  function btnDefault(label, onClick) {
    var b = el("button", {
      text: label,
      style:
        "background:transparent;color:var(--app-accent,#1677ff);" +
        "border:1px solid var(--app-accent-border,#1677ff);" +
        "border-radius:6px;padding:6px 16px;font-size:14px;cursor:pointer;",
    });
    b.addEventListener("click", onClick);
    return b;
  }

  /** 确认弹窗：列出待更新插件，逐个执行。 */
  function confirmAndApply(updates) {
    if (applying || checking) {
      toast(
        applying
          ? "正在更新插件，请等待当前更新完成后再试"
          : "正在检查更新，请等待当前检查完成后再试",
        "warn"
      );
      return;
    }
    var list = el("div");
    var rows = [];
    updates.forEach(function (u) {
      var line = el("div", {
        style:
          "display:flex;align-items:center;gap:8px;padding:6px 0;" +
          "border-bottom:1px dashed var(--app-border-subtle,#f0f0f0);font-size:13px;flex-wrap:wrap;",
      });
      var name = el("span", {
        text: u.name + "（" + u.plugin_id + "）",
        style: "font-weight:600;color:var(--app-text,rgba(0,0,0,.88));",
      });
      var ver = el("span", {
        text: "v" + u.local_version + " → v" + u.market_version,
        style: "color:var(--app-accent,#1677ff);",
      });
      var status = el("span", { text: "", style: "margin-left:auto;" });
      line.appendChild(name);
      line.appendChild(ver);
      line.appendChild(status);
      if (u.author_mismatch) {
        var warn = isDark()
          ? "width:100%;background:rgba(250,173,20,.12);border:1px solid rgba(250,173,20,.35);" +
            "border-radius:6px;padding:6px 8px;color:#ffd666;font-size:12px;"
          : "width:100%;background:#fffbe6;border:1px solid #ffe58f;" +
            "border-radius:6px;padding:6px 8px;color:#d48806;font-size:12px;";
        line.appendChild(
          el("div", {
            html:
              "⚠️ 作者不一致：老作者 <b>" + esc(u.local_author || "（空）") +
              "</b> → 新作者 <b>" + esc(u.market_author) + "</b>（账号 " +
              esc(u.market_owner) + "）。建议先到" +
              '<a href="' + esc(u.detail_url) + '" target="_blank">插件页面</a>' +
              "人工确认后再更新。",
            style: warn,
          })
        );
      }
      list.appendChild(line);
      rows.push({ update: u, status: status });
    });

    var forceChk = el("input", { type: "checkbox" });
    var forceLabel = el("label", {
      style:
        "font-size:12px;color:var(--app-text-secondary,rgba(0,0,0,.65));" +
        "display:flex;gap:4px;align-items:center;",
    });
    forceLabel.appendChild(forceChk);
    forceLabel.appendChild(document.createTextNode(
      "我已确认作者变更，跳过项也强制更新"
    ));

    var cancelBtn = btnDefault("取消", closeModal);
    var running = false;
    var okBtn = btnPrimary("开始更新", function () {
      if (running) return;
      running = true;
      applying = true;
      refreshToolbarState();
      okBtn.disabled = true;
      okBtn.style.opacity = "0.5";
      cancelBtn.disabled = true;
      cancelBtn.style.opacity = "0.5";
      cancelBtn.style.cursor = "not-allowed";
      var modal = document.getElementById("qutp-modal");
      if (modal) modal.setAttribute("data-qutp-closable", "0");
      var confirmMismatch = forceChk.checked;
      forceChk.disabled = true;
      var chain = Promise.resolve();
      rows.forEach(function (row) {
        chain = chain.then(function () {
          var u = row.update;
          row.status.textContent = "更新中…";
          row.status.style.color = "#1677ff";
          return applyOne(u.plugin_id, confirmMismatch)
            .then(function (res) {
              var skipped = (res.skipped_mismatch || []).some(function (s) {
                return s.plugin_id === u.plugin_id;
              });
              var failed = (res.failed || []).some(function (f) {
                return f.plugin_id === u.plugin_id;
              });
              var done = (res.updated || []).some(function (x) {
                return x.plugin_id === u.plugin_id;
              });
              if (done) {
                row.status.textContent = "✓ 已更新";
                row.status.style.color = "#52c41a";
              } else if (skipped) {
                row.status.textContent = "⏭ 已跳过（作者不一致）";
                row.status.style.color = "#faad14";
              } else {
                var msg = failed && failed[0] ? failed[0].message : "未知错误";
                row.status.textContent = "✗ 失败：" + msg;
                row.status.style.color = "#ff4d4f";
              }
            })
            .catch(function (e) {
              row.status.textContent = e.busy
                ? "⏳ " + e.message
                : "✗ 失败：" + e.message;
              row.status.style.color = e.busy ? "#faad14" : "#ff4d4f";
            });
        });
      });
      chain.then(function () {
        applying = false;
        running = false;
        refreshToolbarState();
        var anyDone = rows.some(function (r) {
          return r.status.textContent.indexOf("已更新") >= 0;
        });
        var m = document.getElementById("qutp-modal");
        if (m) m.setAttribute("data-qutp-closable", "1");
        okBtn.disabled = false;
        okBtn.style.opacity = "";
        cancelBtn.disabled = false;
        cancelBtn.style.opacity = "";
        cancelBtn.style.cursor = "pointer";
        if (anyDone) {
          toast("第三方插件更新完成，页面即将刷新…", "ok");
          saveReport(null);
          setTimeout(function () { location.reload(); }, 1200);
        } else {
          toast("没有插件被更新（全部跳过或失败），详见弹窗内状态", "warn");
        }
      });
    });

    var foot = [
      el("div", { style: "margin-right:auto;" }, [forceLabel]),
      cancelBtn,
      okBtn,
    ];
    buildModal(
      "更新全部第三方插件（" + updates.length + " 个）",
      list,
      foot
    );
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /** 单卡片更新（作者不一致时二次确认）。 */
  function updateSingle(u) {
    if (applying || checking) {
      toast(
        applying
          ? "正在更新插件，请等待当前更新完成后再试"
          : "正在检查更新，请等待当前检查完成后再试",
        "warn"
      );
      return;
    }
    applying = true;
    refreshToolbarState();
    var cardBtn = document.querySelector(
      'article[aria-label="' + cssEsc(u.name) + '"] [data-qutp-card-btn]'
    );
    if (cardBtn) {
      cardBtn.setAttribute("data-qutp-label", cardBtn.textContent);
      cardBtn.textContent = "更新中...";
    }
    var finish = function () {
      applying = false;
      refreshToolbarState();
      if (cardBtn) {
        cardBtn.textContent =
          cardBtn.getAttribute("data-qutp-label") || "更新";
      }
    };
    applyOne(u.plugin_id, false)
      .then(function (res) {
        var skipped = (res.skipped_mismatch || []).find(function (s) {
          return s.plugin_id === u.plugin_id;
        });
        if (skipped) {
          finish();
          var body = el("div", {
            html:
              "⚠️ " + esc(skipped.hint) +
              '<br><br>本地版本 v' + esc(u.local_version) +
              " → 市场版本 v" + esc(u.market_version),
            style: "font-size:13px;line-height:1.7;",
          });
          buildModal(
            "作者不一致，需要人工确认",
            body,
            [
              btnDefault("取消", closeModal),
              btnPrimary("我已确认，仍要更新", function () {
                closeModal();
                applying = true;
                refreshToolbarState();
                if (cardBtn) cardBtn.textContent = "更新中...";
                applyOne(u.plugin_id, true)
                  .then(function (res2) {
                    finish();
                    var done = (res2.updated || []).some(function (x) {
                      return x.plugin_id === u.plugin_id;
                    });
                    var failed = (res2.failed || []).find(function (f) {
                      return f.plugin_id === u.plugin_id;
                    });
                    if (done) {
                      toast(u.name + " 已更新到 v" + u.market_version, "ok");
                      saveReport(null);
                      setTimeout(function () { location.reload(); }, 1200);
                    } else {
                      toast(
                        u.name + " 更新失败：" +
                        (failed ? failed.message : "未知错误"),
                        "err"
                      );
                    }
                  })
                  .catch(function (e) {
                    finish();
                    toast(
                      e.busy ? e.message : u.name + " 更新失败：" + e.message,
                      e.busy ? "warn" : "err"
                    );
                  });
              }),
            ]
          );
        } else {
          finish();
          var done = (res.updated || []).some(function (x) {
            return x.plugin_id === u.plugin_id;
          });
          var failed = (res.failed || []).find(function (f) {
            return f.plugin_id === u.plugin_id;
          });
          if (done) {
            toast(u.name + " 已更新到 v" + u.market_version, "ok");
            saveReport(null);
            setTimeout(function () { location.reload(); }, 1200);
          } else {
            toast(
              u.name + " 更新失败：" + (failed ? failed.message : "未知错误"),
              "err"
            );
          }
        }
      })
      .catch(function (e) {
        finish();
        toast(
          e.busy ? e.message : u.name + " 更新失败：" + e.message,
          e.busy ? "warn" : "err"
        );
      });
  }

  /** name 进属性选择器的转义（引号与反斜杠）。 */
  function cssEsc(s) {
    return String(s == null ? "" : s).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  }

  // ── DOM 注入 ─────────────────────────────────────────────

  function btnToolbar(kind, label, primary, onClick) {
    var b = el("button", {
      "data-qutp-btn": kind,
      text: label,
      style:
        "display:inline-flex;align-items:center;gap:4px;border-radius:6px;" +
        "padding:4px 12px;font-size:14px;cursor:pointer;line-height:20px;" +
        (primary
          ? "background:var(--app-accent,#1677ff);color:#fff;border:1px solid var(--app-accent,#1677ff);"
          : "background:transparent;color:var(--app-accent,#1677ff);" +
            "border:1px solid var(--app-accent-border,#1677ff);"),
    });
    b.addEventListener("click", onClick);
    return b;
  }

  function injectedToolbarActions(actions) {
    return !!actions.querySelector('[data-qutp-btn]');
  }

  function ensureToolbar(panel) {
    var actions = panel.querySelector('[class*="controlActions"]');
    if (!actions || injectedToolbarActions(actions)) return;
    var checkBtn = btnToolbar("check", "检查第三方更新", false, runCheck);
    var applyBtn = btnToolbar("applyAll", "更新全部第三方", true, function () {
      if (applying || checking) {
        toast(
          applying
            ? "正在更新插件，请等待当前更新完成后再试"
            : "正在检查更新，请等待当前检查完成后再试",
          "warn"
        );
        return;
      }
      var r = loadReport();
      if (!r || !r.updates || !r.updates.length) {
        toast("还没有检查结果，先自动检查一次…", "info");
        // 检查完成后自动弹确认框（若有可更新项）
        var wait = setInterval(function () {
          if (checking) return;
          clearInterval(wait);
          var rr = loadReport();
          if (rr && rr.updates && rr.updates.length) {
            confirmAndApply(rr.updates);
          }
        }, 500);
        setTimeout(function () { clearInterval(wait); }, 180000);
        runCheck();
        return;
      }
      confirmAndApply(r.updates);
    });
    actions.insertBefore(applyBtn, actions.firstChild);
    actions.insertBefore(checkBtn, applyBtn);
  }

  function renderCardButtons() {
    var panel = document.querySelector(
      '[role="tabpanel"][id$="-panel-installed"]'
    );
    if (!panel) return;
    var r = loadReport();
    if (!r || !r.updates) return;
    var byName = {};
    r.updates.forEach(function (u) { byName[u.name] = u; });
    panel.querySelectorAll('article[class*="pluginCard"]').forEach(function (card) {
      var name = card.getAttribute("aria-label") || "";
      var u = byName[name];
      var actions = card.querySelector('[class*="cardActions"]');
      if (!actions) return;
      var existing = actions.querySelector('[data-qutp-card-btn]');
      if (u && !existing) {
        var b = el("button", {
          "data-qutp-card-btn": "1",
          text: "更新",
          title: "更新 " + u.plugin_id + " 到 v" + u.market_version +
            (u.author_mismatch ? "（作者不一致，点击查看详情）" : ""),
          style:
            "display:inline-flex;align-items:center;gap:4px;border-radius:6px;" +
            "padding:4px 12px;font-size:14px;cursor:pointer;line-height:20px;" +
            "background:var(--app-accent,#1677ff);color:#fff;" +
            "border:1px solid var(--app-accent,#1677ff);" +
            ((checking || applying)
              ? "opacity:0.6;cursor:not-allowed;"
              : ""),
        });
        b.disabled = checking || applying;
        b.addEventListener("click", function () { updateSingle(u); });
        actions.insertBefore(b, actions.firstChild);
      } else if (!u && existing) {
        existing.remove();
      } else if (u && existing) {
        // 已存在的卡片按钮同步忙碌态（checking/applying 期间注入的场景）
        existing.disabled = checking || applying;
        if (!existing.getAttribute("data-qutp-label")) {
          existing.setAttribute("data-qutp-label", existing.textContent);
        }
        if (!applying || existing.textContent.indexOf("更新中") < 0) {
          existing.textContent = existing.getAttribute("data-qutp-label");
        }
      }
      renderUpgradeBanner(card, u);
    });
  }

  /** 卡片顶部覆盖式「可升级」横条（半透明红底白字，圆角贴卡片）。
   *  有更新 → 注入/刷新；无更新 → 移除。pointer-events:none 不挡点击。 */
  function renderUpgradeBanner(card, u) {
    var existing = card.querySelector('[data-qutp-upgrade-banner]');
    if (!u) {
      if (existing) existing.remove();
      return;
    }
    var text = "可升级至 v" + u.market_version +
      (u.author_mismatch ? "（作者不一致）" : "");
    if (existing) {
      if (existing.textContent !== text) existing.textContent = text;
      return;
    }
    // 卡片需要相对定位承载覆盖条；圆角贴卡片自身，避免方角突出。
    try {
      var cs = getComputedStyle(card);
      if (cs.position === "static") card.style.position = "relative";
      var radius = cs.borderTopLeftRadius || "8px";
      var banner = el("div", {
        "data-qutp-upgrade-banner": "1",
        text: text,
        title: u.author_mismatch
          ? u.plugin_id + "：作者不一致（老作者 " + (u.local_author || "（空）") +
            " → 新作者 " + u.market_author + "），更新前请人工确认"
          : u.plugin_id + " 检查到新版本，点击下方「更新」按钮升级",
        style:
          "position:absolute;top:0;left:0;right:0;z-index:2;" +
          "background:rgba(255,77,79,.85);color:#fff;" +
          "font-size:11px;line-height:1.5;padding:2px 8px;" +
          "text-align:center;font-weight:600;pointer-events:none;" +
          "border-top-left-radius:" + radius + ";" +
          "border-top-right-radius:" + radius + ";",
      });
      card.insertBefore(banner, card.firstChild);
    } catch (_) {
      /* computedStyle 异常等极端情况放弃横条，不影响主流程 */
    }
  }

  function ensureUI() {
    if (!isStorePage()) return;
    var panel = document.querySelector(
      '[role="tabpanel"][id$="-panel-installed"]'
    );
    if (!panel) return;
    ensureToolbar(panel);
    renderCardButtons();
  }

  // ── 启动：SPA 路由感知 + DOM 监听 ─────────────────────────

  var scheduled = false;
  function scheduleEnsure() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(function () {
      scheduled = false;
      ensureUI();
    });
  }

  var obs = new MutationObserver(scheduleEnsure);
  function start() {
    obs.observe(document.body, { childList: true, subtree: true });
    ["pushState", "replaceState"].forEach(function (fn) {
      var orig = history[fn];
      history[fn] = function () {
        var ret = orig.apply(this, arguments);
        setTimeout(scheduleEnsure, 50);
        return ret;
      };
    });
    window.addEventListener("popstate", scheduleEnsure);
    scheduleEnsure();
    console.info(TAG, "伴生组件已加载（插件商店页注入检查/更新按钮）");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
