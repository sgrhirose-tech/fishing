"""
海しるAPI フェッチャー
海上保安庁「海しる」のAPIを使って、各釣り場の底質・水深データを取得する

使い方:
    from umishiru_fetcher import UmishiruFetcher
    fetcher = UmishiruFetcher()
    data = fetcher.get_seabed(lat=35.304, lon=139.480)

このモジュールを単独で実行するとテストが走ります:
    python umishiru_fetcher.py

APIキーは .env ファイルから読み込みます（git には含まれません）。
.env ファイルの書き方:
    UMISHIRU_API_KEY_1=xxxxxxxxxxxxxxxx
    UMISHIRU_API_KEY_2=xxxxxxxxxxxxxxxx
    UMISHIRU_API_KEY_3=xxxxxxxxxxxxxxxx
"""

import os
import json
import requests
from datetime import datetime

# ---------------------------------------------------------------------------
# .env ローダー（python-dotenv なしで動かす簡易版）
# ---------------------------------------------------------------------------

def _load_env(path=".env"):
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

_ENV = _load_env()

# ---------------------------------------------------------------------------
# 底質コード → 内部コードのマッピング
# 海しるAPIが返す底質コードを fishing_advisor.py の seabed 形式に変換する
# ---------------------------------------------------------------------------

# 海しる底質コード（地質図や底質図で使われる凡例コード）
# 試用版で確認でき次第、必要に応じて追記してください
SEABED_CODE_MAP = {
    # 砂系
    "S":    "sand",         # Sand（砂）
    "sS":   "sand",         # slightly gravelly Sand（わずかに礫を含む砂）
    "gS":   "sand_gravel",  # gravelly Sand（礫質砂）
    "mS":   "sand_mud",     # muddy Sand（泥質砂）
    "csS":  "sand",         # coarse Sand（粗砂）
    "msS":  "sand",         # medium Sand（中砂）
    "fsS":  "sand",         # fine Sand（細砂）
    # 礫系
    "G":    "rock",         # Gravel（礫）
    "sG":   "sand_gravel",  # sandy Gravel（砂質礫）
    "mG":   "sand_gravel",  # muddy Gravel（泥質礫）
    # 泥系
    "M":    "mud",          # Mud（泥）
    "sM":   "sand_mud",     # sandy Mud（砂質泥）
    "gM":   "mud",          # gravelly Mud（礫質泥）
    # 岩盤
    "R":    "rock",         # Rock（岩盤）
    "CR":   "rock",         # Coral Reef（珊瑚礁）
}

# 底質コードの日本語表記
SEABED_LABEL_MAP = {
    "sand":        "砂地",
    "sand_gravel": "砂礫",
    "sand_mud":    "砂泥",
    "rock":        "岩礁・礫",
    "mud":         "泥底",
}

# ---------------------------------------------------------------------------
# メインクラス
# ---------------------------------------------------------------------------

