/* AtomPages · 条目详情页 */
(function () {
  "use strict";

  function q(name) {
    return new URLSearchParams(location.search).get(name);
  }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    var p = function (n) { return n < 10 ? "0" + n : "" + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " +
           p(d.getHours()) + ":" + p(d.getMinutes());
  }

  var mSlug = q("m");
  var iSlug = q("i");

  function load() {
    fetch("data/modules.json", { cache: "no-cache" })
      .then(function (r) { return r.json(); })
      .then(boot)
      .catch(function () {
        document.getElementById("title").textContent = "载入失败";
      });
  }

  function boot(data) {
    var mod = null, item = null;
    (data.modules || []).forEach(function (m) {
      if (m.slug === mSlug) {
        mod = m;
        (m.items || []).forEach(function (it) { if (it.slug === iSlug) item = it; });
      }
    });
    if (!mod || !item) {
      document.getElementById("title").textContent = "未找到该条目";
      document.getElementById("kicker").textContent = "";
      return;
    }
    document.title = item.title + " · AtomPages";

    document.getElementById("kicker").textContent = mod.no + " " + mod.name + " · 条目 " + item.no;
    document.getElementById("title").textContent = item.title;
    document.getElementById("bar-title").textContent = item.title;
    document.getElementById("desc-text").textContent = mod.desc || "";

    document.getElementById("act-open").href = item.html;
    document.getElementById("act-src").href = item.html;
    if (item.pdf) {
      var p = document.getElementById("act-pdf");
      p.href = item.pdf;
      p.hidden = false;
    }

    var kv = document.getElementById("desc-kv");
    var rows = [
      ["编号", item.no],
      ["模块", mod.no + " " + mod.name],
      ["类型", item.kind === "doc" ? "文档" : "幻灯片"],
      ["规模", item.kind === "doc" ? (item.bytes / 1024).toFixed(0) + " KB" : item.slides + " 页"],
      ["大小", (item.bytes / 1024).toFixed(0) + " KB"],
      ["自包含", item.selfContained ? "是" : "否（含外部引用）"],
      ["来源项目", item.sourceProject],
      ["归档路径", item.html],
      ["最近更新", fmtDate(item.updatedAt)]
    ];
    rows.forEach(function (r) {
      kv.appendChild(el("dt", null, r[0]));
      kv.appendChild(el("dd", null, r[1]));
    });

    // 同模块其他条目
    var box = document.getElementById("siblings");
    if (mod.items && mod.items.length > 1) {
      var h = el("h3", null, "");
      h.style.cssText = "font-family:var(--mono);font-size:12px;color:var(--amber);letter-spacing:.16em;font-weight:700;margin:44px 0 16px";
      h.textContent = "同模块其他条目";
      box.appendChild(h);
      var grid = el("div", "side");
      mod.items.forEach(function (other) {
        var a = el("a", "sidecard" + (other.slug === item.slug ? " on" : ""));
        a.href = "view.html?m=" + encodeURIComponent(mod.slug) + "&i=" + encodeURIComponent(other.slug);
        a.appendChild(el("div", "sidecard__no", other.no));
        a.appendChild(el("div", "sidecard__t", other.title));
        grid.appendChild(a);
      });
      box.appendChild(grid);
    }

    var stage = document.getElementById("stage");
    var frame = document.getElementById("frame");
    frame.src = item.html;

    // 画布尺寸归一：源 deck 可能是 1280x720、1920x1080 或 100vh，
    // 统一按 1920x1080 渲染后缩放到容器宽度。
    function fit(naturalH) {
      var w = stage.clientWidth;
      var h = stage.clientHeight;
      var ch = naturalH || 1080;
      var scale = Math.min(w / 1920, h / ch);
      frame.style.width = "1920px";
      frame.style.height = ch + "px";
      frame.style.transform = "scale(" + scale + ")";
      frame.style.left = (w - 1920 * scale) / 2 + "px";
      frame.style.top = (h - ch * scale) / 2 + "px";
    }
    window.addEventListener("resize", function () { fit(measuredH); });
    var measuredH = 1080;
    frame.addEventListener("load", function () {
      // 读源文件真实画布高度：有的 deck 用 100vh 或 7.5in，
      // 用第一屏可见元素的高度作为自然高度，避免内容被裁掉。
      try {
        var doc = frame.contentDocument;
        if (doc) {
          var vis = doc.querySelector(".slide.active, .slide.on, .slide, .page");
          if (vis) {
            var r = vis.getBoundingClientRect();
            if (r.height > 200) measuredH = Math.round(r.height);
          }
        }
      } catch (e) { /* 跨域时用默认值 */ }
      fit(measuredH);
    });
    fit();

    document.getElementById("stamp").textContent =
      "最近汇集 " + (data.updatedAt ? fmtDate(data.updatedAt) : "");
  }

  load();
})();
