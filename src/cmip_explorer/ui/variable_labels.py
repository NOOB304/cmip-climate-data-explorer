from __future__ import annotations

import re

VARIABLE_NAMES = {
    "clt": "总云量",
    "hfls": "地表向上潜热通量",
    "hfss": "地表向上感热通量",
    "mrro": "总径流量",
    "mrsos": "表层土壤湿度",
    "mrsol": "土壤层总含水量",
    "pr": "降水量",
    "prc": "对流降水量",
    "prsn": "降雪通量",
    "ps": "地表气压",
    "psl": "海平面气压",
    "rlds": "地表向下长波辐射",
    "rldscs": "晴空地表向下长波辐射",
    "rlus": "地表向上长波辐射",
    "rsds": "地表向下短波辐射",
    "rsdscs": "晴空地表向下短波辐射",
    "rsdsdiff": "地表向下漫射短波辐射",
    "rsus": "地表向上短波辐射",
    "rsuscs": "晴空地表向上短波辐射",
    "rlut": "大气顶层向外长波辐射",
    "rlutcs": "晴空大气顶层向外长波辐射",
    "tas": "近地表气温",
    "tasmax": "近地表最高气温",
    "tasmin": "近地表最低气温",
    "ta": "空气温度",
    "ts": "地表温度",
    "tsl": "土壤温度",
    "tslsi": "陆地或海冰表面温度",
    "uas": "近地表东向风",
    "vas": "近地表北向风",
    "ua": "东向风",
    "va": "北向风",
    "sfcWind": "近地表风速",
    "huss": "近地表比湿",
    "hus": "比湿",
    "hurs": "近地表相对湿度",
    "zg": "位势高度",
    "wap": "垂直气压速度",
    "tauu": "地表东向风应力",
    "tauv": "地表北向风应力",
    "prw": "大气可降水量",
    "o3": "O3 质量混合比",
    "co2": "CO2 浓度",
    "n2o": "N2O 摩尔分数",
    "no2": "NO2 浓度",
    "tos": "海表温度",
    "snw": "地表积雪量",
    "bldep": "边界层高度",
    "vortmean": "相对涡度",
    "rv850": "850 hPa 相对涡度",
    "toz": "臭氧总柱量",
    "cod": "云光学厚度",
    "od550aer": "550 nm 环境气溶胶光学厚度",
    "bs550aer": "气溶胶后向散射系数",
    "ec550aer": "气溶胶消光系数",
    "mc": "对流质量通量",
}

LONG_NAME_NAMES = {
    "Air Temperature": "空气温度",
    "Surface Temperature": "地表温度",
    "Temperature of Soil": "土壤温度",
    "Total Cloud Cover Percentage": "总云量百分比",
    "Surface Upward Latent Heat Flux": "地表向上潜热通量",
    "Surface Upward Sensible Heat Flux": "地表向上感热通量",
    "Total Runoff": "总径流量",
    "Convective Precipitation": "对流降水量",
    "Snowfall Flux": "降雪通量",
    "Surface Air Pressure": "地表气压",
    "Specific Humidity": "比湿",
    "Near-Surface Relative Humidity": "近地表相对湿度",
    "Eastward Wind": "东向风",
    "Northward Wind": "北向风",
    "Eastward Near-Surface Wind": "近地表东向风",
    "Northward Near-Surface Wind": "近地表北向风",
    "Boundary Layer Depth": "边界层高度",
    "Relative Vorticity": "相对涡度",
    "Cloud Ice Mixing Ratio": "云冰混合比",
    "Cloud Water Mixing Ratio": "云水混合比",
    "Convective Cloud Optical Depth": "对流云光学厚度",
    "Stratiform Cloud Optical Depth": "层状云光学厚度",
    "Graupel Mixing Ratio": "霰混合比",
    "Mass Fraction of Rain in Air": "空气中雨水质量分数",
    "Mass Fraction of Snow in Air": "空气中雪质量分数",
    "Surface Snow Amount": "地表积雪量",
    "Shortwave Heating Rate Due to Volcanic Aerosols": "火山气溶胶短波加热率",
    "Longwave Flux Due to Volcanic Aerosols at the Surface": "地表火山气溶胶长波通量",
    "TOA Outgoing Clear-Sky Longwave Flux Due to Volcanic Aerosols": (
        "大气顶层晴空火山气溶胶向外长波通量"
    ),
    "TOA Outgoing Clear-Sky Shortwave Flux Due to Volcanic Aerosols": (
        "大气顶层晴空火山气溶胶向外短波通量"
    ),
    "Wet Bulb Potential Temperature": "湿球位温",
    "Geopotential Height": "位势高度",
    "Cloud Optical Depth": "云光学厚度",
    "Total Column Ozone": "O3 总柱量",
    "Aerosol Backscatter Coefficient": "气溶胶后向散射系数",
    "Aerosol Extinction Coefficient": "气溶胶消光系数",
    "Maximum Hourly Precipitation Rate": "最大小时降水率",
}

