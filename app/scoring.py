"""
スコアリングロジックモジュール。
CLI（fishing_advisor_pythonista.py）と FastAPI ウェブアプリで共用。
"""

from .spots import spot_lat, spot_lon, spot_bearing, spot_kisugo, spot_terrain


# ============================================================
# ユーティリティ
# ============================================================

def angle_diff(a: float, b: float) -> float:
    diff = abs(a - b) % 360
    return min(diff, 360 - diff)


def direction_label(deg: float) -> str:
    dirs = [
        "北", "北北東", "北東", "東北東",
        "東", "東南東", "南東", "南南東",
        "南", "南南西", "南西", "西南西",
        "西", "西北西", "北西", "北北西",
    ]
    idx = int((deg + 11.25) / 22.5) % 16
    return dirs[idx]


def weather_code_label(code) -> str:
    """WMO 天気コードを日本語ラベルに変換。"""
    if code is None:          return "不明"
    if code == 0:             return "快晴"
    if code == 1:             return "晴れ"
    if code == 2:             return "晴れ時々くもり"
    if code == 3:             return "くもり"
    if code in (45, 48):      return "霧"
    if code in (51, 53, 55):  return "霧雨"
    if code in (56, 57):      return "着氷性霧雨"
    if 61 <= code <= 63:      return "雨"
    if code == 65:            return "大雨"
    if code in (71, 73):      return "雪"
    if code == 75:            return "大雪"
    if code == 77:            return "霰"
    if code in (80, 81, 82):  return "にわか雨"
    if code in (85, 86):      return "にわか雪"
    if code == 95:            return "雷雨"
    if code in (96, 99):      return "雷雨(雹)"
    return f"天気コード{code}"


# ============================================================
# 個別スコア計算
# ============================================================

def calc_wind_score(wind_speed: float, wind_dir: float, sea_bearing_deg) -> dict:
    """
    sea_bearing_deg: 海方向（度）。None の場合は方位スコアなし（中立値）。
    """
    if sea_bearing_deg is not None:
        inland_dir = (sea_bearing_deg + 180) % 360
        diff = angle_diff(wind_dir, inland_dir)
        if diff <= 45:
            dir_label = "追い風(オフショア)"
            dir_pts = 15
            is_surfer_friendly = False
        elif diff <= 90:
            dir_label = "やや追い風"
            dir_pts = 10
            is_surfer_friendly = False
        elif diff <= 135:
            dir_label = "横風〜やや向かい風"
            dir_pts = 3
            is_surfer_friendly = True
        else:
            dir_label = "向かい風(オンショア)"
            dir_pts = 6
            is_surfer_friendly = True
    else:
        dir_pts = 7
        dir_label = "方位データなし"
        is_surfer_friendly = None

    if wind_speed < 4.0:
        spd_label = f"{wind_speed:.1f}m/s(微風・かなり行きやすい)"
        spd_pts = 25
    elif wind_speed < 5.0:
        spd_label = f"{wind_speed:.1f}m/s(弱風・良条件)"
        spd_pts = 20
    elif wind_speed < 7.0:
        spd_label = f"{wind_speed:.1f}m/s(境目)"
        spd_pts = 10
    elif wind_speed < 8.0:
        spd_label = f"{wind_speed:.1f}m/s(やめた方がいい)"
        spd_pts = 4
    else:
        spd_label = f"{wind_speed:.1f}m/s(中止推奨)"
        spd_pts = 0

    return {
        "dir_pts": dir_pts,
        "spd_pts": spd_pts,
        "dir_label": dir_label,
        "spd_label": spd_label,
        "surfer_friendly": is_surfer_friendly,
        "total_pts": dir_pts + spd_pts,
    }


