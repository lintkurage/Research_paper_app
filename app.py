import os
import re
import uuid
import sqlite3
import json
import requests
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = 'paper-manager-secret-key'
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

# Chrome拡張機能からのリクエストを許可するCORSヘッダー
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

@app.route('/api/projects')
def api_projects():
    """拡張機能用: プロジェクト一覧をJSON返却"""
    with get_db() as conn:
        projects = conn.execute('SELECT id, name FROM projects ORDER BY name').fetchall()
    return jsonify([dict(p) for p in projects])

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@app.template_filter('from_json')
def from_json_filter(value):
    try:
        return json.loads(value) if value else []
    except Exception:
        return []

DB_PATH = os.path.join(os.path.dirname(__file__), 'papers.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                color TEXT DEFAULT '#4A90D9',
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                authors TEXT DEFAULT '',
                abstract TEXT DEFAULT '',
                doi TEXT DEFAULT '',
                url TEXT DEFAULT '',
                venue TEXT DEFAULT '',
                publication_date TEXT DEFAULT '',
                year INTEGER,
                citation_count INTEGER DEFAULT 0,
                citation_updated_at TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                notes TEXT DEFAULT '',
                pdf_path TEXT DEFAULT '',
                read_status TEXT DEFAULT 'unread',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
        ''')


def _clean_doi(doi):
    """DOI文字列を正規化して純粋な 10.xxx/yyy 形式にする。
    lstrip() は文字集合除去なので URL プレフィックスに使うとバグる。startswith で処理する。"""
    doi = doi.strip()
    for prefix in ('https://doi.org/', 'http://doi.org/',
                   'https://dx.doi.org/', 'http://dx.doi.org/'):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix):]
            break
    if doi.lower().startswith('doi:'):
        doi = doi[4:].strip()
    return doi


def fetch_crossref(doi):
    """DOIからCrossRef APIで論文情報を取得"""
    try:
        doi_clean = _clean_doi(doi)
        if not doi_clean.startswith('10.'):
            return None
        url = f'https://api.crossref.org/works/{doi_clean}'
        resp = requests.get(url, timeout=10, headers={'User-Agent': 'PaperManager/1.0'})
        if resp.status_code != 200:
            return None
        data = resp.json().get('message', {})

        authors = []
        for a in data.get('author', []):
            given = a.get('given', '')
            family = a.get('family', '')
            authors.append(f"{given} {family}".strip())

        pub_date = ''
        year = None
        date_parts = data.get('published', {}).get('date-parts', [[]])
        if date_parts and date_parts[0]:
            parts = date_parts[0]
            year = parts[0] if len(parts) > 0 else None
            if len(parts) >= 3:
                pub_date = f"{parts[0]}-{parts[1]:02d}-{parts[2]:02d}"
            elif len(parts) == 2:
                pub_date = f"{parts[0]}-{parts[1]:02d}"
            elif len(parts) == 1:
                pub_date = str(parts[0])

        titles = data.get('title', [''])
        venue = ''
        if data.get('container-title'):
            venue = data['container-title'][0]
        elif data.get('event', {}).get('name'):
            venue = data['event']['name']

        # JATS XML タグを除去（<jats:p>, <jats:italic> など）
        abstract_raw = data.get('abstract', '')
        abstract = re.sub(r'<[^>]+>', '', abstract_raw).strip()

        return {
            'title': titles[0] if titles else '',
            'authors': ', '.join(authors),
            'abstract': abstract,
            'doi': doi_clean,
            'url': data.get('URL', f'https://doi.org/{doi_clean}'),
            'venue': venue,
            'publication_date': pub_date,
            'year': year,
        }
    except Exception as e:
        print(f"CrossRef error: {e}")
        return None


def fetch_semantic_scholar(doi=None, arxiv_id=None):
    """Semantic Scholar APIでメタデータ＋被引用数を取得。CrossRefに未登録の国内論文のフォールバック用。"""
    try:
        if doi:
            paper_id = f'DOI:{doi}'
        elif arxiv_id:
            paper_id = f'ARXIV:{arxiv_id}'
        else:
            return None

        fields = 'title,authors,abstract,year,venue,publicationVenue,publicationDate,citationCount,externalIds'
        url = f'https://api.semanticscholar.org/graph/v1/paper/{paper_id}?fields={fields}'
        resp = requests.get(url, timeout=10, headers={'User-Agent': 'PaperManager/1.0'})
        if resp.status_code != 200:
            return None
        data = resp.json()

        if not data.get('title'):
            return None

        authors = ', '.join(a.get('name', '') for a in data.get('authors', []))

        venue = data.get('venue', '')
        pub_venue = data.get('publicationVenue') or {}
        venue = pub_venue.get('name', '') or venue

        ext_ids = data.get('externalIds', {})
        doi_result = ext_ids.get('DOI', '') or doi or ''

        return {
            'title':            data.get('title', ''),
            'authors':          authors,
            'abstract':         data.get('abstract', ''),
            'doi':              doi_result,
            'url':              f'https://doi.org/{doi_result}' if doi_result else '',
            'venue':            venue,
            'publication_date': data.get('publicationDate', ''),
            'year':             data.get('year'),
            'citation_count':   data.get('citationCount', 0),
        }
    except Exception as e:
        print(f"Semantic Scholar metadata error: {e}")
        return None


def fetch_arxiv(arxiv_id):
    """ArXiv IDから論文情報を取得"""
    try:
        arxiv_clean = arxiv_id.strip()
        if 'arxiv.org/abs/' in arxiv_clean:
            arxiv_clean = arxiv_clean.split('arxiv.org/abs/')[-1].split('v')[0]
        elif arxiv_clean.startswith('arxiv:'):
            arxiv_clean = arxiv_clean[6:]

        url = f'http://export.arxiv.org/api/query?id_list={arxiv_clean}'
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return None

        import xml.etree.ElementTree as ET
        ns = {'atom': 'http://www.w3.org/2005/Atom', 'arxiv': 'http://arxiv.org/schemas/atom'}
        root = ET.fromstring(resp.text)
        entry = root.find('atom:entry', ns)
        if entry is None:
            return None

        title = entry.findtext('atom:title', '', ns).replace('\n', ' ').strip()
        abstract = entry.findtext('atom:summary', '', ns).replace('\n', ' ').strip()
        published = entry.findtext('atom:published', '', ns)
        year = None
        pub_date = ''
        if published:
            pub_date = published[:10]
            year = int(published[:4])

        authors = [a.findtext('atom:name', '', ns) for a in entry.findall('atom:author', ns)]

        doi_elem = entry.find('arxiv:doi', ns)
        doi = doi_elem.text.strip() if doi_elem is not None else ''

        return {
            'title': title,
            'authors': ', '.join(authors),
            'abstract': abstract,
            'doi': doi,
            'url': f'https://arxiv.org/abs/{arxiv_clean}',
            'venue': 'arXiv',
            'publication_date': pub_date,
            'year': year,
        }
    except Exception as e:
        print(f"ArXiv error: {e}")
        return None


def fetch_semantic_scholar_citations(doi=None, arxiv_id=None):
    """被引用数のみ取得（fetch_semantic_scholar の薄いラッパー）"""
    result = fetch_semantic_scholar(doi=doi, arxiv_id=arxiv_id)
    return result.get('citation_count') if result else None


def _extract_text_via_pdfminer(filepath, max_pages=4):
    """pdfminer.six でテキスト抽出（段組みPDF対応）"""
    try:
        from pdfminer.high_level import extract_text
        from pdfminer.layout import LAParams
        # line_margin を広めにとると段組みの混在が減る
        params = LAParams(line_margin=0.5, word_margin=0.1, char_margin=2.0)
        text = extract_text(filepath, maxpages=max_pages, laparams=params)
        return text or ''
    except Exception as e:
        print(f"pdfminer error: {e}")
        return ''


def _extract_text_via_pypdf(filepath, max_pages=4):
    """pypdf でテキスト抽出（フォールバック）"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(filepath)
        return '\n'.join(
            (page.extract_text() or '') for page in reader.pages[:max_pages]
        )
    except Exception as e:
        print(f"pypdf error: {e}")
        return ''


def _extract_doi(text):
    """DOIを高精度で抽出（ラベル付き優先→裸のDOI）"""
    labeled = re.search(
        r'(?:(?:DOI|doi)[:\s]+|https?://(?:dx\.)?doi\.org/)'
        r'(10\.\d{4,9}/[^\s\]\[><"\'\n,;}{]+)',
        text
    )
    if labeled:
        return labeled.group(1).rstrip('.,;:)')
    bare = re.search(r'\b(10\.\d{4,9}/[^\s\]\[><"\'\n,;}{]+)', text)
    if bare:
        doi = bare.group(1).rstrip('.,;:)')
        if len(doi) > 12:
            return doi
    return ''


def _extract_title_by_font(filepath):
    """pdfminer のフォントサイズ解析で 1 ページ目の最大フォントテキストをタイトルとして返す。
    日本語・英語どちらの論文でもタイトルは最大フォントで書かれているため最も信頼性が高い。"""
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextBox, LTChar

        skip = re.compile(
            r'^(\d{1,4}$|©|Proceedings|Conference|Workshop|Journal|IEEE|ACM|Springer'
            r'|arXiv|\d{4}\s+IEEE|Authorized|Licensed|Downloaded|This\s+article)',
            re.IGNORECASE
        )
        candidates = []  # (font_size, y座標, text)
        for page_layout in extract_pages(filepath, maxpages=1):
            for element in page_layout:
                if not isinstance(element, LTTextBox):
                    continue
                text_block = element.get_text().replace('\n', ' ').strip()
                if not text_block or len(text_block) < 4 or len(text_block) > 300:
                    continue
                if skip.match(text_block):
                    continue
                max_size = max(
                    (char.size for line in element for char in line
                     if isinstance(char, LTChar)),
                    default=0
                )
                if max_size > 8:
                    candidates.append((max_size, element.y1, text_block))
            break  # 1 ページのみ

        if not candidates:
            return ''

        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        max_size = candidates[0][0]
        for size, _, text_block in candidates:
            if size < max_size * 0.85:
                break
            if re.search(r'[†‡§]', text_block):  # 著者行のマーカーは除外
                continue
            return text_block
        return candidates[0][2]
    except Exception as e:
        print(f"Font-based title extraction error: {e}")
        return ''


def _extract_authors_from_text(text):
    """テキストのアブストラクト前セクションから著者行を抽出する。
    IPSJ/IEICE の †‡ マーカー形式と英語論文のカンマ区切り形式に対応。"""
    abs_pos = len(text)
    for kw in ('あらまし', '概要', 'Abstract', 'ABSTRACT'):
        p = text.find(kw)
        if 0 < p < abs_pos:
            abs_pos = p

    header = text[:abs_pos]
    lines = [l.strip() for l in header.split('\n') if l.strip()]

    skip_affil = re.compile(
        r'(大学|学院|学部|研究科|研究所|Department|University|Institute'
        r'|Laboratory|Lab\.|Corp\.|Inc\.|Ltd\.|E-?mail|@|〒|\d{3}-\d{4})',
        re.IGNORECASE
    )
    # 論文誌ヘッダー・タイトルっぽい長い文をスキップ
    skip_header = re.compile(r'Vol\.\s*\w+|No\.\s*\d+|pp\.\s*\d+', re.IGNORECASE)

    author_lines = []
    # lines[0] はタイトル or 論文誌名なのでスキップ、lines[1:] から探す
    for line in lines[1:20]:
        if skip_affil.search(line) or skip_header.search(line):
            continue
        # † ‡ § などの所属マーカーを含む → 著者行
        if re.search(r'[†‡§¶]', line):
            author_lines.append(line)
        # 英語著者: カンマまたは "and" で区切られた名前が 2 名以上
        # タイトル行との誤検出を防ぐため「区切り文字の存在」を必須とする
        elif (len(line) < 200 and
              (', ' in line or re.search(r'\band\b', line, re.IGNORECASE)) and
              len(re.findall(r'[A-Z][a-z]+\s+[A-Z][a-z]+', line)) >= 2):
            author_lines.append(line)

    if not author_lines:
        return ''

    combined = ' '.join(author_lines)
    combined = re.sub(r'[†‡§¶]+', '', combined)
    combined = re.sub(r'[ \t]{2,}', ', ', combined)
    combined = re.sub(r'\s+', ' ', combined).strip().strip(',')
    return combined


def _extract_abstract(text):
    """アブストラクトを抽出。
    対応形式: IPSJ/IEICE（あらまし・概要）、IEEE（Abstract—）、ACM、Springer、arXiv"""
    text = re.sub(r'\r\n?', '\n', text)

    end_en = (
        r'(?:Index\s+Terms|Keywords?|CCS\s+CONCEPTS|ACM\s+Reference'
        r'|I\.\s+Introduction|1\.\s+Introduction|INTRODUCTION|\Z)'
    )
    end_ja = r'(?:キーワード|キーワード：|索引語|\Z)'

    patterns = [
        # --- 日本語（IPSJ / IEICE） ---
        # 全角スペース・半角スペースどちらも対応
        (rf'あらまし[　\s]+(.*?)(?={end_ja})',        re.DOTALL),
        (rf'概要[　\s]+(.*?)(?={end_ja})',             re.DOTALL),
        (rf'アブストラクト[　\s]+(.*?)(?={end_ja})',   re.DOTALL),
        # --- 英語 ---
        (rf'Abstract\s*[—–\-]\s*(.*?)\n\s*{end_en}', re.DOTALL | re.IGNORECASE),
        (rf'Abstract\s*\n+\s*(.*?)\n\s*{end_en}',    re.DOTALL | re.IGNORECASE),
        (rf'ABSTRACT\s*\n+(.*?)\n\s*{end_en}',       re.DOTALL | re.IGNORECASE),
        (rf'Abstract\s*:\s*(.*?)\n\s*{end_en}',      re.DOTALL | re.IGNORECASE),
        # フォールバック（終端が見えない場合）
        (r'(?:Abstract|ABSTRACT|あらまし|概要)[—–\-:\s　]+([\s\S]{30,3000}?)(?:\n\n|\Z)',
         re.IGNORECASE),
    ]

    for pat, flags in patterns:
        m = re.search(pat, text, flags)
        if m:
            abstract = m.group(1).strip()
            abstract = re.sub(r'[ \t　]+', ' ', abstract)  # 全角スペースも除去
            abstract = re.sub(r'\n+', ' ', abstract)
            abstract = abstract.strip()
            if 30 <= len(abstract) <= 5000:
                return abstract

    return ''


def _extract_title(text, pdf_title='', filepath=None):
    """タイトルを抽出。優先順: PDFメタデータ → フォントサイズ解析 → テキスト先頭"""
    # 1) PDF 埋め込みメタデータ（信頼度高）
    if pdf_title and 4 < len(pdf_title) < 300:
        bad = re.compile(r'^(Microsoft|Untitled|Document\d*|無題|\d+\.pdf)', re.IGNORECASE)
        if not bad.match(pdf_title):
            return pdf_title

    # 2) フォントサイズ解析（最も確実）
    if filepath:
        font_title = _extract_title_by_font(filepath)
        if font_title:
            return font_title

    # 3) テキスト先頭フォールバック
    skip = re.compile(
        r'^(\d{1,4}$|Page\s*\d+|©|Proceedings|Conference|Workshop'
        r'|Journal|IEEE|ACM|Springer|arXiv|\d{4}\s+IEEE'
        r'|This\s+article|Authorized|Licensed|Downloaded)',
        re.IGNORECASE
    )
    # 論文誌ヘッダー行（Vol. / No. / pp. を含む）
    vol_no = re.compile(r'Vol\.\s*\w+|No\.\s*\d+|pp\.\s*\d+', re.IGNORECASE)

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for line in lines[:25]:
        if not (4 <= len(line) <= 250):
            continue
        if skip.match(line):
            continue
        if vol_no.search(line):  # "Vol.64 No.3" のような論文誌ヘッダーをスキップ
            continue
        if re.match(r'^[\w\s\-/&]+\s\d{4}$', line) and len(line) < 40:
            continue  # "NeurIPS 2023", "ICML 2022" のような会議名+年
        if re.search(r'[†‡§]', line):
            continue
        return line

    return lines[0][:200] if lines else ''


def extract_metadata_from_pdf(filepath):
    """PDFからタイトル・DOI・アブストラクトなどを抽出する"""
    try:
        # --- PDFの埋め込みメタデータ ---
        from pypdf import PdfReader
        reader = PdfReader(filepath)
        info = reader.metadata or {}
        pdf_title   = (info.get('/Title')  or '').strip()
        pdf_authors = (info.get('/Author') or '').strip()

        # --- テキスト抽出: pdfminer → pypdf フォールバック ---
        text = _extract_text_via_pdfminer(filepath, max_pages=4)
        if not text.strip():
            text = _extract_text_via_pypdf(filepath, max_pages=4)

        # --- DOI抽出 ---
        doi = _extract_doi(text)

        # --- DOIがあれば CrossRef → Semantic Scholar の順で取得 ---
        if doi:
            api_result, source = _resolve_doi(doi)
            if api_result:
                # API 取得成功でも abstract・authors が空なら PDF から補完
                if not api_result.get('abstract'):
                    api_result['abstract'] = _extract_abstract(text)
                if not api_result.get('authors'):
                    api_result['authors'] = pdf_authors or _extract_authors_from_text(text)
                return {**api_result, 'doi_found': True, 'doi': doi, 'source': source}

        # --- DOI なし、または API 未登録: PDF から全フィールドを抽出 ---
        title    = _extract_title(text, pdf_title, filepath=filepath)
        abstract = _extract_abstract(text)
        authors  = pdf_authors or _extract_authors_from_text(text)

        return {
            'title':            title,
            'authors':          authors,
            'doi':              doi,
            'doi_found':        False,
            'abstract':         abstract,
            'url':              '',
            'venue':            '',
            'publication_date': '',
            'year':             None,
            'citation_count':   0,
        }
    except Exception as e:
        print(f"PDF parse error: {e}")
        return {'title': '', 'doi': '', 'doi_found': False, 'error': str(e)}


def _extract_text_pymupdf_2col(filepath):
    """pymupdf (fitz) で2列レイアウトを考慮してPDF全ページを抽出する。
    各ページのテキストブロックをx座標で左列・右列に分類し、
    左列→右列の順（それぞれy座標昇順）で結合する。
    """
    import fitz
    doc = fitz.open(filepath)
    all_pages = []
    try:
        for page in doc:
            page_width = page.rect.width
            mid = page_width / 2

            # テキストブロック取得: (x0, y0, x1, y1, text, block_no, block_type)
            blocks = page.get_text('blocks')
            text_blocks = [b for b in blocks if b[6] == 0 and b[4].strip()]
            if not text_blocks:
                continue

            # ブロック重心のx座標で左右を判定
            left_blocks  = [b for b in text_blocks if (b[0] + b[2]) / 2 <  mid]
            right_blocks = [b for b in text_blocks if (b[0] + b[2]) / 2 >= mid]

            # 両側に2ブロック以上 → 2列と判断
            if len(left_blocks) >= 2 and len(right_blocks) >= 2:
                ordered = (sorted(left_blocks,  key=lambda b: b[1]) +
                           sorted(right_blocks, key=lambda b: b[1]))
            else:
                ordered = sorted(text_blocks, key=lambda b: b[1])

            page_text = '\n'.join(b[4].strip() for b in ordered if b[4].strip())
            all_pages.append(page_text)
    finally:
        doc.close()
    return '\n\n'.join(all_pages)


def _extract_references_from_pdf(filepath):
    """PDFから参考文献リストを抽出する。
    全ページを対象に References / 参考文献 セクションを探し、
    番号付きエントリに分割して返す。
    pymupdf（2列対応）→ pdfminer → pypdf の順でフォールバック。
    """
    # --- 全ページテキスト抽出（pymupdf優先）---
    text = ''
    try:
        text = _extract_text_pymupdf_2col(filepath)
    except Exception as e:
        print(f"pymupdf extraction error: {e}")

    if not text.strip():
        try:
            from pdfminer.high_level import extract_text
            from pdfminer.layout import LAParams
            params = LAParams(line_margin=0.5, word_margin=0.1, char_margin=2.0)
            text = extract_text(filepath, laparams=params) or ''
        except Exception:
            pass

    if not text.strip():
        try:
            from pypdf import PdfReader
            reader = PdfReader(filepath)
            text = '\n'.join((page.extract_text() or '') for page in reader.pages)
        except Exception:
            return []

    if not text.strip():
        return []

    # --- 参考文献セクション検出（最後の出現箇所を優先） ---
    section_re = re.compile(
        r'(?:^|\n)[ \t]*(?:References|REFERENCES|参考文献|Bibliography|BIBLIOGRAPHY)[ \t]*\n'
    )
    matches = list(section_re.finditer(text))
    if not matches:
        return []

    ref_start = matches[-1].end()
    ref_text = text[ref_start:].strip()
    if len(ref_text) < 20:
        return []

    # --- 参考文献後の付録・謝辞等で打ち切る ---
    # 行頭にキーワードが現れた時点で打ち切る（後ろに A / A: / 何かが続いてもOK）
    stop_re = re.compile(
        r'\n[ \t]*(?:'
        r'Appendix\b|'
        r'Supplement(?:ary(?:\s+Material)?)?\b|'
        r'Author\s+Biograph(?:y|ies)\b|'
        r'Biograph(?:y|ies)\b|'
        r'Index\b|'
        r'About\s+the\s+Author\b|'
        r'Acknowledgment\b|Acknowledgement\b|'
        r'付録|謝辞'
        r')',
        re.IGNORECASE
    )
    stop_m = stop_re.search(ref_text)
    if stop_m:
        ref_text = ref_text[:stop_m.start()]

    # --- ページヘッダー・フッターっぽい行を除去 ---
    # ページ番号のみの行・英字も日本語もない短い行をスキップ
    cleaned = []
    for line in ref_text.split('\n'):
        s = line.strip()
        if re.match(r'^\d{1,4}$', s):                                    # ページ番号
            continue
        if s and len(s) < 6 and not re.search(r'[A-Za-z぀-鿿]', s):  # 記号のみ
            continue
        cleaned.append(line)
    ref_text = '\n'.join(cleaned)

    # --- ライセンス・著作権表記の行を除去 ---
    # IEEE / ACM / Springer / CC など各出版社のフッター定型文に対応
    license_re = re.compile(
        r'^[^\n]*(?:'
        r'Authorized\s+licensed\s+use|'          # IEEE: "Authorized licensed use limited to:..."
        r'Personal\s+use\s+of\s+this\s+material|'# IEEE: "Personal use of this material is permitted"
        r'Downloaded\s+on\s+\w+\s+\d|'          # IEEE: "Downloaded on January 01,2024"
        r'Restrictions\s+apply|'                  # IEEE: "Restrictions apply."
        r'This\s+article\s+has\s+been\s+accepted|'# IEEE: "This article has been accepted for publication"
        r'IEEE\s+Xplore|'                         # IEEE Xplore への言及
        r'©\s*(?:19|20)\d{2}\s+(?:IEEE|ACM|Springer|Elsevier|The\s+Author)|'
        r'Copyright\s*©\s*(?:19|20)\d{2}|'       # Copyright © 20XX
        r'Published\s+by\s+(?:IEEE|ACM|Springer|Elsevier)|'
        r'This\s+(?:work|paper|article)\s+is\s+licensed\s+under|'  # CC
        r'Creative\s+Commons\s+(?:Attribution|License)|'           # CC
        r'under\s+a\s+CC\s+BY|'                  # CC BY
        r'(?:ACM|IEEE)\s+Digital\s+Library'
        r')[^\n]*$',
        re.IGNORECASE | re.MULTILINE
    )
    ref_text = license_re.sub('', ref_text)

    # --- 番号スタイルを検出して分割 ---
    bracket_matches    = list(re.finditer(r'(?m)^\s*\[(\d+)\]', ref_text))   # [1] 形式
    number_dot_matches = list(re.finditer(r'(?m)^\s*(\d+)\.\s', ref_text))   # 1. 形式

    def split_by(match_list, group=1):
        result = []
        for i, m in enumerate(match_list):
            start = m.end()
            end = match_list[i + 1].start() if i + 1 < len(match_list) else len(ref_text)
            raw = re.sub(r'\s+', ' ', ref_text[start:end].strip())
            # 短すぎる・文字を含まないエントリを除外
            if len(raw) < 15:
                continue
            if not re.search(r'[A-Za-z぀-鿿]', raw):
                continue
            result.append({'num': int(m.group(group)), 'raw': raw})

        # 安全網: 最後のエントリが異常に長い場合は付録等が混入している可能性あり
        # 他エントリの中央値の5倍 or 900文字を超えたら最後のピリオド位置で切り捨て
        if len(result) >= 3:
            lengths = sorted(len(e['raw']) for e in result[:-1])
            median_len = lengths[len(lengths) // 2]
            threshold  = max(median_len * 5, 900)
            last = result[-1]
            if len(last['raw']) > threshold:
                truncated = last['raw'][:threshold]
                cut = truncated.rfind('.')
                if cut > 50:
                    truncated = truncated[:cut + 1]
                result[-1] = {**last, 'raw': truncated}

        return result

    if len(bracket_matches) >= max(len(number_dot_matches), 1) and bracket_matches:
        return split_by(bracket_matches)        # [1] IEEE/ACM 形式
    elif number_dot_matches:
        return split_by(number_dot_matches)     # 1. APA 形式
    else:
        # 番号なし: 空行で区切る
        entries = []
        for i, part in enumerate(re.split(r'\n{2,}', ref_text)[:60]):
            raw = re.sub(r'\s+', ' ', part.strip())
            if len(raw) > 20 and re.search(r'[A-Za-z぀-鿿]', raw):
                entries.append({'num': i + 1, 'raw': raw})
        return entries


@app.route('/paper/upload_and_extract', methods=['POST'])
def upload_and_extract():
    """PDFをアップロードしてメタデータを抽出する（論文追加前の一時保存）"""
    if 'pdf' not in request.files:
        return jsonify({'error': 'ファイルが選択されていません'}), 400
    file = request.files['pdf']
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'PDFファイルのみ対応しています'}), 400

    temp_id = str(uuid.uuid4())
    temp_filename = f'temp_{temp_id}.pdf'
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
    file.save(filepath)

    metadata = extract_metadata_from_pdf(filepath)
    metadata['temp_pdf'] = temp_filename

    # citation_count が未取得（DOI はあるが API 呼び出しがまだ）の場合に補完
    if not metadata.get('citation_count') and metadata.get('doi'):
        arxiv_id = None
        if 'arxiv.org' in metadata.get('url', ''):
            arxiv_id = metadata['url'].split('arxiv.org/abs/')[-1].split('v')[0]
        citations = fetch_semantic_scholar_citations(doi=metadata['doi'])
        if citations is None and arxiv_id:
            citations = fetch_semantic_scholar_citations(arxiv_id=arxiv_id)
        if citations is not None:
            metadata['citation_count'] = citations

    return jsonify(metadata)


@app.route('/')
def index():
    with get_db() as conn:
        projects = conn.execute('''
            SELECT p.*, COUNT(pa.id) as paper_count
            FROM projects p
            LEFT JOIN papers pa ON p.id = pa.project_id
            GROUP BY p.id
            ORDER BY p.created_at DESC
        ''').fetchall()
    return render_template('index.html', projects=projects)


@app.route('/project/create', methods=['POST'])
def create_project():
    data = request.json
    name = data.get('name', '').strip()
    description = data.get('description', '').strip()
    color = data.get('color', '#4A90D9')
    if not name:
        return jsonify({'error': '名前を入力してください'}), 400
    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO projects (name, description, color) VALUES (?, ?, ?)',
            (name, description, color)
        )
        project_id = cur.lastrowid
    return jsonify({'id': project_id, 'name': name})


@app.route('/project/<int:project_id>/delete', methods=['POST'])
def delete_project(project_id):
    with get_db() as conn:
        conn.execute('DELETE FROM papers WHERE project_id = ?', (project_id,))
        conn.execute('DELETE FROM projects WHERE id = ?', (project_id,))
    return jsonify({'success': True})


@app.route('/project/<int:project_id>')
def project_view(project_id):
    with get_db() as conn:
        project = conn.execute('SELECT * FROM projects WHERE id = ?', (project_id,)).fetchone()
        if not project:
            return redirect(url_for('index'))
        papers = conn.execute(
            'SELECT * FROM papers WHERE project_id = ? ORDER BY created_at DESC',
            (project_id,)
        ).fetchall()
        all_projects = conn.execute('SELECT id, name FROM projects ORDER BY name').fetchall()

    # 各スタイルで番号付き一括引用テキストを生成
    bulk_citations = {}
    for style in VALID_STYLES:
        entries = [
            f'[{i + 1}] {_generate_citation(dict(p), style)}'
            for i, p in enumerate(papers)
        ]
        bulk_citations[style] = '\n\n'.join(entries)

    return render_template('project.html', project=project, papers=papers,
                           all_projects=all_projects, bulk_citations=bulk_citations)


def _resolve_doi(doi):
    """DOIからメタデータを取得。CrossRef → Semantic Scholar の順で試みる。"""
    doi_clean = _clean_doi(doi)
    result = fetch_crossref(doi_clean)
    if result:
        return result, 'crossref'
    result = fetch_semantic_scholar(doi=doi_clean)
    if result:
        return result, 'semantic_scholar'
    return None, None


@app.route('/paper/fetch_metadata', methods=['POST'])
def fetch_metadata():
    data = request.json
    identifier = data.get('identifier', '').strip()
    if not identifier:
        return jsonify({'error': '入力が空です'}), 400

    result = None
    arxiv_id = None

    # ArXiv
    if 'arxiv.org' in identifier or identifier.lower().startswith('arxiv:'):
        result = fetch_arxiv(identifier)
        if result and 'arxiv.org' in result.get('url', ''):
            arxiv_id = result['url'].split('arxiv.org/abs/')[-1].split('v')[0]
    # DOI / doi.org URL
    elif _clean_doi(identifier).startswith('10.'):
        result, _ = _resolve_doi(identifier)
    # どちらでもなければ両方試す
    else:
        result = fetch_arxiv(identifier)
        if not result:
            result, _ = _resolve_doi(identifier)

    if not result:
        doi_tried = _clean_doi(identifier)
        msg = (f'DOI "{doi_tried}" の情報を取得できませんでした。'
               'CrossRef・Semantic Scholar ともに未登録の可能性があります。'
               '手動で入力するか、PDF をアップロードしてください。')
        return jsonify({'error': msg}), 404

    # 被引用数（Semantic Scholar がすでに返した場合は上書きしない）
    if not result.get('citation_count'):
        citations = None
        if result.get('doi'):
            citations = fetch_semantic_scholar_citations(doi=result['doi'])
        if citations is None and arxiv_id:
            citations = fetch_semantic_scholar_citations(arxiv_id=arxiv_id)
        result['citation_count'] = citations if citations is not None else 0

    return jsonify(result)


@app.route('/paper/add', methods=['POST'])
def add_paper():
    data = request.json
    project_id = data.get('project_id')
    title = data.get('title', '').strip()
    if not title:
        return jsonify({'error': 'タイトルを入力してください'}), 400

    tags = json.dumps(data.get('tags', []))
    year = data.get('year')
    try:
        year = int(year) if year else None
    except (ValueError, TypeError):
        year = None

    with get_db() as conn:
        cur = conn.execute('''
            INSERT INTO papers (project_id, title, authors, abstract, doi, url, venue,
                                publication_date, year, citation_count, tags, notes, read_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            project_id,
            title,
            data.get('authors', ''),
            data.get('abstract', ''),
            data.get('doi', ''),
            data.get('url', ''),
            data.get('venue', ''),
            data.get('publication_date', ''),
            year,
            data.get('citation_count', 0),
            tags,
            data.get('notes', ''),
            data.get('read_status', 'unread'),
        ))
        paper_id = cur.lastrowid

        # 一時保存PDFを正式なファイル名に移動して紐付け
        temp_pdf = data.get('temp_pdf', '').strip()
        if temp_pdf and re.match(r'^temp_[a-f0-9\-]+\.pdf$', temp_pdf):
            temp_path = os.path.join(app.config['UPLOAD_FOLDER'], temp_pdf)
            if os.path.exists(temp_path):
                new_filename = f'paper_{paper_id}_{secure_filename(temp_pdf)}'
                new_path = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
                os.rename(temp_path, new_path)
                conn.execute('UPDATE papers SET pdf_path=? WHERE id=?', (new_filename, paper_id))

    return jsonify({'id': paper_id})


