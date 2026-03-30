# スポットデータ プロジェクト 引き継ぎ書

このドキュメントは `claude/plan-fishing-spot-coords-d49As` プロジェクトの担当者向けです。

---

## このプロジェクトの役割

**釣りスポットの JSON データを作成・修正すること。**

ウェブサイトのコード（`app/`・`templates/`・`static/`）には触らない。
スポットデータが正確であれば、ウェブは自動的に正しい情報を表示する。

---

## やること・やらないこと

| やること | やらないこと |
|---------|------------|
| `spots/*.json` の作成・修正 | `app/` 以下のコード変更 |
| `unadjusted/*.json` の確認・移動 | `templates/` の変更 |
| `tools/` スクリプトの実行・改善 | `static/` の変更 |
| `_marine_areas.json` の更新（要ウェブ側相談）| スキーマ変更（要ウェブ側相談） |

---

## スポット登録フロー

```
① TSV → JSON 生成
   python tools/mac_batch_from_tsv.py
   入力: tsv/*.tsv
   出力: unadjusted/<slug>.json

② 座標・方向を目視確認
   spot_editor.py を使うか、JSON を直接編集して確認
   確認済みファイルを unadjusted/ → spots/ に移動

③ 底質・等深線・施設種別を取得
   python tools/refetch_physical_data.py --apply
   （classification のみ更新したい場合は --classification-only）

④ unknown スポットを名称で補完
   python tools/classify_by_name.py --apply
```

詳細は `tools/README.md` を参照。

---

## ウェブ側への反映方法

スポット JSON を修正したら、以下の手順でウェブに反映する。

```
1. git add spots/<slug>.json
2. git commit -m "スポット名: 内容の説明"
3. git push -u origin claude/plan-fishing-spot-coords-d49As
4. GitHub で master へ PR を作成
5. PR がマージされると Render が自動デプロイ
```

ウェブ側プロジェクト (`claude/fishing-guide-website-BdGZB`) は master から定期的に pull して
新スポットを取り込む。

---

## スキーマ（JSON 構造）を変えたいとき

スポット JSON のフィールドを追加・削除・名称変更したい場合は、必ず先にウェブ側と相談すること。

1. `SPOT_SCHEMA.md` を確認して現在の仕様を把握
2. ウェブ側プロジェクトの担当者に変更内容を伝える
3. ウェブ側が `app/spots.py` を更新してから、データ側がスキーマを変更する
4. `SPOT_SCHEMA.md` を最新状態に更新する

> 理由: ウェブがフィールドを読み込んでいる最中にデータ側がフィールド名を変えると、
> 本番サイトが壊れる可能性があるため。

---

## 手動で分類を確認するとき

ストリートビューなどで目視確認した場合は、以下のように `source` を `manual` にする。

```json
"classification": {
  "primary_type": "sand_beach",
  "confidence": 0.80,
  "secondary_flags": [],
  "source": "manual",
  "osm_evidence": []
}
```

`source: "manual"` にすると、ウェブでは `confidence` の値に関わらず確定表示（注釈なし）になる。

---

## 重要ファイルの場所

| ファイル | 内容 |
|---------|------|
| `SPOT_SCHEMA.md` | ウェブが依存する JSON フィールドの仕様 |
| `spots/_marine_areas.json` | エリア・都道府県の定義 |
| `tools/README.md` | 各ツールの詳細説明 |
| `spots/*.json` | 公開済みスポットデータ |
| `unadjusted/*.json` | 確認前の作業中スポット |
