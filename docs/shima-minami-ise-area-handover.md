# 志摩・南伊勢 エリア追加 フロントエンド申し送り書

## 概要

三重県の海域区分を「伊勢湾」「熊野灘」の2区分から、以下の**3区分**に変更しました。

| area_name    | area_slug         | pref_slug | 対象市町村                       |
|--------------|-------------------|-----------|----------------------------------|
| 伊勢湾       | `isewan`          | `mie`     | 桑名市・四日市市・津市 etc.      |
| 志摩・南伊勢 | `shima-minami-ise`| `mie`     | 志摩市・南伊勢町・大紀町         |
| 熊野灘       | `kumano-nada`     | `mie`     | 紀北町・尾鷲市・熊野市 etc.      |

---

## バックエンド変更内容

### 修正ファイル一覧

| ファイル | 変更内容 |
|----------|----------|
| `app/constants.py` | `VALID_AREA_SLUGS` に `"shima-minami-ise"` を追加 |
| `spots/_marine_areas.json` | `"志摩・南伊勢"` エントリ（center + bbox）を追加 |
| `spot_editor.py` | `_VALID_AREA_SLUGS`・`AREA_MAP`・JS `AREA_SLUG_MAP` に追加 |
| `tools/build_spots.py` | `AREA_MAP` に追加 |
| `tools/mac_batch_from_tsv.py` | `AREA_MAP` に追加 |
| `tools/fix_area_assignments.py` | `AREA_MAP` に追加 |

### 追加したエリア定義

```json
"志摩・南伊勢": {
  "center_lat": 34.32,
  "center_lon": 136.75,
  "fetch_km": 50,
  "lat_min": 34.10,
  "lat_max": 34.55,
  "lon_min": 136.25,
  "lon_max": 137.00
}
```

---

## フロントエンドへの依頼事項

### 1. エリア一覧ページ（三重県）

`/mie/` の都道府県ページにエリア一覧を表示している場合、
以下の3エリアが並ぶように更新してください。

```
伊勢湾      → /mie/isewan/
志摩・南伊勢 → /mie/shima-minami-ise/
熊野灘      → /mie/kumano-nada/
```

### 2. ナビゲーション・パンくずリスト

パンくずリストや地図上のエリア選択 UI で、`shima-minami-ise` に対する表示名として
**「志摩・南伊勢」** を使用してください。

### 3. エリアページ見出し・メタ情報

`/mie/shima-minami-ise/` のページには以下を想定しています。

- **ページタイトル**: `志摩・南伊勢の釣り場 | Tsuricast`
- **h1**: `志摩・南伊勢の釣り場`
- **description**: `志摩市・南伊勢町・大紀町の釣り場情報。エギングやルアー釣りに人気のエリア。`

### 4. OGP・構造化データ

既存の `isewan` / `kumano-nada` エリアと同様のテンプレートで
`shima-minami-ise` のページを生成してください。

### 5. サイトマップ

`/mie/shima-minami-ise/` をサイトマップに追加してください。

---

## 現状のスポット登録状況

2026-04-02 時点では `spots/` ディレクトリに三重県スポットはまだありません。
`tsv/not_use/mie_fishing_spots_northcentral_south.tsv` に候補データがあり、
今後バッチ処理でスポットを追加予定です。

三重県スポットを TSV から生成する際、志摩市・南伊勢町・大紀町に該当するスポットの
`area` 列には **`志摩・南伊勢`** を指定してください（`mac_batch_from_tsv.py` の `AREA_MAP` に対応済み）。

---

## URL 設計

```
/mie/shima-minami-ise/                       # エリアトップ
/mie/shima-minami-ise/{city_slug}/           # 市町村ページ
/mie/shima-minami-ise/{city_slug}/{slug}     # スポット詳細
```

`city_slug` の例:
- 志摩市 → `shima`
- 南伊勢町 → `minami-ise`
- 大紀町 → `taiki`

---

*作成: 2026-04-02*
