(function() {
  const STORAGE_KEY = 'sf_assistant_thread';

  function getThread() {
    try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '[]'); } catch { return []; }
  }
  function setThread(msgs) {
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(msgs)); } catch {}
  }

  function createEl(tag, cls) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    return el;
  }

  function findSFID() {
    // Best-effort detection across pages
    const meta = document.querySelector('meta[name="sf-sfid"]');
    if (meta && meta.content) return meta.content.trim();
    const ds = document.querySelector('[data-sfid]');
    if (ds && ds.getAttribute('data-sfid')) return ds.getAttribute('data-sfid');
    const input = document.querySelector('input[name="sfid"], #sfid');
    if (input && input.value) return input.value.trim();
    return '';
  }

  function openModal() {
    document.getElementById('assistant-modal')?.classList.remove('hidden');
    document.getElementById('assistant-input')?.focus();
  }
  function closeModal() {
    document.getElementById('assistant-modal')?.classList.add('hidden');
  }

  function renderMessages() {
    const wrap = document.getElementById('assistant-messages');
    if (!wrap) return;
    wrap.innerHTML = '';
    const msgs = getThread();
    msgs.forEach((m) => appendMessage(m.role, m.content, m.citations));
    wrap.scrollTop = wrap.scrollHeight;
  }

  function appendMessage(role, content, citations) {
    const wrap = document.getElementById('assistant-messages');
    if (!wrap) return;
    const item = createEl('div', 'mb-3');
    const bubble = createEl('div', role === 'user' ? 'bg-blue-50 text-blue-900 p-3 rounded-lg' : 'bg-gray-100 text-gray-900 p-3 rounded-lg');
    bubble.textContent = content;
    item.appendChild(bubble);

    if (citations && citations.length) {
      const cite = createEl('div', 'mt-1 text-xs text-gray-500');
      cite.textContent = 'Sources: ' + citations.map(c => `${c.source}${c.heading ? ' §' + c.heading : ''}`).join(' · ');
      item.appendChild(cite);
    }

    wrap.appendChild(item);
    wrap.scrollTop = wrap.scrollHeight;
  }

  async function sendMessage() {
    const ta = document.getElementById('assistant-input');
    if (!ta) return;
    const text = (ta.value || '').trim();
    if (!text) return;

    const thread = getThread();
    thread.push({ role: 'user', content: text });
    setThread(thread);
    appendMessage('user', text);
    ta.value = '';

    const ctx = {
      route: window.location.pathname,
      sfid: findSFID(),
      page_title: document.title || ''
    };

    const payload = { messages: thread, context: ctx };
    const btn = document.getElementById('assistant-send');
    if (btn) { btn.disabled = true; btn.classList.add('opacity-50'); }

    try {
      const res = await fetch('/api/assistant/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      const reply = data && data.reply ? String(data.reply) : 'Sorry, an error occurred.';
      const citations = Array.isArray(data && data.citations) ? data.citations : [];
      appendMessage('assistant', reply, citations);
      const updated = getThread();
      updated.push({ role: 'assistant', content: reply, citations });
      setThread(updated);
    } catch (e) {
      appendMessage('assistant', 'Error: ' + (e && e.message ? e.message : 'request failed'));
    } finally {
      if (btn) { btn.disabled = false; btn.classList.remove('opacity-50'); }
    }
  }

  function bindEvents() {
    const openBtn = document.getElementById('assistant-open');
    const closeBtn = document.getElementById('assistant-close');
    const sendBtn = document.getElementById('assistant-send');
    const input = document.getElementById('assistant-input');

    openBtn && openBtn.addEventListener('click', openModal);
    closeBtn && closeBtn.addEventListener('click', closeModal);
    sendBtn && sendBtn.addEventListener('click', sendMessage);
    input && input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        sendMessage();
      }
    });

    // Render existing messages for this session
    renderMessages();
  }

  document.addEventListener('DOMContentLoaded', bindEvents);
})();
