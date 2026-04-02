#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
対象エリアの海岸線データを Overpass API から一括ダウンロードしてローカルに保存する。

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
# 対象エリア定義 (south, west, north, east)
# ──────────────────────────────────────────

BBOXES = {
    # 既存エリア（相模湾・三浦・東京湾・内房・外房・九十九里）
    "関東":         (34.7, 138.4, 36.0, 141.5),
    # 三重・愛知（伊勢湾・熊野灘）、静岡西部
    "東海":         (33.7, 136.0, 35.1, 138.4),
    # 和歌山・大阪湾・兵庫（紀伊水道・瀬戸内海・淡路島）
    "近畿太平洋側": (33.3, 134.4, 34.9, 136.0),
    # 兵庫（日本海側）
    "兵庫日本海側": (35.2, 134.2, 35.8, 135.1),
}

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

def download_bbox(endpoint: str, bbox: tuple) -> list:
    query = (
        f'[out:json][timeout:120];'
        f'way["natural"="coastline"]'
        f'({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});'
        f'out geom qt;'
    )
    url = endpoint + "?" + urllib.parse.urlencode({"data": query})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=150, context=_SSL_CTX) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("elements", [])


def fetch_bbox(label: str, bbox: tuple) -> list:
    """エンドポイントをフォールバックしながら 1 BBOX を取得して返す。失敗時は []。"""
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            print(f"    接続中: {endpoint}")
            elements = download_bbox(endpoint, bbox)
            print(f"    取得成功: {len(elements)}ウェイ")
            return elements
        except urllib.error.HTTPError as e:
            print(f"    HTTP {e.code} — 次のエンドポイントへ")
            time.sleep(3)
        except Exception as e:
            print(f"    エラー: {e} — 次のエンドポイントへ")
            time.sleep(3)
    print(f"  [{label}] すべてのエンドポイントが失敗しました")
    return []


def main():
    OUTPUT_PATH.parent.mkdir(exist_ok=True)

    # 各 BBOX を順番に取得し、way ID で重複排除しながらマージ
    merged: dict[int, dict] = {}
    failed = []

    for label, bbox in BBOXES.items():
        print(f"\n[{label}] bbox={bbox}")
        elements = fetch_bbox(label, bbox)
        if not elements:
            failed.append(label)
            continue
        before = len(merged)
        for w in elements:
            merged[w["id"]] = w
        added = len(merged) - before
        print(f"    追加: {added}ウェイ（累計: {len(merged)}ウェイ）")
        # Overpass への連続リクエストを避けるため少し待機
        time.sleep(5)

    if failed:
        print(f"\n[警告] 取得失敗エリア: {', '.join(failed)}")

    if not merged:
        print("データを取得できませんでした。時間をおいて再試行してください。")
        return

    all_elements = list(merged.values())

    # 保存
    OUTPUT_PATH.write_text(
        json.dumps(all_elements, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    # 統計表示
    file_size = OUTPUT_PATH.stat().st_size
    total_nodes = sum(len(w.get("geometry", [])) for w in all_elements)
    lats = [n["lat"] for w in all_elements for n in w.get("geometry", [])]
    lons = [n["lon"] for w in all_elements for n in w.get("geometry", [])]

    print()
    print("── 完了 ──────────────────────────────────────────")
    print(f"ウェイ数    : {len(all_elements):,}")
    print(f"総ノード数  : {total_nodes:,}")
    print(f"ファイルサイズ: {file_size / 1024 / 1024:.1f} MB  ({OUTPUT_PATH})")
    print(f"カバー範囲  : {min(lats):.3f}〜{max(lats):.3f}N, {min(lons):.3f}〜{max(lons):.3f}E")
    print()
    print("次回から build_spots.py / refetch_physical_data.py は")
    print("このキャッシュを使って Overpass なしで海方向を計算します。")


if __name__ == "__main__":
    main()