@app.route('/paper/<int:paper_id>')
def paper_detail(paper_id):
    with get_db() as conn:
        paper = conn.execute('SELECT * FROM papers WHERE id = ?', (paper_id,)).fetchone()
        if not paper:
            return redirect(url_for('index'))
        project = conn.execute('SELECT * FROM projects WHERE id = ?', (paper['project_id'],)).fetchone()
        all_projects = conn.execute('SELECT id, name FROM projects ORDER BY name').fetchall()
    citations = {s: _generate_citation(dict(paper), s) for s in VALID_STYLES}
    return render_template('paper_detail.html', paper=paper, project=project,
                           all_projects=all_projects, citations=citations)


@app.route('/paper/<int:paper_id>/update', methods=['POST'])
def update_paper(paper_id):
    data = request.json
    tags = json.dumps(data.get('tags', []))
    year = data.get('year')
    try:
        year = int(year) if year else None
    except (ValueError, TypeError):
        year = None

    with get_db() as conn:
        conn.execute('''
            UPDATE papers SET title=?, authors=?, abstract=?, doi=?, url=?, venue=?,
                publication_date=?, year=?, tags=?, notes=?, read_status=?,
                updated_at=datetime('now','localtime')
            WHERE id=?
        ''', (
            data.get('title', ''),
            data.get('authors', ''),
            data.get('abstract', ''),
            data.get('doi', ''),
            data.get('url', ''),
            data.get('venue', ''),
            data.get('publication_date', ''),
            year,
            tags,
            data.get('notes', ''),
            data.get('read_status', 'unread'),
            paper_id,
        ))
    return jsonify({'success': True})


