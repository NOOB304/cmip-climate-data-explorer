from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
from rapidfuzz import fuzz

_CURRENT_YEAR = date.today().year


@dataclass(frozen=True, slots=True)
class ProviderProduct:
    id: str
    name: str
    description: str
    guide: str = ""
    start_year: int | None = None
    end_year: int | None = None
    default_start_year: int | None = None
    default_end_year: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    id: str
    name: str
    icon_text: str
    icon_color: str
    description: str
    products: tuple[ProviderProduct, ...]
    visible_filters: frozenset[str]


@dataclass(frozen=True, slots=True)
class ProviderVariable:
    id: str
    english_name: str
    chinese_name: str | None = None
    units: str | None = None
    aliases: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        return self.chinese_name or self.english_name or self.id


PROVIDERS = (
    ProviderDefinition(
        id="esgf",
        name="ESGF",
        icon_text="E",
        icon_color="#246b78",
        description="CMIP6 原始全球气候模式数据, 使用多个 ESGF 节点分页检索。",
        products=(ProviderProduct("cmip6", "CMIP6 原始数据", "ESGF CMIP6 文件"),),
        visible_filters=frozenset({"model", "scenario", "table", "frequency", "grid"}),
    ),
    ProviderDefinition(
        id="cds",
        name="Copernicus CDS",
        icon_text="C",
        icon_color="#005b96",
        description="Copernicus Climate Data Store 公共目录; 下载需要 CDS 账号和许可。",
        products=(
            ProviderProduct("projections-cmip6", "CMIP6 气候预估", "日/月 CMIP6 全球气候预估"),
            ProviderProduct(
                "reanalysis-era5-single-levels",
                "ERA5 单层再分析",
                "1940 年至今的逐小时单层再分析",
            ),
            ProviderProduct("reanalysis-era5-land", "ERA5-Land", "1950 年至今的陆面再分析"),
        ),
        visible_filters=frozenset({"model", "scenario", "frequency"}),
    ),
    ProviderDefinition(
        id="aws",
        name="AWS Open Data",
        icon_text="AWS",
        icon_color="#8a4b08",
        description="AWS 公共 NEX-GDDP-CMIP6 NetCDF, 无需 AWS 账号即可下载。",
        products=(
            ProviderProduct(
                "nex-gddp-cmip6",
                "NEX-GDDP-CMIP6",
                "0.25 度日尺度偏差订正气候预估",
            ),
        ),
        visible_filters=frozenset({"model", "scenario"}),
    ),
    ProviderDefinition(
        id="planetary",
        name="Planetary Computer",
        icon_text="PC",
        icon_color="#196127",
        description="Microsoft Planetary Computer STAC 中的 NEX-GDDP-CMIP6。",
        products=(
            ProviderProduct(
                "nasa-nex-gddp-cmip6",
                "NEX-GDDP-CMIP6",
                "0.25 度日尺度偏差订正气候预估",
            ),
        ),
        visible_filters=frozenset({"model", "scenario"}),
    ),
    ProviderDefinition(
        id="openmeteo",
        name="Open-Meteo",
        icon_text="OM",
        icon_color="#087f5b",
        description="免账号的点位气候数据 API, 可直接下载易于查看的 CSV。",
        products=(
            ProviderProduct(
                "historical",
                "历史再分析",
                "ERA5 / ERA5-Land 历史天气估计, 不是未来预估",
                "适合查看某个经纬度过去的气温、降水和风速; 日尺度 CSV, 近期约延迟 5 天。",
                1940,
                _CURRENT_YEAR,
                2000,
                _CURRENT_YEAR,
            ),
            ProviderProduct(
                "climate",
                "CMIP6 局地预估",
                "7 个 CMIP6 高分辨率模式, 经 ERA5-Land 偏差订正到约 10 km",
                "适合比较某个经纬度的长期气候变化; 日尺度数据截至 2050-01-01。",
                1950,
                2050,
                2000,
                2050,
            ),
        ),
        visible_filters=frozenset({"model", "location"}),
    ),
    ProviderDefinition(
        id="power",
        name="NASA POWER",
        icon_text="P",
        icon_color="#6b3fa0",
        description="指定经纬度的气象与太阳能时间序列, 可直接生成 CSV。",
        products=(
            ProviderProduct("daily", "日数据", "NASA POWER 日尺度点数据"),
            ProviderProduct("monthly", "月数据", "NASA POWER 月尺度点数据"),
        ),
        visible_filters=frozenset({"location"}),
    ),
    ProviderDefinition(
        id="cmr",
        name="NASA Earthdata",
        icon_text="N",
        icon_color="#b02a37",
        description="NASA Common Metadata Repository 粒度目录; 受保护文件需 Earthdata 登录。",
        products=(ProviderProduct("earthdata", "Earthdata 数据产品", "CMR 可下载粒度目录"),),
        visible_filters=frozenset(),
    ),
    ProviderDefinition(
        id="noaa",
        name="NOAA NCEI",
        icon_text="NCEI",
        icon_color="#1261a0",
        description="NOAA NCEI 站点观测数据服务, 可直接生成 CSV。",
        products=(
            ProviderProduct("daily-summaries", "全球日值摘要", "全球地面站逐日观测"),
            ProviderProduct(
                "global-summary-of-the-day",
                "全球每日天气摘要",
                "全球每日综合天气观测",
            ),
            ProviderProduct("global-hourly", "全球逐小时观测", "全球地面站逐小时观测"),
        ),
        visible_filters=frozenset({"station"}),
    ),
    ProviderDefinition(
        id="worldbank",
        name="World Bank",
        icon_text="WB",
        icon_color="#1f6f8b",
        description=(
            "世界银行公开指标 API。固定查询中国且无需账号或密钥。"
            "下载结果为 JSON。适合人口、经济、教育、就业和公共服务分析。"
        ),
        products=(
            ProviderProduct(
                "china-indicators",
                "中国社会经济指标",
                "中国历年人口、经济、教育、就业与公共服务指标",
                "选择一个中文指标和年份范围即可下载。缺失年份会保留为空值。",
                1960,
                _CURRENT_YEAR,
                2000,
                max(2000, _CURRENT_YEAR - 1),
            ),
        ),
        visible_filters=frozenset(),
    ),
    ProviderDefinition(
        id="who",
        name="WHO GHO",
        icon_text="WHO",
        icon_color="#2b78b8",
        description=(
            "世界卫生组织全球健康观察站公开接口。固定查询中国且无需账号或密钥。"
            "下载结果为 JSON。包含寿命、死亡率和医疗资源等指标。"
        ),
        products=(
            ProviderProduct(
                "china-health",
                "中国健康指标",
                "WHO 发布的中国年度健康与医疗资源指标",
                "部分指标按性别分别记录。下载文件会保留 WHO 原始字段。",
                1950,
                _CURRENT_YEAR,
                2000,
                max(2000, _CURRENT_YEAR - 1),
            ),
        ),
        visible_filters=frozenset(),
    ),
    ProviderDefinition(
        id="worldpop",
        name="WorldPop",
        icon_text="WP",
        icon_color="#2b7a4b",
        description=(
            "WorldPop 中国人口空间分布公开数据。无需账号或密钥。"
            "每年一个约 1 公里 GeoTIFF。可作为多年系列一次下载。"
        ),
        products=(
            ProviderProduct(
                "china-population-1km",
                "中国人口栅格",
                "中国 2015 至 2030 年人口空间分布 GeoTIFF",
                "单位为每个像元的人口数。坐标系为 WGS 84。",
                2015,
                2030,
                2015,
                2030,
            ),
        ),
        visible_filters=frozenset(),
    ),
)