def calc_wave_score(wave_height, swell_period=None) -> dict:
    if wave_height is None:
        base_pts, height_label = 15, "データなし"
    elif wave_height <= 0.4:
        base_pts, height_label = 30, f"{wave_height:.1f}m(ベタ凪・かなり行きやすい)"
    elif wave_height <= 0.8:
        base_pts, height_label = 22, f"{wave_height:.1f}m(良好)"
    elif wave_height <= 1.2:
        base_pts, height_label = 10, f"{wave_height:.1f}m(境目・場所次第)"
    elif wave_height <= 1.5:
        base_pts, height_label = 3,  f"{wave_height:.1f}m(やめた方がいい)"
    else:
        base_pts, height_label = 0,  f"{wave_height:.1f}m(中止推奨)"

    period_penalty, period_label = 0, ""
    if swell_period is not None:
        period_label = f" 周期{swell_period:.0f}s"
        if swell_period >= 8:
            period_penalty, period_label = -8, period_label + "(長周期うねり・危険)"
        elif swell_period >= 7:
            period_penalty, period_label = -5, period_label + "(うねりあり・注意)"
        elif swell_period >= 6:
            period_penalty, period_label = -3, period_label + "(やや長い)"

    return {
        "pts": max(0, base_pts + period_penalty),
        "label": height_label + period_label,
        "height_label": height_label,
        "period_label": period_label,
    }


def calc_temp_score(sst) -> dict:
    if sst is None:
        return {"pts": 8, "label": "データなし"}
    if 20.0 <= sst <= 24.0:
        return {"pts": 15, "label": f"{sst:.1f}°C(最適)"}
    elif 18.0 <= sst < 20.0 or 24.0 < sst <= 26.0:
        return {"pts": 11, "label": f"{sst:.1f}°C(良好)"}
    elif 15.0 <= sst < 18.0 or 26.0 < sst <= 28.0:
        return {"pts": 5,  "label": f"{sst:.1f}°C(やや不向き)"}
    else:
        return {"pts": 1,  "label": f"{sst:.1f}°C(厳しい)"}


def calc_air_temp_score(temp_max) -> dict:
    if temp_max is None:
        return {"pts": 3, "label": "データなし"}
    if 15.0 <= temp_max <= 24.0:
        pts, label = 5, "最も快適"
    elif 10.0 <= temp_max < 15.0 or 25.0 <= temp_max <= 27.0:
        pts, label = 4, "快適"
    elif 5.0 <= temp_max < 10.0 or 28.0 <= temp_max <= 30.0:
        pts, label = 3, "対策が必要"
    elif 0.0 <= temp_max < 5.0 or 31.0 <= temp_max <= 34.0:
        pts, label = 2, "厳しい"
    elif temp_max < 0.0 or 35.0 <= temp_max <= 37.0:
        pts, label = 1, "危険寄り"
    else:
        pts, label = 0, "危険(熱中症リスク高)"
    return {"pts": pts, "label": f"{temp_max:.1f}°C({label})"}


def calc_seabed_score(kisugo_score: float) -> dict:
    """kisugo_score: 0〜100 → 0〜15点に換算。"""
    pts = round(kisugo_score / 100 * 15)
    if kisugo_score >= 80:
        label = "砂地主体(シロギス最適)"
    elif kisugo_score >= 60:
        label = "砂混じり(良好)"
    elif kisugo_score >= 40:
        label = "混合底(可)"
    else:
        label = "砂以外主体(不向き)"
    return {"pts": pts, "label": label}


# ============================================================
# 総合スコア計算
# ============================================================

