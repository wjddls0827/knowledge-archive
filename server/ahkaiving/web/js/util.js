/* AH!카이빙 대시보드 — 순수 유틸(날짜·이스케이프·URL·분류).
 * index.html의 거대한 인라인 <script>에서 분리한 첫 조각. DOM·전역상태 의존 없음.
 * 일반 <script src>로 로드돼 전역 함수로 노출된다(메인 스크립트가 그대로 호출). */

/* HTML 이스케이프 */
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

/* URL → 호스트(www 제거) */
function hostOf(u){ try{ return new URL(u).hostname.replace(/^www\./,''); }catch(e){ return ''; } }
function shortUrl(u){ try{ return new URL(u).hostname.replace(/^www\./,''); }catch(e){ return u; } }

/* 날짜/마감(D-day) */
function isDateStr(s){ return /^\d{4}-\d{2}-\d{2}$/.test(s||''); }
function ddayNum(ds){ try{ const t=new Date(); const today=new Date(t.getFullYear(),t.getMonth(),t.getDate()); const[y,m,d]=ds.split('-').map(Number); return Math.round((new Date(y,m-1,d)-today)/86400000); }catch(e){ return 999; } }
function ddayStr(ds){ const n=ddayNum(ds); return n===0?'D-DAY':n>0?('D-'+n):('D+'+(-n)); }
function fmtDs(d){ return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; }

/* 문서 분류 — 변형 kind를 정본 키로 정규화(예: recruit/job/채용 → jd) */
const KIND_ALIAS={recruit:'jd',recruiting:'jd',job:'jd',jobs:'jd',jobposting:'jd','job-posting':'jd',posting:'jd',jobpost:'jd',hiring:'jd',career:'jd',careers:'jd',채용:'jd',채용공고:'jd'};
function kindKey(i){ const k=(i.kind||'').toLowerCase(); return KIND_ALIAS[k]||k; }
