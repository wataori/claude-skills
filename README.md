# claude-skills

wataori's personal Claude Code skills.

## Install

```bash
/plugin marketplace add wataori/claude-skills
/plugin install my-skills@wataori-claude-skills
```

## Skills

### google-meet-downloader

Google Meet の録画・文字起こし・Gemini会議メモを Google Drive からローカルにダウンロードする。

**前提条件:**
- `gcloud` CLI のインストールと認証
- `pip install requests html2text`

**セットアップ（複数アカウントを使う場合）:**
```bash
gcloud auth login user@company-a.com
gcloud auth login user@company-b.com
```

**使い方の例:**
- 「今日の録画をダウンロードして」
- 「先週のプロダクトレビューのGeminiメモを取得して」
- 「〇〇のアカウントで2月の全ミーティングを保存して」

---

### notion-reader

Notion のページをMarkdownで取得、データベースをCSV/JSONで取得、ページを検索する。

**前提条件:**
- `pip install requests`
- Notion Integration Token の発行（https://www.notion.so/my-integrations）

**セットアップ:**
```bash
python3 ~/.claude/plugins/cache/wataori-claude-skills/my-skills/*/skills/notion-reader/scripts/notion_reader.py setup
```

プロファイル名（例: `work-a`、`work-b`）と Integration Token を登録する。

**使い方の例:**
- 「このNotionページをMarkdownで取得して: https://www.notion.so/...」
- 「work-aのNotionで'スプリント'を検索して」
- 「work-bのデータベースをCSVでダウンロードして」
