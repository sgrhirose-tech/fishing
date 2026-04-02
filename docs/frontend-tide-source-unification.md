# フロントエンド提案書: 海況セクションの「潮」を潮汐グラフと同一データソースに統一

**作成日**: 2026-04-02  
**対象ファイル**: `templates/spot.html`

---

## 背景・課題

スポット詳細ページの「潮」情報は現在 **2つの異なるソース** から取得されている。

| 表示場所 | 現在のソース | データ |
|----------|-------------|--------|
| 今日・明日の海況テーブル「潮」行 | 天気予報 API (`PRELOADED_FORECAST`) | `p.tide`（精度・出典が不明） |
| 一週間予報テーブル「潮」列 | 天気予報 API (`PRELOADED_FORECAST`) | `p.tide` |
| 潮汐グラフ上部サマリ | tide736.net 推算値 (`/api/spots/{slug}/tide`) | `d.tide_name` |

結果として **同じページ内で潮の値が食い違う** 可能性があり、ユーザーが混乱する。

---

## 提案

海況テーブル・一週間予報テーブルの「潮」を、潮汐グラフと同じ `tideCache[i].tide_name` に差し替える。

### データソース

`/api/spots/{slug}/tide?date=YYYY-MM-DD` のレスポンス:
```json
{
  "tide_name": "大潮",
  "moon_age": 16.1,
  ...
}
```

`tide_name` の値: `大潮 / 中潮 / 小潮 / 長潮 / 若潮`

---

## 実装方針

### ポイント: tideCache は非同期でロードされる

`tideCache[i]` は `fetch('/api/spots/{slug}/tide?date=...')` で非同期取得される。
海況テーブルの描画 (`renderForecast`) より後にデータが届く場合があるため、
**「tideCache がロードされたら潮セルを上書き更新する」** 方式を採用する。

### ステップ 1: 潮セルに識別子を付与

一週間テーブルと今日・明日テーブルの「潮」値を描画する `<td>` / `<dd>` に
`data-tide-idx` 属性を追加する。

```js
// 一週間テーブル（forecastDays ループ内、i = 日付インデックス）
`<td data-tide-idx="${i}">${p.tide || 'ー'}</td>`

// 今日・明日テーブル（buildPeriodTable 内）
// ['潮', p.tide || 'ー', false] の行を
// ['潮', `<span data-tide-idx="${dayIndex}"></span>`, true] などに変更
```

### ステップ 2: tideCache ロード完了後にセルを更新

既存の tideCache への書き込み箇所（`fetch` の `.then`）の後に以下を追加:

```js
.then(function(data) {
    tideCache[idx] = (data && data.hourly && data.hourly.length) ? data : null;

    // ── 追加: 海況テーブルの「潮」セルを更新 ──
    if (tideCache[idx] && tideCache[idx].tide_name) {
        var cells = document.querySelectorAll('[data-tide-idx="' + idx + '"]');
        cells.forEach(function(el) {
            el.textContent = tideCache[idx].tide_name;
        });
    }
    // ──────────────────────────────────────────
})
```

### ステップ 3: フォールバック

`tideCache[i]` が null（API 未取得・エラー）の場合は、
既存の `p.tide` 値（天気予報 API）を初期値として残す。

---

## 表示仕様

| 状態 | 表示 |
|------|------|
| tide736.net データ取得成功 | `大潮` / `中潮` / `小潮` / `長潮` / `若潮` |
| tide736.net データ取得失敗 | 天気予報 API の値（現状維持） |
| 天気予報 API の値もなし | `ー` |

月齢（`moon_age`）を「大潮（月齢16.1）」の形式で表示したい場合は、
セル内を `tide_name + '（月齢' + moon_age + '）'` に変更する。

---

## 変更範囲（最小限）

- `renderForecast()` 内の 一週間テーブル描画部分（`p.tide` の行）に `data-tide-idx` 追加
- `buildPeriodTable()` / 今日・明日テーブル描画部分（`p.tide` の行）に `data-tide-idx` 追加
- `tideCache` への書き込み後にセル更新処理を追加（約 8 行）

---

## 関連バックエンド変更

バックエンドは別途以下を予定（バックエンドチームで対応）:
- 事前バッチ廃止（`scripts/fetch_tides.py` → 削除）
- `/api/spots/{slug}/tide` をオンデマンド呼び出しに変更（インターフェース変わらず）
- `harbor_mapping.json` / `data/tides/` を廃止
- **フロントへの影響なし**（レスポンス構造は現状維持）