GLOSSARY = {
    "mass": "质量",
    "mole": "摩尔",
    "fraction": "分数",
    "concentration": "浓度",
    "flux": "通量",
    "rate": "速率",
    "total": "总",
    "mean": "平均",
    "maximum": "最大",
    "minimum": "最小",
    "surface": "地表",
    "ocean": "海洋",
    "sea": "海洋",
    "sea-ice": "海冰",
    "land": "陆地",
    "soil": "土壤",
    "air": "空气",
    "atmosphere": "大气",
    "water": "水",
    "ice": "冰",
    "snow": "雪",
    "rainfall": "降雨",
    "precipitation": "降水",
    "temperature": "温度",
    "pressure": "气压",
    "humidity": "湿度",
    "wind": "风",
    "velocity": "速度",
    "height": "高度",
    "depth": "深度",
    "thickness": "厚度",
    "content": "含量",
    "percentage": "百分比",
    "cover": "覆盖率",
    "area": "面积",
    "volume": "体积",
    "layer": "层",
    "cloud": "云",
    "aerosol": "气溶胶",
    "radiation": "辐射",
    "shortwave": "短波",
    "longwave": "长波",
    "heat": "热量",
    "clear-sky": "晴空",
    "downward": "向下",
    "upward": "向上",
    "downwelling": "向下",
    "upwelling": "向上",
    "outgoing": "向外",
    "eastward": "东向",
    "northward": "北向",
    "near-surface": "近地表",
    "carbon": "碳",
    "nitrogen": "氮",
    "oxygen": "氧",
    "iron": "铁",
    "organic": "有机",
    "inorganic": "无机",
    "dissolved": "溶解",
    "particulate": "颗粒态",
    "phytoplankton": "浮游植物",
    "vegetation": "植被",
    "litter": "凋落物",
    "dust": "沙尘",
    "salt": "盐",
    "production": "生产量",
    "respiration": "呼吸量",
    "deposition": "沉降",
    "emission": "排放",
    "transport": "输送",
    "advection": "平流",
    "mixing": "混合",
    "ratio": "比率",
    "tendency": "变化趋势",
    "change": "变化",
    "potential": "位势",
    "relative": "相对",
    "incident": "入射",
    "speed": "速度",
    "vapor": "水汽",
    "path": "路径",
    "sublimation": "升华",
    "boundary": "边界",
    "physics": "物理过程",
    "convection": "对流",
    "full-levels": "模式全层",
    "half-levels": "模式半层",
    "optical": "光学",
    "convective": "对流",
    "stratiform": "层状",
    "natural": "自然",
    "dry": "干",
    "liquid": "液态",
    "net": "净",
    "primary": "初级",
    "stress": "应力",
    "diffusivity": "扩散系数",
    "model": "模式",
    "tile": "地表类型",
    "tiles": "地表类型",
    "toa": "大气顶层",
    "pbl": "边界层",
}

IGNORED_WORDS = {
    "a",
    "all",
    "and",
    "as",
    "at",
    "by",
    "due",
    "each",
    "expressed",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "over",
    "parameterized",
    "the",
    "through",
    "to",
}


def friendly_variable_name(
    variable_id: str,
    long_name: str | None,
    chinese_name: str | None = None,
) -> str:
    if chinese_name:
        return chinese_name
    if variable_id in VARIABLE_NAMES:
        return VARIABLE_NAMES[variable_id]
    if long_name in LONG_NAME_NAMES:
        return LONG_NAME_NAMES[long_name]
    derived = _derived_name(variable_id, long_name or "")
    glossary_name = _glossary_name(long_name or "")
    return derived or glossary_name or long_name or variable_id or "未知变量"


def _derived_name(variable_id: str, long_name: str) -> str | None:
    rules = (
        (r"^ta(?:\d|$)", "空气温度"),
        (r"^hus(?:\d|$)", "比湿"),
        (r"^ua\d", "东向风"),
        (r"^va\d", "北向风"),
        (r"^zg\d", "位势高度"),
        (r"^wap\d", "垂直气压速度"),
    )
    for pattern, name in rules:
        if re.match(pattern, variable_id):
            level = re.search(r"\d+", variable_id)
            return f"{name} ({level.group()} 层)" if level else name
    chemical = re.search(r"\b(CO2|NO2|N2O|O3|CH4|SO2)\b", long_name)
    if chemical and "fraction" in long_name.casefold():
        return f"{chemical.group(1)} 浓度"
    return None


def _glossary_name(long_name: str) -> str | None:
    if not long_name:
        return None
    pieces = re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)?\d*|\d+(?:\.\d+)?|[^\x00-\x7F]+", long_name)
    translated: list[str] = []
    unknown: list[str] = []
    for piece in pieces:
        key = piece.casefold()
        if key in IGNORED_WORDS:
            continue
        if key in GLOSSARY:
            translated.append(GLOSSARY[key])
        elif re.fullmatch(r"(?:CO2|NO2|N2O|O3|CH4|SO2|C|N|P|pH|\d+(?:\.\d+)?)", piece):
            translated.append(piece)
        elif len(piece) <= 2:
            continue
        else:
            unknown.append(piece)
    if not translated or len(unknown) > 1:
        return None
    result = "".join(translated)
    if unknown:
        result += f" ({unknown[0]})"
    return result
