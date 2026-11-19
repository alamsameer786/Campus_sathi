/* Campus IQ shared front-end helpers */
(function () {
  'use strict';

  const csrf = () => document.querySelector('meta[name="csrf-token"]').content;

  function toast(msg, ms = 4500) {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.classList.remove('hidden');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add('hidden'), ms);
  }

  async function post(url, data) {
    const body = data instanceof FormData ? data : new URLSearchParams(data || {});
    let res;
    try {
      res = await fetch(url, { method: 'POST', body, headers: { 'X-CSRFToken': csrf() }, credentials: 'same-origin' });
    } catch (e) {
      return { ok: false, error: 'Network error. Check your connection and try again.' };
    }
    try { return await res.json(); }
    catch (e) { return { ok: false, error: 'Unexpected server response (' + res.status + ').' }; }
  }

  /* ---------- Markdown + LaTeX rendering (sanitised) ---------- */
  function renderMarkdown(text) {
    const maths = [];
    // Protect $$...$$ and $...$ from the markdown parser, then restore.
    const guarded = text.replace(/\$\$[\s\S]+?\$\$|\$[^\n$]+?\$/g, m => {
      maths.push(m);
      return 'MATHPLACEHOLDER' + (maths.length - 1) + 'X';
    });
    let html = marked.parse(guarded, { breaks: true, gfm: true });
    html = html.replace(/MATHPLACEHOLDER(\d+)X/g, (_, i) =>
      maths[+i].replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'));
    return DOMPurify.sanitize(html);
  }

  function typeset(el) {
    if (window.renderMathInElement) {
      try {
        renderMathInElement(el, {
          delimiters: [
            { left: '$$', right: '$$', display: true },
            { left: '$', right: '$', display: false },
          ],
          throwOnError: false,
        });
      } catch (e) { /* ignore math errors */ }
    }
  }

  function renderAll(root) {
    (root || document).querySelectorAll('[data-md]').forEach(el => {
      if (el.dataset.rendered) return;
      el.innerHTML = renderMarkdown(el.textContent);
      el.classList.add('prose-md');
      el.dataset.rendered = '1';
      typeset(el);
    });
  }

  /* ---------- AJAX forms: <form data-ajax data-loading="Working..."> ---------- */
  function setBusy(btn, busy, label) {
    if (!btn) return;
    if (busy) {
      btn.dataset.html = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<span class="material-symbols-outlined spin text-[20px]">progress_activity</span><span>' + (label || 'Working…') + '</span>';
    } else {
      btn.disabled = false;
      if (btn.dataset.html) btn.innerHTML = btn.dataset.html;
    }
  }

  document.addEventListener('submit', async (e) => {
    const form = e.target;
    if (form.dataset.confirm && !confirm(form.dataset.confirm)) { e.preventDefault(); return; }
    if (!form.hasAttribute('data-ajax')) return;
    e.preventDefault();
    const btn = e.submitter || form.querySelector('[type=submit]');
    const errBox = form.querySelector('[data-error]');
    if (errBox) errBox.classList.add('hidden');
    setBusy(btn, true, form.dataset.loading);
    const res = await post(form.action, new FormData(form));
    if (res.ok && res.redirect) { window.location.href = res.redirect; return; }
    setBusy(btn, false);
    if (res.ok) { window.location.reload(); return; }
    if (errBox) { errBox.textContent = res.error; errBox.classList.remove('hidden'); errBox.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    else toast(res.error || 'Something went wrong.');
  });

  /* One-click AI buttons: <button data-post="/url" data-fields='{"kind":"kit"}' data-loading="Generating…"> */
  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-post]');
    if (!btn) return;
    e.preventDefault();
    if (btn.dataset.confirm && !confirm(btn.dataset.confirm)) return;
    setBusy(btn, true, btn.dataset.loading);
    const res = await post(btn.dataset.post, JSON.parse(btn.dataset.fields || '{}'));
    if (res.ok && res.redirect) { window.location.href = res.redirect; return; }
    setBusy(btn, false);
    if (res.ok) { if (btn.dataset.reload) window.location.reload(); return; }
    toast(res.error || 'Something went wrong.');
  });

  /* ---------- Chat widget: <div data-chat data-url="..."> ---------- */
  function addBubble(list, role, text) {
    const wrap = document.createElement('div');
    wrap.className = 'flex ' + (role === 'user' ? 'justify-end' : 'justify-start');
    const b = document.createElement('div');
    if (role === 'user') {
      b.className = 'max-w-[85%] rounded-2xl rounded-br-md bg-primary text-on-primary px-4 py-2.5 font-body-sm text-body-sm whitespace-pre-wrap';
      b.textContent = text;
    } else {
      b.className = 'max-w-[92%] rounded-2xl rounded-bl-md bg-surface-container-lowest shadow-sm px-4 py-3 prose-md';
      b.innerHTML = renderMarkdown(text);
      typeset(b);
    }
    wrap.appendChild(b);
    list.appendChild(wrap);
    wrap.scrollIntoView({ block: 'end', behavior: 'smooth' });
    return b;
  }

  function initChat(root) {
    const list = root.querySelector('[data-chat-list]');
    const form = root.querySelector('form');
    const input = form.querySelector('textarea');
    list.querySelectorAll('[data-chat-md]').forEach(el => {
      el.innerHTML = renderMarkdown(el.textContent); typeset(el);
    });
    input.addEventListener('keydown', ev => {
      if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); form.requestSubmit(); }
    });
    form.addEventListener('submit', async ev => {
      ev.preventDefault();
      const msg = input.value.trim();
      if (!msg) return;
      const empty = root.querySelector('[data-chat-empty]');
      if (empty) empty.remove();
      addBubble(list, 'user', msg);
      input.value = '';
      const typing = addBubble(list, 'model', '…');
      typing.classList.add('animate-pulse');
      const btn = form.querySelector('[type=submit]');
      btn.disabled = true;
      const res = await post(form.dataset.url, { message: msg });
      btn.disabled = false;
      typing.parentElement.remove();
      if (res.ok) addBubble(list, 'model', res.reply);
      else addBubble(list, 'model', '⚠️ ' + (res.error || 'Something went wrong.'));
    });
  }


  /* ---------- Theme ---------- */
  function applyThemeUi() {
    const dark = document.documentElement.classList.contains('dark');
    document.querySelectorAll('[data-theme-icon]').forEach(i => { i.textContent = dark ? 'light_mode' : 'dark_mode'; });
    document.querySelectorAll('[data-theme-label]').forEach(i => { i.textContent = dark ? 'Light mode' : 'Dark mode'; });
  }
  document.addEventListener('click', (e) => {
    if (!e.target.closest('[data-theme-toggle]')) return;
    const dark = document.documentElement.classList.toggle('dark');
    try { localStorage.setItem('ciq-theme', dark ? 'dark' : 'light'); } catch (err) { /* private mode */ }
    applyThemeUi();
  });

  /* ---------- Avatar menu ---------- */
  document.addEventListener('click', (e) => {
    const toggle = e.target.closest('[data-menu-toggle]');
    document.querySelectorAll('[data-menu-root]').forEach(root => {
      const menu = root.querySelector('[data-menu]');
      const btn = root.querySelector('[data-menu-toggle]');
      if (toggle && root.contains(toggle)) {
        const open = menu.classList.toggle('hidden') === false;
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      } else if (!menu.contains(e.target) || e.target.closest('a,button[data-theme-toggle],button[data-feedback-open],button[data-avatar-remove]')) {
        menu.classList.add('hidden');
        btn.setAttribute('aria-expanded', 'false');
      }
    });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    document.querySelectorAll('[data-menu]').forEach(m => m.classList.add('hidden'));
    closeAssistant(); closeSheet();
  });

  function setAvatar(url) {
    document.querySelectorAll('[data-avatar-wrap]').forEach(w => {
      const img = w.querySelector('[data-avatar-img]'), ini = w.querySelector('[data-avatar-initial]');
      if (url) { img.src = url + '?t=' + Date.now(); img.classList.remove('hidden'); ini.classList.add('hidden'); }
      else { img.removeAttribute('src'); img.classList.add('hidden'); ini.classList.remove('hidden'); }
    });
    document.querySelectorAll('[data-avatar-remove]').forEach(b => b.classList.toggle('hidden', !url));
  }
  document.addEventListener('change', async (e) => {
    const input = e.target.closest('[data-avatar-input]');
    if (!input || !input.files.length) return;
    const file = input.files[0];
    if (file.size > 5 * 1024 * 1024) { toast('Image is larger than 5 MB.'); input.value = ''; return; }
    const fd = new FormData(); fd.append('avatar', file);
    toast('Uploading…', 8000);
    const res = await post(input.dataset.url, fd);
    input.value = '';
    if (res.ok) { setAvatar(res.url); toast('Avatar updated.'); } else toast(res.error || 'Upload failed.');
  });
  document.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-avatar-remove]');
    if (!b) return;
    const res = await post(b.dataset.url, {});
    if (res.ok) { setAvatar(''); toast('Avatar removed.'); } else toast(res.error || 'Could not remove avatar.');
  });

  /* ---------- Mobile tools sheet ---------- */
  function closeSheet() { const s = document.getElementById('sheet'); if (s) s.classList.add('hidden'); }
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-sheet-open]')) document.getElementById('sheet').classList.remove('hidden');
    if (e.target.closest('[data-sheet-close]')) closeSheet();
  });

  /* ---------- Assistant ---------- */
  const assistant = { history: [] };
  function closeAssistant() { const a = document.getElementById('assistant'); if (a) a.classList.add('hidden'); }
  function openAssistant(prefill) {
    const a = document.getElementById('assistant'); if (!a) return;
    a.classList.remove('hidden');
    const ta = a.querySelector('textarea'); ta.focus();
    if (prefill) { ta.value = prefill; }
  }
  async function assistantSend(msg) {
    const a = document.getElementById('assistant'), list = document.getElementById('assistant-list');
    const intro = a.querySelector('[data-assistant-intro]'); if (intro) intro.remove();
    addBubble(list, 'user', msg);
    const typing = addBubble(list, 'model', '…'); typing.classList.add('animate-pulse');
    const res = await post(a.dataset.url, { message: msg, history: JSON.stringify(assistant.history), page: location.pathname });
    typing.parentElement.remove();
    if (res.ok) {
      addBubble(list, 'model', res.reply);
      assistant.history.push({ role: 'user', text: msg }, { role: 'model', text: res.reply });
      assistant.history = assistant.history.slice(-8);
    } else addBubble(list, 'model', '⚠️ ' + (res.error || 'Something went wrong.'));
  }
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-assistant-open]')) openAssistant();
    if (e.target.closest('[data-assistant-close]')) closeAssistant();
    const q = e.target.closest('[data-assistant-quick]');
    if (q) assistantSend(q.dataset.assistantQuick);
    if (e.target.closest('[data-assistant-clear]')) {
      assistant.history = [];
      document.getElementById('assistant-list').innerHTML = '<div class="rounded-2xl rounded-bl-md bg-surface-container-low px-4 py-3 font-body-sm text-body-sm">New chat. What would you like to do?</div>';
    }
    const ask = e.target.closest('[data-ask-assistant]');
    if (ask) { e.preventDefault(); openAssistant(ask.dataset.askAssistant); }
  });
  document.addEventListener('submit', (e) => {
    if (e.target.id !== 'assistant-form') return;
    e.preventDefault();
    const ta = e.target.querySelector('textarea'), msg = ta.value.trim();
    if (!msg) return;
    ta.value = ''; ta.style.height = 'auto';
    assistantSend(msg);
  });
  document.addEventListener('keydown', (e) => {
    if (e.target.matches('#assistant-form textarea') && e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault(); document.getElementById('assistant-form').requestSubmit();
    }
  });
  document.addEventListener('input', (e) => {
    if (e.target.matches('#assistant-form textarea')) { e.target.style.height = 'auto'; e.target.style.height = Math.min(e.target.scrollHeight, 128) + 'px'; }
  });

  /* ---------- Feedback dialog ---------- */
  document.addEventListener('click', (e) => {
    const d = document.getElementById('feedback');
    if (!d) return;
    if (e.target.closest('[data-feedback-open]')) d.showModal();
    if (e.target.closest('[data-feedback-close]')) d.close();
  });
  document.addEventListener('submit', async (e) => {
    if (e.target.id !== 'feedback-form') return;
    e.preventDefault();
    const f = e.target;
    const res = await post(f.dataset.url, { message: f.message.value, page: location.pathname });
    if (res.ok) { f.message.value = ''; document.getElementById('feedback').close(); toast('Thanks for the feedback!'); }
    else toast(res.error || 'Could not send feedback.');
  });

  document.addEventListener('DOMContentLoaded', () => {
    applyThemeUi();
    renderAll();
    document.querySelectorAll('[data-chat]').forEach(initChat);
    const flash = document.getElementById('flash');
    if (flash) setTimeout(() => flash.remove(), 7000);
  });

  window.CampusIQ = { toast, post, renderAll, renderMarkdown, typeset, addBubble, setBusy };
})();
