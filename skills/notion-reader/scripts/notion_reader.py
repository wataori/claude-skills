#!/usr/bin/env python3
"""
Notion Reader

Notion のページ・データベース・検索結果をローカルに取得する。
Integration Token をプロファイルとして ~/.notion-profiles.json で管理。
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print("Error: requests ライブラリが必要です。`pip install requests` を実行してください。")
    sys.exit(1)

PROFILES_PATH = Path.home() / ".notion-profiles.json"
NOTION_API_VERSION = "2022-06-28"
NOTION_API_BASE = "https://api.notion.com/v1"


# ---------------------------------------------------------------------------
# プロファイル管理
# ---------------------------------------------------------------------------

def load_profiles() -> dict[str, str]:
    """プロファイル一覧（{名前: token}）を読み込む"""
    if not PROFILES_PATH.exists():
        return {}
    with open(PROFILES_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_profiles(profiles: dict[str, str]) -> None:
    """プロファイルを保存する"""
    PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)
    PROFILES_PATH.chmod(0o600)  # オーナーのみ読み書き可（トークン保護）


def resolve_token(profile: str | None) -> tuple[str, str]:
    """プロファイル名からトークンを解決する。(profile_name, token) を返す"""
    profiles = load_profiles()

    if not profiles:
        print("Error: プロファイルが登録されていません。")
        print("  `notion_reader.py setup` を実行して Integration Token を登録してください。")
        sys.exit(1)

    if profile:
        if profile not in profiles:
            print(f"Error: プロファイル '{profile}' が見つかりません。")
            print(f"  登録済み: {', '.join(profiles.keys())}")
            sys.exit(1)
        return profile, profiles[profile]

    # 未指定で1つだけなら自動選択
    if len(profiles) == 1:
        name = next(iter(profiles))
        print(f"プロファイル: {name}（自動選択）")
        return name, profiles[name]

    # 複数ある場合は一覧を表示して終了
    print("Error: --profile を指定してください。")
    print("  登録済みプロファイル:")
    for name in profiles:
        print(f"    {name}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Notion URL / ID ユーティリティ
# ---------------------------------------------------------------------------

def extract_notion_id(url_or_id: str) -> str:
    """Notion の URL または ID から UUID 形式のページ ID を抽出する"""
    # すでに UUID 形式の場合
    if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', url_or_id, re.I):
        return url_or_id

    # 32文字の16進数（ハイフンなし）
    if re.match(r'^[0-9a-f]{32}$', url_or_id, re.I):
        h = url_or_id.lower()
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"

    # URL から抽出
    parsed = urlparse(url_or_id)
    path = parsed.path.rstrip("/")

    # パスの末尾または ? の前にある 32 文字の16進数を探す
    # 例: /workspace/Title-32hexchars?v=...
    match = re.search(r'([0-9a-f]{32})(?:[?#]|$)', path + "?" , re.I)
    if match:
        h = match.group(1).lower()
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"

    # パスセグメントの末尾から探す（ハイフン区切りを含む場合）
    last_segment = path.split("/")[-1]
    # "Title-Name-32hexchars" のパターン
    match = re.search(r'([0-9a-f]{32})$', last_segment.replace("-", ""), re.I)
    if match:
        h = match.group(1).lower()
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"

    print(f"Error: Notion の ID を '{url_or_id}' から抽出できませんでした。")
    print("  Notion ページの URL または 32 文字の ID を指定してください。")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Notion API クライアント
# ---------------------------------------------------------------------------

def notion_get(endpoint: str, token: str, params: dict | None = None) -> dict:
    """Notion API の GET リクエスト"""
    resp = requests.get(
        f"{NOTION_API_BASE}{endpoint}",
        headers={"Authorization": f"Bearer {token}", "Notion-Version": NOTION_API_VERSION},
        params=params,
    )
    _handle_error(resp)
    return resp.json()


def notion_post(endpoint: str, token: str, payload: dict) -> dict:
    """Notion API の POST リクエスト"""
    resp = requests.post(
        f"{NOTION_API_BASE}{endpoint}",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
        },
        json=payload,
    )
    _handle_error(resp)
    return resp.json()


def _handle_error(resp: requests.Response) -> None:
    if resp.status_code == 401:
        print("Error: 認証失敗（401）。Integration Token が無効です。")
        print("  `notion_reader.py setup` でトークンを更新してください。")
        sys.exit(1)
    if resp.status_code == 403:
        print("Error: アクセス拒否（403）。")
        print("  対象ページ／データベースに Integration が接続されているか確認してください。")
        print("  Notion のページ右上「...」→「Connections」→ Integration を追加")
        sys.exit(1)
    if resp.status_code == 404:
        print("Error: ページが見つかりません（404）。ID または URL を確認してください。")
        sys.exit(1)
    if not resp.ok:
        print(f"Error: Notion API エラー ({resp.status_code}): {resp.text[:200]}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# ブロック → Markdown 変換
# ---------------------------------------------------------------------------

def fetch_blocks(block_id: str, token: str) -> list[dict]:
    """ブロックの子要素を再帰的に取得する"""
    blocks = []
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        data = notion_get(f"/blocks/{block_id}/children", token, params)
        blocks.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return blocks


def rich_text_to_str(rich_texts: list[dict]) -> str:
    """rich_text 配列をプレーンテキストに変換する"""
    result = []
    for rt in rich_texts:
        text = rt.get("plain_text", "")
        ann = rt.get("annotations", {})
        if ann.get("code"):
            text = f"`{text}`"
        if ann.get("bold"):
            text = f"**{text}**"
        if ann.get("italic"):
            text = f"*{text}*"
        if ann.get("strikethrough"):
            text = f"~~{text}~~"
        href = rt.get("href")
        if href:
            text = f"[{text}]({href})"
        result.append(text)
    return "".join(result)


def blocks_to_markdown(blocks: list[dict], token: str, indent: int = 0) -> str:
    """Notion ブロックのリストを Markdown 文字列に変換する"""
    lines = []
    prefix = "  " * indent

    for block in blocks:
        btype = block.get("type", "")
        bdata = block.get(btype, {})
        rich = bdata.get("rich_text", [])
        text = rich_text_to_str(rich)

        if btype == "paragraph":
            lines.append(f"{prefix}{text}\n")
        elif btype in ("heading_1", "heading_2", "heading_3"):
            level = int(btype[-1])
            lines.append(f"{prefix}{'#' * level} {text}\n")
        elif btype == "bulleted_list_item":
            lines.append(f"{prefix}- {text}")
        elif btype == "numbered_list_item":
            lines.append(f"{prefix}1. {text}")
        elif btype == "to_do":
            checked = "x" if bdata.get("checked") else " "
            lines.append(f"{prefix}- [{checked}] {text}")
        elif btype == "toggle":
            lines.append(f"{prefix}<details><summary>{text}</summary>\n")
        elif btype == "quote":
            lines.append(f"{prefix}> {text}")
        elif btype == "callout":
            icon = bdata.get("icon") or {}
            emoji = icon.get("emoji", "")
            lines.append(f"{prefix}> {emoji} {text}")
        elif btype == "code":
            lang = bdata.get("language", "")
            code_text = rich_text_to_str(rich)
            lines.append(f"{prefix}```{lang}\n{code_text}\n{prefix}```")
        elif btype == "divider":
            lines.append(f"{prefix}---")
        elif btype == "image":
            url = bdata.get("external", {}).get("url") or bdata.get("file", {}).get("url", "")
            caption = rich_text_to_str(bdata.get("caption", []))
            lines.append(f"{prefix}![{caption}]({url})")
        elif btype == "bookmark":
            url = bdata.get("url", "")
            caption = rich_text_to_str(bdata.get("caption", []))
            lines.append(f"{prefix}[{caption or url}]({url})")
        elif btype == "table_of_contents":
            pass  # 目次は省略
        elif btype == "child_page":
            title = bdata.get("title", "")
            child_id = block.get("id", "")
            lines.append(f"{prefix}📄 [{title}](https://www.notion.so/{child_id.replace('-', '')})")
        elif btype == "child_database":
            title = bdata.get("title", "")
            lines.append(f"{prefix}🗄️ {title}")

        # 子ブロックを再帰取得
        if block.get("has_children") and btype not in ("child_page", "child_database"):
            children = fetch_blocks(block["id"], token)
            lines.append(blocks_to_markdown(children, token, indent + 1))

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# page コマンド
# ---------------------------------------------------------------------------

def cmd_page(args: argparse.Namespace) -> None:
    profile_name, token = resolve_token(args.profile)
    page_id = extract_notion_id(args.target)

    print(f"ページを取得中... (プロファイル: {profile_name})")

    # ページメタデータ取得
    page = notion_get(f"/pages/{page_id}", token)
    props = page.get("properties", {})

    # タイトルを取得（title プロパティは型が "title"）
    title = "Untitled"
    for prop in props.values():
        if prop.get("type") == "title":
            title = rich_text_to_str(prop.get("title", []))
            break

    print(f"タイトル: {title}")

    # ブロック取得
    blocks = fetch_blocks(page_id, token)
    markdown = f"# {title}\n\n" + blocks_to_markdown(blocks, token)

    # 保存
    output_dir = Path(args.output) if args.output else Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r'[<>:"/\\|?*]', "_", title).strip() or "notion-page"
    output_path = output_dir / f"{safe_title}.md"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    print(f"保存: {output_path} ({len(markdown):,} 文字)")


# ---------------------------------------------------------------------------
# database コマンド
# ---------------------------------------------------------------------------

def cmd_database(args: argparse.Namespace) -> None:
    profile_name, token = resolve_token(args.profile)
    db_id = extract_notion_id(args.target)
    fmt = args.format or "csv"

    print(f"データベースを取得中... (プロファイル: {profile_name})")

    # データベースメタデータ
    db_meta = notion_get(f"/databases/{db_id}", token)
    title = rich_text_to_str(db_meta.get("title", []))
    schema = db_meta.get("properties", {})

    print(f"データベース名: {title}")

    # レコードをページネーションで全件取得
    records = []
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        data = notion_post(f"/databases/{db_id}/query", token, payload)
        records.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    print(f"{len(records)} 件取得")

    # プロパティ値を文字列に変換
    def prop_to_str(prop: dict) -> str:
        ptype = prop.get("type", "")
        val = prop.get(ptype, "")
        if ptype == "title":
            return rich_text_to_str(val)
        elif ptype == "rich_text":
            return rich_text_to_str(val)
        elif ptype == "number":
            return str(val) if val is not None else ""
        elif ptype == "select":
            return val.get("name", "") if val else ""
        elif ptype == "multi_select":
            return ", ".join(v.get("name", "") for v in (val or []))
        elif ptype == "date":
            if not val:
                return ""
            start = val.get("start", "")
            end = val.get("end", "")
            return f"{start} 〜 {end}" if end else start
        elif ptype == "checkbox":
            return "✓" if val else ""
        elif ptype == "url":
            return val or ""
        elif ptype == "email":
            return val or ""
        elif ptype == "phone_number":
            return val or ""
        elif ptype == "status":
            return val.get("name", "") if val else ""
        elif ptype == "people":
            return ", ".join(p.get("name", "") for p in (val or []))
        elif ptype == "relation":
            return ", ".join(r.get("id", "") for r in (val or []))
        elif ptype in ("created_time", "last_edited_time"):
            return val or ""
        elif ptype in ("created_by", "last_edited_by"):
            return val.get("name", "") if val else ""
        return ""

    columns = list(schema.keys())
    rows = []
    for record in records:
        props = record.get("properties", {})
        row = {col: prop_to_str(props.get(col, {})) for col in columns}
        row["_notion_url"] = f"https://www.notion.so/{record['id'].replace('-', '')}"
        rows.append(row)

    columns_with_url = columns + ["_notion_url"]

    # 保存
    output_dir = Path(args.output) if args.output else Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r'[<>:"/\\|?*]', "_", title).strip() or "notion-database"

    if fmt == "json":
        output_path = output_dir / f"{safe_title}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
    else:
        output_path = output_dir / f"{safe_title}.csv"
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns_with_url)
            writer.writeheader()
            writer.writerows(rows)

    print(f"保存: {output_path} ({len(rows)} 行)")


# ---------------------------------------------------------------------------
# search コマンド
# ---------------------------------------------------------------------------

def cmd_search(args: argparse.Namespace) -> None:
    profile_name, token = resolve_token(args.profile)

    print(f"検索中: '{args.query}' (プロファイル: {profile_name})")

    payload = {
        "query": args.query,
        "page_size": args.limit,
        "sort": {"direction": "descending", "timestamp": "last_edited_time"},
    }
    data = notion_post("/search", token, payload)
    results = data.get("results", [])

    if not results:
        print("ヒットするページが見つかりませんでした。")
        return

    print(f"\n{len(results)} 件ヒット:\n")
    for item in results:
        itype = item.get("object", "")
        props = item.get("properties", {})
        title = "Untitled"

        if itype == "page":
            for prop in props.values():
                if prop.get("type") == "title":
                    title = rich_text_to_str(prop.get("title", []))
                    break
            # タイトルが空の場合、page の title プロパティから直接取得
            if title == "Untitled":
                title_prop = item.get("properties", {}).get("title", {})
                if title_prop.get("type") == "title":
                    title = rich_text_to_str(title_prop.get("title", []))
        elif itype == "database":
            title = rich_text_to_str(item.get("title", []))

        page_id = item["id"].replace("-", "")
        url = f"https://www.notion.so/{page_id}"
        last_edited = item.get("last_edited_time", "")[:10]
        type_label = "DB" if itype == "database" else "Page"

        print(f"  [{type_label}] {title}")
        print(f"         {url}  (更新: {last_edited})")


# ---------------------------------------------------------------------------
# setup コマンド
# ---------------------------------------------------------------------------

def cmd_setup(args: argparse.Namespace) -> None:
    profiles = load_profiles()

    print("=== Notion プロファイル設定 ===")
    print(f"設定ファイル: {PROFILES_PATH}")
    if profiles:
        print(f"登録済み: {', '.join(profiles.keys())}")
    print()

    name = input("プロファイル名を入力 (例: work-a, work-b): ").strip()
    if not name:
        print("プロファイル名が空です。中止します。")
        return

    token = input(f"'{name}' の Integration Token (secret_xxx...): ").strip()
    if not token:
        print("Token が空です。中止します。")
        return

    profiles[name] = token
    save_profiles(profiles)
    print(f"\n✓ プロファイル '{name}' を保存しました。")


# ---------------------------------------------------------------------------
# profiles コマンド
# ---------------------------------------------------------------------------

def cmd_profiles(args: argparse.Namespace) -> None:
    profiles = load_profiles()
    if not profiles:
        print("登録済みプロファイルがありません。")
        print("  `notion_reader.py setup` で登録してください。")
        return

    print("登録済みプロファイル:")
    for name, token in profiles.items():
        masked = f"{token[:10]}...{token[-4:]}" if len(token) > 14 else "****"
        print(f"  {name}: {masked}")


# ---------------------------------------------------------------------------
# CLI エントリーポイント
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Notion のページ・データベースをローカルに取得する"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # page
    p_page = sub.add_parser("page", help="ページ内容を Markdown で取得")
    p_page.add_argument("target", help="Notion ページの URL または ID")
    p_page.add_argument("--profile", "-p", help="使用するプロファイル名")
    p_page.add_argument("--output", "-o", help="保存先ディレクトリ（デフォルト: カレント）")

    # database
    p_db = sub.add_parser("database", help="データベースレコードを取得")
    p_db.add_argument("target", help="Notion データベースの URL または ID")
    p_db.add_argument("--profile", "-p", help="使用するプロファイル名")
    p_db.add_argument("--format", "-f", choices=["csv", "json"], default="csv", help="出力形式（デフォルト: csv）")
    p_db.add_argument("--output", "-o", help="保存先ディレクトリ（デフォルト: カレント）")

    # search
    p_search = sub.add_parser("search", help="ページを検索")
    p_search.add_argument("query", help="検索キーワード")
    p_search.add_argument("--profile", "-p", help="使用するプロファイル名")
    p_search.add_argument("--limit", type=int, default=10, help="最大取得件数（デフォルト: 10）")

    # setup
    sub.add_parser("setup", help="Integration Token を登録する")

    # profiles
    sub.add_parser("profiles", help="登録済みプロファイルを表示")

    args = parser.parse_args()

    if args.command == "page":
        cmd_page(args)
    elif args.command == "database":
        cmd_database(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "setup":
        cmd_setup(args)
    elif args.command == "profiles":
        cmd_profiles(args)


if __name__ == "__main__":
    main()
