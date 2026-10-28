(function () {
  "use strict";
  var toast = document.querySelector(".share-toast");
  var showToast = function (text, bad) {
    if (!toast) return;
    toast.textContent = text;
    toast.classList.toggle("is-error", Boolean(bad));
    toast.classList.add("is-visible");
    window.setTimeout(function () { toast.classList.remove("is-visible"); }, 1500);
  };
  if (window.ClipboardJS) {
    var clipboard = new window.ClipboardJS(".copy-button");
    clipboard.on("success", function (event) { event.clearSelection(); showToast("已复制到剪贴板"); });
    clipboard.on("error", function () { showToast("复制失败，请手动复制", true); });
  }
  var expiry = document.getElementById("share-expiry");
  if (!expiry) return;
  var target = new Date(String(expiry.dataset.expires || "").replace(" ", "T")).getTime();
  var output = expiry.querySelector(".share-metric-value");
  var badge = expiry.querySelector(".share-metric-badge");
  var update = function () {
    var seconds = Math.max(0, Math.floor((target - Date.now()) / 1000));
    if (!Number.isFinite(seconds) || seconds <= 0) {
      output.textContent = "已过期"; badge.textContent = "已失效";
      badge.classList.remove("share-metric-badge-live"); badge.classList.add("share-metric-badge-danger");
      var section = document.querySelector(".share-account-section");
      if (section) section.hidden = true;
      var note = document.getElementById("share-expired-note");
      if (note) note.hidden = false;
      return;
    }
    var days = Math.floor(seconds / 86400); seconds %= 86400;
    var hours = Math.floor(seconds / 3600); seconds %= 3600;
    var minutes = Math.floor(seconds / 60); seconds %= 60;
    output.textContent = (days ? days + " 天 " : "") + String(hours).padStart(2, "0") + ":" + String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0");
  };
  update(); window.setInterval(update, 1000);
}());