def score_spot(spot: dict, weather_data: dict, marine_data: dict,
               sst_noaa=None, fetch_km: float | None = None) -> dict:
    """スポット・気象・海況データからスコアを計算して返す。"""
    from .scoring import (calc_seabed_score, calc_wind_score, calc_wave_score,
                          calc_temp_score, calc_air_temp_score, direction_label,
                          weather_code_label, angle_diff)
    details = {}

    # 底質スコア
    kisugo = spot_kisugo(spot)
    sb = calc_seabed_score(kisugo)
    seabed_pts = sb["pts"]
    details["seabed"] = sb["label"]
    details["terrain"] = spot_terrain(spot)

    # 風スコア
    wind_speed = wind_dir = None
    if weather_data and "daily" in weather_data:
        d = weather_data["daily"]
        spd_list = d.get("wind_speed_10m_max", [])
        dir_list = d.get("wind_direction_10m_dominant", [])
        if spd_list and spd_list[0] is not None:
            wind_speed = spd_list[0]
        if dir_list and dir_list[0] is not None:
            wind_dir = dir_list[0]

    sea_bearing = spot_bearing(spot)
    if wind_speed is not None and wind_dir is not None:
        ws = calc_wind_score(wind_speed, wind_dir, sea_bearing)
        wind_pts = ws["total_pts"]
        details["wind_speed"] = ws["spd_label"]
        details["wind_dir"] = f"{direction_label(wind_dir)}({ws['dir_label']})"
        details["surfer_friendly"] = ws["surfer_friendly"]
    else:
        wind_pts = 20
        details["wind_speed"] = "データなし"
        details["wind_dir"] = "データなし"
        details["surfer_friendly"] = None

    # 波高スコア
    wave_height = wave_period = None
    wave_source = None
    if marine_data and "daily" in marine_data:
        wh_list = marine_data["daily"].get("wave_height_max", [])
        if wh_list and wh_list[0] is not None:
            wave_height = wh_list[0]
            wave_source = "open-meteo"
        wp_list = marine_data["daily"].get("wave_period_max", [])
        if wp_list and wp_list[0] is not None:
            wave_period = wp_list[0]
    if wave_height is None:
        wh = marine_data.get("wave_height_max")
        if wh is not None:
            wave_height = wh
            wave_source = "weatherapi"
        wp = marine_data.get("swell_period_max")
        if wp is not None:
            wave_period = wp
    if wave_height is None and fetch_km is not None and wind_speed is not None:
        from .weather import estimate_wave_from_wind
        wave_height = estimate_wave_from_wind(wind_speed, fetch_km)
        wave_source = "estimate"

    wv = calc_wave_score(wave_height, wave_period)
    wave_pts = wv["pts"]
    details["wave"] = wv["label"]
    details["wave_height"] = wv["height_label"] + ("(風推定)" if wave_source == "estimate" else "")
    details["wave_period"] = wv["period_label"] if wv["period_label"] else "データなし"
    details["wave_source"] = wave_source

    # 水温スコア
    sst = sst_noaa
    tp = calc_temp_score(sst)
    temp_pts = tp["pts"]
    details["sst"] = tp["label"]

    # 降水ペナルティ
    precip = None
    if weather_data and "daily" in weather_data:
        pr_list = weather_data["daily"].get("precipitation_sum", [])
        if pr_list and pr_list[0] is not None:
            precip = pr_list[0]

    rain_penalty = 0
    if precip is not None:
        details["precip"] = f"{precip:.1f}mm"
        if precip > 10:
            rain_penalty = -30
            details["rain_warning"] = "大雨(釣行非推奨)"
        elif precip > 5:
            rain_penalty = -15
            details["rain_warning"] = "雨(注意)"
        elif precip > 1:
            rain_penalty = -5
            details["rain_warning"] = "小雨"
    else:
        details["precip"] = "データなし"

    # 気温・天気スコア
    temp_6am = temp_max = weather_code = None
    if weather_data and "daily" in weather_data:
        wc_list = weather_data["daily"].get("weather_code", [])
        if wc_list and wc_list[0] is not None:
            weather_code = int(wc_list[0])
        tm_list = weather_data["daily"].get("temperature_2m_max", [])
        if tm_list and tm_list[0] is not None:
            temp_max = tm_list[0]
    if weather_data and "hourly" in weather_data:
        t2m = weather_data["hourly"].get("temperature_2m", [])
        if len(t2m) > 6 and t2m[6] is not None:
            temp_6am = t2m[6]

    at = calc_air_temp_score(temp_max)
    air_temp_pts = at["pts"]
    details["sky"] = weather_code_label(weather_code)
    details["temp_max"] = at["label"]
    details["temp_6am"] = f"{temp_6am:.1f}°C" if temp_6am is not None else "データなし"

    # 生データ
    details["_wind_speed_raw"] = wind_speed
    details["_wind_dir_raw"] = wind_dir
    details["_wave_height_raw"] = wave_height
    details["_wave_period_raw"] = wave_period
    details["_sst_raw"] = sst
    details["_precip_raw"] = precip
    details["_kisugo_raw"] = kisugo
    details["_temp_6am_raw"] = temp_6am
    details["_temp_max_raw"] = temp_max
    details["_weather_code_raw"] = weather_code

    total = seabed_pts + wind_pts + wave_pts + temp_pts + air_temp_pts + rain_penalty

    return {
        "spot": spot,
        "total": total,
        "scores": {
            "seabed": seabed_pts,
            "wind": wind_pts,
            "wave": wave_pts,
            "temp": temp_pts,
            "air_temp": air_temp_pts,
            "rain_penalty": rain_penalty,
        },
        "details": details,
    }