class UmishiruFetcher:
    """
    海しるAPI を使って海底情報（底質・水深）を取得するクラス

    APIキーは .env ファイルから自動で読み込まれます。
    3つのキーは試用版で割り当てられたもので、
    それぞれのキーが対応するデータ種別はAPIドキュメントを参照してください。
    """

    # 海しるAPIのベースURL
    # ※試用版の正確なURLはAPIキー発行時のメールまたは利用規約を確認してください
    BASE_URLS = [
        "https://gisapi.kaiho.mlit.go.jp",
        "https://gis.kaiho.mlit.go.jp/umishiruAPI",
    ]

    def __init__(self):
        self.keys = {
            "key1": _ENV.get("UMISHIRU_API_KEY_1") or os.environ.get("UMISHIRU_API_KEY_1", ""),
            "key2": _ENV.get("UMISHIRU_API_KEY_2") or os.environ.get("UMISHIRU_API_KEY_2", ""),
            "key3": _ENV.get("UMISHIRU_API_KEY_3") or os.environ.get("UMISHIRU_API_KEY_3", ""),
        }
        self._base_url = None  # 動作確認後に確定する
        self._key_param = "apikey"  # APIキーのクエリパラメータ名

    def is_configured(self):
        """APIキーが設定されているか確認"""
        return any(v for v in self.keys.values())

    def _get(self, path, params, key_name="key1", timeout=10):
        """
        APIリクエストを送る共通メソッド
        複数のベースURLとキーパラメータ名を試みる
        """
        key = self.keys.get(key_name, "")
        if not key:
            return None

        errors = []
        for base_url in self.BASE_URLS:
            for key_param in ["apikey", "api_key", "key", "token"]:
                url = f"{base_url}{path}"
                all_params = {key_param: key, **params}
                try:
                    resp = requests.get(url, params=all_params, timeout=timeout,
                                        headers={"User-Agent": "FishingAdvisor/1.0"})
                    if resp.status_code == 200:
                        self._base_url = base_url
                        self._key_param = key_param
                        return resp
                    elif resp.status_code not in (403, 404):
                        # 認証エラー以外はレスポンスを返す（エラー内容を確認するため）
                        return resp
                except requests.exceptions.Timeout:
                    errors.append(f"タイムアウト: {url}")
                except Exception as e:
                    errors.append(f"エラー: {url} → {e}")

        if errors:
            print(f"    [海しるAPI] 接続失敗: {errors[0]}")
        return None

    def get_seabed(self, lat, lon):
        """
        指定座標の底質データを取得する

        Returns:
            dict: {
                "seabed_code": 内部コード (例: "sand"),
                "seabed_raw": APIの生データ,
                "label": 日本語ラベル,
                "source": "umishiru" or "fallback"
            }
            取得失敗時は None
        """
        resp = self._get(
            "/seabed",
            {"lat": lat, "lon": lon, "format": "json"},
            key_name="key1",
        )
        if resp is None:
            return None

        try:
            data = resp.json()
            # レスポンス構造はAPIによって異なる。以下はよくあるパターンを試みる
            raw_code = (
                data.get("substrate_code") or
                data.get("seabed_code") or
                data.get("bottom_type") or
                data.get("code") or
                (data.get("features", [{}])[0].get("properties", {}).get("code") if data.get("features") else None)
            )
            if raw_code:
                seabed_type = SEABED_CODE_MAP.get(str(raw_code), "sand")
                return {
                    "seabed_code": seabed_type,
                    "seabed_raw": raw_code,
                    "label": SEABED_LABEL_MAP.get(seabed_type, seabed_type),
                    "source": "umishiru",
                    "raw_response": data,
                }
        except Exception as e:
            print(f"    [海しるAPI] 底質データ解析エラー: {e}")
            print(f"    レスポンス: {resp.text[:200]}")

        return None

    def get_depth(self, lat, lon):
        """
        指定座標の水深データを取得する（メートル）

        Returns:
            float: 水深（m）または None
        """
        resp = self._get(
            "/depth",
            {"lat": lat, "lon": lon, "format": "json"},
            key_name="key2",
        )
        if resp is None:
            return None

        try:
            data = resp.json()
            depth = (
                data.get("depth") or
                data.get("depth_m") or
                data.get("value") or
                (data.get("features", [{}])[0].get("properties", {}).get("depth") if data.get("features") else None)
            )
            if depth is not None:
                return float(depth)
        except Exception as e:
            print(f"    [海しるAPI] 水深データ解析エラー: {e}")

        return None

    def get_bathymetry_info(self, lat, lon):
        """
        指定座標の海底地形情報を取得する（底質+水深+その他）

        Returns:
            dict or None
        """
        resp = self._get(
            "/bathymetry",
            {"lat": lat, "lon": lon, "format": "json"},
            key_name="key3",
        )
        if resp is None:
            return None

        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text[:500]}

    def diagnose(self, lat=35.304, lon=139.480):
        """
        APIの動作確認（テスト用）
        実際にリクエストを送り、レスポンスを表示する
        """
        print("=" * 60)
        print("海しるAPI 動作確認")
        print("=" * 60)
        print(f"テスト座標: lat={lat}, lon={lon}")
        print()

        if not self.is_configured():
            print("[エラー] APIキーが設定されていません。.env ファイルを確認してください")
            return

        print("キー設定:")
        for name, key in self.keys.items():
            status = f"{key[:8]}..." if key else "未設定"
            print(f"  {name}: {status}")
        print()

        # 各エンドポイントを試す
        endpoints_to_try = [
            ("/seabed", {"lat": lat, "lon": lon, "format": "json"}, "key1", "底質"),
            ("/depth",  {"lat": lat, "lon": lon, "format": "json"}, "key2", "水深"),
            ("/bathymetry", {"lat": lat, "lon": lon, "format": "json"}, "key3", "海底地形"),
        ]

        for path, params, key_name, label in endpoints_to_try:
            print(f"[{label}] {path} ({key_name}):")
            resp = self._get(path, params, key_name=key_name)
            if resp:
                print(f"  HTTP {resp.status_code} ({resp.headers.get('Content-Type', '')})")
                try:
                    data = resp.json()
                    print(f"  JSON keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
                    print(f"  内容: {json.dumps(data, ensure_ascii=False)[:300]}")
                except Exception:
                    print(f"  テキスト: {resp.text[:300]}")
            else:
                print("  → レスポンスなし")
            print()

        print("=" * 60)
        print("確認完了")
        print()
        print("上記で正常なレスポンスが得られた場合は、")
        print("umishiru_fetcher.py の get_seabed() / get_depth() 内の")
        print("レスポンス解析部分を実際の構造に合わせて修正してください。")


if __name__ == "__main__":
    fetcher = UmishiruFetcher()
    fetcher.diagnose()
