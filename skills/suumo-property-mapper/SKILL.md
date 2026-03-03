---
name: suumo-property-mapper
description: >
  SUUMOの中古戸建・マンション物件ページから住所・付近施設・写真などの情報を読み取り、
  物件の正確な位置座標を特定してGoogle Mapsのリストにピンとして登録するスキル。
  ブラウザ自動操作（Claude in Chrome）を使ってSUUMOページの解析とGoogle Mapsへの
  登録を行う。SUUMOの座標は意図的にずらされていることがあるため、付近施設の距離情報
  による整合性チェックとGoogleストリートビューでの目視確認を組み合わせて精度を高める。
  ユーザーが「SUUMOの物件をGoogle Mapに登録」「SUUMOのURLからピンを打って」
  「物件の場所を地図に追加」「suumo.jpのURLをマップに保存」などと言ったとき、
  またはsuumo.jpの物件URLが含まれているときは必ずこのスキルを使う。
  物件の価格・間取り・面積・築年数などの情報をメモとして付与する。
---

# SUUMO Property Mapper

SUUMOの物件ページから位置情報を解析し、Google Mapsのリストにピンとして登録するスキル。

## 前提条件

- Claude Code + Claude in Chrome（ブラウザ自動操作）が利用可能であること
- Google Mapsにログイン済みのChromeブラウザがあること
- 対象のGoogle Mapsリストが作成済みであること

## 入力

ユーザーから以下の情報を受け取る。不足している場合はAskUserQuestionで確認する。

- **SUUMO物件URL**（必須）: `https://suumo.jp/...` 形式のURL
- **Google Mapsリスト URL**（必須）: `https://maps.app.goo.gl/...` 形式、またはGoogle Mapsリストの共有URL。スキル内にハードコードせず、毎回ユーザーから受け取る。
- **追加メモ**（任意）: 価格・間取り・確認日など、ピンのメモに追記したい情報。ユーザーが独自フォーマットのメモを提供した場合はそのフォーマットを優先する。

## ワークフロー

### Step 1: SUUMOページから情報を抽出する

SUUMOの物件URLをClaude in Chromeで開く（navigateツール）。ページ読み込み完了まで3〜5秒待つ。

**1-a: 埋め込み座標の取得**

javascript_toolで以下を実行してSUUMOが埋め込んでいる座標を取得する。

```javascript
const scripts = document.querySelectorAll('script');
let lat, lng;
for (const s of scripts) {
  const text = s.textContent;
  const latMatch = text.match(/initIdo\s*[:=]\s*'([^']+)'/);
  const lngMatch = text.match(/initKeido\s*[:=]\s*'([^']+)'/);
  if (latMatch) lat = latMatch[1];
  if (lngMatch) lng = lngMatch[1];
}
JSON.stringify({lat, lng});
```

**1-b: 物件概要の取得**

javascript_toolで以下を実行して物件概要を取得する。

```javascript
const info = {};
document.querySelectorAll('table th').forEach(th => {
  const text = th.textContent.trim().replace(/\s+/g, ' ');
  const td = th.nextElementSibling;
  if (td) {
    const val = td.textContent.trim().replace(/\s+/g, ' ');
    if (text.match(/価格|間取|土地面積|建物面積|完成|住所|向き|私道|道路/) && val.length < 200) {
      info[text] = val;
    }
  }
});
JSON.stringify(info);
```

**1-c: 付近施設の取得（周辺環境ページ）**

物件トップページには付近施設が記載されていないことがある。
「周辺環境・地図」タブをクリックして遷移するか、URLに `/kankyo/` を付与してアクセスする。

例: `https://suumo.jp/chukoikkodate/tokyo/sc_suginami/nc_20351677/kankyo/`

周辺環境ページでjavascript_toolを実行して施設情報を取得する。