@app.route('/paper/<int:paper_id>/delete', methods=['POST'])
def delete_paper(paper_id):
    with get_db() as conn:
        paper = conn.execute('SELECT pdf_path FROM papers WHERE id = ?', (paper_id,)).fetchone()
        if paper and paper['pdf_path']:
            pdf_full = os.path.join(app.config['UPLOAD_FOLDER'], paper['pdf_path'])
            if os.path.exists(pdf_full):
                os.remove(pdf_full)
        conn.execute('DELETE FROM papers WHERE id = ?', (paper_id,))
    return jsonify({'success': True})


@app.route('/paper/<int:paper_id>/refresh_citations', methods=['POST'])
def refresh_citations(paper_id):
    with get_db() as conn:
        paper = conn.execute('SELECT doi, url FROM papers WHERE id = ?', (paper_id,)).fetchone()
        if not paper:
            return jsonify({'error': '論文が見つかりません'}), 404

        doi = paper['doi']
        arxiv_id = None
        if 'arxiv.org' in (paper['url'] or ''):
            arxiv_id = paper['url'].split('arxiv.org/abs/')[-1].split('v')[0]

        count = fetch_semantic_scholar_citations(doi=doi or None, arxiv_id=arxiv_id)
        if count is None:
            return jsonify({'error': '被引用数を取得できませんでした'}), 404

        conn.execute(
            "UPDATE papers SET citation_count=?, citation_updated_at=datetime('now','localtime') WHERE id=?",
            (count, paper_id)
        )
    return jsonify({'citation_count': count})


