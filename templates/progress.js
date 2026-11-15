(function () {
  "use strict";
  var issue = document.getElementById("issue");
  if (!issue) return;

  var items = [];
  document.querySelectorAll(".rail nav a[href^='#']").forEach(function (link) {
    var target = document.getElementById(link.getAttribute("href").slice(1));
    if (target) items.push({ link: link, target: target });
  });

  var fill = null;
  if (document.body.getAttribute("data-progress") === "continuous") {
    var bar = document.createElement("div");
    bar.className = "progress";
    bar.setAttribute("aria-hidden", "true");
    fill = bar.appendChild(document.createElement("i"));
    document.body.appendChild(bar);
  }

  var current = null, queued = false;

  function update() {
    queued = false;
    var line = window.innerHeight * 0.35, best = null, bestTop = -Infinity;
    items.forEach(function (item) {
      var top = item.target.getBoundingClientRect().top;
      if (top <= line && top > bestTop) { best = item; bestTop = top; }
    });
    if (best !== current) {
      if (current) current.link.removeAttribute("aria-current");
      if (best) best.link.setAttribute("aria-current", "location");
      current = best;
    }
    if (fill) {
      var room = document.documentElement.scrollHeight - window.innerHeight;
      var done = room > 0 ? Math.min(1, Math.max(0, window.scrollY / room)) : 1;
      fill.style.transform = "scaleX(" + done.toFixed(4) + ")";
    }
  }

  function queue() {
    if (!queued) { queued = true; window.requestAnimationFrame(update); }
  }

  window.addEventListener("scroll", queue, { passive: true });
  window.addEventListener("resize", queue);
  window.addEventListener("load", queue);
  document.addEventListener("toggle", queue, true);
  update();
})();
