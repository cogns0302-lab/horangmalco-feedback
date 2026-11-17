/* 캐러셀 렌더: 정지 이미지(PNG 1080×1350)와 모션 영상(MP4 30fps).
   node carousel/render.js                 전부
   node carousel/render.js --stills        정지 이미지만
   node carousel/render.js --only=1,4      일부 슬라이드만
   모든 움직임은 CSS 애니메이션이라, 애니메이션 시간을 프레임마다 직접 옮겨 가며 찍는다(실시간 녹화 아님). */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { chromium } = require('playwright');

const DIR = __dirname;
const OUT = path.join(DIR, '..', 'dist', 'carousel');
const TMP = path.join(DIR, 'build');
const FPS = 30;
const W = 1080, H = 1350;
const args = process.argv.slice(2);
const stillsOnly = args.includes('--stills');
const only = (args.find(a => a.startsWith('--only=')) || '').slice(7).split(',').filter(Boolean).map(Number);

// [파일 이름, 슬라이드 번호, 추가 쿼리]
const SLIDES = [
  ['01_표지', 1], ['02_60일', 2], ['03_일정', 3], ['04_지역구분', 4], ['05_실연앞뒤', 5], ['06_로드맵', 6],
  ['07_스터디', 7], ['08_감점', 8], ['09_미리보기', 9], ['10_받는법_프로필링크', 10], ['10_받는법_댓글DM', 10, '&cta=dm'],
].filter(s => !only.length || only.includes(s[1]));

const url = (n, extra = '') => 'file://' + path.join(DIR, 'carousel.html') + `?slide=${n}${extra}`;

async function ready(page) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map(i => i.complete ? null : new Promise(r => { i.onload = i.onerror = r; })));
  });
}
const seek = (page, ms) => page.evaluate(t => { document.getAnimations().forEach(a => { a.pause(); a.currentTime = t; }); }, ms);

async function renderSlide(browser, [name, n, extra]) {
  // 정지 이미지: 2배로 찍어 1080 폭으로 줄인다
  const p2 = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 2 });
  await p2.goto(url(n, extra)); await ready(p2);
  const dur = Number(await p2.evaluate(() => document.querySelector('.slide.on').dataset.dur || 6));
  await seek(p2, dur * 1000);
  const big = path.join(TMP, `${name}@2x.png`);
  await (await p2.$('.slide.on')).screenshot({ path: big });
  await p2.close();
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', big, '-vf', `scale=${W}:${H}:flags=lanczos`, path.join(OUT, `${name}.png`)]);
  fs.unlinkSync(big);
  if (stillsOnly) return { name, dur };

  // 모션: 프레임 단위로 시간 이동
  const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
  await page.goto(url(n, extra)); await ready(page);
  const fdir = path.join(TMP, 'f_' + name); fs.rmSync(fdir, { recursive: true, force: true }); fs.mkdirSync(fdir, { recursive: true });
  const el = await page.$('.slide.on');
  const total = Math.round(dur * FPS);
  for (let f = 0; f < total; f++) {
    await seek(page, (f / FPS) * 1000);
    await el.screenshot({ path: path.join(fdir, String(f).padStart(4, '0') + '.jpg'), type: 'jpeg', quality: 96 });
  }
  await page.close();
  const mp4 = path.join(OUT, `${name}.mp4`);
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', String(FPS), '-i', path.join(fdir, '%04d.jpg'),
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '17', '-pix_fmt', 'yuv420p', '-r', String(FPS), '-movflags', '+faststart', mp4]);
  fs.rmSync(fdir, { recursive: true, force: true });
  return { name, dur, mp4 };
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true }); fs.mkdirSync(TMP, { recursive: true });
  const browser = await chromium.launch();
  const queue = [...SLIDES], done = [];
  await Promise.all(Array.from({ length: 3 }, async () => {
    while (queue.length) {
      const s = queue.shift();
      const r = await renderSlide(browser, s);
      done.push(r); console.log('완료', r.name);
    }
  }));
  await browser.close();

  if (!stillsOnly && !only.length) {
    // 한 편으로 이어 붙인 영상(댓글·DM 문구판은 제외)
    const order = SLIDES.filter(s => !s[2]).map(s => path.join(OUT, s[0] + '.mp4'));
    const list = path.join(TMP, 'concat.txt');
    fs.writeFileSync(list, order.map(f => `file '${f.replace(/'/g, "'\\''")}'`).join('\n'));
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list, '-c', 'copy', '-movflags', '+faststart', path.join(OUT, '00_전체_이어보기.mp4')]);
    console.log('이어 붙임 → 00_전체_이어보기.mp4');
  }
  fs.readdirSync(OUT).sort().forEach(f => console.log(' ', f, Math.round(fs.statSync(path.join(OUT, f)).size / 1024) + 'KB'));
})();
