(function () {
  'use strict';
  var paused = document.body.dataset.sharePaused === '1';
  var endpoint = location.pathname.replace(/\/embed\/?$/, '/availability');
  var checking = false;
  async function check() {
    if (checking) return;
    checking = true;
    try {
      var response = await fetch(endpoint, {cache: 'no-store', credentials: 'omit'});
      if (!response.ok) return;
      var state = await response.json();
      if (state.available) {
        if (paused) location.replace(location.href);
      } else {
        paused = true;
        var main = document.querySelector('main');
        var card = document.createElement('section');
        card.className = 'share-paused';
        card.setAttribute('role', 'status');
        var title = document.createElement('h1');
        title.textContent = state.message || '暂时停止分享';
        var note = document.createElement('p');
        note.textContent = '请稍后再试';
        card.append(title, note);
        if (main) main.replaceChildren(card);
      }
    } catch (_) {
      // A transient network error does not change the last confirmed state.
    } finally { checking = false; }
  }
  setInterval(check, 15000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) check(); });
  window.addEventListener('pageshow', check);
})();