@app.route('/paper/<int:paper_id>/upload_pdf', methods=['POST'])
def upload_pdf(paper_id):
    if 'pdf' not in request.files:
        return jsonify({'error': 'ファイルが選択されていません'}), 400
    file = request.files['pdf']
    if file.filename == '':
        return jsonify({'error': 'ファイルが選択されていません'}), 400
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'PDFファイルのみアップロードできます'}), 400

    filename = secure_filename(f"paper_{paper_id}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    with get_db() as conn:
        old = conn.execute('SELECT pdf_path FROM papers WHERE id = ?', (paper_id,)).fetchone()
        if old and old['pdf_path'] and old['pdf_path'] != filename:
            old_path = os.path.join(app.config['UPLOAD_FOLDER'], old['pdf_path'])
            if os.path.exists(old_path):
                os.remove(old_path)
        conn.execute("UPDATE papers SET pdf_path=? WHERE id=?", (filename, paper_id))

    return jsonify({'success': True, 'filename': filename})


@app.route('/paper/<int:paper_id>/pdf')
def view_pdf(paper_id):
    with get_db() as conn:
        paper = conn.execute('SELECT pdf_path FROM papers WHERE id = ?', (paper_id,)).fetchone()
        if not paper or not paper['pdf_path']:
            return 'PDFが見つかりません', 404
    return send_from_directory(app.config['UPLOAD_FOLDER'], paper['pdf_path'])


@app.route('/paper/<int:paper_id>/move', methods=['POST'])
def move_paper(paper_id):
    data = request.json
    new_project_id = data.get('project_id')
    with get_db() as conn:
        conn.execute('UPDATE papers SET project_id=? WHERE id=?', (new_project_id, paper_id))
    return jsonify({'success': True})


def _generate_citation(paper, style='APA'):
    """論文の引用テキストをスタイル別に生成する

    APA  : Authors (Year). Title. Venue. https://doi.org/DOI
    IEEE : Authors, "Title," Venue, Year. doi: DOI
    MLA  : Authors. "Title." Venue, Year. URL.
    """
    authors = (paper.get('authors') or '').strip()
    title   = (paper.get('title')   or '').strip()
    venue   = (paper.get('venue')   or '').strip()
    year    = paper.get('year') or ''
    doi     = (paper.get('doi') or '').strip()
    url     = (paper.get('url') or '').strip()
    link    = f'https://doi.org/{doi}' if doi else url

    if style == 'IEEE':
        parts = []
        if authors:
            parts.append(authors + ',')
        if title:
            parts.append(f'"{title},"')
        venue_year = ', '.join(filter(None, [venue, str(year) if year else '']))
        if venue_year:
            parts.append(venue_year + '.')
        if doi:
            parts.append(f'doi: {doi}')
        elif url:
            parts.append(url)
        return ' '.join(parts)

    elif style == 'MLA':
        parts = []
        if authors:
            parts.append(authors + '.')
        if title:
            parts.append(f'"{title}."')
        venue_year = ', '.join(filter(None, [venue, str(year) if year else '']))
        if venue_year:
            parts.append(venue_year + '.')
        if link:
            parts.append(link + '.')
        return ' '.join(parts)

    else:  # APA（デフォルト）
        parts = []
        if authors:
            parts.append(authors)
        if year:
            parts.append(f'({year}).')
        if title:
            parts.append(f'{title}.')
        if venue:
            parts.append(f'{venue}.')
        if link:
            parts.append(link)
        return ' '.join(parts)


VALID_STYLES = ('APA', 'IEEE', 'MLA')


@app.route('/paper/<int:paper_id>/citation_text')
def paper_citation_text(paper_id):
    """単体論文の引用テキストをダウンロード（?style=APA/IEEE/MLA）"""
    style = request.args.get('style', 'APA').upper()
    if style not in VALID_STYLES:
        style = 'APA'
    with get_db() as conn:
        paper = conn.execute('SELECT * FROM papers WHERE id = ?', (paper_id,)).fetchone()
        if not paper:
            return 'Not found', 404
    text = _generate_citation(dict(paper), style)
    return text, 200, {
        'Content-Type': 'text/plain; charset=utf-8',
        'Content-Disposition': f'attachment; filename="citation_{style}_{paper_id}.txt"',
    }


@app.route('/project/<int:project_id>/citation_text')
def project_citation_text(project_id):
    """プロジェクト内全論文の引用テキストを一括ダウンロード（?style=APA/IEEE/MLA）"""
    style = request.args.get('style', 'APA').upper()
    if style not in VALID_STYLES:
        style = 'APA'
    with get_db() as conn:
        project = conn.execute('SELECT name FROM projects WHERE id = ?', (project_id,)).fetchone()
        if not project:
            return 'Not found', 404
        papers = conn.execute(
            'SELECT * FROM papers WHERE project_id = ? ORDER BY year DESC, created_at DESC',
            (project_id,)
        ).fetchall()
    entries = [
        f'[{i + 1}] {_generate_citation(dict(p), style)}'
        for i, p in enumerate(papers)
    ]
    text = '\n\n'.join(entries)
    safe_name = re.sub(r'[^\w\-]', '_', project['name'])
    return text, 200, {
        'Content-Type': 'text/plain; charset=utf-8',
        'Content-Disposition': f'attachment; filename="{safe_name}_{style}.txt"',
    }


@app.route('/paper/<int:paper_id>/related_papers')
def related_papers(paper_id):
    """Semantic Scholar APIで参考文献・被引用論文を取得"""
    with get_db() as conn:
        paper = conn.execute('SELECT doi, url FROM papers WHERE id = ?', (paper_id,)).fetchone()
        if not paper:
            return jsonify({'error': '論文が見つかりません'}), 404

    doi = paper['doi'] or ''
    url = paper['url'] or ''
    arxiv_id = ''
    if 'arxiv.org' in url:
        arxiv_id = url.split('arxiv.org/abs/')[-1].split('v')[0]

    if doi:
        ss_id = f'DOI:{doi}'
    elif arxiv_id:
        ss_id = f'ARXIV:{arxiv_id}'
    else:
        return jsonify({'error': 'DOIまたはArXiv IDが必要です（関連論文の取得にはどちらかが必要です）'}), 400

    fields = 'title,authors,year,venue,citationCount,externalIds'
    headers = {'User-Agent': 'PaperManager/1.0'}

    def parse_paper(p):
        ext = p.get('externalIds') or {}
        p_doi = ext.get('DOI', '')
        p_arxiv = ext.get('ArXiv', '')
        link = ''
        if p_doi:
            link = f'https://doi.org/{p_doi}'
        elif p_arxiv:
            link = f'https://arxiv.org/abs/{p_arxiv}'
        return {
            'title':          p.get('title', ''),
            'authors':        ', '.join(a.get('name', '') for a in (p.get('authors') or [])[:3]),
            'year':           p.get('year'),
            'venue':          p.get('venue', ''),
            'citation_count': p.get('citationCount', 0),
            'link':           link,
        }

    try:
        references, citations = [], []

        ref_res = requests.get(
            f'https://api.semanticscholar.org/graph/v1/paper/{ss_id}/references'
            f'?fields={fields}&limit=10',
            timeout=10, headers=headers
        )
        if ref_res.status_code == 200:
            references = [
                parse_paper(item.get('citedPaper', {}))
                for item in ref_res.json().get('data', [])
                if item.get('citedPaper', {}).get('title')
            ]

        cit_res = requests.get(
            f'https://api.semanticscholar.org/graph/v1/paper/{ss_id}/citations'
            f'?fields={fields}&limit=10',
            timeout=10, headers=headers
        )
        if cit_res.status_code == 200:
            citations = [
                parse_paper(item.get('citingPaper', {}))
                for item in cit_res.json().get('data', [])
                if item.get('citingPaper', {}).get('title')
            ]

        return jsonify({'references': references, 'citations': citations})

    except Exception as e:
        print(f"Related papers error: {e}")
        return jsonify({'error': f'関連論文の取得に失敗しました: {str(e)}'}), 500


@app.route('/paper/<int:paper_id>/pdf_references')
def pdf_references(paper_id):
    """添付PDFから参考文献リストを抽出する"""
    with get_db() as conn:
        paper = conn.execute('SELECT pdf_path FROM papers WHERE id = ?', (paper_id,)).fetchone()
        if not paper:
            return jsonify({'error': '論文が見つかりません'}), 404
        if not paper['pdf_path']:
            return jsonify({'error': 'PDFが添付されていません'}), 400

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], paper['pdf_path'])
    if not os.path.exists(filepath):
        return jsonify({'error': 'PDFファイルが見つかりません'}), 404

    entries = _extract_references_from_pdf(filepath)
    if not entries:
        return jsonify({
            'error': '参考文献セクションが見つかりませんでした。'
                     '（段組みPDFや画像スキャンPDFでは取得できないことがあります）'
        }), 404

    results = []
    for entry in entries:
        doi = _extract_doi(entry['raw'])
        # arXiv ID の抽出（例: arXiv:2310.01234 / arXiv 2310.01234）
        arxiv_m = re.search(r'arXiv[:\s]+(\d{4}\.\d{4,5})', entry['raw'], re.IGNORECASE)
        arxiv_id = arxiv_m.group(1) if arxiv_m else ''

        if doi:
            link = f'https://doi.org/{doi}'
        elif arxiv_id:
            link = f'https://arxiv.org/abs/{arxiv_id}'
        else:
            link = ''

        results.append({
            'num':   entry['num'],
            'raw':   entry['raw'],
            'doi':   doi,
            'arxiv': arxiv_id,
            'link':  link,
        })

    return jsonify({'references': results, 'total': len(entries)})