NEX_VARIABLES = (
    ProviderVariable("pr", "Precipitation", "降水量", "kg m-2 s-1", ("降雨", "precipitation")),
    ProviderVariable("tas", "Near-Surface Air Temperature", "近地表气温", "K", ("气温",)),
    ProviderVariable("tasmax", "Daily Maximum Near-Surface Air Temperature", "近地表最高气温", "K"),
    ProviderVariable("tasmin", "Daily Minimum Near-Surface Air Temperature", "近地表最低气温", "K"),
    ProviderVariable("hurs", "Near-Surface Relative Humidity", "近地表相对湿度", "%"),
    ProviderVariable("huss", "Near-Surface Specific Humidity", "近地表比湿", "1"),
    ProviderVariable("rlds", "Surface Downwelling Longwave Radiation", "地表向下长波辐射", "W m-2"),
    ProviderVariable(
        "rsds", "Surface Downwelling Shortwave Radiation", "地表向下短波辐射", "W m-2"
    ),
    ProviderVariable("sfcWind", "Near-Surface Wind Speed", "近地表风速", "m s-1"),
)


CMR_VARIABLES = (
    ProviderVariable(
        "GPM_3IMERGDF",
        "GPM IMERG Final Daily Precipitation",
        "GPM IMERG 日降水",
        "mm/day",
        ("IMERG", "降雨"),
    ),
    ProviderVariable(
        "M2T1NXSLV",
        "MERRA-2 Hourly Single-Level Diagnostics",
        "MERRA-2 逐小时单层气象",
        aliases=("MERRA2", "再分析", "气温"),
    ),
    ProviderVariable(
        "MOD11A1",
        "MODIS Daily Land Surface Temperature",
        "MODIS 日地表温度",
        aliases=("LST", "地温"),
    ),
    ProviderVariable(
        "SPL3SMP_E",
        "SMAP Enhanced Daily Soil Moisture",
        "SMAP 日土壤湿度",
        aliases=("SMAP", "土壤水分"),
    ),
)


