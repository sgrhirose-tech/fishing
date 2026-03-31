# 施設情報バッチ処理 申し送り書

**対象チーム:** json保守エンハンスチーム
**作成日:** 2026-03-31
**ステータス:** 初回運用開始

---

## 1. 背景・目的

スポット詳細ページで表示する周辺施設情報（駐車場・トイレ・釣具屋・コンビニ）は、
従来ページアクセスのたびに Overpass API（OSM）をリアルタイムで叩いていた。

スポット数が 320+ に達したことで以下の課題が生じ、バッチ処理方式に変更した。

| 課題 | 対応 |
|------|------|
| ページ表示のたびに外部 API を叩く → レイテンシ大 | 起動時に JSON をメモリに読み込み、O(1) で返す |
| アクセス集中時の Overpass API レート制限リスク | 週1回バッチに集約 |
| API 障害時にマップ上の施設が消える | facilities.json が存在すれば API 障害に依存しない |

---

## 2. ファイル構成

```
fishing/
├── data/
│   └── facilities.json          # バッチ生成済み施設データ（週次更新）
├── scripts/
│   └── fetch_facilities.py      # バッチスクリプト本体
├── app/
│   └── osm.py                   # 施設取得モジュール（load/get 関数を追加）
└── render.yaml                  # Cron Job 定義（tsuricast-facilities-weekly）
```

---

## 3. facilities.json の構造

```json
{
  "_meta": {
    "generated_at": "2026-04-07T03:00:00+09:00",  // バッチ実行日時（JST）
    "spot_count": 325,                              // 処理したスポット数
    "radius_m": 1000                                // 検索半径（メートル）
  },
  "abegawa-kako": [
    {
      "type": "駐車場",
      "name": "安倍川河口駐車場",
      "lat": 34.930,
      "lon": 138.399,
      "color": "#1565C0",
      "symbol": "P"
    },
    ...
  ],
  "futtu-kaigansui": [ ... ]
}
```

### フィールド定義

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `type` | string | 施設種別（駐車場／トイレ／釣具屋／コンビニ） |
| `name` | string | 施設名（OSM の name タグ、なければ type 名） |
| `lat` / `lon` | float | 施設の緯度・経度 |
| `color` | string | マップアイコンの色（16進） |
| `symbol` | string | マップアイコンのラベル（P / WC / 釣 / C） |

---

## 4. 自動実行（Render Cron Job）

| 項目 | 値 |
|------|-----|
| ジョブ名 | `tsuricast-facilities-weekly` |
| スケジュール | `0 18 * * 0`（UTC日曜18:00 = **JST月曜03:00**） |
| ブランチ | `master` |
| 実行コマンド | `python scripts/fetch_facilities.py` |
| 必要環境変数 | `GITHUB_TOKEN`（後述） |

### GITHUB_TOKEN の設定手順

1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. 対象リポジトリ: `sgrhirose-tech/fishing`
3. 必要権限: **Contents: Read and write**
4. Render ダッシュボード → `tsuricast-facilities-weekly` → Environment → `GITHUB_TOKEN` に貼り付け

### 実行フロー

```
Render Cron (毎週日曜 18:00 UTC)
    ↓
scripts/fetch_facilities.py 実行
    ├─ spots/*.json 全件読み込み
    ├─ 各スポット: Overpass API 取得（1.2 秒間隔）
    │   └─ 約 320 スポット × 1.2 秒 ≈ 6〜7 分
    ├─ data/facilities.json にローカル保存
    └─ GitHub REST API (PUT) で master にコミット
         → 次回デプロイ/再起動時にウェブサービスが読み込む
```

> **Note:** Render 無料プランはウェブサービスとCronJobが別コンテナ。
> Cron が書いたファイルはウェブに届かないため、GitHub 経由でデータを共有する。

---

## 5. 手動実行（ローカル）

```bash
# リポジトリルートで実行

# dry-run: 取得のみ、GitHub へのプッシュなし（動作確認用）
python scripts/fetch_facilities.py --dry-run

# 本番実行: 取得 + GitHub へプッシュ
GITHUB_TOKEN=your_token python scripts/fetch_facilities.py
```

実行後、`data/facilities.json` が生成されたことを確認してコミット:

```bash
git add data/facilities.json
git commit -m "chore: 施設情報バッチ手動更新"
git push origin master
```

---

## 6. ウェブサービス側の動作

`app/main.py` の lifespan（起動時）に `load_facilities_json()` が呼ばれ、
`data/facilities.json` の内容がメモリに読み込まれる。

`/api/osm/{slug}` エンドポイント:
1. `get_cached_facilities(slug)` でメモリから取得 → あればそのまま返す
2. なければ（未収録スポット）Overpass API にフォールバック

---

## 7. フロント改修時の参考

現時点でフロントは変更なし（`/api/osm/{slug}` を fetch するまま）。
将来的にフロントを改修する場合は以下を参照:

- `templates/spot.html` L78–237 にマップ初期化と施設マーカー追加ロジックがある
- `facilities.json` の各施設オブジェクトは `app/osm.py` の `FACILITY_TYPES` 定数と対応
- 施設種別を追加する場合は `FACILITY_TYPES` に追記し、次回バッチで自動反映される

---

## 8. トラブルシューティング

### Overpass API がタイムアウトする

`app/osm.py` の `AMENITY_SEARCH_RADIUS_M` を 1000 → 750m に下げると改善する場合がある。
または `scripts/fetch_facilities.py` の `REQUEST_INTERVAL_SEC` を 2.0 以上に伸ばす。

### GitHub プッシュが失敗する（403）

- `GITHUB_TOKEN` の権限が `Contents: Read and write` になっているか確認
- トークンの有効期限切れ（Fine-grained tokens はデフォルト 1 年）

### facilities.json が存在しないためウェブが Overpass API を叩き続ける

`data/facilities.json` を手動実行で生成して master に push すれば解消する。

---

## 9. 今後の拡張案

- **エリア別分割**: `data/facilities/suruga-bay.json` のように分割して起動時のメモリ削減
- **施設種別追加**: コインパーキング・自販機・ガソリンスタンドなど
- **施設情報の品質向上**: OSM の `name:ja` タグ優先化、重複除去
- **フロント改修**: 施設リストをカード形式で表示（アクセス情報の充実）
- **差分更新**: 全件ではなく更新日が古いスポットだけ再取得してバッチ時間を短縮
