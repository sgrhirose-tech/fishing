# リソースブランチ

このブランチには写真・アイコン等のバイナリファイルのみを管理します。
コードはコードブランチ (`claude/fishing-guide-website-BdGZB`) で管理します。

## ディレクトリ構成

```
photos/
  <slug>.jpg    # 各釣り場の写真（スラッグ名に合わせる）
  <slug>.webp   # WebP 形式も可
```

## コードからの参照方法

`spots/*.json` の `photo_url` フィールドに以下の形式で記載:

```
https://raw.githubusercontent.com/sgrhirose-tech/fishing/resources/photos/<slug>.jpg
```

## 写真追加手順

```bash
git checkout resources
cp /path/to/photo.jpg photos/<slug>.jpg
git add photos/<slug>.jpg
git commit -m "add photo: <slug>"
git push origin resources
```