@app.route('/paper/<int:paper_id>/translate_abstract', methods=['POST'])
def translate_abstract(paper_id):
    """アブストラクトを日本語に翻訳する（無料・GoogleTranslator使用）"""
    with get_db() as conn:
        paper = conn.execute('SELECT abstract FROM papers WHERE id = ?', (paper_id,)).fetchone()
        if not paper:
            return jsonify({'error': '論文が見つかりません'}), 404
        abstract = paper['abstract']

    if not abstract or not abstract.strip():
        return jsonify({'error': 'アブストラクトがありません'}), 400

    try:
        from deep_translator import GoogleTranslator

        # 既に日本語の場合はそのまま返す（ひらがな・カタカナ・漢字を含む場合）
        ja_ratio = sum(1 for c in abstract if '　' <= c <= '鿿' or '゠' <= c <= 'ヿ') / max(len(abstract), 1)
        if ja_ratio > 0.1:
            return jsonify({'translated': abstract, 'note': 'すでに日本語です'})

        # GoogleTranslator の上限（約5000文字）を超える場合は文単位で分割
        MAX_LEN = 4500
        if len(abstract) <= MAX_LEN:
            translated = GoogleTranslator(source='auto', target='ja').translate(abstract)
        else:
            chunks = []
            current = ''
            for sentence in re.split(r'(?<=[.!?])\s+', abstract):
                if len(current) + len(sentence) + 1 <= MAX_LEN:
                    current = (current + ' ' + sentence).strip()
                else:
                    if current:
                        chunks.append(current)
                    current = sentence
            if current:
                chunks.append(current)

            translated_parts = [
                GoogleTranslator(source='auto', target='ja').translate(chunk)
                for chunk in chunks
            ]
            translated = '　'.join(translated_parts)

        return jsonify({'translated': translated})

    except Exception as e:
        print(f"Translation error: {e}")
        return jsonify({'error': f'翻訳に失敗しました: {str(e)}'}), 500