NCEI_VARIABLES = (
    ProviderVariable("PRCP", "Precipitation", "降水量", "mm", ("降雨",)),
    ProviderVariable("TAVG", "Average Temperature", "平均气温", "degC"),
    ProviderVariable("TMAX", "Maximum Temperature", "最高气温", "degC"),
    ProviderVariable("TMIN", "Minimum Temperature", "最低气温", "degC"),
    ProviderVariable("SNOW", "Snowfall", "降雪量", "mm"),
    ProviderVariable("SNWD", "Snow Depth", "积雪深度", "mm"),
    ProviderVariable("AWND", "Average Wind Speed", "平均风速", "m/s"),
    ProviderVariable("RHAV", "Average Relative Humidity", "平均相对湿度", "%"),
    ProviderVariable("PSUN", "Percent Possible Sunshine", "日照百分率", "%"),
)


NCEI_GSOD_VARIABLES = (
    ProviderVariable("TEMP", "Mean Temperature", "平均气温", "degC"),
    ProviderVariable("MAX", "Maximum Temperature", "最高气温", "degC"),
    ProviderVariable("MIN", "Minimum Temperature", "最低气温", "degC"),
    ProviderVariable("PRCP", "Precipitation", "降水量", "mm"),
    ProviderVariable("WDSP", "Mean Wind Speed", "平均风速", "m/s"),
    ProviderVariable("SLP", "Sea Level Pressure", "海平面气压", "hPa"),
    ProviderVariable("DEWP", "Mean Dew Point", "平均露点温度", "degC"),
    ProviderVariable("VISIB", "Mean Visibility", "平均能见度", "km"),
)


NCEI_HOURLY_VARIABLES = (
    ProviderVariable("TMP", "Air Temperature", "气温", "degC"),
    ProviderVariable("DEW", "Dew Point Temperature", "露点温度", "degC"),
    ProviderVariable("SLP", "Sea Level Pressure", "海平面气压", "hPa"),
    ProviderVariable("WND", "Wind Observation", "风向和风速"),
    ProviderVariable("AA1", "Liquid Precipitation", "液态降水量", "mm"),
    ProviderVariable("CIG", "Ceiling Height", "云底高度", "m"),
    ProviderVariable("VIS", "Visibility", "能见度", "m"),
)


OPEN_METEO_HISTORICAL_VARIABLES = (
    ProviderVariable(
        "temperature_2m_mean", "Mean Temperature (2 m)", "2 米平均气温", "°C", ("气温",)
    ),
    ProviderVariable(
        "temperature_2m_max", "Maximum Temperature (2 m)", "2 米最高气温", "°C"
    ),
    ProviderVariable(
        "temperature_2m_min", "Minimum Temperature (2 m)", "2 米最低气温", "°C"
    ),
    ProviderVariable("precipitation_sum", "Precipitation Sum", "总降水量", "mm", ("降雨",)),
    ProviderVariable("rain_sum", "Rain Sum", "降雨量", "mm"),
    ProviderVariable("snowfall_sum", "Snowfall Sum", "降雪量", "cm"),
    ProviderVariable(
        "wind_speed_10m_max", "Maximum Wind Speed (10 m)", "10 米最大风速", "m/s"
    ),
    ProviderVariable(
        "wind_gusts_10m_max", "Maximum Wind Gusts (10 m)", "10 米最大阵风", "m/s"
    ),
    ProviderVariable(
        "shortwave_radiation_sum", "Shortwave Radiation Sum", "短波辐射总量", "MJ/m²"
    ),
    ProviderVariable(
        "et0_fao_evapotranspiration", "Reference Evapotranspiration", "参考蒸散量", "mm"
    ),
)


