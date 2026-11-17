/* 쪽 안의 아이콘·도표를 그린다. 브라우저에서 실행되고, 끝나면 window.__ready = true. */
(function () {
  // ── 아이콘(24×24, 선) ────────────────────────────────────────────────
  const I = {
    calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M8 3v4M16 3v4M3.5 10h17"/>',
    map: '<path d="M3 6.5l6-2.5 6 2.5 6-2.5v13.5l-6 2.5-6-2.5-6 2.5z"/><path d="M9 4v13.5M15 6.5V20"/>',
    megaphone: '<path d="M4 10v4h3l6 4V6l-6 4H4z"/><path d="M16.5 9a4 4 0 0 1 0 6M19 6.5a8 8 0 0 1 0 11"/>',
    users: '<circle cx="9" cy="8" r="3.2"/><path d="M3 19.5c0-3.2 2.7-5.3 6-5.3s6 2.1 6 5.3"/><path d="M15.5 5.2a3 3 0 0 1 0 5.6M17.5 14.6c2.2.5 3.5 2.2 3.5 4.9"/>',
    book: '<path d="M5 4.5h10a3 3 0 0 1 3 3V20H8a3 3 0 0 1-3-3V4.5z"/><path d="M5 17a3 3 0 0 1 3-3h10"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M9 21h6"/>',
    chat: '<path d="M4 5h16v11H10l-5 4v-4H4V5z"/><path d="M8 9.5h8M8 12.5h5"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    pencil: '<path d="M4 20l1.2-4.5L16.5 4.2l3.3 3.3L8.5 18.8 4 20z"/><path d="M14.2 6.5l3.3 3.3"/>',
    door: '<path d="M6.5 21V4h11v17M4 21h16"/><path d="M14 12.5v.01"/>',
    camera: '<path d="M4 8h3.2l1.6-2.2h6.4L16.8 8H20v11H4V8z"/><circle cx="12" cy="13.2" r="3.2"/>',
    target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".8"/>',
    flag: '<path d="M5.5 21V4M5.5 5h12l-2.2 3.5 2.2 3.5h-12"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.6-4.6"/>',
    doc: '<path d="M7 3.5h7l4 4V20.5H7V3.5z"/><path d="M14 3.5v4h4M10 12h5M10 16h5"/>',
    board: '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20.5l4-4.5 4 4.5M7 8.5h6M7 11.5h3.5"/>',
    blocks: '<rect x="4" y="12" width="7.5" height="7.5" rx="1"/><rect x="12.5" y="12" width="7.5" height="7.5" rx="1"/><rect x="8.2" y="4" width="7.5" height="7.5" rx="1"/>',
    school: '<path d="M3 21h18M5 21V9.5L12 4.5l7 5V21M10 21v-5h4v5"/><path d="M12 10.5v.01"/>',
    bulb: '<path d="M9.5 18h5M10.5 21h3M12 3a6 6 0 0 0-4 10.5c.8.8 1.2 1.6 1.2 2.5h5.6c0-.9.4-1.7 1.2-2.5A6 6 0 0 0 12 3z"/>',
    shield: '<path d="M12 3l7 3v6c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6l7-3z"/><path d="M9 12l2.2 2.2L15 10.4"/>',
    watch: '<rect x="7" y="7" width="10" height="10" rx="3"/><path d="M9.2 7l.6-3.5h4.4l.6 3.5M9.2 17l.6 3.5h4.4l.6-3.5M12 10v2.2l1.4 1"/>',
    bottle: '<path d="M10 3h4v3l2 3v11a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V9l2-3V3z"/><path d="M8 13h8"/>',
    idcard: '<rect x="3" y="6" width="18" height="13" rx="2"/><circle cx="8.5" cy="11.5" r="2"/><path d="M13 11h5M13 14.5h5M5.8 16c.6-1.6 4.8-1.6 5.4 0"/>',
    timer: '<circle cx="12" cy="13.5" r="7.5"/><path d="M12 9.5v4M9.5 2.5h5M12 2.5V6"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    eye: '<path d="M2.5 12s3.8-6.5 9.5-6.5S21.5 12 21.5 12s-3.8 6.5-9.5 6.5S2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
    layers: '<path d="M12 4l9 4.5-9 4.5-9-4.5L12 4z"/><path d="M3 13l9 4.5 9-4.5"/>',
    compass: '<circle cx="12" cy="12" r="8.5"/><path d="M15.5 8.5l-2 5-5 2 2-5 5-2z"/>',
    building: '<path d="M4 21V6.5L12 3l8 3.5V21M3 21h18M10 21v-4h4v4"/><path d="M8 10h.01M12 10h.01M16 10h.01M8 13.5h.01M12 13.5h.01M16 13.5h.01"/>',
    phone: '<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 18h2"/>',
    home: '<path d="M4 11l8-7 8 7M6 10v10h12V10M10 20v-5.5h4V20"/>',
    repeat: '<path d="M4.5 11a7.5 7.5 0 0 1 13.2-4.2L20 9M20 4v5h-5M19.5 13a7.5 7.5 0 0 1-13.2 4.2L4 15M4 20v-5h5"/>',
    alert: '<path d="M12 4l9 16H3L12 4z"/><path d="M12 10v5M12 17.4v.01"/>',
    ban: '<circle cx="12" cy="12" r="8.5"/><path d="M6 6l12 12"/>',
    smile: '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 14.3c1 1.2 2.2 1.8 3.5 1.8s2.5-.6 3.5-1.8M9 10v.01M15 10v.01"/>',
    list: '<path d="M8.5 6h11.5M8.5 12h11.5M8.5 18h11.5M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
    play: '<path d="M7.5 5l11.5 7-11.5 7V5z"/>',
    grid: '<rect x="4" y="4" width="16" height="16" rx="1.5"/><path d="M9.3 4v16M14.7 4v16M4 9.3h16M4 14.7h16"/>',
    plane: '<path d="M3 11l18-7-7 18-3-8-8-3z"/><path d="M11 14l4-4"/>',
    wide: '<path d="M3 7.5l5 2h8l5-2v9l-5-2H8l-5 2v-9z"/>',
    tripod: '<rect x="8.5" y="3.5" width="7" height="5" rx="1"/><path d="M12 8.5V13M12 13l-5 8M12 13l5 8M12 13v8"/>',
    sparkle: '<path d="M12 3l1.9 5.3L19 10l-5.1 1.7L12 17l-1.9-5.3L5 10l5.1-1.7L12 3z"/><path d="M19 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8z"/>',
    church: '<path d="M12 2.5v4M10.2 4.2h3.6M7 21V11.5l5-4 5 4V21M4 21h16M10 21v-4a2 2 0 0 1 4 0v4"/>',
    cap: '<path d="M2 9l10-5 10 5-10 5L2 9z"/><path d="M6 11.2V16c1.6 1.6 3.6 2.4 6 2.4s4.4-.8 6-2.4v-4.8M22 9v6"/>',
    scale: '<path d="M12 4v16M7.5 20h9M5 7h14"/><path d="M5 7l-2.8 6a2.9 2.9 0 0 0 5.6 0L5 7zM19 7l-2.8 6a2.9 2.9 0 0 0 5.6 0L19 7z"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3A4 4 0 0 0 13 5.3l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 18.7l1-1"/>',
    gift: '<path d="M4.5 10.5h15V20h-15zM3 7h18v3.5H3zM12 7v13"/><path d="M12 7c-1.6-3.2-5.2-3-5.200-1 0 1.600 2.600 1.300 5.200 1zM12 7c1.6-3.2 5.2-3 5.2-1 0 1.600-2.600 1.300-5.200 1z"/>',
    ruler: '<path d="M4 15.500L15.500 4l4.500 4.500L8.500 20 4 15.500z"/><path d="M8 11.500l1.800 1.800M10.800 8.700l1.800 1.800M13.600 5.900l1.800 1.800"/>',
    hand: '<path d="M8 12V5.500a1.500 1.500 0 0 1 3 0V11M11 10.500V4.500a1.500 1.500 0 0 1 3 0v6M14 11V6a1.500 1.500 0 0 1 3 0v8c0 4-2.500 7-6 7-2.600 0-4.200-1.200-5.500-3.500L3.500 14a1.400 1.400 0 0 1 2.400-1.500L8 15"/>',
    heart: '<path d="M12 20s-7.500-4.600-7.500-10.200A4.300 4.300 0 0 1 12 7.200a4.300 4.300 0 0 1 7.500 2.600C19.500 15.400 12 20 12 20z"/>',
    star: '<path d="M12 3.500l2.600 5.400 5.900.8-4.300 4.100 1 5.900L12 16.900l-5.200 2.800 1-5.900-4.300-4.100 5.900-.8L12 3.500z"/>',
    pin: '<path d="M12 21s-6.500-5.600-6.500-11a6.500 6.500 0 0 1 13 0c0 5.400-6.500 11-6.500 11z"/><circle cx="12" cy="10" r="2.400"/>',
    mail: '<rect x="3" y="5.500" width="18" height="13" rx="2"/><path d="M3.500 7l8.500 6 8.500-6"/>',
    coffee: '<path d="M5 9h11v5a5 5 0 0 1-5 5h-1a5 5 0 0 1-5-5V9zM16 10.500h1.500a2.500 2.500 0 0 1 0 5H16M8 3.500v2.500M11.500 3.500v2.500"/>',
    shirt: '<path d="M8 4l-5 3 2 3.500 2-1V20h10V9.500l2 1L21 7l-5-3a4 4 0 0 1-8 0z"/>',
    puzzle: '<path d="M4 8h4a2 2 0 1 1 4 0h4v4a2 2 0 1 1 0 4v4H4V8z"/>',
    talk: '<path d="M3.500 5h11v8h-6l-3 2.500V13h-2V5z"/><path d="M14.500 9h6v8h-2v2.500L15.500 17h-4"/>',
    scissors: '<circle cx="6.500" cy="7" r="2.500"/><circle cx="6.500" cy="17" r="2.500"/><path d="M8.600 8.500L20 18M8.600 15.500L20 6"/>',
  };
  document.querySelectorAll('i[data-i]').forEach(el => {
    const name = el.getAttribute('data-i');
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('class', 'i ' + (el.className || ''));
    svg.innerHTML = I[name] || '<circle cx="12" cy="12" r="8"/>';
    el.replaceWith(svg);
  });

  const D = window.GUIDE_DATA || {};
  const h = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

  // ── 배점 누적 막대(100점 만점) ─────────────────────────────────────────
  const CAT = { iv: ['심층면접', 'var(--s1)', '#fff'], demo: ['수업실연', 'var(--s2)', '#fff'], ref: ['성찰·설계·수업면접', 'var(--s4)', 'var(--ink)'],
                doc: ['과정안', 'var(--s3)', '#fff'], en: ['영어(실연+면접)', 'var(--s5)', 'var(--ink)'] };
  document.querySelectorAll('[data-chart="stack"]').forEach(el => {
    const rows = D[el.dataset.key] || [];
    rows.forEach(r => {
      if (r.g) { el.appendChild(h('div', 'sk-group', r.g)); return; }
      const row = h('div', 'sk-row' + (r.pending ? ' pending' : ''));
      const name = h('div', 'sk-name', `<i class="ty ty-${r.t}"></i><b>${r.n}</b>` + (r.mark ? `<sup>${r.mark}</sup>` : ''));
      const bar = h('div', 'sk-bar');
      if (r.pending) bar.appendChild(h('div', 'sk-pending', r.pending));
      else r.s.forEach(([c, v, lab]) => {
        const seg = h('div', 'sk-seg', `<span>${lab || v}</span>`);
        seg.style.flex = `${v} 0 0`; seg.style.background = CAT[c][1]; seg.style.color = CAT[c][2];
        seg.title = CAT[c][0];
        bar.appendChild(seg);
      });
      row.append(name, bar, h('div', 'sk-note', r.note || ''));
      el.appendChild(row);
    });
  });

  // ── 17개 시·도 타일 지도 ───────────────────────────────────────────────
  document.querySelectorAll('[data-chart="tiles"]').forEach(el => {
    const cfg = D[el.dataset.key]; if (!cfg) return;
    cfg.tiles.forEach(([n, c, r]) => {
      const t = cfg.type[n] || 'k';
      const extra = cfg.badge && cfg.badge[n] ? `<br><u>${cfg.badge[n]}</u>` : '';
      const tile = h('div', 'tile tile-' + t, `<b>${n}</b><span>${cfg.label[t]}${extra}</span>`);
      tile.style.gridColumn = c + 1; tile.style.gridRow = r + 1;
      el.appendChild(tile);
    });
  });

  // ── 60일 달력(2026.11.7 ~ 2027.1.8) ────────────────────────────────────
  document.querySelectorAll('[data-chart="cal60"]').forEach(el => {
    const key = { '2026-11-07': 1, '2026-12-09': 1, '2027-01-06': 1, '2027-01-07': 1, '2027-01-08': 1 };
    const hol = { '2026-12-25': 1, '2027-01-01': 1 };
    el.appendChild(h('div', 'wk', ''));
    ['월', '화', '수', '목', '금', '토', '일'].forEach(d => el.appendChild(h('div', 'dow', d)));
    const start = new Date(Date.UTC(2026, 10, 2)), end = new Date(Date.UTC(2027, 0, 10));
    const a0 = Date.UTC(2026, 10, 7), b0 = Date.UTC(2026, 11, 9), z = Date.UTC(2027, 0, 8);
    let lastMonth = -1;
    for (let t = new Date(start); t <= end; t = new Date(t.getTime() + 864e5)) {
      const iso = t.toISOString().slice(0, 10), ms = t.getTime();
      if (t.getUTCDay() === 1) {
        const sun = new Date(ms + 6 * 864e5);
        const m = sun.getUTCMonth();
        el.appendChild(h('div', 'wk', m !== lastMonth ? (m + 1) + '월' : ''));
        lastMonth = m;
      }
      let cls = 'd';
      if (ms < a0 || ms > z) cls += ' off';
      else if (key[iso]) cls += ' key';
      else if (ms < b0) cls += ' a';
      else cls += ' b';
      if (hol[iso]) cls += ' hol';
      el.appendChild(h('div', cls, String(t.getUTCDate())));
    }
  });

  // ── 쪽 번호 ────────────────────────────────────────────────────────────
  document.querySelectorAll('.page').forEach((p, i) => { const pn = p.querySelector('.pn'); if (pn) pn.textContent = String(i + 1).padStart(2, '0'); });

  (document.fonts ? document.fonts.ready : Promise.resolve()).then(() => { window.__ready = true; });
})();
