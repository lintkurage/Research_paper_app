// テキストファイルをクライアント側で生成してダウンロード
function blobDownload(text, filename) {
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// トースト通知
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const id = 'toast-' + Date.now();
    const colors = {
        success: 'bg-success text-white',
        danger: 'bg-danger text-white',
        warning: 'bg-warning text-dark',
        info: 'bg-info text-dark',
    };
    const html = `
        <div id="${id}" class="toast align-items-center border-0 ${colors[type] || colors.info}" role="alert">
            <div class="d-flex">
                <div class="toast-body">${message}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>`;
    container.insertAdjacentHTML('beforeend', html);
    const toast = new bootstrap.Toast(document.getElementById(id), {delay: 3000});
    toast.show();
    document.getElementById(id).addEventListener('hidden.bs.toast', () => {
        document.getElementById(id)?.remove();
    });
}

// グローバル検索
const globalSearch = document.getElementById('global-search');
const searchOverlay = document.getElementById('search-results-overlay');
let searchTimer = null;

if (globalSearch) {
    globalSearch.addEventListener('input', () => {
        clearTimeout(searchTimer);
        const q = globalSearch.value.trim();
        if (!q) { searchOverlay.classList.add('d-none'); return; }
        searchTimer = setTimeout(() => performGlobalSearch(q), 300);
    });

    document.getElementById('global-search-form').addEventListener('submit', e => {
        e.preventDefault();
        const q = globalSearch.value.trim();
        if (q) performGlobalSearch(q);
    });
}

async function performGlobalSearch(q) {
    const res = await fetch(`/search?q=${encodeURIComponent(q)}`);
    const papers = await res.json();
    const body = document.getElementById('search-results-body');

    if (papers.length === 0) {
        body.innerHTML = '<p class="text-muted text-center py-3 mb-0">「' + q + '」に一致する論文が見つかりませんでした</p>';
    } else {
        body.innerHTML = papers.map(p => `
            <a href="/paper/${p.id}" class="d-block text-decoration-none text-dark border-bottom p-3 search-result-item">
                <div class="fw-semibold small">${p.title}</div>
                <div class="text-muted" style="font-size:0.78rem">
                    ${p.project_name} • ${p.authors ? p.authors.split(',')[0].trim() + ' et al.' : ''}
                    ${p.year ? '• ' + p.year : ''}
                </div>
            </a>
        `).join('');
    }
    searchOverlay.classList.remove('d-none');
}

function closeSearch() {
    searchOverlay.classList.add('d-none');
    if (globalSearch) globalSearch.value = '';
}

document.addEventListener('click', e => {
    if (searchOverlay && !searchOverlay.contains(e.target) && e.target !== globalSearch) {
        searchOverlay.classList.add('d-none');
    }
});
