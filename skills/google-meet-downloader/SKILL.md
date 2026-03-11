---
name: google-meet-downloader
description: >
  Google Meet の会議データ（録画・文字起こし・Gemini会議メモ）を Google Drive から
  ローカルにダウンロードするスキル。日付や会議名でフィルタリングして一括取得できる。
  ユーザーが「Google Meet」「ミーティング録画」「文字起こしをダウンロード」
  「会議メモ を保存」「Meet の録画を取得」などと言ったとき、または Google Drive から
  Meet 関連のファイルを取得したいときは必ずこのスキルを使う。
  gcloud CLI を使って認証し、Google Drive API 経由でファイルを取得する。
---

# Google Meet ダウンローダー

Google Drive に保存された Google Meet のデータ（録画・文字起こし・Geminiメモ）を
ローカルにダウンロードするスキル。

## 前提条件

- `gcloud` CLI がインストール・設定済みであること
- `pip install requests html2text` がインストール済みであること
- Google Drive API へのアクセス権限があること（`gcloud auth login` で認証済み）

## ワークフロー

### Step 1: 依存確認

```bash
gcloud auth print-access-token 2>&1 | head -1  # 認証確認
python3 -c "import requests, html2text; print('OK')"  # ライブラリ確認
```

依存が欠けている場合はユーザーに通知してインストールを促す。

### Step 2: ユーザーの要求を解釈

ユーザーの発言から以下を読み取る：

| ユーザーの意図 | パラメータ |
|---|---|
| 「今日の」「昨日の」「先週の」 | 日付範囲（`--from` / `--to`）に変換 |
| 会議名・プロジェクト名のキーワード | `--query` に設定 |
| 「録画だけ」「文字起こしだけ」 | `--types` で絞る |
| ダウンロード先の指定 | `--output` に設定（未指定なら `~/Downloads/meet-data/` を提案） |
| 「〇〇社のアカウントで」「〇〇のアカウントで」など | `--account` にメールアドレスを設定 |

**アカウント指定について：**
- `--account` が未指定の場合は gcloud のアクティブアカウントを使用する
- アカウントを特定できない場合は、`gcloud auth list` で一覧を表示してユーザーに選択を求める
- 事前に `gcloud auth login <メールアドレス>` で各アカウントを認証しておく必要がある（初回のみ）

**日付変換の例：**
- 「今日」→ 当日の日付（YYYY-MM-DD 形式）
- 「昨日」→ 前日の日付
- 「今週」→ 月曜日〜今日
- 「先週」→ 先週の月曜〜日曜
- 「今月」→ 月初〜今日

### Step 3: スクリプトを実行

```bash
python3 <SKILL_DIR>/scripts/download_meet_data.py \
  [--from YYYY-MM-DD] \
  [--to YYYY-MM-DD] \
  [--query "会議名キーワード"] \
  [--types recording,transcript,notes] \
  [--output ~/Downloads/meet-data/] \
  [--account user@example.com]
```

`<SKILL_DIR>` はこのSKILL.mdが置かれたディレクトリのパス。

**`--types` の指定値：**
- `recording` → 録画（.mp4）
- `transcript` → 文字起こし（.md に変換）
- `notes` → Gemini会議メモ（.md に変換）
- 未指定なら全て取得

### Step 4: 結果を報告

ダウンロード完了後、以下を伝える：
- ダウンロードしたファイルの一覧（名前・種類・サイズ）
- 保存先ディレクトリ
- エラーがあれば原因と解決策

## ファイル整理ルール

ダウンロードしたファイルは以下の構造で保存する：

```
<output_dir>/
└── YYYY-MM-DD_会議名/
    ├── recording.mp4         （録画）
    ├── transcript.md         （文字起こし）
    └── meeting-notes.md      （Geminiメモ）
```

会議ごとにフォルダを作成し、関連ファイルをまとめる。

## Google Drive でのファイル検索ロジック

Meet のファイルは以下のパターンで識別する：

| 種類 | MIMEタイプ | 名前パターン |
|---|---|---|
| 録画 | `video/mp4` | `Meet` を含む、または `Meet Recordings` フォルダ内 |
| 文字起こし | `application/vnd.google-apps.document` | `文字起こし` または `Transcript` を含む |
| Geminiメモ | `application/vnd.google-apps.document` | `会議メモ`、`Meeting notes`、`Gemini によるメモ`、`Gemini memo` を含む |

## エラーハンドリング

| エラー | 対処 |
|---|---|
| `gcloud` 認証エラー | `gcloud auth login` を実行してもらう |
| 権限エラー (403) | Google Drive の共有設定を確認してもらう |
| ファイルが見つからない | 検索条件を広げるか、別のキーワードを試す |
| `html2text` 未インストール | `pip install html2text` を実行してもらう |
