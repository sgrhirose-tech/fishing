#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
対象エリアの海岸線データを Overpass API から一括ダウンロードしてローカルに保存する。

エリアごとに個別ファイル（coastline_{slug}.json）に保存し、
coastline_index.json でインデックスを管理する。
pythonista_spot_tools.py はスポット座標に対応するファイルだけをロードする。

実行は初回のみ（または海岸線データを更新したいとき）。

使い方:
  python tools/download_coastline.py           # 全エリア取得
  python tools/download_coastline.py --area kanto  # 特定エリアのみ再取得

出力:
  tools/data/coastline_{slug}.json  （エリアごと）
  tools/data/coastline_index.json   （bbox インデックス）
"""

import argparse
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ──────────────────────────────────────────
# 対象エリア定義
# slug: ファイル名に使用（英数字・アンダースコアのみ）
# bbox: (south, west, north, east)
# ──────────────────────────────────────────

BBOXES = {
    "kanto":         ((34.7, 138.4, 36.0, 141.5), "関東（相模湾・三浦・東京湾・内房・外房・九十九里）"),
    "tokai":         ((33.7, 136.0, 35.1, 138.4), "東海（三重・愛知・静岡西部）"),
    "kinki_pacific": ((33.3, 134.4, 34.9, 136.0), "近畿太平洋側（和歌山・大阪湾・兵庫瀬戸内・淡路島）"),
    "hyogo_sea":     ((35.2, 134.2, 35.8, 135.1), "兵庫日本海側"),
}

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

USER_AGENT = "ShirogisuSpotBuilder/1.0 (personal-use; coastline-downloader)"

DATA_DIR = Path(__file__).parent / "data"
INDEX_PATH = DATA_DIR / "coastline_index.json"

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


def fetch_bbox(slug: str, bbox: tuple) -> list:
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
    print(f"  [{slug}] すべてのエンドポイントが失敗しました")
    return []


def save_area(slug: str, elements: list) -> Path:
    """エリアデータをファイルに保存してパスを返す。"""
    out = DATA_DIR / f"coastline_{slug}.json"
    out.write_text(
        json.dumps(elements, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return out


def update_index(slug: str, bbox: tuple, label: str, n_ways: int) -> None:
    """coastline_index.json を更新する。"""
    index = {}
    if INDEX_PATH.exists():
        try:
            index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    index[slug] = {
        "file": f"coastline_{slug}.json",
        "bbox": list(bbox),   # [south, west, north, east]
        "label": label,
        "ways": n_ways,
    }
    INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--area", metavar="SLUG",
                        help="特定エリアのみ再取得（例: kanto）")
    args = parser.parse_args()

    DATA_DIR.mkdir(exist_ok=True)

    targets = {}
    if args.area:
        if args.area not in BBOXES:
            print(f"[エラー] 不明なエリア: {args.area}")
            print(f"有効: {', '.join(BBOXES)}")
            return
        targets = {args.area: BBOXES[args.area]}
    else:
        targets = BBOXES

    failed = []
    for slug, (bbox, label) in targets.items():
        print(f"\n[{slug}] {label}")
        print(f"  bbox={bbox}")
        elements = fetch_bbox(slug, bbox)
        if not elements:
            failed.append(slug)
            continue

        out = save_area(slug, elements)
        update_index(slug, bbox, label, len(elements))

        total_nodes = sum(len(w.get("geometry", [])) for w in elements)
        file_size = out.stat().st_size
        print(f"  → {out.name}  {len(elements):,}ウェイ / {total_nodes:,}ノード / {file_size/1024/1024:.1f}MB")

        if len(targets) > 1:
            time.sleep(5)  # Overpass への連続リクエストを避ける

    print()
    if failed:
        print(f"[警告] 取得失敗: {', '.join(failed)}")

    # インデックスのサマリ表示
    if INDEX_PATH.exists():
        index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        print("── インデックス ──────────────────────────────────")
        for s, info in index.items():
            b = info["bbox"]
            print(f"  {s:20s} {info['ways']:5,}ウェイ  "
                  f"{b[0]:.1f}〜{b[2]:.1f}N, {b[1]:.1f}〜{b[3]:.1f}E  {info['label']}")
    print()
    print("次回から build_spots.py / refetch_physical_data.py は")
    print("スポット座標に対応するファイルだけをロードします。")


if __name__ == "__main__":
    main()
