// Early pre-paint shell setup — loaded right after ui.<lang>.js and BEFORE the large
// data.<lang>.js parse, so both run before first paint (no flash). Three jobs:
//   1. Localize the static chrome from window.TS_UI.
//   2. Restore the last-active tab so the saved view paints first (not the default Masteries tab).
//   3. Fix directory-index links for the file:// protocol.
// app.js (which loads after the big data parse) re-applies the full setup; this only sets the
// visible state early. Both jobs are data-independent.
(function () {
  // 1) localize the static page shell
  var UI = window.TS_UI || {};
  var set = function (sel, key, prop) {
    document.querySelectorAll(sel).forEach(function (el) {
      var s = UI[el.dataset[key]];
      if (s != null) el[prop] = s;
    });
  };
  set("[data-i18n]", "i18n", "textContent");
  set("[data-i18n-ph]", "i18nPh", "placeholder");
  set("[data-i18n-title]", "i18nTitle", "title");

  // 2) restore the last-active tab (mirror the *visual* part of selectTab in app.js — the data
  //    setup runs later there). The "ts:activeTab" key + .tab/.view/.active/.controls contract
  //    must stay in sync with app.js's TAB_KEY / selectTab.
  try {
    var saved = JSON.parse(localStorage.getItem("ts:activeTab") || "null");
    if (saved && saved.view) {
      var tabs = [].slice.call(document.querySelectorAll(".tab"));
      var tab = tabs.filter(function (x) {
        return x.dataset.view === saved.view && (x.dataset.group || "") === (saved.group || "");
      })[0];
      if (tab) {
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        document.querySelectorAll(".view").forEach(function (v) {
          v.classList.toggle("active", v.id === "view-" + saved.view);
        });
        var controls = document.querySelector(".controls");
        if (controls) controls.style.display = saved.view === "builder" ? "none" : "";
      }
    }
  } catch (e) { /* storage blocked / bad json — keep the default tab */ }

  // 3) file:// has no directory-index resolution: a browser opening "ko/" from disk shows a
  //    directory listing, not ko/index.html. Any HTTP server (GitHub Pages, localhost, …) resolves
  //    it, so the committed hrefs stay pretty ("ko/", "../") and we only append index.html on file:.
  //    Marker: .dir-index anchors, whose hrefs must end in "/" so appending is a plain concat.
  if (location.protocol === "file:") {
    document.querySelectorAll("a.dir-index").forEach(function (a) {
      a.setAttribute("href", a.getAttribute("href") + "index.html");
    });
  }
})();
