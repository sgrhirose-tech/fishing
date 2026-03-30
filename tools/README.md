# tools/ ディレクトリ — スクリプト一覧

> **管轄**: このディレクトリはデータプロジェクト (`claude/plan-fishing-spot-coords-d49As`) が管理する。
> ウェブサイトプロジェクトからは変更しない。

スポット登録・管理・分類に使うオフラインツール群。
実行はすべてリポジトリルートから `python tools/<スクリプト名>` で行う。

---

## スポット登録フロー

```
① TSVから JSON 生成          mac_batch_from_tsv.py
② 座標・方向を目視確認・修正   spot_editor.py  ← tools/ 外（ルート直下）
③ 底質・等深線・施設種別を取得 refetch_physical_data.py
④ unknown スポットを名称で補完 classify_by_name.py
```

---

## 各スクリプトの用途

### `mac_batch_from_tsv.py`
**役割**: TSV ファイルを一括処理し、`unadjusted/` に釣りスポット JSON を生成する。

**入力**: `tsv/*.tsv`（タブ区切り、列: name / lat / lon / slug / notes / access / [area]）
**出力**: `unadjusted/<slug>.json`

**処理内容**:
- Nominatim（OSM）で緯度経度から都道府県・市区町村・city_slug を逆ジオコーディング
- `_marine_areas.json` のセンター座標との距離でエリア（area_name / area_slug / pref_slug）を自動判定
- 海岸線データ（`tools/data/coastline_elements.json`）から海方向（sea_bearing_deg）を計算
- 底質・等深線・施設種別は空欄で出力（`refetch_physical_data.py` で後から取得）

---

### `refetch_physical_data.py`
**役割**: 確定済みスポットの物理データ（底質・等深線）と施設種別分類を取得・更新する。

**入力/出力**: `unadjusted/` → `spots/`（通常モード）、`spots/` 直接更新（`--classification-only`）

**主なオプション**:
| オプション | 説明 |
|-----------|------|
| `--apply` | `spots/` に書き込み（省略時はドライラン） |
| `--classification-only` | Overpass 分類のみ実行（海しる呼び出しなし、`spots/` を直接更新） |
| `--slug <slug>` | 1件のみ処理 |
| `--skip-classified` | 分類済みスポットをスキップ |
| `--force` | 分類済みでも上書き（`--classification-only` と併用） |
| `--verbose` | 取得した OSM タグと距離を詳細表示（調査用） |

**取得データ**:
- 底質（seabed_type）・最寄り等深線距離 ← 海しる API
- 施設種別（classification） ← OSM Overpass API

**分類ロジック（Overpass）**:
- 半径 300m 以内の OSM 地物タグ（breakwater / beach / harbour / dock など）をスコアリング
- 距離係数: ≤15m→1.0 / ≤50m→0.85 / ≤150m→0.65 / ≤300m→0.45
- primary_type: `sand_beach` / `rocky_shore` / `breakwater` / `fishing_facility` / `unknown`
- source: `osm_rule`（自動）/ `name_keyword`（名称補完）/ `manual`（手動）

---

### `classify_by_name.py`
**役割**: `refetch_physical_data.py --classification-only` 実行後も `unknown` のままのスポットを、
日本語スポット名のキーワードマッチで補完する。

**入力/出力**: `spots/*.json`（`primary_type == "unknown"` のものだけ対象）

**主なオプション**:
| オプション | 説明 |
|-----------|------|
| （なし） | ドライラン（候補一覧を表示するだけ） |
| `--apply` | 自動候補を `spots/` に書き込む |
| `--all` | 分類済みスポットも含めて全件表示 |

**マッチング方式**: スポット名の末尾キーワードのみ採用（先頭・中間は不採用）
例: `漁港`→fishing_facility / `海岸`→sand_beach / `磯`→rocky_shore / `堤防`→breakwater

**出力フィールド**: `source: "name_keyword"`, `osm_evidence: ["keyword:海岸"]`

**要目視リスト**: バッティング・公園のみ一致・キーワードなしのスポットを一覧表示

---

### `survey_osm_tags.py`
**役割**: スポット周辺の OSM タグを調査・集計する軽量ツール。
海しる API は呼ばず Overpass だけを使う。`refetch_physical_data.py` の分類精度改善や
TERRAIN_TAGS の見直しに使う。

**入力**: `spots/*.json`
**出力**: 標準出力のみ

**主なオプション**:
| オプション | 説明 |
|-----------|------|
| （なし） | 全件タグ出現ランキングを集計表示 |
| `--slug <slug>` | 1件の取得タグを距離付きで詳細表示 |
| `--radius <m>` | 検索半径（デフォルト 300m） |

---

### `mac_batch_from_tsv.py` で使用する共通ライブラリ: `pythonista_spot_tools.py`
**役割**: iPhone の Pythonista 上で動かす対話型スポット作成・修正ツール兼、
Mac ツールから `import` して使う共通ライブラリ。

**主な公開関数**:
- `calculate_sea_bearing(lat, lon, coastline_elements)` — 海岸線データから海方向を計算
- `fetch_physical_data(lat, lon, sea_bearing)` — 海しる API で底質・等深線を取得

---

### `download_coastline.py`
**役割**: 対象エリア（相模湾・三浦半島・東京湾・内房・外房・九十九里）の
海岸線データを Overpass API から一括ダウンロードしてキャッシュする。

**入力**: なし
**出力**: `tools/data/coastline_elements.json`

**実行タイミング**: 初回のみ（またはエリアを追加・更新したいとき）。
キャッシュがあれば `mac_batch_from_tsv.py` は Overpass を叩かない。

---

### `fix_area_assignments.py`
**役割**: `unadjusted/` と `spots/` の全 JSON について、
`_marine_areas.json` の最新センター座標を使ってエリア割り当てを再計算し、
`area_name` / `area_slug` / `pref_slug` を一括修正する。

**主なオプション**: `--apply`（省略時はドライラン）

**注意**: `prefecture` / `city` / `city_slug` は変更しない。

---

### `fix_pref_slugs.py`
**役割**: `prefecture` と `pref_slug` が食い違っているスポット JSON を一括修正する。
API 呼び出しなし。`prefecture` フィールドの値をもとに `pref_slug` を上書き。

**主なオプション**: `--apply`（省略時はドライラン）

---

### `migrate_spot_json.py`
**役割**: スポット JSON のスキーマ変更を一括マイグレーションする単発スクリプト。
過去に実施した変更: `depth_near_m` / `depth_far_m` 削除、`photo_url` 削除、
`terrain_summary` → `seabed_summary` リネーム（傾斜キーワード除去）。

**主なオプション**: `--apply`（省略時はドライラン）

---

## 補足: Overpass API エンドポイント

以下の4エンドポイントを順番に試し、失敗したら次へフォールバックする（全スクリプト共通）。

1. `http://overpass-api.de/api/interpreter`
2. `https://overpass.kumi.systems/api/interpreter`
3. `https://overpass.openstreetmap.fr/api/interpreter`
4. `https://maps.mail.ru/osm/tools/overpass/api/interpreter`

HTTP 403 / 429 / 502 / 503 / 504 はフォールバック対象。HTTPS は SSL 検証スキップ。
エンドポイント切り替え時に 2 秒待機。リクエスト間スリープは動的に増減（1〜30 秒）。
