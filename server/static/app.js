// 확인이 필요한 버튼
document.querySelectorAll('form[data-confirm]').forEach((f) => {
  f.addEventListener('submit', (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); });
});

// 작업 진행 상태 폴링
const statusBox = document.getElementById('status');
if (statusBox) {
  const tick = async () => {
    try {
      const r = await fetch('/api/jobs/' + statusBox.dataset.job);
      if (r.status === 401) { location.href = '/login'; return; }
      const j = await r.json();
      document.getElementById('step').textContent = j.step || '대기 중';
      document.getElementById('bar').style.width = j.progress + '%';
      if (j.status === 'done' || j.status === 'error') { location.reload(); return; }
    } catch (e) { /* 일시적인 네트워크 오류는 다음 주기에 다시 시도 */ }
    setTimeout(tick, 3000);
  };
  document.getElementById('bar').style.width = document.getElementById('bar').dataset.progress + '%';
  setTimeout(tick, 2000);
}

// 문제 고르기: 강사(박해인·서진) → 기출/예상 → 문제 목록. '문제 업로드'는 외부·새 문제용.
const picker = document.getElementById('picker');
if (picker) {
  const mode = document.getElementById('pb-mode');
  const bankBox = document.getElementById('pb-bank');
  const uploadBox = document.getElementById('pb-upload');
  const select = document.getElementById('pb-problem');
  const count = document.getElementById('pb-count');
  const preview = document.getElementById('pb-preview');
  const levelSel = document.getElementById('level');
  const state = { instructor: '', kind: '기출', wanted: picker.dataset.selected || '' };
  const level = () => (levelSel ? levelSel.value : picker.dataset.level) || '';
  const mark = (group, attr, value) => group.querySelectorAll('.seg-btn').forEach((b) => b.classList.toggle('on', b.dataset[attr] === value));

  const showPreview = () => {
    preview.hidden = !select.value;
    if (select.value) preview.href = '/problems/' + select.value;
  };
  const load = async () => {
    if (!state.instructor) return;
    select.innerHTML = '<option value="">불러오는 중…</option>';
    const q = new URLSearchParams({ level: level(), instructor: state.instructor, kind: state.kind });
    let rows = [];
    try { const r = await fetch('/api/problems?' + q); if (r.ok) rows = await r.json(); } catch (e) { /* 아래에서 빈 목록 처리 */ }
    select.innerHTML = '';
    const first = document.createElement('option');
    first.value = '';
    first.textContent = rows.length ? '문제를 선택하세요' : '이 조건의 문제가 없습니다 — 문제 업로드를 쓰세요';
    select.appendChild(first);
    rows.forEach((p) => {
      const o = document.createElement('option');
      o.value = p.id; o.textContent = p.title;
      if (p.id === state.wanted) o.selected = true;
      select.appendChild(o);
    });
    count.textContent = level() + ' · ' + state.instructor + ' · ' + state.kind + ' ' + rows.length + '문제';
    showPreview();
  };
  const useBank = (instructor) => {
    state.instructor = instructor;
    mode.value = 'bank';
    bankBox.hidden = false; uploadBox.hidden = true;
    document.querySelectorAll('#pb-instructor .seg-btn').forEach((b) => b.classList.toggle('on', b.dataset.instructor === instructor));
    mark(document.getElementById('pb-kind'), 'kind', state.kind);
    load();
  };
  const useUpload = () => {
    state.instructor = '';
    mode.value = 'upload';
    bankBox.hidden = true; uploadBox.hidden = false;
    document.querySelectorAll('#pb-instructor .seg-btn').forEach((b) => b.classList.toggle('on', !!b.dataset.upload));
  };
  document.querySelectorAll('#pb-instructor .seg-btn').forEach((b) => b.addEventListener('click', () => {
    if (b.dataset.upload) useUpload(); else useBank(b.dataset.instructor);
  }));
  document.querySelectorAll('#pb-kind .seg-btn').forEach((b) => b.addEventListener('click', () => {
    state.kind = b.dataset.kind;
    mark(document.getElementById('pb-kind'), 'kind', state.kind);
    load();
  }));
  select.addEventListener('change', () => { state.wanted = select.value; showPreview(); });
  if (levelSel) levelSel.addEventListener('change', load);

  // 지난번에 고른 문제가 있으면 그 강사·유형으로 바로 연다
  if (state.wanted) {
    fetch('/api/problems?' + new URLSearchParams({ level: level() })).then((r) => (r.ok ? r.json() : [])).then((rows) => {
      const hit = rows.find((p) => p.id === state.wanted);
      if (hit) { state.kind = hit.kind; useBank(hit.instructor); }
    }).catch(() => {});
  }
}