@app.route('/search')
def search():
    q = request.args.get('q', '').strip()
    project_id = request.args.get('project_id')
    if not q:
        return jsonify([])

    like = f'%{q}%'
    with get_db() as conn:
        if project_id:
            rows = conn.execute('''
                SELECT pa.*, pr.name as project_name FROM papers pa
                JOIN projects pr ON pa.project_id = pr.id
                WHERE pa.project_id = ? AND (pa.title LIKE ? OR pa.authors LIKE ? OR pa.abstract LIKE ? OR pa.tags LIKE ?)
                ORDER BY pa.created_at DESC LIMIT 50
            ''', (project_id, like, like, like, like)).fetchall()
        else:
            rows = conn.execute('''
                SELECT pa.*, pr.name as project_name FROM papers pa
                JOIN projects pr ON pa.project_id = pr.id
                WHERE pa.title LIKE ? OR pa.authors LIKE ? OR pa.abstract LIKE ? OR pa.tags LIKE ?
                ORDER BY pa.created_at DESC LIMIT 50
            ''', (like, like, like, like)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/bookmarklet')
def bookmarklet_page():
    """拡張機能設定ページ / ポップアップ"""
    url = request.args.get('url', '').strip()
    with get_db() as conn:
        projects = conn.execute('SELECT id, name FROM projects ORDER BY name').fetchall()
    if url:
        return render_template('bookmarklet_popup.html', url=url, projects=projects)
    ext_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chrome_extension')
    return render_template('bookmarklet_setup.html', projects=projects, ext_path=ext_path)


def _doi_from_url_path(url):
    """URLのパスに埋め込まれた DOI を抽出する（ACM・Springer 等）。
    例: https://dl.acm.org/doi/10.1145/3292500.3330701
        https://link.springer.com/article/10.1007/s00453-020-00123-4
    """
    m = re.search(r'/(10\.\d{4,9}/[^\s?&#]+)', url)
    if m:
        candidate = m.group(1).rstrip('.,;:)')
        if len(candidate) > 10:
            return candidate
    return ''


def _fetch_page_doi_and_resolve(url):
    """Webページの meta タグ / JSON-LD から DOI・メタデータを抽出する。
    citation_doi, dc.identifier, og:description などに対応。"""
    try:
        from bs4 import BeautifulSoup
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; PaperManager/1.0)',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        resp = requests.get(url, timeout=12, headers=headers)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, 'html.parser')

        # --- meta タグから DOI を探す ---
        doi = ''
        for name in ('citation_doi', 'dc.identifier', 'DC.identifier', 'prism.doi'):
            tag = soup.find('meta', attrs={'name': name})
            if tag and tag.get('content'):
                candidate = _clean_doi(tag['content'].strip())
                if candidate.startswith('10.'):
                    doi = candidate
                    break

        # --- JSON-LD から DOI を探す ---
        if not doi:
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    ld = json.loads(script.string or '')
                    items = ld if isinstance(ld, list) else [ld]
                    for item in items:
                        raw = item.get('identifier') or item.get('@id') or ''
                        if isinstance(raw, list):
                            raw = next(
                                (r.get('value', '') for r in raw
                                 if isinstance(r, dict) and 'doi' in r.get('propertyID', '').lower()),
                                ''
                            )
                        candidate = _clean_doi(str(raw))
                        if candidate.startswith('10.'):
                            doi = candidate
                            break
                    if doi:
                        break
                except Exception:
                    pass

        # DOI が取れた場合は API で解決を試みる
        if doi:
            result, _ = _resolve_doi(doi)
            if result:
                return result

        # --- meta タグから直接メタデータを組み立てる（DOI解決失敗時のフォールバック） ---
        def meta_content(name_val=None, prop_val=None):
            tag = None
            if name_val:
                tag = soup.find('meta', attrs={'name': name_val})
            if not tag and prop_val:
                tag = soup.find('meta', attrs={'property': prop_val})
            return (tag.get('content') or '').strip() if tag else ''

        title = (meta_content('citation_title') or
                 meta_content('dc.title', 'og:title') or
                 (soup.title.string.strip() if soup.title else ''))

        authors_tags = soup.find_all('meta', attrs={'name': 'citation_author'})
        authors = ', '.join(t.get('content', '').strip() for t in authors_tags if t.get('content'))

        abstract = (meta_content('citation_abstract') or
                    meta_content('dc.description', 'og:description'))

        venue = (meta_content('citation_journal_title') or
                 meta_content('citation_conference_title'))

        year_str = meta_content('citation_publication_date') or meta_content('citation_year') or ''
        year = None
        pub_date = ''
        if year_str:
            pub_date = year_str[:10]
            try:
                year = int(year_str[:4])
            except ValueError:
                year = None

        if not title:
            return None

        return {
            'title':            title,
            'authors':          authors,
            'abstract':         abstract,
            'doi':              doi,
            'url':              url,
            'venue':            venue,
            'publication_date': pub_date,
            'year':             year,
            'citation_count':   0,
        }
    except Exception as e:
        print(f"Page scrape error: {e}")
        return None


