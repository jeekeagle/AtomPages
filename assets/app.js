/* AtomPages · 首页档案墙 */
(function () {
  "use strict";

  var DATA_URL = "data/modules.json";
  var VIEWS = ["grid", "archive", "list"];
  var VIEW_KEY = "atompages.view";
  var state = { modules: [], q: "", filter: "all", view: "grid" };

  // 记住上次选的视图
  try {
    var saved = localStorage.getItem(VIEW_KEY);
    if (saved && VIEWS.indexOf(saved) !== -1) state.view = saved;
  } catch (e) { /* 隐私模式下不可用，用默认值 */ }

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
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }

  function fmtTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    var p = function (n) { return n < 10 ? "0" + n : "" + n; };
    return p(d.getHours()) + ":" + p(d.getMinutes());
  }

  function allItems() {
    var out = [];
    state.modules.forEach(function (m) {
      (m.items || []).forEach(function (it) { it._module = m; out.push(it); });
    });
    return out;
  }

  function matches(it) {
    if (state.filter !== "all" && it.module !== state.filter) return false;
    if (!state.q) return true;
    var q = state.q.toLowerCase();
    var hay = [it.title, it.sourceProject, it._module.name, it._module.desc]
      .join(" ").toLowerCase();
    return hay.indexOf(q) !== -1;
  }

  function card(it, m) {
    var a = el("a", "card");
    a.href = "view.html?m=" + encodeURIComponent(m.slug) + "&i=" + encodeURIComponent(it.slug);

    var no = el("div", "card__no", it.no);
    a.appendChild(no);

    var th = el("div", "card__thumb");
    if (it.thumb) {
      var img = document.createElement("img");
      img.src = it.thumb;
      img.alt = it.title;
      img.loading = "lazy";
      th.appendChild(img);
    }
    a.appendChild(th);

    var body = el("div", "card__body");
    body.appendChild(el("div", "card__title", it.title));
    var meta = el("div", "card__meta");
    meta.appendChild(el("span", "card__kind", it.kind === "doc" ? "文档" : it.slides + " 页"));
    var src = el("span", "card__src");
    src.innerHTML = "来源 <b></b>";
    src.querySelector("b").textContent = it.sourceProject;
    meta.appendChild(src);
    var d = el("span", "card__date", fmtDate(it.updatedAt));
    meta.appendChild(d);
    body.appendChild(meta);
    a.appendChild(body);

    // 清单视图用的独立列（网格/档案视图里隐藏）
    a.appendChild(el("div", "card__col card__col--kind", it.kind === "doc" ? "文档" : it.slides + " 页"));
    var colSrc = el("div", "card__col card__col--src");
    colSrc.innerHTML = "来源 <b></b>";
    colSrc.querySelector("b").textContent = it.sourceProject;
    a.appendChild(colSrc);
    a.appendChild(el("div", "card__col card__col--date", fmtDate(it.updatedAt)));
    return a;
  }

  function render() {
    var wrap = document.getElementById("modules");
    wrap.innerHTML = "";
    wrap.className = "view-" + state.view;
    var hits = allItems().filter(matches);
    var shown = 0;

    state.modules.forEach(function (m) {
      var items = (m.items || []).filter(matches);
      if (!items.length) return;
      shown += items.length;

      var sec = el("section", "module");
      sec.id = "m-" + m.slug;
      var head = el("div", "module__head");
      head.appendChild(el("span", "module__no", m.no));
      head.appendChild(el("span", "module__name", m.name));
      if (m.desc) head.appendChild(el("span", "module__desc", m.desc));
      head.appendChild(el("span", "module__count", items.length + " 条"));
      sec.appendChild(head);

      var grid = el("div", "items");
      items.forEach(function (it) { grid.appendChild(card(it, m)); });
      sec.appendChild(grid);
      wrap.appendChild(sec);
    });

    if (!shown) {
      var e = el("div", "empty");
      e.appendChild(el("b", null, "没有匹配的条目"));
      e.appendChild(el("span", null, "换一个关键词，或清空检索条件"));
      wrap.appendChild(e);
    }
  }

  function renderStats() {
    var box = document.getElementById("stats");
    box.innerHTML = "";
    var items = allItems();
    var pages = 0;
    items.forEach(function (i) { if (i.kind !== "doc") pages += (i.slides || 1); });
    var pdfs = items.filter(function (i) { return i.pdf; }).length;

    function stat(n, k) {
      var d = el("div", "stat");
      var num = el("div", "stat__n");
      num.innerHTML = n;
      d.appendChild(num);
      d.appendChild(el("div", "stat__k", k));
      return d;
    }
    box.appendChild(stat(String(state.modules.length), "模块"));
    box.appendChild(stat(String(items.length), "条目"));
    box.appendChild(stat(String(pages), "总页数"));
    box.appendChild(stat(String(pdfs), "含 PDF"));
  }

  function renderFilters() {
    var box = document.getElementById("filters");
    box.innerHTML = "";
    var mk = function (slug, label) {
      var b = el("button", "chip" + (state.filter === slug ? " on" : ""), label);
      b.addEventListener("click", function () {
        state.filter = slug;
        renderFilters();
        render();
      });
      return b;
    };
    box.appendChild(mk("all", "全部"));
    state.modules.forEach(function (m) {
      box.appendChild(mk(m.slug, m.no + " " + m.name));
    });
  }

  function renderViews() {
    var box = document.getElementById("views");
    if (!box) return;
    var labels = { grid: "网格", archive: "档案", list: "清单" };
    box.innerHTML = "";
    VIEWS.forEach(function (v) {
      var b = el("button", "viewbtn" + (state.view === v ? " on" : ""), labels[v]);
      b.type = "button";
      b.setAttribute("data-view", v);
      b.setAttribute("aria-pressed", state.view === v ? "true" : "false");
      b.addEventListener("click", function () {
        if (state.view === v) return;
        state.view = v;
        try { localStorage.setItem(VIEW_KEY, v); } catch (e) { /* 忽略 */ }
        renderViews();
        render();
      });
      box.appendChild(b);
    });
  }

  function boot(data) {
    state.modules = data.modules || [];
    renderStats();
    renderFilters();
    renderViews();
    render();

    var st = document.getElementById("stamp");
    var t = data.updatedAt ? fmtDate(data.updatedAt) + " " + fmtTime(data.updatedAt) : "";
    st.textContent = "最近汇集 " + t;

    var q = document.getElementById("q");
    q.addEventListener("input", function () {
      state.q = q.value.trim();
      render();
    });
  }

  fetch(DATA_URL, { cache: "no-cache" })
    .then(function (r) { return r.json(); })
    .then(boot)
    .catch(function (e) {
      document.getElementById("modules").innerHTML =
        '<div class="empty"><b>清单载入失败</b><span>请确认 data/modules.json 存在</span></div>';
      document.getElementById("stamp").textContent = "载入失败";
    });
})();