OPEN_METEO_CLIMATE_VARIABLES = (
    ProviderVariable(
        "temperature_2m_mean", "Mean Temperature (2 m)", "2 米平均气温", "°C", ("气温",)
    ),
    ProviderVariable(
        "temperature_2m_max", "Maximum Temperature (2 m)", "2 米最高气温", "°C"
    ),
    ProviderVariable(
        "temperature_2m_min", "Minimum Temperature (2 m)", "2 米最低气温", "°C"
    ),
    ProviderVariable("precipitation_sum", "Precipitation Sum", "总降水量", "mm", ("降雨",)),
    ProviderVariable("rain_sum", "Rain Sum", "降雨量", "mm"),
    ProviderVariable("snowfall_sum", "Snowfall Sum", "降雪量", "cm"),
    ProviderVariable(
        "wind_speed_10m_mean", "Mean Wind Speed (10 m)", "10 米平均风速", "m/s"
    ),
    ProviderVariable(
        "wind_speed_10m_max", "Maximum Wind Speed (10 m)", "10 米最大风速", "m/s"
    ),
    ProviderVariable("cloud_cover_mean", "Mean Cloud Cover", "平均云量", "%"),
    ProviderVariable(
        "relative_humidity_2m_mean",
        "Mean Relative Humidity (2 m)",
        "2 米平均相对湿度",
        "%",
    ),
    ProviderVariable(
        "dew_point_2m_mean", "Mean Dew Point (2 m)", "2 米平均露点温度", "°C"
    ),
    ProviderVariable(
        "shortwave_radiation_sum", "Shortwave Radiation Sum", "短波辐射总量", "MJ/m²"
    ),
    ProviderVariable("pressure_msl_mean", "Mean Sea Level Pressure", "平均海平面气压", "hPa"),
    ProviderVariable(
        "soil_moisture_0_to_10cm_mean",
        "Mean Soil Moisture (0-10 cm)",
        "0-10 厘米平均土壤湿度",
        "m³/m³",
    ),
    ProviderVariable(
        "et0_fao_evapotranspiration", "Reference Evapotranspiration", "参考蒸散量", "mm"
    ),
)


WORLD_BANK_VARIABLES = (
    ProviderVariable("SP.POP.TOTL", "Population, total", "总人口", "人", ("人口",)),
    ProviderVariable("SP.POP.GROW", "Population growth", "人口增长率", "%"),
    ProviderVariable("SP.URB.TOTL.IN.ZS", "Urban population", "城镇人口占比", "%"),
    ProviderVariable("SP.DYN.CBRT.IN", "Birth rate, crude", "粗出生率", "每千人"),
    ProviderVariable("SP.DYN.CDRT.IN", "Death rate, crude", "粗死亡率", "每千人"),
    ProviderVariable("SP.DYN.LE00.IN", "Life expectancy at birth", "出生时预期寿命", "年"),
    ProviderVariable("SL.UEM.TOTL.ZS", "Unemployment, total", "失业率", "%"),
    ProviderVariable("SL.TLF.CACT.ZS", "Labor force participation rate", "劳动参与率", "%"),
    ProviderVariable("NY.GDP.MKTP.CD", "GDP", "国内生产总值 (GDP)", "现价美元", ("经济总量",)),
    ProviderVariable("NY.GDP.PCAP.CD", "GDP per capita", "人均国内生产总值", "现价美元"),
    ProviderVariable("SI.POV.GINI", "Gini index", "基尼指数", "指数"),
    ProviderVariable(
        "SE.XPD.TOTL.GD.ZS",
        "Government expenditure on education",
        "教育支出占 GDP",
        "%",
    ),
    ProviderVariable("SE.TER.ENRR", "School enrollment, tertiary", "高等教育毛入学率", "%"),
    ProviderVariable("SH.XPD.CHEX.GD.ZS", "Current health expenditure", "卫生支出占 GDP", "%"),
    ProviderVariable("SH.MED.BEDS.ZS", "Hospital beds", "每千人医院床位数", "每千人"),
)


