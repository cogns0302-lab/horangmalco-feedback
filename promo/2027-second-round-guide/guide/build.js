/* 가이드 조립 → PDF.
   node guide/build.js            PDF와 쪽별 미리보기(PNG)를 만든다
   node guide/build.js --no-png   PDF만
   pages/*.html 의 "<!--page ...-->" 한 줄이 한 쪽의 시작이다. */
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const DIR = __dirname;
const ROOT = path.join(DIR, '..');
const OUT = path.join(ROOT, 'dist');
const BUILD = path.join(DIR, 'build');
const PDF_NAME = '27학년도_유초등_임용2차_가이드.pdf';
const BRAND = '2027학년도 유·초등 임용 2차 가이드';

function attrs(s) {
  const o = {};
  s.replace(/([\w-]+)="([^"]*)"/g, (_, k, v) => { o[k] = v; return ''; });
  return o;
}

function assemble() {
  const files = fs.readdirSync(path.join(DIR, 'pages')).filter(f => f.endsWith('.html')).sort();
  const pages = [];
  for (const f of files) {
    const src = fs.readFileSync(path.join(DIR, 'pages', f), 'utf8');
    const parts = src.split(/<!--page\s+([\s\S]*?)-->/);
    for (let i = 1; i < parts.length; i += 2) pages.push({ a: attrs(parts[i]), body: parts[i + 1].trim(), file: f });
  }
  const html = pages.map(({ a, body }) => {
    if (a.raw) return `<section class="page ${a.class || ''}">${body}</section>`;
    const lead = a.lead ? `<p>${a.lead}</p>` : '';
    return `<section class="page ${a.class || ''}">
  <header class="hd"><span class="part"><i>${a.part || ''}</i>${a.name || ''}</span><span class="brand">${BRAND}</span></header>
  <div class="ttl"><span class="no">${a.no || ''}</span><div><h1>${a.title || ''}</h1>${lead}</div></div>
  <div class="body">${body}</div>
  <footer class="ft"><span class="src">${a.src || ''}</span><span class="pn"></span></footer>
</section>`;
  }).join('\n');
  const doc = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>${BRAND}</title>
<link rel="stylesheet" href="../styles.css"><link rel="stylesheet" href="../charts.css"></head><body>
${html}
<script src="../data.js"></script><script src="../lib.js"></script></body></html>`;
  fs.mkdirSync(BUILD, { recursive: true });
  fs.writeFileSync(path.join(BUILD, 'guide.html'), doc);
  return pages.length;
}

(async () => {
  const n = assemble();
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 794, height: 1123 }, deviceScaleFactor: 2 });
  page.on('console', m => { if (m.type() === 'error') console.log('  [console]', m.text()); });
  page.on('pageerror', e => console.log('  [pageerror]', String(e)));
  await page.goto('file://' + path.join(BUILD, 'guide.html'));
  await page.waitForFunction('window.__ready === true', null, { timeout: 30000 });

  // 넘침 검사: 본문이 쪽 밖으로 나가거나, 본문 안에서 잘리는 요소를 찾는다
  const issues = await page.evaluate(() => {
    const out = [];
    document.querySelectorAll('.page').forEach((p, i) => {
      const pr = p.getBoundingClientRect();
      const body = p.querySelector('.body');
      const ft = p.querySelector('.ft');
      const limit = ft ? ft.getBoundingClientRect().top : pr.bottom;
      let worst = 0, who = '';
      (body || p).querySelectorAll('*').forEach(el => {
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) return;
        if (el.closest('.cv-stripe, .cv-dots, [data-deco]')) return;
        const over = Math.max(r.bottom - limit, r.right - pr.right + 1, pr.left - r.left);
        if (over > worst + 0.5) { worst = over; who = (el.className && String(el.className).slice(0, 30)) || el.tagName; }
      });
      const used = body ? Math.round(100 * (Math.max(...[...body.children].map(c => c.getBoundingClientRect().bottom), body.getBoundingClientRect().top) - body.getBoundingClientRect().top) / body.getBoundingClientRect().height) : null;
      // 글자가 상자 밖으로 넘치는지(가로)
      let clip = '';
      p.querySelectorAll('.sk-seg span, .tile b, .tb > span, .chip').forEach(el => {
        if (el.scrollWidth > el.clientWidth + 1 && !clip) clip = (el.textContent || '').slice(0, 14);
      });
      if (worst > 1 || clip || (used != null && used < 82)) out.push({ page: i + 1, overflowPx: Math.round(worst), who, clip, fillPct: used });
    });
    return out;
  });
  console.log(`쪽 수: ${n}`);
  if (issues.length) { console.log('점검 필요:'); issues.forEach(x => console.log('  ', JSON.stringify(x))); } else console.log('넘침 없음');

  await page.pdf({ path: path.join(OUT, PDF_NAME), width: '210mm', height: '297mm', printBackground: true, preferCSSPageSize: true });
  console.log('PDF →', path.join('dist', PDF_NAME), Math.round(fs.statSync(path.join(OUT, PDF_NAME)).size / 1024) + 'KB');

  if (!process.argv.includes('--no-png')) {
    const only = (process.argv.find(a => a.startsWith('--pages=')) || '').slice(8).split(',').filter(Boolean).map(Number);
    const dir = path.join(BUILD, 'png'); fs.mkdirSync(dir, { recursive: true });
    const els = await page.$$('.page');
    for (let i = 0; i < els.length; i++) {
      if (only.length && !only.includes(i + 1)) continue;
      await els[i].screenshot({ path: path.join(dir, `p${String(i + 1).padStart(2, '0')}.png`) });
    }
  }
  await browser.close();
})();
