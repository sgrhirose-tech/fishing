#!/usr/bin/env python3
"""
tide736.net から対象都県の港コードと座標を取得して data/harbor_list.json を生成する。

このスクリプトは tide736.net への初回セットアップ用ツール。
一度実行して harbor_list.json を生成したら、以降は assign_harbor_mapping.py で
自動割り当てが可能になる。

処理:
  1. tide736.net HTML から指定都県の港コード（pc, hc）と港名を取得
  2. 各港名を Nominatim でジオコーディングして緯度経度を付与
  3. data/harbor_list.json に保存

使い方:
    python tools/fetch_harbor_list.py              # デフォルト都県（神奈川・千葉・東京）
    python tools/fetch_harbor_list.py --pc 14 12   # 神奈川・千葉のみ
    python tools/fetch_harbor_list.py --no-geocode # 座標付与をスキップ
    python tools/fetch_harbor_list.py --dry-run    # 取得内容を表示するのみ

注意: Nominatim は利用規約上 1 秒以上の間隔が必要。
"""

import argparse
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# SSL 証明書検証を無効化（macOS の証明書ストア問題 / 自己署名証明書対策）
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_REPO_ROOT = Path(__file__).parent.parent
OUTPUT_PATH = _REPO_ROOT / "data" / "harbor_list.json"

# 対象都県のデフォルト（神奈川=14, 千葉=12, 東京=13）
DEFAULT_PC = [14, 12, 13]
PC_NAMES = {14: "神奈川県", 12: "千葉県", 13: "東京都"}

TIDE_SITE = "https://tide736.net"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "TsuricastSetup/1.0 (personal-use)"
GEOCODE_INTERVAL = 1.2  # Nominatim 利用規約: 1秒以上の間隔


# ─────────────────────────────────────────────────────────
# tide736.net から港コードを取得
# ─────────────────────────────────────────────────────────

def fetch_harbor_codes(pc: int) -> list[dict]:
    """
    tide736.net のページを取得し、指定都県（pc）の港コードと港名を抽出して返す。

    tide736.net の HTML には以下のような select 要素が含まれる想定:
      <select name="hc">
        <option value="1">小田原</option>
        <option value="2">真鶴</option>
        ...
      </select>

    動的ロードの場合は JavaScript API エンドポイントを試みる。
    """
    harbors = []

    # ── Step 1: pc パラメータ付きページを取得
    url = f"{TIDE_SITE}/?pc={pc}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [警告] pc={pc} HTML取得失敗: {e}")
        html = ""

    # ── Step 2: <select name="hc"> のオプションを解析
    # パターン1: <option value="数字">港名</option>
    if html:
        # select タグを探して中身を抽出
        select_match = re.search(
            r'<select[^>]*name=["\']hc["\'][^>]*>(.*?)</select>',
            html, re.DOTALL | re.IGNORECASE
        )
        if select_match:
            options_html = select_match.group(1)
            for m in re.finditer(r'<option[^>]*value=["\'](\d+)["\'][^>]*>(.*?)</option>',
                                 options_html, re.IGNORECASE):
                hc = int(m.group(1))
                name = re.sub(r'<[^>]+>', '', m.group(2)).strip()
                if name:
                    harbors.append({"pc": pc, "hc": hc, "harbor_name": name})

    # ── Step 3: JavaScript API エンドポイントを試みる（静的HTMLに無い場合）
    if not harbors:
        # tide736.net が JSON API を提供している場合の候補エンドポイント
        json_candidates = [
            f"{TIDE_SITE}/api/harbors?pc={pc}",
            f"{TIDE_SITE}/harbors.php?pc={pc}",
            f"{TIDE_SITE}/get_harbor.php?pc={pc}",
        ]
        for json_url in json_candidates:
            req = urllib.request.Request(json_url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(body)
                # [{"hc": 1, "name": "小田原"}, ...] 形式を想定
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "hc" in item:
                            name = item.get("name") or item.get("harbor_name") or ""
                            harbors.append({"pc": pc, "hc": int(item["hc"]), "harbor_name": name})
                    if harbors:
                        break
            except Exception:
                continue

    # ── Step 4: 上記で取得できない場合、hc=1〜99 を総当たりで試す（フォールバック）
    if not harbors:
        print(f"  [情報] pc={pc}: HTMLからの取得失敗。番号を総当たりで確認します（時間がかかります）...")
        harbors = probe_harbor_codes(pc, max_hc=99)

    return harbors


def probe_harbor_codes(pc: int, max_hc: int = 99) -> list[dict]:
    """
    hc=1〜max_hc を試してデータが返る港コードを発見する（フォールバック）。
    1リクエストごとに0.5秒待機。
    """
    from datetime import datetime
    now = datetime.now()
    harbors = []
    for hc in range(1, max_hc + 1):
        url = (
            f"https://api.tide736.net/get_tide.php"
            f"?pc={pc}&hc={hc}&yr={now.year}&mn={now.month}&dy=1&rg=day"
        )
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body)
            # データが入っていれば有効な港コード
            chart = data.get("tide", {}).get("chart", {})
            if chart:
                # レスポンス内に港名が含まれることがある
                harbor_name = data.get("harbor_name") or data.get("name") or f"港{hc:02d}"
                harbors.append({"pc": pc, "hc": hc, "harbor_name": harbor_name})
                print(f"    hc={hc}: {harbor_name} ✓")
        except Exception as e:
            if hc == 1:
                # 最初のリクエストだけエラーを表示して原因把握に役立てる
                print(f"    [hc=1 エラー] {e}")
        time.sleep(0.5)

    return harbors


