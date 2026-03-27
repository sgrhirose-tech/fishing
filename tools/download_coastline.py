#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
対象エリア（相模湾・三浦半島・東京湾・内房・外房・九十九里）の
海岸線データを Overpass API から一括ダウンロードしてローカルに保存する。

実行は初回のみ（または海岸線データを更新したいとき）。
生成したキャッシュを使うことで、バッチ処理中の Overpass API 呼び出しを
完全に排除できる。

使い方:
  python tools/download_coastline.py

出力:
  tools/data/coastline_elements.json
"""

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ──────────────────────────────────────────
# 設定
# ──────────────────────────────────────────

# 対象6エリアを包む bounding box (south, west, north, east)
BBOX = (34.7, 138.4, 36.0, 141.5)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

USER_AGENT = "ShirogisuSpotBuilder/1.0 (personal-use; coastline-downloader)"

OUTPUT_PATH = Path(__file__).parent / "data" / "coastline_elements.json"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


# ──────────────────────────────────────────
# ダウンロード
# ──────────────────────────────────────────

def download(endpoint: str) -> list:
    query = (
        f'[out:json][timeout:120];'
        f'way["natural"="coastline"]'
        f'({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});'
        f'out geom qt;'
    )
    url = endpoint + "?" + urllib.parse.urlencode({"data": query})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    print(f"  接続中: {endpoint}")
    with urllib.request.urlopen(req, timeout=150, context=_SSL_CTX) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("elements", [])


def main():
    OUTPUT_PATH.parent.mkdir(exist_ok=True)

    elements = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            elements = download(endpoint)
            print(f"  取得成功: {len(elements)}ウェイ")
            break
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code} — 次のエンドポイントへ")
            time.sleep(3)
        except Exception as e:
            print(f"  エラー: {e} — 次のエンドポイントへ")
            time.sleep(3)

    if not elements:
        print("すべてのエンドポイントが失敗しました。時間をおいて再試行してください。")
        return

    # 保存
    OUTPUT_PATH.write_text(
        json.dumps(elements, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    # 統計表示
    file_size = OUTPUT_PATH.stat().st_size
    total_nodes = sum(len(w.get("geometry", [])) for w in elements)

    lats = [n["lat"] for w in elements for n in w.get("geometry", [])]
    lons = [n["lon"] for w in elements for n in w.get("geometry", [])]

    print()
    print("── 完了 ──────────────────────────────────────────")
    print(f"ウェイ数    : {len(elements):,}")
    print(f"総ノード数  : {total_nodes:,}")
    print(f"ファイルサイズ: {file_size / 1024 / 1024:.1f} MB  ({OUTPUT_PATH})")
    print(f"カバー範囲  : {min(lats):.3f}〜{max(lats):.3f}N, {min(lons):.3f}〜{max(lons):.3f}E")
    print()
    print("次回から mac_batch_from_tsv.py / refetch_physical_data.py は")
    print("このキャッシュを使って Overpass なしで海方向を計算します。")


if __name__ == "__main__":
    main()