WHO_VARIABLES = (
    ProviderVariable(
        "WHOSIS_000001",
        "Life expectancy at birth",
        "出生时预期寿命",
        "年",
        ("寿命",),
    ),
    ProviderVariable("WHOSIS_000002", "Healthy life expectancy at birth", "健康预期寿命", "年"),
    ProviderVariable("MDG_0000000001", "Infant mortality rate", "婴儿死亡率", "每千活产"),
    ProviderVariable(
        "MDG_0000000007",
        "Under-five mortality rate",
        "五岁以下儿童死亡率",
        "每千活产",
    ),
    ProviderVariable("MDG_0000000026", "Maternal mortality ratio", "孕产妇死亡率", "每十万活产"),
    ProviderVariable("HWF_0001", "Medical doctors", "每万人医生数", "每万人"),
    ProviderVariable("HWF_0002", "Medical doctors, number", "医生总数", "人"),
    ProviderVariable("WHS6_102", "Hospital beds", "每万人医院床位数", "每万人"),
)


WORLDPOP_VARIABLES = (
    ProviderVariable(
        "population_count",
        "Population count per grid cell",
        "人口空间分布",
        "人/像元",
        ("人口栅格", "人口密度", "population"),
    ),
)


CHINESE_NAMES = {
    "air_temperature": "空气温度",
    "capacity_of_soil_to_store_water": "土壤储水能力",
    "eastward_wind": "东向风",
    "evaporation_including_sublimation_and_transpiration": "蒸发、升华和蒸腾总量",
    "geopotential_height": "位势高度",
    "land_ice_area_percentage": "陆冰面积百分比",
    "moisture_in_upper_portion_of_soil_column": "上层土壤含水量",
    "northward_wind": "北向风",
    "percentage_of_the_grid_cell_occupied_by_land": "网格陆地面积百分比",
    "sea_area_percentage": "海洋面积百分比",
    "sea_ice_thickness": "海冰厚度",
    "sea_surface_height_above_geoid": "海表面相对大地水准面高度",
    "sea_surface_salinity": "海表盐度",
    "soil_moisture_content": "土壤含水量",
    "2m_temperature": "2 米气温",
    "2m_dewpoint_temperature": "2 米露点温度",
    "10m_u_component_of_wind": "10 米东向风",
    "10m_v_component_of_wind": "10 米北向风",
    "mean_sea_level_pressure": "平均海平面气压",
    "surface_pressure": "地表气压",
    "sea_surface_temperature": "海表温度",
    "total_precipitation": "总降水量",
    "skin_temperature": "地表皮肤温度",
    "snowfall": "降雪量",
    "snow_depth": "积雪深度",
    "surface_solar_radiation_downwards": "地表向下太阳辐射",
    "surface_thermal_radiation_downwards": "地表向下热辐射",
    "volumetric_soil_water_layer_1": "第一层土壤体积含水量",
    "near_surface_air_temperature": "近地表气温",
    "daily_maximum_near_surface_air_temperature": "近地表日最高气温",
    "daily_minimum_near_surface_air_temperature": "近地表日最低气温",
    "precipitation": "降水量",
    "near_surface_relative_humidity": "近地表相对湿度",
    "near_surface_specific_humidity": "近地表比湿",
    "near_surface_wind_speed": "近地表风速",
    "eastward_near_surface_wind": "近地表东向风",
    "northward_near_surface_wind": "近地表北向风",
    "sea_level_pressure": "海平面气压",
    "T2M": "2 米气温",
    "T2M_MAX": "2 米最高气温",
    "T2M_MIN": "2 米最低气温",
    "T2MDEW": "2 米露点温度",
    "PRECTOTCORR": "订正总降水量",
    "RH2M": "2 米相对湿度",
    "QV2M": "2 米比湿",
    "WS10M": "10 米风速",
    "WD10M": "10 米风向",
    "PS": "地表气压",
    "CLOUD_AMT": "云量",
    "GWETROOT": "根区土壤湿度",
    "GWETTOP": "表层土壤湿度",
    "ALLSKY_SFC_SW_DWN": "全天空地表向下短波辐射",
}


