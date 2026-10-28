(function () {
  'use strict';

  var root = document.documentElement;
  var sidebar = document.querySelector('[data-sidebar-panel]');
  var overlay = document.querySelector('[data-sidebar-overlay]');
  var mobileButtons = document.querySelectorAll('[data-sidebar-mobile-toggle]');
  var desktopButton = document.querySelector('[data-sidebar-desktop-toggle]');
  var themeButton = document.querySelector('[data-theme-toggle]');
  var authTabs = document.querySelectorAll('[data-auth-tab]');
  var authPanels = document.querySelectorAll('[data-auth-panel]');
  var passwordToggles = document.querySelectorAll('[data-password-toggle]');
  var authHeading = document.querySelector('[data-auth-heading]');
  var authDescription = document.querySelector('[data-auth-description]');
  var adminBase = root.dataset.adminBase || '';

  function adminUrl(value) {
    if (!adminBase || adminBase === '/admin' || typeof value !== 'string') return value;
    return value.indexOf('/admin') === 0 && (value.length === 6 || value.charAt(6) === '/')
      ? adminBase + value.slice(6) : value;
  }

  if (adminBase) {
    document.querySelectorAll('a[href^="/admin"], form[action^="/admin"]').forEach(function (element) {
      var attribute = element.tagName === 'FORM' ? 'action' : 'href';
      element.setAttribute(attribute, adminUrl(element.getAttribute(attribute)));
    });
    var nativeFetch = window.fetch;
    window.fetch = function (input, init) {
      if (typeof input === 'string') return nativeFetch(adminUrl(input), init);
      if (input instanceof URL) return nativeFetch(new URL(adminUrl(input.pathname) + input.search, input.origin), init);
      return nativeFetch(input, init);
    };
  }

  function setMobile(open) {
    if (!sidebar || !overlay) return;
    sidebar.dataset.open = open ? 'true' : 'false';
    overlay.dataset.open = open ? 'true' : 'false';
    mobileButtons.forEach(function (button) {
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    document.body.style.overflow = open ? 'hidden' : '';
  }

  mobileButtons.forEach(function (button) {
    button.addEventListener('click', function () {
      setMobile(!sidebar || sidebar.dataset.open !== 'true');
    });
  });
  if (overlay) overlay.addEventListener('click', function () { setMobile(false); });

  if (desktopButton) {
    desktopButton.setAttribute('aria-expanded', root.dataset.sidebar === 'collapsed' ? 'false' : 'true');
    desktopButton.addEventListener('click', function () {
      var collapsed = root.dataset.sidebar !== 'collapsed';
      root.dataset.sidebar = collapsed ? 'collapsed' : 'expanded';
      localStorage.setItem('panel-sidebar', root.dataset.sidebar);
      desktopButton.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    });
  }

  if (themeButton) {
    themeButton.addEventListener('click', function () {
      var dark = !root.classList.contains('dark');
      root.classList.toggle('dark', dark);
      localStorage.setItem('panel-theme', dark ? 'dark' : 'light');
      themeButton.setAttribute('aria-label', dark ? '切换到浅色模式' : '切换到深色模式');
    });
  }

  function setAuthTab(name) {
    if (!name) return;
    authTabs.forEach(function (tab) {
      var active = tab.dataset.authTab === name;
      tab.dataset.state = active ? 'active' : 'inactive';
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
      tab.tabIndex = active ? 0 : -1;
    });
    authPanels.forEach(function (panel) {
      panel.hidden = panel.dataset.authPanel !== name;
    });
    var trigger = document.querySelector('[data-auth-tab="' + name + '"]');
    if (trigger && authHeading && trigger.dataset.authTitle) authHeading.textContent = trigger.dataset.authTitle;
    if (trigger && authDescription && trigger.dataset.authDescription) authDescription.textContent = trigger.dataset.authDescription;
  }

  authTabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      setAuthTab(tab.dataset.authTab);
      if (window.history && window.history.replaceState) {
        window.history.replaceState(null, '', tab.dataset.authTab === 'register' ? '#register' : window.location.pathname);
      }
    });
    tab.addEventListener('keydown', function (event) {
      if (!tab.classList.contains('auth-tab')) return;
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      var tabs = Array.prototype.slice.call(authTabs);
      var index = tabs.indexOf(tab);
      var next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : (index - 1 + tabs.length) % tabs.length;
      event.preventDefault();
      tabs[next].focus();
      tabs[next].click();
    });
  });
  if (window.location.hash === '#register') setAuthTab('register');

  passwordToggles.forEach(function (button) {
    button.addEventListener('click', function () {
      var input = document.getElementById(button.dataset.passwordToggle);
      if (!input) return;
      var visible = input.type === 'text';
      input.type = visible ? 'password' : 'text';
      button.setAttribute('aria-pressed', visible ? 'false' : 'true');
      button.setAttribute('aria-label', visible ? '显示密码' : '隐藏密码');
    });
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') setMobile(false);
  });

  window.addEventListener('resize', function () {
    if (window.innerWidth >= 1024) setMobile(false);
  });
})();