@app.route('/bookmarklet/fetch')
def bookmarklet_fetch():
    """URLからメタデータを取得（拡張機能ポップアップ用Ajax）。
    4段階フォールバック: arXiv → doi.org → URLパス中のDOI → ページスクレイピング
    """
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify({'error': 'URLが指定されていません'}), 400

    result = None

    # Step 1: arXiv (abs / pdf / html)
    if 'arxiv.org' in url:
        result = fetch_arxiv(url)
        if not result:
            m = re.search(r'arxiv\.org/(?:abs|pdf|html)/([0-9]+\.[0-9]+)', url)
            if m:
                result = fetch_semantic_scholar(arxiv_id=m.group(1))

    # Step 2: doi.org URL
    if not result and 'doi.org/' in url:
        doi = _clean_doi(url)
        if doi.startswith('10.'):
            result, _ = _resolve_doi(doi)

    # Step 3: DOI がURLパスに埋め込まれている (ACM, Springer, IEEE 等)
    if not result:
        doi = _doi_from_url_path(url)
        if doi:
            result, _ = _resolve_doi(doi)

    # Step 4: ページの meta タグ / JSON-LD からスクレイピング
    if not result:
        result = _fetch_page_doi_and_resolve(url)

    if not result:
        return jsonify({'error': 'メタデータを取得できませんでした。手動で入力してください。'}), 404

    # URL が空の場合は元URLを補完
    if not result.get('url'):
        result['url'] = url

    # 被引用数補完
    if not result.get('citation_count') and result.get('doi'):
        c = fetch_semantic_scholar_citations(doi=result['doi'])
        if c is not None:
            result['citation_count'] = c

    return jsonify(result)


@app.route('/bookmarklet/add', methods=['POST'])
def bookmarklet_add():
    """ブックマークレットポップアップから論文を追加"""
    data = request.json
    project_id = data.get('project_id')
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'タイトルが空です'}), 400
    if not project_id:
        return jsonify({'error': 'プロジェクトを選択してください'}), 400

    year = data.get('year')
    try:
        year = int(year) if year else None
    except (ValueError, TypeError):
        year = None

    with get_db() as conn:
        cur = conn.execute('''
            INSERT INTO papers (project_id, title, authors, abstract, doi, url, venue,
                                publication_date, year, citation_count, tags, read_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            project_id, title,
            data.get('authors', ''),
            data.get('abstract', ''),
            data.get('doi', ''),
            data.get('url', ''),
            data.get('venue', ''),
            data.get('publication_date', ''),
            year,
            data.get('citation_count', 0),
            json.dumps([]),
            'unread',
        ))
        paper_id = cur.lastrowid
    return jsonify({'id': paper_id, 'title': title})


if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5001)