# ─────────────────────────────────────────────────────────
# Nominatim ジオコーディング
# ─────────────────────────────────────────────────────────

def geocode_harbor(harbor_name: str, pref_name: str) -> tuple[float, float] | None:
    """
    Nominatim で港名をジオコーディングして (lat, lon) を返す。
    失敗した場合は None。
    """
    # 検索クエリ: 「港名 都県名」で絞り込む
    queries = [
        f"{harbor_name}港 {pref_name}",
        f"{harbor_name} {pref_name}",
        f"{harbor_name}漁港",
        harbor_name,
    ]

    for query in queries:
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "limit": 1,
            "countrycodes": "jp",
            "accept-language": "ja",
        })
        url = f"{NOMINATIM_URL}?{params}"
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja",
        })
        try:
            with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
                results = json.loads(resp.read().decode("utf-8"))
            if results:
                lat = float(results[0]["lat"])
                lon = float(results[0]["lon"])
                return lat, lon
        except Exception:
            pass
        time.sleep(GEOCODE_INTERVAL)

    return None


# ─────────────────────────────────────────────────────────
# メイン処理
# ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="tide736.net から港コードリストを生成")
    parser.add_argument("--pc", nargs="+", type=int, default=DEFAULT_PC,
                        help=f"対象都県コード（デフォルト: {DEFAULT_PC}）")
    parser.add_argument("--no-geocode", action="store_true",
                        help="Nominatim ジオコーディングをスキップ（座標なしで保存）")
    parser.add_argument("--dry-run", action="store_true",
                        help="取得内容を表示するのみ（ファイル保存しない）")
    args = parser.parse_args()

    print("=== 港コードリスト生成 ===")
    print(f"対象都県: {[PC_NAMES.get(pc, str(pc)) for pc in args.pc]}")

    all_harbors: list[dict] = []

    for pc in args.pc:
        pref_name = PC_NAMES.get(pc, f"都県{pc}")
        print(f"\n[{pref_name} (pc={pc})] 港コードを取得中...")
        harbors = fetch_harbor_codes(pc)
        print(f"  → {len(harbors)} 港を発見")

        if not args.no_geocode and harbors:
            print(f"  ジオコーディング中（Nominatim, {GEOCODE_INTERVAL}秒間隔）...")
            for i, h in enumerate(harbors, 1):
                coords = geocode_harbor(h["harbor_name"], pref_name)
                if coords:
                    h["lat"], h["lon"] = coords
                    print(f"    [{i}/{len(harbors)}] {h['harbor_name']}: {coords[0]:.4f}, {coords[1]:.4f}")
                else:
                    h["lat"] = None
                    h["lon"] = None
                    print(f"    [{i}/{len(harbors)}] {h['harbor_name']}: 座標取得失敗")
                time.sleep(GEOCODE_INTERVAL)
        else:
            for h in harbors:
                h["lat"] = None
                h["lon"] = None

        all_harbors.extend(harbors)
        time.sleep(1.0)

    if args.dry_run:
        print("\n[dry-run] 取得結果:")
        for h in all_harbors:
            lat_str = f"{h['lat']:.4f}" if h.get("lat") else "未取得"
            lon_str = f"{h['lon']:.4f}" if h.get("lon") else "未取得"
            print(f"  {h['pc']}-{h['hc']:02d}  {h['harbor_name']}  ({lat_str}, {lon_str})")
        print(f"\n合計 {len(all_harbors)} 港")
        return

    # harbor_code フィールドを追加
    for h in all_harbors:
        h["harbor_code"] = f"{h['pc']}-{h['hc']}"

    output = {
        "_meta": {
            "description": "tide736.net 港コードリスト（fetch_harbor_list.py で自動生成）",
            "target_prefectures": {str(pc): PC_NAMES.get(pc, str(pc)) for pc in args.pc},
            "fields": {
                "harbor_code": "pc-hc 形式（例: 14-5）",
                "pc": "都道府県コード（tide736.net）",
                "hc": "港コード（tide736.net）",
                "harbor_name": "港名",
                "lat": "緯度（Nominatim ジオコーディング）",
                "lon": "経度（Nominatim ジオコーディング）",
            },
        },
        "harbors": all_harbors,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[保存] {OUTPUT_PATH}  ({len(all_harbors)} 港)")
    print("\n次のステップ:")
    print("  python tools/assign_harbor_mapping.py")


if __name__ == "__main__":
    main()
