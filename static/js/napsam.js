/* ==========================================================================
   NAPSAM - istemci mantigi

   Kurallar:
   - Ekranda tek birincil eylem var; bu dosya onun etrafinda kurulu.
   - Yeni oneri geldiginde SAYFA YENILENMEZ; kart yerinde guncellenir.
   - 1 saniyeyi gecen beklemede spinner degil iskelet gosterilir (Bolum 14).
   - Gosterilen oneriler yalnizca sekme belleginde tutulur; tekrari onlemek
     icin sunucuya "excluded" olarak gonderilir. Sunucuya fazladan yazma yok.
   ========================================================================== */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  /* ---------------------------------------------------------------- tema */
  var themeToggle = $("theme-toggle");
  var themeIcon = $("theme-icon");

  function paintThemeIcon() {
    if (!themeIcon) return;
    var dark = document.documentElement.dataset.theme === "dark";
    // Tasarimdaki gibi MEVCUT modu gosterir; etiket ise eylemi anlatir.
    themeIcon.firstElementChild.setAttribute("href", dark ? "#i-moon" : "#i-sun");
    themeToggle.setAttribute("aria-label", dark ? "Açık temaya geç" : "Koyu temaya geç");
  }
  paintThemeIcon();

  if (themeToggle) {
    themeToggle.addEventListener("click", function () {
      var next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("napsam-theme", next); } catch (e) { /* gizli sekme */ }
      paintThemeIcon();
    });
  }

  /* ---------------------------------------------------------------- csrf */
  function csrfToken() {
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      credentials: "same-origin",
      body: JSON.stringify(body || {})
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  /* ------------------------------------------------- kaydedilenler sayfasi */
  Array.prototype.forEach.call(document.querySelectorAll(".js-unsave"), function (btn) {
    btn.addEventListener("click", function () {
      var row = btn.closest(".saved-item");
      post("/api/state/", { slug: btn.dataset.slug, action: "unsave" })
        .then(function () {
          row.remove();
          if (!document.querySelector(".saved-item")) location.reload();
        })
        .catch(function () { btn.disabled = false; });
      btn.disabled = true;
    });
  });

  /* ------------------------------------------------------------ ana ekran */
  var card = $("card");
  if (!card) return;

  var boot = { saved: [] };
  try { boot = JSON.parse($("bootstrap").textContent); } catch (e) { /* yoksay */ }

  var state = {
    budget: "bedava",
    mode: "chips",
    keywords: [],
    text: "",
    seen: [],                       // bu sekmede gosterilenler
    saved: new Set(boot.saved || []),
    busy: false
  };

  /* --- butce --- */
  var budgetBox = $("budget");
  budgetBox.addEventListener("click", function (e) {
    var btn = e.target.closest(".budget__item");
    if (!btn) return;
    Array.prototype.forEach.call(budgetBox.children, function (b) {
      b.setAttribute("aria-pressed", String(b === btn));
    });
    state.budget = btn.dataset.budget;
  });

  /* --- sekmeler --- */
  var tabChips = $("tab-chips"), tabText = $("tab-text");
  var panelChips = $("panel-chips"), panelText = $("panel-text");

  function selectTab(mode) {
    state.mode = mode;
    var isChips = mode === "chips";
    tabChips.setAttribute("aria-selected", String(isChips));
    tabText.setAttribute("aria-selected", String(!isChips));
    panelChips.hidden = !isChips;
    panelText.hidden = isChips;
    if (!isChips) $("free-text").focus();
  }
  tabChips.addEventListener("click", function () { selectTab("chips"); });
  tabText.addEventListener("click", function () { selectTab("text"); });

  /* --- chipler --- */
  panelChips.addEventListener("click", function (e) {
    var chip = e.target.closest(".chip");
    if (!chip) return;
    var on = chip.getAttribute("aria-pressed") === "true";
    chip.setAttribute("aria-pressed", String(!on));
    var tag = chip.dataset.tag;
    state.keywords = on
      ? state.keywords.filter(function (k) { return k !== tag; })
      : state.keywords.concat([tag]);
  });

  /* --- serbest metin: Enter da tetikler --- */
  $("free-text").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); requestSuggestion(); }
  });

  /* --- kart cizimi --- */
  function renderCard(s) {
    card.dataset.slug = s.slug;

    $("card-band").dataset.category = s.category;
    $("card-category").textContent = s.categoryLabel;

    function badge(id, text) {
      var el = $(id);
      var svg = el.querySelector("svg");
      el.textContent = "";
      if (svg) el.appendChild(svg);
      el.appendChild(document.createTextNode(text));
    }
    badge("badge-cost", s.badges.cost);
    badge("badge-duration", s.badges.duration);
    badge("badge-place", s.badges.place);
    badge("badge-companion", s.badges.companion);

    $("card-title").textContent = s.title;
    $("card-hook").textContent = s.hook;
    $("card-fallback").textContent = s.fallback;

    var stepsEl = $("card-steps");
    stepsEl.textContent = "";
    s.steps.forEach(function (text, i) {
      var li = document.createElement("li");
      li.className = "step";
      var n = document.createElement("span");
      n.className = "step__n";
      n.textContent = String(i + 1);
      var p = document.createElement("p");
      p.textContent = text;
      li.appendChild(n); li.appendChild(p);
      stepsEl.appendChild(li);
    });
    stepsEl.previousElementSibling.textContent =
      "Nasıl yapılır (" + s.steps.length + " adım)";

    var needsWrap = $("card-needs-wrap"), needs = $("card-needs");
    needs.textContent = "";
    if (s.requiredItems && s.requiredItems.length) {
      s.requiredItems.forEach(function (item) {
        var span = document.createElement("span");
        span.className = "need";
        span.textContent = item;
        needs.appendChild(span);
      });
      needsWrap.hidden = false;
    } else {
      needsWrap.hidden = true;
    }

    paintSaveButton();
    $("commit-toast").hidden = true;

    // Bolum 14: mikro animasyon yalnizca kart gecisinde, ~260ms.
    card.classList.remove("card-enter");
    void card.offsetWidth;
    card.classList.add("card-enter");
  }

  /* --- iskelet --- */
  var skeletonTimer = null;
  function startBusy() {
    state.busy = true;
    $("napsam").disabled = true;
    $("btn-reroll").disabled = true;
    skeletonTimer = setTimeout(function () {
      card.classList.add("skeleton");
      ["card-title", "card-hook", "card-fallback"].forEach(function (id) {
        $(id).classList.add("sk");
      });
      Array.prototype.forEach.call(card.querySelectorAll(".step p"), function (p) {
        p.classList.add("sk");
      });
    }, 1000);
  }
  function endBusy() {
    state.busy = false;
    clearTimeout(skeletonTimer);
    card.classList.remove("skeleton");
    Array.prototype.forEach.call(card.querySelectorAll(".sk"), function (el) {
      el.classList.remove("sk");
    });
    $("napsam").disabled = false;
    $("btn-reroll").disabled = false;
  }

  /* --- oneri istegi --- */
  function requestSuggestion() {
    if (state.busy) return;
    startBusy();

    var body = {
      mode: state.mode,
      budget: state.budget,
      keywords: state.mode === "chips" ? state.keywords : [],
      text: state.mode === "text" ? $("free-text").value : "",
      excluded: state.seen.slice(-30)
    };

    post("/api/suggest/", body)
      .then(function (data) {
        if (!data.suggestion) return;
        state.seen.push(data.suggestion.slug);
        renderCard(data.suggestion);
      })
      .catch(function () {
        // Sessizce basarisiz olma: kullanici ne oldugunu bilmeli.
        $("card-hook").textContent =
          "Bağlantı kurulamadı. Bir kez daha dene.";
      })
      .finally(endBusy);
  }

  $("napsam").addEventListener("click", function () {
    requestSuggestion();
    $("result-stage").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  /* --- baska fikir: gecilen oneri 30 gun geri gelmez --- */
  $("btn-reroll").addEventListener("click", function () {
    var slug = card.dataset.slug;
    if (slug && state.seen.indexOf(slug) === -1) state.seen.push(slug);
    post("/api/state/", { slug: slug, action: "dismiss" }).catch(function () {});
    requestSuggestion();
  });

  /* --- kaydet --- */
  function paintSaveButton() {
    var on = state.saved.has(card.dataset.slug);
    $("btn-save").setAttribute("aria-pressed", String(on));
    $("btn-save").setAttribute("aria-label", on ? "Kayıttan çıkar" : "Kaydet");
  }
  paintSaveButton();

  $("btn-save").addEventListener("click", function () {
    var slug = card.dataset.slug;
    var on = state.saved.has(slug);
    var action = on ? "unsave" : "save";

    if (on) state.saved.delete(slug); else state.saved.add(slug);
    paintSaveButton();

    post("/api/state/", { slug: slug, action: action })
      .then(function (data) { updateSavedCount(data.savedCount); })
      .catch(function () {                       // geri al
        if (on) state.saved.add(slug); else state.saved.delete(slug);
        paintSaveButton();
      });
  });

  function updateSavedCount(n) {
    var el = $("saved-count");
    if (el) { el.textContent = n; el.hidden = n === 0; return; }
    if (n > 0) {
      var link = document.querySelector('.navlink[href="/kaydedilenler/"]');
      if (!link) return;
      var badge = document.createElement("span");
      badge.className = "navlink__count";
      badge.id = "saved-count";
      badge.textContent = n;
      link.appendChild(badge);
    }
  }

  /* --- tamam, yapiyorum: amac uygulamayi KAPATTIRMAK (Bolum 3 kural 9) --- */
  $("btn-commit").addEventListener("click", function () {
    post("/api/state/", { slug: card.dataset.slug, action: "done" }).catch(function () {});
    var toast = $("commit-toast");
    toast.hidden = false;
    toast.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });

  $("toast-close").addEventListener("click", function () {
    $("commit-toast").hidden = true;
  });

  // Sunucudan gelen ilk kart da "gorulmus" sayilir.
  if (card.dataset.slug) state.seen.push(card.dataset.slug);
})();