def provider_definition(provider_id: str) -> ProviderDefinition:
    return next(provider for provider in PROVIDERS if provider.id == provider_id)


async def discover_provider_variables(
    provider_id: str,
    product_id: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[ProviderVariable, ...]:
    if provider_id in {"aws", "planetary"}:
        return NEX_VARIABLES
    if provider_id == "cmr":
        return CMR_VARIABLES
    if provider_id == "noaa":
        if product_id == "global-summary-of-the-day":
            return NCEI_GSOD_VARIABLES
        if product_id == "global-hourly":
            return NCEI_HOURLY_VARIABLES
        return NCEI_VARIABLES
    if provider_id == "openmeteo":
        return (
            OPEN_METEO_CLIMATE_VARIABLES
            if product_id == "climate"
            else OPEN_METEO_HISTORICAL_VARIABLES
        )
    if provider_id == "cds":
        return await _discover_cds_variables(product_id, client)
    if provider_id == "power":
        return await _discover_power_variables(product_id, client)
    if provider_id == "worldbank":
        return WORLD_BANK_VARIABLES
    if provider_id == "who":
        return WHO_VARIABLES
    if provider_id == "worldpop":
        return WORLDPOP_VARIABLES
    return ()


def filter_provider_variables(
    variables: tuple[ProviderVariable, ...], query: str, limit: int = 100
) -> tuple[ProviderVariable, ...]:
    normalized = query.strip().casefold()
    if not normalized:
        return variables[:limit]

    def score(variable: ProviderVariable) -> float:
        fields = (
            variable.id,
            variable.english_name,
            variable.chinese_name or "",
            *variable.aliases,
        )
        lowered = tuple(field.casefold() for field in fields)
        if normalized == lowered[0]:
            return 1000
        if any(normalized in field for field in lowered):
            return 800
        return max(fuzz.WRatio(normalized, field) for field in lowered)

    ranked = sorted(variables, key=lambda variable: (-score(variable), variable.id))
    return tuple(variable for variable in ranked if score(variable) >= 45)[:limit]


async def _discover_cds_variables(
    product_id: str, client: httpx.AsyncClient | None
) -> tuple[ProviderVariable, ...]:
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        collection_url = (
            "https://cds.climate.copernicus.eu/api/catalogue/v1/collections/"
            f"{product_id}"
        )
        collection_response = await http.get(collection_url)
        collection_response.raise_for_status()
        form_url = next(
            link["href"]
            for link in collection_response.json().get("links", ())
            if link.get("rel") == "form"
        )
        form_response = await http.get(form_url)
        form_response.raise_for_status()
        variable_field = next(
            field for field in form_response.json() if field.get("name") == "variable"
        )
        choices = _form_choices(variable_field.get("details", {}))
        return tuple(
            ProviderVariable(
                id=value,
                english_name=label,
                chinese_name=CHINESE_NAMES.get(value),
            )
            for value, label in choices.items()
        )
    finally:
        if owns_client:
            await http.aclose()


async def _discover_power_variables(
    product_id: str, client: httpx.AsyncClient | None
) -> tuple[ProviderVariable, ...]:
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        response = await http.get(
            "https://power.larc.nasa.gov/api/system/manager/parameters",
            params={"community": "AG", "temporal": product_id.upper()},
        )
        response.raise_for_status()
        payload: dict[str, dict[str, Any]] = response.json()
        return tuple(
            ProviderVariable(
                id=code,
                english_name=str(details.get("name") or code),
                chinese_name=CHINESE_NAMES.get(code),
                units=str(details.get("units")) if details.get("units") else None,
                aliases=(str(details.get("definition") or ""),),
            )
            for code, details in payload.items()
        )
    finally:
        if owns_client:
            await http.aclose()


def _form_choices(details: dict[str, Any]) -> dict[str, str]:
    choices: dict[str, str] = {}
    values = details.get("values", ())
    labels = details.get("labels", {})
    for value in values:
        choices[str(value)] = str(labels.get(value) or str(value).replace("_", " ").title())
    for group in details.get("groups", ()):
        group_labels = group.get("labels", {})
        for value in group.get("values", ()):
            choices[str(value)] = str(
                group_labels.get(value) or str(value).replace("_", " ").title()
            )
    return choices