```javascript
const facilities = [];
const allText = document.body.innerText;

// パターン1: 「〇〇まで△△m」
const p1 = /([^\n]{3,50}?)まで(\d+)m/g;
let m;
while ((m = p1.exec(allText)) !== null) {
  const name = m[1].trim();
  if (!facilities.some(f => f.name === name)) {
    facilities.push({name, distanceM: parseInt(m[2])});
  }
}

// パターン2: 「〇〇：徒歩X分（△△m）」
const p2 = /([^\n：]{3,50}?)：徒歩(\d+)分[（(](\d+)[ｍm][）)]/g;
while ((m = p2.exec(allText)) !== null) {
  const name = m[1].trim();
  if (!facilities.some(f => f.name === name)) {
    facilities.push({name, walkMin: parseInt(m[2]), distanceM: parseInt(m[3])});
  }
}

JSON.stringify(facilities.slice(0, 15));
```

施設情報の取得後は物件トップページに戻る（ブラウザの「戻る」またはURLから `/kankyo/` を除去）。

**1-d: 外観写真の確認**

findツールで「外観写真」を検索し、scroll_toで表示してscreenshotを撮る。
この写真は後のストリートビュー照合で使う。

### Step 2: 物件の位置を推理する

SUUMOの座標はプライバシー保護のため数十〜数百メートルずらされていることが多い。
SUUMO座標を出発点として、物件ページの情報を手がかりに段階的に位置を絞り込む。
この推理プロセスが物件特定の肝であり、単純な座標補正では不十分。

**Step 2-a: 手がかりの整理**

Step 1で取得した情報から、位置特定に使える手がかりを整理する：

| 手がかり | 推理への活用 |
|---|---|
| 住所（丁目まで） | 丁目の境界内に物件がある |
| 最寄り学校名と距離 | 学区から丁目内のどのエリアかを絞れる。例えば「南が丘小学校」が最寄りなら、地図上でより近い「南田中小学校」の学区ではなく南が丘小学校の学区側（＝丁目の南側など）にある |
| 道路幅・接道方向 | 「北6m幅」なら北側に幅員6mの道路がある物件を探す。住宅街の細い道ではなく比較的広い道に面している |
| 向き（方角） | 「東南向き」なら建物の東南側が開けている（＝道路や空地がある方向） |
| 付近施設と距離 | 施設からの距離感で大まかなエリアを確認できる（※徒歩距離なので直線距離の1.2〜1.5倍程度） |
| 土地面積 | 航空写真で区画サイズの目安になる |
| 築年月 | ストリートビューの撮影時期と照合して建物の新しさを確認できる |

**Step 2-b: 学区・施設による大まかなエリア特定**

1. 物件の最寄り学校（小学校・中学校）をGoogle Mapsで検索して位置を確認する
2. 住所の丁目内で、その学校に近い側のエリアを特定する
   - 同じ丁目内に別の学校がある場合、学区境界から物件がどちら側にあるかわかる
3. 近距離の施設（500m以内）を2〜3件Google Mapsで検索し、位置関係を把握する
   - Google Mapsで施設名を検索: `https://www.google.com/maps/search/施設名`
   - URLの `/@LAT,LNG,...` からLAT,LNGを読み取る

**Step 2-c: 道路・区画情報による絞り込み**

1. Google Mapsで SUUMO座標付近を地図表示する: `https://www.google.com/maps/@LAT,LNG,17z`
   - 注意: ズームレベル18z以上だとストリートビューに自動で入ることがある。17z程度を推奨。
   - ストリートビューに入ってしまった場合はURLを `@LAT,LNG,17z` に書き換えてnavigate
2. **航空写真モードに切り替える**: 地図左下の「レイヤ」アイコンをクリック → 「航空写真」を選択
3. Step 2-aで整理した道路情報に合致する区画を探す：
   - 接道方向（北道路・南道路など）と道路幅から候補を絞る
   - 土地面積から区画サイズの目安をつける（110m2 ≒ 10m×11m程度）
   - 築年月が新しい物件は航空写真で周辺と色味が違って見えることがある
4. 候補が見つかったら、その座標を記録する（右クリックで座標を確認できる）

### Step 3: ストリートビューで確認する

Step 2で推定したエリア付近のストリートビューを表示して建物を照合する。

