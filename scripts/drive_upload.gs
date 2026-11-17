/**
 * 호랑말코 피드백 완성본 → 구글 드라이브 업로드용 Apps Script 웹 앱.
 *
 * 설치(관리자 구글 계정으로 한 번만):
 *   1. https://script.google.com 에서 새 프로젝트를 만들고 이 파일 내용을 붙여넣어 저장
 *   2. 배포 → 새 배포 → 유형: 웹 앱 / 다음 사용자로 실행: 나 / 액세스 권한: 모든 사용자 → 배포
 *   3. 권한 승인 후 나오는 웹 앱 URL(…/exec)을 웹앱 관리 화면의 "드라이브 연결 주소"에 붙여넣기
 *
 * 받은 파일은 ROOT 폴더 아래의 학교급 폴더(유특/초특/중특)에 저장한다. 폴더가 없으면 만든다.
 * 웹 앱 주소를 아는 사람만 올릴 수 있으므로 주소를 다른 곳에 공유하지 않는다.
 */
const ROOT_ID = '1LGvYFsj6c938enXjN0xk67IwfbXKyS0M';   // 27 피드백
const LEVELS = ['유특', '초특', '중특'];

function doPost(e) {
  try {
    const req = JSON.parse(e.postData.contents);
    if (LEVELS.indexOf(req.level) < 0) return out_({ ok: false, error: '학교급이 올바르지 않습니다: ' + req.level });
    const root = DriveApp.getFolderById(ROOT_ID);
    const it = root.getFoldersByName(req.level);
    const folder = it.hasNext() ? it.next() : root.createFolder(req.level);
    const blob = Utilities.newBlob(Utilities.base64Decode(req.data), req.mime || 'application/octet-stream', req.name);
    const file = folder.createFile(blob);
    return out_({ ok: true, id: file.getId(), url: file.getUrl() });
  } catch (err) {
    return out_({ ok: false, error: String(err) });
  }
}

function doGet() {
  return out_({ ok: true, ping: 'horangmalco-drive-upload' });
}

function out_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
