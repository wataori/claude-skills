---
name: notion-reader
description: >
  Notion のページ内容・データベースレコードをローカルに取得するスキル。
  ページをMarkdown形式で保存したり、データベースをCSV/JSONで取得したり、
  キーワードでページを検索できる。複数ワークスペース（会社ごと）のアカウントを
  プロファイル名で切り替えられる。
  ユーザーが「Notionのページを取得」「Notionのドキュメントを読み込んで」
  「Notionのデータベースをダウンロード」「Notionを検索して」などと言ったとき、
  またはNotionのURLが含まれているときは必ずこのスキルを使う。
---

# Notion Reader

Notion のページ・データベース・検索結果をローカルに取得するスキル。

## 前提条件

- `pip install requests` がインストール済みであること
- プロファイル設定ファイル `~/.notion-profiles.json` に Integration Token が登録済みであること

## プロファイルのセットアップ

初回または新しいワークスペースを追加する場合：

```bash
python3 <SKILL_DIR>/scripts/notion_reader.py setup
```

対話形式でプロファイル名（例: `work-a`、`work-b`）と Integration Token を登録する。

設定ファイルは `~/.notion-profiles.json` に保存される：
```json
{
  "work-a": "secret_xxxxxxxxxx",
  "work-b": "secret_yyyyyyyyyy"
}
```

Integration Token の取得方法：
1. https://www.notion.so/my-integrations を開く
2. 「New integration」→ 名前を入力 → Submit
3. 「Internal Integration Token」をコピー
4. 対象ページ／データベースの「...」→「Connect to」→作成したIntegrationを追加

## ワークフロー

### Step 1: プロファイル確認

```bash
python3 <SKILL_DIR>/scripts/notion_reader.py profiles
```

登録済みプロファイルの一覧を表示。

### Step 2: ユーザーの要求を解釈

| ユーザーの意図 | コマンド |
|---|---|
| 「このNotionページを取得して」+ URL | `page <URL>` |
| 「このデータベースをCSVで取得して」+ URL | `database <URL> --format csv` |
| 「〇〇というページを検索して」 | `search "〇〇"` |
| 「〇〇社のアカウントで」「〇〇のプロファイルで」 | `--profile <プロファイル名>` |
| プロファイル未指定 | 登録済み一覧を表示して選択を促す（1つだけなら自動選択） |

### Step 3: スクリプトを実行

**ページ取得（Markdown保存）**
```bash
python3 <SKILL_DIR>/scripts/notion_reader.py page <URL-or-ID> \
  --profile <プロファイル名> \
  [--output <保存先ディレクトリ>]
```

**データベース取得**
```bash
python3 <SKILL_DIR>/scripts/notion_reader.py database <URL-or-ID> \
  --profile <プロファイル名> \
  [--format csv|json] \
  [--output <保存先ディレクトリ>]
```

**キーワード検索**
```bash
python3 <SKILL_DIR>/scripts/notion_reader.py search "キーワード" \
  --profile <プロファイル名> \
  [--limit 10]
```

**プロファイル管理**
```bash
python3 <SKILL_DIR>/scripts/notion_reader.py setup     # 新規プロファイル追加
python3 <SKILL_DIR>/scripts/notion_reader.py profiles  # 一覧表示
```

### Step 4: 結果を報告

- **page**: 保存した Markdown ファイルのパスと文字数
- **database**: 保存したファイルのパスと行数
- **search**: ヒットしたページの一覧（タイトル・URL・最終更新日）

## エラーハンドリング

| エラー | 対処 |
|---|---|
| プロファイルが見つからない | `setup` コマンドで登録を促す |
| 401 Unauthorized | Integration Token が無効。再発行してプロファイルを更新 |
| 403 Forbidden | ページに Integration が接続されていない。Notion 側で「Connect to」設定が必要 |
| プロファイル未指定で複数登録あり | `profiles` コマンドで一覧を表示してユーザーに選択を求める |
