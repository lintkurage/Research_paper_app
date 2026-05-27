const BASE = 'http://localhost:5001';
let meta = {};

// 起動時にタブURLを取得して処理開始
chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
    const url = tabs[0].url;
    await init(url);
});

async function init(url) {
    // プロジェクト一覧を取得
    let projects = [];
    try {
        const res = await fetch(`${BASE}/api/projects`);
        projects = await res.json();
    } catch (_) {
        showServerError();
        return;
    }

    // プロジェクトをセレクトに追加
    const select = document.getElementById('p-project');
    if (projects.length === 0) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = '⚠️ プロジェクトがありません';
        select.appendChild(opt);
    } else {
        projects.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = p.name;
            select.appendChild(opt);
        });
    }

    // メタデータを取得
    try {
        const res  = await fetch(`${BASE}/bookmarklet/fetch?url=${encodeURIComponent(url)}`);
        const data = await res.json();

        if (!res.ok || data.error) {
            setStatus('warning', `⚠️ ${data.error || '自動取得できませんでした。手動で入力してください。'}`);
        } else {
            setStatus('success', '✓ 論文情報を取得しました');
            meta = data;
            setValue('p-title',    data.title    || '');
            setValue('p-authors',  data.authors  || '');
            setValue('p-venue',    data.venue    || '');
            setValue('p-year',     data.year     || '');
            setValue('p-abstract', data.abstract || '');
        }
    } catch (_) {
        setStatus('warning', '⚠️ 自動取得に失敗しました。手動で入力してください。');
    }

    show('form-area');
}

async function addPaper() {
    const title     = document.getElementById('p-title').value.trim();
    const projectId = document.getElementById('p-project').value;

    if (!title)     { alert('タイトルを入力してください'); return; }
    if (!projectId) { alert('プロジェクトを選択してください'); return; }

    const btn = document.getElementById('add-btn');
    btn.disabled = true;
    btn.textContent = '追加中...';

    try {
        const res = await fetch(`${BASE}/bookmarklet/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id:       projectId,
                title,
                authors:          document.getElementById('p-authors').value,
                abstract:         document.getElementById('p-abstract').value,
                venue:            document.getElementById('p-venue').value,
                year:             document.getElementById('p-year').value,
                doi:              meta.doi              || '',
                url:              meta.url              || '',
                publication_date: meta.publication_date || '',
                citation_count:   meta.citation_count   || 0,
            })
        });
        const data = await res.json();

        if (data.id) {
            hide('form-area');
            hide('status');
            document.getElementById('done-title').textContent = title;
            show('done-area');
        } else {
            alert(data.error || '追加に失敗しました');
            btn.disabled = false;
            btn.textContent = '＋ 追加する';
        }
    } catch (_) {
        alert('サーバーへの接続に失敗しました');
        btn.disabled = false;
        btn.textContent = '＋ 追加する';
    }
}

// ユーティリティ
function show(id) { document.getElementById(id).classList.remove('hidden'); }
function hide(id) { document.getElementById(id).classList.add('hidden'); }
function setValue(id, val) { document.getElementById(id).value = val; }
function setStatus(type, msg) {
    const el = document.getElementById('status');
    el.className = `status ${type}`;
    el.textContent = msg;
}
function showServerError() {
    hide('status');
    show('server-error');
}

// インラインonclickはMV3のCSPでブロックされるため addEventListener で登録
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('add-btn').addEventListener('click', addPaper);
    document.getElementById('close-btn').addEventListener('click', () => window.close());
});
