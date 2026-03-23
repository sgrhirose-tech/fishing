#!/usr/bin/env python3
"""
海しるAPI エンドポイント探索スクリプト
3つの試用APIキーで各エンドポイントを試し、何のデータが取れるか確認する

実行:
    python umishiru_explore.py
"""

import os
import json
import requests

# .env ファイルを読み込む
def load_env(path=".env"):
    env = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env

env = load_env()
KEYS = [
    env.get("UMISHIRU_API_KEY_1", ""),
    env.get("UMISHIRU_API_KEY_2", ""),
    env.get("UMISHIRU_API_KEY_3", ""),
]

# テスト座標（片瀬西浜付近）
TEST_LAT = 35.304
TEST_LON = 139.480

# 既知の海しるAPIエンドポイント候補
# 参考: https://www.kaiho.mlit.go.jp/syoukai/soshiki/toudai/umishiru/
ENDPOINTS = [
    # 基本URL候補
    "https://gisapi.kaiho.mlit.go.jp/",
    "https://gis.kaiho.mlit.go.jp/umishiruAPI/",
    "https://gis.kaiho.mlit.go.jp/api/",
    # 底質（海底底質）
    "https://gisapi.kaiho.mlit.go.jp/seabed/",
    "https://gisapi.kaiho.mlit.go.jp/substrate/",
    # 水深
    "https://gisapi.kaiho.mlit.go.jp/depth/",
    "https://gisapi.kaiho.mlit.go.jp/bathymetry/",
    # WMS/WFS形式
    "https://gisapi.kaiho.mlit.go.jp/wms",
    "https://gisapi.kaiho.mlit.go.jp/wfs",
]

def try_endpoint(url, key, params=None):
    """エンドポイントを試す"""
    test_params = params or {}
    # よくあるAPIキーの渡し方を両方試す
    for key_param in ["apikey", "api_key", "key", "token", "access_token"]:
        p = {**test_params, key_param: key, "lat": TEST_LAT, "lon": TEST_LON}
        try:
            resp = requests.get(url, params=p, timeout=8,
                                headers={"User-Agent": "FishingAdvisor/1.0"})
            status = resp.status_code
            content_type = resp.headers.get("Content-Type", "")
            content_preview = resp.text[:300].replace("\n", " ")
            if status != 404:
                return {
                    "url": resp.url,
                    "status": status,
                    "content_type": content_type,
                    "preview": content_preview,
                    "key_param": key_param,
                }
        except Exception as e:
            pass
    return None

def main():
    print("=" * 60)
    print("海しるAPI エンドポイント探索")
    print("=" * 60)
    print(f"テスト座標: {TEST_LAT}, {TEST_LON}")
    print(f"APIキー数: {len([k for k in KEYS if k])}")
    print()

    for i, key in enumerate(KEYS, 1):
        if not key:
            print(f"[キー{i}] 未設定\n")
            continue

        print(f"[キー{i}] {key[:8]}...")
        found_any = False
        for url in ENDPOINTS:
            result = try_endpoint(url, key)
            if result:
                print(f"  ✓ {result['status']} {result['url']}")
                print(f"    Content-Type: {result['content_type']}")
                print(f"    プレビュー: {result['preview'][:150]}")
                found_any = True
        if not found_any:
            print("  → 有効なレスポンスなし（ネットワーク制限またはエンドポイント不明）")
        print()

    print("=" * 60)
    print("探索完了")
    print()
    print("もし上記で有効なエンドポイントが見つからない場合:")
    print("1. 海しるのAPI利用登録ページで正確なエンドポイントURLを確認してください")
    print("   https://www.kaiho.mlit.go.jp/")
    print("2. 利用登録確認メールにエンドポイントURLが記載されている場合があります")

if __name__ == "__main__":
    main()