1. Google Mapsでストリートビューを開く:
   `https://www.google.com/maps/@LAT,LNG,3a,75y,90h,90t/data=!3m6!1e1!3m4!1s!2e0!7i16384!8i8192`
2. ページ読み込みを5秒待ち、screenshotを撮る
3. Step 1で確認したSUUMOの外観写真と見比べる：
   - 建物の外壁の色・素材（白いサイディング、タイル張りなど）
   - 建物の形状・階数・屋根の形
   - 玄関の位置や階段の有無
   - 駐車スペースの有無と位置
   - 周辺の塀・植栽・電柱の位置
   - 道路の幅（物件概要の接道幅と一致するか）
4. AskUserQuestionで確認する:
   - 「ストリートビューを表示しました。SUUMOの外観写真と比較して、この場所で合っていますか？」
   - 選択肢: 「合っている」「少しずれている」「全然違う」
5. ずれている場合:
   - 「少しずれている」→ ストリートビュー内を移動して周辺を探索する
   - 「全然違う」→ Step 2の推理を見直す。航空写真で別の候補区画を探す

### Step 4: Google Mapsリストにピンを登録する

座標が確定したら、Google Mapsのリストにピンを追加する。

**手順（ブラウザ自動操作）：**

1. Google Mapsの検索バーに確定座標を入力して検索する
   - 検索バーをクリック → `LAT, LNG`（例: `35.7349356, 139.6117274`）を入力 → Enter
   - これにより座標にピンが立ち、左パネルに場所情報が表示される
2. 左パネルの「保存」ボタンをクリック
3. リスト選択画面で対象のリストを選択する
   - ユーザーが指定したGoogle Mapsリストの名前を探してクリック
   - リストが表示されていない場合は下にスクロールして探す
   - 見つからない場合はユーザーに確認する
4. メモを入力する（メモ欄が表示される場合）
5. 「完了」ボタンをクリックして保存する

**代替手順（検索バーでうまくいかない場合）：**

1. `https://www.google.com/maps/@LAT,LNG,18z` で地図を開く
2. 地図の中央付近を右クリック → コンテキストメニューの座標値をクリック（座標がクリップボードにコピーされると同時にピンが立つ）
3. 画面下部に表示される座標パネルをクリック → 場所の詳細が開く
4. 「保存」ボタンからリストに追加

**メモのフォーマット：**

```
{価格}, {間取り}, 土地{土地面積}, 建物{建物面積} {築年月}築 {確認日}確認
{SUUMO物件URL}
```

例:
```
6380万円, 4LDK, 土地110m2, 建物98.53m2 2010.12築 2026.3.3確認
https://suumo.jp/chukoikkodate/tokyo/sc_nerima/nc_20350718/
```

ユーザーから追加メモが提供されている場合は、そのフォーマットを優先して使う。

### Step 5: 結果を報告する

登録完了後、以下を報告する：
- 登録した座標（緯度, 経度）
- 座標の信頼度（ストリートビューで確認済み or SUUMO座標のまま）
- メモの内容
- Google MapsリストのURL

## エラーハンドリング

| エラー | 対処 |
|---|---|
| SUUMOページが表示されない | URLが正しいか確認。掲載終了の可能性をユーザーに通知 |
| 座標が取得できない（initIdo/initKeidoがない） | 住所をGoogle Mapsの検索バーに入力してジオコーディングする |
| 付近施設がGoogle Mapsで見つからない | 施設名を短縮して再検索（例:「サミットストア環八南田中店」→「サミット 南田中」）。見つからなければ別の施設を使う |
| ストリートビューが利用できない | 航空写真（衛星写真）ビューで確認を試みる。地図上で建物の形状を確認する |
| Google Mapsリストへの保存に失敗 | Googleアカウントのログイン状態を確認。手動での保存手順をユーザーに案内する |
| ブラウザ拡張機能が応答しない | ユーザーにChromeの再接続を依頼する |
| get_page_textがエラーを返す | javascript_toolでdocument.body.innerTextを取得するか、read_pageで構造を確認する |
