# スポット JSON スキーマ仕様

ウェブサイト (`app/spots.py`) が読み込むフィールドを定義する。
**データプロジェクトがスポット JSON を編集・追加する際、このファイルで示す仕様に従うこと。**
スキーマ変更が必要な場合はウェブ側プロジェクトと相談してから行うこと。

---

## ファイル配置

| ディレクトリ | 内容 |
|------------|------|
| `spots/*.json` | 公開済みスポット（ウェブが読み込む） |
| `unadjusted/*.json` | 確認前スポット（ウェブには出ない） |
| `spots/_marine_areas.json` | エリア定義（特殊ファイル。変更時は要相談） |

---

## 必須フィールド一覧

```json
{
  "slug": "abosaki",
  "name": "安房崎",

  "location": {
    "latitude": 35.1288943,
    "longitude": 139.629364
  },

  "area": {
    "prefecture": "神奈川県",
    "pref_slug": "kanagawa",
    "area_name": "三浦半島",
    "area_slug": "miura",
    "city": "三浦市",
    "city_slug": "miura"
  },

  "physical_features": {
    "sea_bearing_deg": 120,
    "seabed_type": "rock",
    "surfer_spot": false,
    "nearest_20m_contour_distance_m": 318.3
  },

  "derived_features": {
    "bottom_kisugo_score": 35,
    "seabed_summary": "石・岩主体、貝殻混じり、近傍に石要素あり"
  },

  "info": {
    "notes": "磯場中心の名所",
    "access": "三崎口駅からバス25分"
  },

  "classification": {
    "primary_type": "rocky_shore",
    "confidence": 0.55,
    "secondary_flags": [],
    "source": "name_keyword",
    "osm_evidence": ["keyword:崎"]
  }
}
```

---

## フィールド詳細

### トップレベル

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `slug` | string | ◎ | URLの識別子。英数字とハイフンのみ。変更不可 |
| `name` | string | ◎ | スポット名（日本語）。ウェブ画面に表示される |

### `location`

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `latitude` | float | ◎ | 緯度（WGS84）。天気API・地図表示に使用 |
| `longitude` | float | ◎ | 経度（WGS84）。天気API・地図表示に使用 |

### `area`

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `prefecture` | string | ◎ | 都道府県名（例: 神奈川県） |
| `pref_slug` | string | ◎ | 都道府県スラッグ（例: kanagawa）。URLルーティングに使用 |
| `area_name` | string | ◎ | 海域エリア名（例: 三浦半島）。`_marine_areas.json` の定義と一致させること |
| `area_slug` | string | ◎ | エリアスラッグ（例: miura）。URLルーティングに使用 |
| `city` | string | ◎ | 市区町村名 |
| `city_slug` | string | ◎ | 市区町村スラッグ。URLルーティングに使用 |

### `physical_features`

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `sea_bearing_deg` | float | ◎ | 海方向（度）。風向スコアリングに使用 |
| `seabed_type` | string | △ | 底質種別（sand / rock / mud / mixed など）。スコアリングの補助情報 |
| `surfer_spot` | bool | △ | サーフポイントか否か |
| `nearest_20m_contour_distance_m` | float | △ | 最寄り20m等深線までの距離（m）。傾斜分類に使用 |

### `derived_features`

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `bottom_kisugo_score` | int | ◎ | キス・底質スコア 0〜100。省略時は50として扱われる |
| `seabed_summary` | string | △ | 底質の説明文（ウェブに表示） |

### `info`

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `notes` | string | △ | スポット説明・補足（ウェブに表示） |
| `access` | string | △ | アクセス方法（ウェブに表示） |

### `classification`

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `primary_type` | string | ◎ | 釣り場タイプ（下記参照） |
| `confidence` | float | ◎ | 信頼度 0.0〜1.0 |
| `secondary_flags` | array | ◎ | 補足分類の配列。空配列可 |
| `source` | string | ◎ | 分類根拠（下記参照） |
| `osm_evidence` | array | △ | OSM/キーワード根拠の説明。調査用 |

#### `primary_type` の選択肢

| 値 | ウェブ表示 | 説明 |
|----|----------|------|
| `sand_beach` | 砂浜 | 砂浜・サーフビーチ |
| `rocky_shore` | 磯・岩場 | 磯、岩場 |
| `breakwater` | 堤防・防波堤 | 防波堤、堤防、テトラ |
| `fishing_facility` | 漁港・岸壁・釣り施設 | 漁港、岸壁、桟橋 |
| `unknown` | （非表示） | 未分類 |

#### `source` の選択肢

| 値 | 意味 |
|----|------|
| `osm_rule` | Overpass API の OSM データから自動判定 |
| `name_keyword` | スポット名のキーワードから自動判定 |
| `manual` | 人間が目視確認して設定（最高信頼度扱い） |

> **注意:** `source: "manual"` にした場合、`confidence` の値に関わらずウェブでは断定表示（注釈なし）になる。

---

## 廃止フィールド（使用禁止）

以下はスキーマ移行済みの旧フィールドで、ウェブは読み込まない。新規スポットに記載しないこと。

| フィールド | 廃止理由 |
|-----------|---------|
| `depth_near_m` | `nearest_20m_contour_distance_m` に統合 |
| `depth_far_m` | 同上 |
| `photo_url` | `static/photos/{slug}*.jpg` の命名規則に移行 |
| `terrain_summary` | `seabed_summary` にリネーム済み |
