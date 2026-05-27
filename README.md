# 📄 Aurelia — 論文管理アプリ

研究者・大学院生向けのローカル動作する論文管理ツールです。  
PDF から自動でメタデータを抽出し、引用テキスト生成・関連論文取得・日本語翻訳などを無料で行えます。

---

## ✨ 主な機能

| 機能 | 説明 |
|------|------|
| 📁 プロジェクト管理 | 研究テーマ別に論文を整理 |
| 🔍 メタデータ自動取得 | DOI / arXiv ID 入力で著者・タイトル等を自動入力 |
| 📄 PDF アップロード | PDF からタイトル・DOI・アブストを自動抽出 |
| 📚 参考文献抽出 | 添付 PDF から参考文献リストを抽出（2列レイアウト対応） |
| 🌐 関連論文 | Semantic Scholar から参考文献・被引用論文を取得 |
| 🌏 日本語翻訳 | アブストを Google 翻訳で日本語化（無料） |
| 📝 Markdown メモ | 論文ごとにメモをMarkdownで記録 |
| 📋 引用テキスト生成 | APA / IEEE / MLA スタイルで引用テキストを出力 |
| 🔌 Chrome 拡張機能 | arXiv・ACM・Springer 等の論文ページからワンクリック登録 |

---

## 🖥 動作環境

- Python 3.10 以上
- Google Chrome（拡張機能を使う場合）
- インターネット接続（外部 API 使用のため）

---

## 🚀 セットアップ

### 1. リポジトリをクローン

```bash
git clone https://github.com/your-username/aurelia.git
cd aurelia
```

### 2. 仮想環境を作成・有効化（推奨）

```bash
python3 -m venv venv

# Mac / Linux
source venv/bin/activate

# Windows
venv\Scripts\activate
```

### 3. ライブラリをインストール

```bash
pip install -r requirements.txt
```

### 4. アプリを起動

```bash
python3 app.py
```

ブラウザで [http://localhost:5001](http://localhost:5001) を開くと使えます。

> **初回起動時にデータベースが自動作成されます。**

---

## 🔌 Chrome 拡張機能のセットアップ

arXiv・ACM・Springer などの論文ページからワンクリックで Aurelia に追加できます。

1. Chrome で `chrome://extensions` を開く
2. 右上の「**デベロッパーモード**」をオンにする
3. 「**パッケージ化されていない拡張機能を読み込む**」をクリック
4. このリポジトリの `chrome_extension/` フォルダを選択

> Aurelia サーバー（`python3 app.py`）が起動している状態で使用してください。

---

## 📁 ファイル構成

```
aurelia/
├── app.py                   # Flask アプリ本体
├── requirements.txt         # 依存ライブラリ
├── uploads/                 # PDF 保存先（自動生成）
├── papers.db                # SQLite DB（自動生成）
├── chrome_extension/        # Chrome 拡張機能
│   ├── manifest.json
│   ├── popup.html
│   ├── popup.css
│   └── popup.js
├── templates/               # HTML テンプレート
│   ├── base.html
│   ├── index.html
│   ├── project.html
│   ├── paper_detail.html
│   └── bookmarklet_setup.html
└── static/
    ├── css/style.css
    └── js/main.js
```

---

## 🔗 使用している外部 API（すべて無料）

| API | 用途 |
|-----|------|
| [arXiv API](https://arxiv.org/help/api) | arXiv 論文のメタデータ取得 |
| [CrossRef API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | DOI から論文情報取得 |
| [Semantic Scholar API](https://www.semanticscholar.org/product/api) | 被引用数・関連論文取得 |
| Google 翻訳（deep-translator） | アブストの日本語翻訳 |

API キーは不要です。

---

## 🛠 技術スタック

- **バックエンド**: Python / Flask / SQLite
- **PDF 処理**: pymupdf・pdfminer.six・pypdf
- **フロントエンド**: Bootstrap 5 / Vanilla JS / marked.js
- **Chrome 拡張**: Manifest V3

---

## 📝 ライセンス

MIT License