// 수업실연 업로드: 영상을 5MB 조각으로 나눠 올린 뒤 폼을 제출한다
const form = document.getElementById('lesson-form');
if (form) {
  const CHUNK = 5 * 1024 * 1024;
  const csrf = form.dataset.csrf;
  const input = document.getElementById('video');
  const drop = document.getElementById('drop');
  const bar = document.getElementById('up-bar');
  const progress = document.getElementById('up-progress');
  const text = document.getElementById('up-text');
  const errBox = document.getElementById('form-error');
  const submit = document.getElementById('start-btn');
  let file = null;

  const showError = (m) => { errBox.textContent = m; errBox.hidden = false; submit.disabled = false; submit.textContent = '피드백 시작'; };
  const pick = (f) => {
    file = f;
    document.getElementById('drop-title').textContent = f.name;
    document.getElementById('drop-sub').textContent = (f.size / 1048576).toFixed(1) + ' MB';
  };
  input.addEventListener('change', () => { if (input.files[0]) pick(input.files[0]); });
  ['dragover', 'dragenter'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', (e) => { if (e.dataTransfer.files[0]) pick(e.dataTransfer.files[0]); });

  const api = async (url, opts) => {
    const r = await fetch(url, opts);
    if (!r.ok) {
      let m = '업로드 실패 (' + r.status + ')';
      try { const j = await r.json(); m = j.detail || j.error || m; } catch (e) {}
      throw new Error(m);
    }
    return r.json();
  };

  const upload = async () => {
    const init = await api('/api/uploads', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify({ name: file.name, size: file.size }),
    });
    progress.hidden = false; text.hidden = false;
    let index = 0, sent = 0;
    while (sent < file.size) {
      const blob = file.slice(sent, sent + CHUNK);
      let res = null;
      for (let attempt = 0; attempt < 5 && !res; attempt++) {
        try {
          res = await api('/api/uploads/' + init.upload_id + '/' + index, { method: 'PUT', headers: { 'X-CSRF-Token': csrf }, body: blob });
        } catch (e) {
          if (attempt === 4) throw e;
          await new Promise((ok) => setTimeout(ok, 1500 * (attempt + 1)));
        }
      }
      index = res.next; sent = res.received;
      const pct = Math.round((sent / file.size) * 100);
      bar.style.width = pct + '%';
      text.textContent = '영상 올리는 중… ' + pct + '% (' + (sent / 1048576).toFixed(0) + ' / ' + (file.size / 1048576).toFixed(0) + ' MB)';
    }
    return init.upload_id;
  };

  const problemReady = () => {
    const mode = form.problem_mode ? form.problem_mode.value : 'upload';
    if (mode === 'bank') return !!(form.problem_id && form.problem_id.value);
    const saved = Number(form.dataset.savedFiles || 0);
    return !!(form.problem_text.value.trim() || form.problem_files.files.length || saved);
  };

  form.addEventListener('submit', async (e) => {
    if (form.dataset.ready) return;
    e.preventDefault();
    errBox.hidden = true;
    const url = document.getElementById('video_url').value.trim();
    if (!problemReady()) return showError('문제를 골라 주세요. 강사(박해인·서진)를 누른 뒤 기출·예상 문제를 고르거나, 문제 업로드로 파일을 올리면 됩니다.');
    if (!file && !url) return showError('영상 파일을 선택하거나 영상 링크를 입력해 주세요.');
    submit.disabled = true; submit.textContent = '처리 중…';
    try {
      if (file) document.getElementById('upload_id').value = await upload();
      text.hidden = false; text.textContent = '업로드 완료. 작업을 등록하는 중…';
      form.dataset.ready = '1';
      HTMLFormElement.prototype.submit.call(form);
    } catch (err) { showError(err.message); }
  });
}
