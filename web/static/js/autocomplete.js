/*
 * Entity Autocomplete Component
 * - Reusable, no dependencies (uses Tailwind classes for styling)
 * - Queries /api/entities/search?q=&type=&limit=
 * - Auto-initializes for inputs named "sfid" and "l_sfid" (and ids as fallback)
 * - Supports keyboard navigation and mouse interactions
 * - Accessible: proper roles and ARIA attributes
 */
(function(){
  'use strict';

  const DEFAULT_LIMIT = 10;
  const MIN_CHARS = 1;

  function debounce(fn, wait){
    let t = null;
    return function(...args){
      clearTimeout(t);
      t = setTimeout(()=>fn.apply(this, args), wait);
    };
  }

  function createEl(tag, cls, attrs){
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (attrs){
      for (const [k,v] of Object.entries(attrs)){
        if (k === 'text') el.textContent = v;
        else if (k === 'html') el.innerHTML = v;
        else el.setAttribute(k, v);
      }
    }
    return el;
  }

  function getEntityTypeFor(input, opts){
    if (opts && opts.type) return String(opts.type);
    const ds = input.dataset || {};
    if (ds.entityType) return String(ds.entityType);
    const name = (input.getAttribute('name') || '').toLowerCase();
    const pattern = input.getAttribute('pattern') || '';
    if (name === 'l_sfid' || /\bl_\.\*\b/.test(pattern)) return 'l';
    // If user typed an obvious prefix like "p_" or "l_", hint the backend
    const v = (input.value || '').trim();
    const m = v.match(/^([a-z])_/i);
    if (m) return m[1].toLowerCase();
    return '';
  }

  function wrapInput(input){
    // If already wrapped in a relative container (e.g., QR enhancer), reuse it
    const parent = input.parentElement;
    if (parent && parent.classList && parent.classList.contains('relative')){
      return parent;
    }
    const wrapper = createEl('div', 'relative');
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);
    return wrapper;
  }

  function attachEntityAutocomplete(input, options){
    if (!input || input.dataset.acEnhanced === '1') return;
    input.dataset.acEnhanced = '1';

    // Tokenization mode: support comma-separated multi-values when requested
    const tokenMode = (input.dataset.acTokenized || '').toLowerCase();
    const isCommaTokenized = tokenMode === 'comma';

    const wrapper = wrapInput(input);
    const list = createEl('div', 'absolute z-50 mt-1 w-full bg-white border border-gray-200 rounded-md shadow-lg max-h-64 overflow-auto hidden', {
      role: 'listbox',
      id: `${input.id || input.name || 'sfid'}-ac-list`
    });
    wrapper.appendChild(list);

    let items = [];
    let activeIndex = -1;
    let lastController = null;

    function hide(){
      list.classList.add('hidden');
      activeIndex = -1;
    }
    function show(){
      if (!items.length){ hide(); return; }
      list.classList.remove('hidden');
    }
    function clear(){
      list.innerHTML = '';
      items = [];
      activeIndex = -1;
    }
    function render(results){
      clear();
      results.forEach((r, idx) => {
        const btn = createEl('div', 'px-3 py-2 cursor-pointer hover:bg-blue-50', {
          role: 'option',
          'data-sfid': r.sfid
        });
        btn.innerHTML = `<div class="flex flex-col">
          <span class="font-mono text-sm text-gray-900">${escapeHtml(r.sfid)}</span>
          ${r.name ? `<span class="text-xs text-gray-500 truncate">${escapeHtml(r.name)}</span>` : ''}
        </div>`;
        btn.addEventListener('mousedown', (e)=>{ e.preventDefault(); commit(r); });
        btn.addEventListener('mouseover', ()=>{ setActive(idx); });
        list.appendChild(btn);
        items.push({ el: btn, data: r });
      });
      show();
    }
    function setActive(idx){
      if (idx < -1 || idx >= items.length) return;
      if (activeIndex >= 0 && activeIndex < items.length){
        items[activeIndex].el.classList.remove('bg-blue-50');
      }
      activeIndex = idx;
      if (activeIndex >= 0){
        items[activeIndex].el.classList.add('bg-blue-50');
        ensureVisible(items[activeIndex].el, list);
      }
    }
    function ensureVisible(child, container){
      const cTop = container.scrollTop;
      const cBot = cTop + container.clientHeight;
      const eTop = child.offsetTop;
      const eBot = eTop + child.offsetHeight;
      if (eTop < cTop) container.scrollTop = eTop;
      else if (eBot > cBot) container.scrollTop = eBot - container.clientHeight;
    }
    function commit(data){
      const sfid = data.sfid || '';
      if (isCommaTokenized){
        const raw = String(input.value || '');
        let parts = raw.split(',');
        if (parts.length === 0) parts = [''];
        // Replace the last token (after last comma) with selected sfid
        parts[parts.length - 1] = sfid;
        // Normalize: trim tokens, drop empties, join with ", "
        const normalized = parts
          .map(s => String(s || '').trim())
          .filter(Boolean)
          .join(', ');
        input.value = normalized;
      } else {
        input.value = sfid;
      }
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      hide();
      flashInput(input);
    }

    const doSearch = debounce(async function(){
      try {
        const raw = String(input.value || '');
        const q = (isCommaTokenized ? raw.split(',').pop() : raw).trim();
        if (q.length < MIN_CHARS){ clear(); hide(); return; }
        // Cancel prior request
        if (lastController) lastController.abort();
        lastController = new AbortController();
        let type = getEntityTypeFor(input, options);
        // If current token looks like "p_"/"l_" etc, prefer that as type hint
        const m = q.match(/^([a-z])_/i);
        if (m) type = m[1].toLowerCase();
        const params = new URLSearchParams();
        params.set('q', q);
        params.set('limit', String((options && options.limit) || DEFAULT_LIMIT));
        if (type) params.set('type', type);
        const res = await fetch(`/api/entities/search?${params.toString()}`, { signal: lastController.signal });
        if (!res.ok){ throw new Error(`HTTP ${res.status}`); }
        const data = await res.json();
        const arr = (data && data.results) || [];
        render(arr);
      } catch (e){
        if (e && e.name === 'AbortError') return;
        // Silently ignore network errors to avoid noisy UX
        clear(); hide();
        // Optionally show a toast once when typing starts; skipping to keep quiet
      }
    }, 200);

    input.setAttribute('autocomplete', 'off');

    input.addEventListener('input', doSearch);
    input.addEventListener('keydown', (e)=>{
      if (list.classList.contains('hidden')) return;
      if (e.key === 'ArrowDown'){
        e.preventDefault();
        setActive(Math.min(items.length - 1, activeIndex + 1));
      } else if (e.key === 'ArrowUp'){
        e.preventDefault();
        setActive(Math.max(-1, activeIndex - 1));
      } else if (e.key === 'Enter'){
        if (activeIndex >= 0){ e.preventDefault(); commit(items[activeIndex].data); }
      } else if (e.key === 'Escape'){
        hide();
      }
    });
    input.addEventListener('blur', ()=>{ setTimeout(hide, 120); });

    // Utilities
    function flashInput(el){
      try {
        el.classList.add('ring-2', 'ring-blue-400');
        setTimeout(()=> el.classList.remove('ring-2', 'ring-blue-400'), 800);
      } catch(_){}
    }
    function escapeHtml(s){
      return String(s || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }
  }

  function enhanceAll(){
    const selector = [
      'input[name="sfid"]',
      'input#sfid',
      'input[name="l_sfid"]',
      'input#l_sfid',
      'input[data-autocomplete="entity"]'
    ].join(',');
    document.querySelectorAll(selector).forEach((el)=> attachEntityAutocomplete(el, {}));

    // Observe DOM mutations for dynamically added inputs
    const obs = new MutationObserver((mutations)=>{
      mutations.forEach((m)=>{
        m.addedNodes && m.addedNodes.forEach((node)=>{
          if (!(node instanceof HTMLElement)) return;
          if (node.matches && node.matches(selector)) attachEntityAutocomplete(node, {});
          if (node.querySelectorAll){
            node.querySelectorAll(selector).forEach((el)=> attachEntityAutocomplete(el, {}));
          }
        });
      });
    });
    obs.observe(document.body, { childList: true, subtree: true });
  }

  // Public API
  window.EntityAutocomplete = {
    attach: attachEntityAutocomplete,
    enhanceAll: enhanceAll
  };

  document.addEventListener('DOMContentLoaded', enhanceAll);
})();
