#!/usr/bin/env python3
"""
Google Meet データダウンローダー

Google Drive から Meet の録画・文字起こし・Geminiメモを取得してローカルに保存する。
gcloud CLI で認証し、Google Drive API v3 を使用。
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: requests ライブラリが必要です。`pip install requests` を実行してください。")
    sys.exit(1)


# ファイル種別の判定パターン
FILE_PATTERNS = {
    "recording": {
        "mime": ["video/mp4", "video/quicktime"],
        "name_keywords": [],  # MIMEタイプで判別
    },
    "transcript": {
        "mime": ["application/vnd.google-apps.document"],
        "name_keywords": ["文字起こし", "transcript", "字幕"],
    },
    "notes": {
        "mime": ["application/vnd.google-apps.document"],
        "name_keywords": ["会議メモ", "meeting notes", "ミーティング メモ", "gemini notes"],
    },
}


def list_authenticated_accounts() -> list[str]:
    """gcloud に登録済みのアカウント一覧を返す"""
    result = subprocess.run(
        ["gcloud", "auth", "list", "--format=value(account)"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [a.strip() for a in result.stdout.strip().splitlines() if a.strip()]


def get_access_token(account: str | None = None) -> str:
    """gcloud CLI でアクセストークンを取得する"""
    cmd = ["gcloud", "auth", "print-access-token"]
    if account:
        cmd.append(f"--account={account}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("Error: gcloud 認証トークンの取得に失敗しました。")
        if account:
            print(f"  アカウント '{account}' が認証されていない可能性があります。")
            print(f"  `gcloud auth login {account}` を実行してください。")
        else:
            print("  `gcloud auth login` を実行して認証してください。")
        accounts = list_authenticated_accounts()
        if accounts:
            print(f"  認証済みアカウント: {', '.join(accounts)}")
        print(f"  詳細: {result.stderr.strip()}")
        sys.exit(1)
    return result.stdout.strip()


def build_drive_query(date_from: str | None, date_to: str | None, query: str | None, types: list[str]) -> str:
    """Google Drive API の検索クエリを構築する"""
    parts = []

    # キーワード検索
    if query:
        parts.append(f"name contains '{query}'")

    # 日付範囲
    if date_from:
        parts.append(f"createdTime >= '{date_from}T00:00:00'")
    if date_to:
        parts.append(f"createdTime <= '{date_to}T23:59:59'")

    # MIMEタイプ絞り込み
    mime_types = set()
    for t in types:
        for mime in FILE_PATTERNS.get(t, {}).get("mime", []):
            mime_types.add(mime)

    if mime_types:
        mime_conditions = [f"mimeType='{m}'" for m in mime_types]
        parts.append(f"({' or '.join(mime_conditions)})")

    parts.append("trashed=false")

    return " and ".join(parts)


def classify_file(file: dict, requested_types: list[str]) -> str | None:
    """ファイルの種別を判定する。該当しない場合は None を返す"""
    mime = file.get("mimeType", "")
    name_lower = file.get("name", "").lower()

    for file_type in requested_types:
        pattern = FILE_PATTERNS.get(file_type, {})
        mime_list = pattern.get("mime", [])
        keywords = pattern.get("name_keywords", [])

        if mime not in mime_list:
            continue

        # 録画はMIMEタイプだけで判別
        if file_type == "recording":
            return "recording"

        # 文字起こし・メモはキーワードで判別
        for kw in keywords:
            if kw.lower() in name_lower:
                return file_type

    return None


def list_meet_files(token: str, date_from: str | None, date_to: str | None, query: str | None, types: list[str]) -> list[dict]:
    """Google Drive から Meet 関連ファイルを検索する"""
    headers = {"Authorization": f"Bearer {token}"}
    q = build_drive_query(date_from, date_to, query, types)

    print(f"検索クエリ: {q}")

    url = "https://www.googleapis.com/drive/v3/files"
    params = {
        "q": q,
        "fields": "nextPageToken,files(id,name,mimeType,createdTime,size,parents)",
        "orderBy": "createdTime desc",
        "pageSize": 100,
    }

    all_files = []
    while True:
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code == 401:
            print("Error: 認証エラー。`gcloud auth login` で再認証してください。")
            sys.exit(1)
        if resp.status_code == 403:
            print("Error: アクセス権限がありません。Google Drive の権限を確認してください。")
            sys.exit(1)
        resp.raise_for_status()

        data = resp.json()
        all_files.extend(data.get("files", []))

        next_page = data.get("nextPageToken")
        if not next_page:
            break
        params["pageToken"] = next_page

    return all_files


def sanitize_filename(name: str) -> str:
    """ファイル名に使えない文字を除去する"""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def get_or_create_meeting_dir(output_dir: Path, file: dict) -> Path:
    """ファイルの日付と名前から会議ディレクトリを作成または取得する"""
    created = file.get("createdTime", "")
    date_str = created[:10] if created else "unknown-date"

    # 会議名を推測（録画・文字起こし・メモで共通の名前部分を抽出）
    name = file.get("name", "")
    # 既知のサフィックスを除いて会議名を取得
    for suffix in ["の文字起こし", " Transcript", "文字起こし", "会議メモ", "Meeting notes", "ミーティング メモ"]:
        if suffix.lower() in name.lower():
            idx = name.lower().index(suffix.lower())
            name = name[:idx].strip()
            break

    safe_name = sanitize_filename(name) or "meet"
    dir_name = f"{date_str}_{safe_name}"

    meeting_dir = output_dir / dir_name
    meeting_dir.mkdir(parents=True, exist_ok=True)
    return meeting_dir


def download_binary(token: str, file_id: str, output_path: Path) -> None:
    """バイナリファイル（録画）をダウンロードする"""
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"

    with requests.get(url, headers=headers, stream=True) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0

        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):  # 1MB chunks
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
                    print(f"\r    [{bar}] {pct:.1f}% ({downloaded // 1024 // 1024}MB / {total // 1024 // 1024}MB)", end="", flush=True)
        print()


def export_doc_as_markdown(token: str, file_id: str, output_path: Path) -> None:
    """Google Doc を HTML でエクスポートし Markdown に変換して保存する"""
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"

    # まず HTML でエクスポート
    resp = requests.get(url, headers=headers, params={"mimeType": "text/html"})
    resp.raise_for_status()
    html_content = resp.text

    # html2text で Markdown に変換
    try:
        import html2text
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.body_width = 0  # 改行しない
        h.unicode_snob = True
        markdown = h.handle(html_content)
    except ImportError:
        # フォールバック: プレーンテキストとして取得
        print("    (html2text が見つからないのでプレーンテキストとして保存します)")
        resp2 = requests.get(url, headers=headers, params={"mimeType": "text/plain"})
        resp2.raise_for_status()
        markdown = resp2.text

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown)


def format_size(size_str: str | None) -> str:
    """ファイルサイズを人間が読みやすい形式に変換する"""
    if not size_str:
        return "不明"
    size = int(size_str)
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024:.1f}MB"
    else:
        return f"{size / 1024 / 1024 / 1024:.1f}GB"


def main():
    parser = argparse.ArgumentParser(
        description="Google Meet の録画・文字起こし・Geminiメモをダウンロードする"
    )
    parser.add_argument("--from", dest="date_from", help="開始日 (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", help="終了日 (YYYY-MM-DD)")
    parser.add_argument("--query", help="会議名のキーワード検索")
    parser.add_argument(
        "--types",
        default="recording,transcript,notes",
        help="取得する種別 (recording,transcript,notes のカンマ区切り)",
    )
    parser.add_argument(
        "--output",
        default=str(Path.home() / "Downloads" / "meet-data"),
        help="保存先ディレクトリ (デフォルト: ~/Downloads/meet-data/)",
    )
    parser.add_argument("--account", help="使用する Google アカウント (例: user@company-a.com)")
    parser.add_argument("--dry-run", action="store_true", help="ダウンロードせずにファイル一覧だけ表示する")
    args = parser.parse_args()

    types = [t.strip() for t in args.types.split(",")]
    output_dir = Path(args.output)

    print("=== Google Meet ダウンローダー ===")
    if args.account:
        print(f"アカウント: {args.account}")
    print(f"保存先: {output_dir}")
    if args.date_from:
        print(f"期間: {args.date_from} 〜 {args.date_to or '(未指定)'}")
    if args.query:
        print(f"キーワード: {args.query}")
    print(f"種別: {', '.join(types)}")
    print()

    # 認証
    print("認証トークンを取得中...")
    token = get_access_token(args.account)
    print(f"認証OK{f' ({args.account})' if args.account else ''}")
    print()

    # ファイル検索
    print("Google Drive を検索中...")
    files = list_meet_files(token, args.date_from, args.date_to, args.query, types)
    print(f"{len(files)} 件のファイルが見つかりました")
    print()

    if not files:
        print("ダウンロードするファイルがありません。")
        print("ヒント: 日付範囲を広げるか、キーワードを変更してみてください。")
        return

    # ファイルを種別で分類
    classified = []
    for f in files:
        file_type = classify_file(f, types)
        if file_type:
            classified.append((f, file_type))

    print(f"Meet 関連ファイル: {len(classified)} 件")
    print()

    if args.dry_run:
        print("--- ドライラン（実際のダウンロードはしません）---")
        for f, ftype in classified:
            created = f.get("createdTime", "")[:10]
            size = format_size(f.get("size"))
            type_label = {"recording": "録画", "transcript": "文字起こし", "notes": "Geminiメモ"}.get(ftype, ftype)
            print(f"  [{type_label}] {f['name']} ({size}) - {created}")
        return

    # ダウンロード
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded_files = []
    errors = []

    for i, (f, ftype) in enumerate(classified, 1):
        type_label = {"recording": "録画", "transcript": "文字起こし", "notes": "Geminiメモ"}.get(ftype, ftype)
        size = format_size(f.get("size"))
        print(f"[{i}/{len(classified)}] {type_label}: {f['name']} ({size})")

        meeting_dir = get_or_create_meeting_dir(output_dir, f)

        try:
            if ftype == "recording":
                safe_name = sanitize_filename(f["name"])
                output_path = meeting_dir / f"{safe_name}.mp4"
                download_binary(token, f["id"], output_path)
            else:
                filename = "transcript.md" if ftype == "transcript" else "meeting-notes.md"
                output_path = meeting_dir / filename
                export_doc_as_markdown(token, f["id"], output_path)
                print(f"    保存: {output_path.relative_to(output_dir)}")

            downloaded_files.append((output_path, type_label))
        except Exception as e:
            error_msg = f"  Error: {f['name']} のダウンロードに失敗しました: {e}"
            print(error_msg)
            errors.append(error_msg)

    # 結果サマリー
    print()
    print("=== 完了 ===")
    print(f"✓ {len(downloaded_files)} 件をダウンロードしました")
    print(f"  保存先: {output_dir}")

    if errors:
        print(f"\n⚠ {len(errors)} 件のエラーがありました:")
        for e in errors:
            print(f"  {e}")

    # JSON でも出力（他のツールが使いやすいように）
    result = {
        "downloaded": [
            {"path": str(p), "type": t}
            for p, t in downloaded_files
        ],
        "errors": errors,
        "output_dir": str(output_dir),
    }
    result_path = output_dir / "download_result.json"
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"  結果JSON: {result_path}")


if __name__ == "__main__":
    main()
