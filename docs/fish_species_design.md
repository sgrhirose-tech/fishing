# 魚種データ設計書

**対象チーム:** フロント開発チーム
**作成日:** 2026-03-31
**ステータス:** 初期データ構築済み・フロント実装待ち

---

## 1. 概要

スポット詳細ページへの対象魚種表示、魚種別釣り場検索、絞り込みフィルタに対応するため、
以下のデータを新規構築した。

| データ | 内容 |
|--------|------|
| `data/fish_master.json` | 魚種ごとの季節・釣法・底質マスタ（25魚種） |
| `spots/*.json` の `target_fish` | 各スポットで狙える魚種リスト |

---

## 2. ファイル構成

```
fishing/
├── data/
│   └── fish_master.json     # 魚種マスタ（季節・釣法・底質）
└── spots/
    └── *.json               # 各スポット（target_fish フィールドを追加済み）
```

---

## 3. fish_master.json の構造

```json
{
  "アジ": {
    "season":      [1,2,3,4,5,6,7,8,9,10,11,12],
    "peak_season": [5,6,7,8,9,10],
    "method":      ["サビキ釣り", "アジング", "カゴ釣り"],
    "bottom":      ["砂地", "岩礁"]
  },
  "シロギス": {
    "season":      [4,5,6,7,8,9,10],
    "peak_season": [6,7,8],
    "method":      ["投げ釣り"],
    "bottom":      ["砂地"]
  },
  ...
}
```

### フィールド定義

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `season` | int[] | 釣れる月（1〜12）|
| `peak_season` | int[] | 最盛期の月。`season` の部分集合 |
| `method` | string[] | 代表的な釣法 |
| `bottom` | string[] | 適した底質（砂地 / 岩礁 / 藻場 / 泥地） |

### 収録魚種（25種）

アジ / シロギス / メジナ / クロダイ / アオリイカ / カサゴ / メバル / スズキ /
ヒラメ / マゴチ / タコ / サバ / イワシ / ウミタナゴ / ハゼ / コウイカ /
タチウオ / マダイ / カマス / ソウダガツオ / ブリ / イシダイ / サヨリ / カレイ / シマアジ

---

## 4. スポット JSON の target_fish フィールド

各スポット JSON のトップレベルに追加済み。

```json
{
  "slug": "ajiro-ko",
  "name": "網代港",
  "target_fish": ["アジ", "メジナ", "クロダイ"],
  ...
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `target_fish` | string[] | 対象魚種の日本語名リスト。魚名は `fish_master.json` のキーと一致する。情報なしの場合は空配列 `[]` |

現時点では `info.notes` のテキストから自動抽出した初期値が入っている。
精度は「notes に魚名が記載されているスポット」に限られるため、今後は手動補完を想定。

---

## 5. 想定するフロント用途と実装方針

### 5-1. スポット詳細ページ — 対象魚種バッジ表示

スポット JSON の `target_fish` を読み取り、各魚名をバッジ表示する。
`fish_master.json` の `season` / `peak_season` を参照することで
「今月が釣期かどうか」のハイライトも可能。

```javascript
// 例: 現在月が peak_season に含まれるか
const month = new Date().getMonth() + 1;
const isInSeason = fishMaster[name]?.peak_season.includes(month);
```

### 5-2. 魚種別釣り場一覧ページ

`/fish/{魚名}` などのルートで、`target_fish` に当該魚種を含むスポットを一覧表示。
既存の `/api/spots` エンドポイントに `fish` クエリパラメータを追加する形が自然。

```
GET /api/spots?fish=アジ
→ target_fish に "アジ" を含むスポットの配列を返す
```

### 5-3. スポット検索 — 魚種絞り込みフィルタ

既存の検索 UI に魚種チェックボックスを追加し、
チェックされた魚種のいずれかを持つスポットに絞り込む。

```javascript
// 例: 複数魚種 OR 絞り込み
const filtered = spots.filter(s =>
  selectedFish.some(f => s.target_fish.includes(f))
);
```

---

## 6. app/spots.py への追加推奨ヘルパー

フロント開発チームからの要求に応じて、以下のヘルパーを追加することを推奨する。
（現時点では未実装。フロント実装のタイミングに合わせて json 保守チームが対応する）

```python
def spot_target_fish(spot: dict) -> list[str]:
    """スポットの対象魚種リストを返す（なければ空リスト）。"""
    return spot.get("target_fish", [])
```

---

## 7. 今後の保守フロー

| タイミング | 作業 | 担当 |
|-----------|------|------|
| 新規スポット追加時 | `python tools/extract_target_fish.py --dir spots_wip` を実行 | json 保守チーム |
| target_fish の手動修正 | spot_editor のチェックボックスから編集 | json 保守チーム |
| 魚種マスタの更新 | `data/fish_master.json` を直接編集（初期は月1回、充実後は年1回程度） | json 保守チーム |
| 魚種の追加 | `fish_master.json` に追記 → `extract_target_fish.py` の `FISH_NORMALIZE` にも追記 | json 保守チーム |

---

## 8. 注意事項

- `target_fish` の魚名は日本語（`fish_master.json` のキーと同一）。英語スラッグは持たない
- `fish_master.json` のキーを変更（改名）した場合、`spots/*.json` の `target_fish` も一括更新が必要
- `season` / `peak_season` は関東・東海の一般的な釣期を基準にしており、地域差がある点に留意
