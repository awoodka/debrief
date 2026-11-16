(function () {
  "use strict";
  var cal = document.querySelector(".cal");
  if (!cal) return;

  var months = Array.prototype.slice.call(cal.querySelectorAll(".cal-month"));
  var buttons = cal.querySelectorAll(".cal-nav");
  var shown = months.findIndex(function (m) { return !m.hidden; });

  var pop = cal.appendChild(document.createElement("div"));
  pop.className = "pop";
  pop.hidden = true;
  pop.setAttribute("aria-hidden", "true");
  var head = pop.appendChild(document.createElement("p"));
  head.className = "pop-head";
  var title = pop.appendChild(document.createElement("p"));
  title.className = "pop-title";

  function show(i) {
    if (i !== shown) {
      pop.hidden = true;
      months[shown].hidden = true;
      months[i].hidden = false;
      shown = i;
    }
    buttons[0].disabled = i === 0;
    buttons[1].disabled = i === months.length - 1;
  }

  function showMonth(ym) {
    show(months.findIndex(function (m) { return m.getAttribute("data-month") === ym; }));
  }

  function place(cell) {
    head.textContent = cell.getAttribute("aria-label");
    title.textContent = cell.getAttribute("data-title");
    pop.hidden = false;
    var gap = 6, bottom = window.innerHeight - gap, h = pop.offsetHeight, r = cell.getBoundingClientRect();
    var top = r.bottom + gap + h > bottom ? r.top - gap - h : r.bottom + gap;
    pop.style.top = Math.max(gap, Math.min(top, bottom - h)) - cal.getBoundingClientRect().top + "px";
  }

  function point(link) {
    var href = link.getAttribute("href"), cell = cal.querySelector('.cal-month a[href="' + href + '"]');
    if (cell.closest(".cal-month").hidden) showMonth(href.slice(0, 7));
    document.querySelectorAll('.cal-month a[href="' + href + '"], .issues a[href="' + href + '"]').forEach(function (n) {
      n.classList.add("hl");
    });
    if (link === cell) place(cell);
  }

  function unpoint() {
    document.querySelectorAll(".hl").forEach(function (n) { n.classList.remove("hl"); });
    pop.hidden = true;
  }

  function linkOf(e) {
    return e.target.closest(".cal-month a, .issues a");
  }

  ["pointerover", "focusin"].forEach(function (type) {
    document.addEventListener(type, function (e) {
      var link = linkOf(e);
      if (link) point(link);
    });
  });
  ["pointerout", "focusout"].forEach(function (type) {
    document.addEventListener(type, function (e) {
      var link = linkOf(e);
      if (link && !link.contains(e.relatedTarget)) unpoint();
    });
  });

  buttons.forEach(function (b) {
    b.hidden = false;
    b.addEventListener("click", function () { show(shown + Number(b.getAttribute("data-step"))); });
  });
  show(shown);

  var follow = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) showMonth(e.target.getAttribute("data-month"));
    });
  }, { rootMargin: "-25% 0px -74% 0px" });
  document.querySelectorAll("main [data-month]").forEach(function (s) { follow.observe(s); });
})();
