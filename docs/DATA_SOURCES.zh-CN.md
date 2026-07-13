# 数据源与访问方式

软件中的“数据源”标签决定本次变量目录、筛选条件和结果列表查询哪个服务。
切换标签不会改变其他数据源，也不会把不同来源的同名文件混在一起。

| 数据源 | 当前产品 | 变量来源 | 下载方式 |
| --- | --- | --- | --- |
| ESGF | CMIP6 原始数据 | 本地 CMIP6 变量目录 | 直接进入下载任务，支持镜像和断点续传 |
| Copernicus CDS | CMIP6、ERA5、ERA5-Land | 实时读取 CDS 表单 API | 公共目录可查；取数需 CDS 账号、许可和异步任务 |
| AWS Open Data | NEX-GDDP-CMIP6 | NEX-GDDP STAC 资产 | AWS 公共 S3 NetCDF 直链，无需 AWS 账号 |
| Planetary Computer | NEX-GDDP-CMIP6 | Planetary Computer STAC | Azure 公共 NetCDF 直链 |
| NASA POWER | 日、月点数据 | 实时读取 POWER 参数 API | 按经纬度生成 CSV 并进入下载任务 |
| NASA Earthdata | GPM、MERRA-2、MODIS、SMAP | CMR 集合/粒度 API | 目录可分页；受保护文件需 Earthdata 登录 |
| NOAA NCEI | 日摘要、GSOD、全球逐小时 | NCEI 产品字段 | 按站点生成 CSV 并进入下载任务 |

## 长时间序列下载

AWS Open Data 和 Planetary Computer 的 NEX-GDDP-CMIP6 在服务端按年份保存文件。
软件会按数据源、变量、模型、情景、成员、网格和频率把这些年度文件合并成一个数据系列。
检索表格中的一行代表一个系列，并显示所选年份范围内的覆盖时间、文件数和总大小。
勾选一次会把该系列内全部年度文件加入下载任务；任务页仍逐文件显示进度，已存在的文件不会重复创建任务。

## 中文变量

常用气温、降水、湿度、风、气压、辐射和土壤变量内置中文名称及别名。
CDS 与 POWER 的变量清单在切换产品时实时读取，因此服务以后增加的新变量仍会显示，
没有内置中文名称时使用服务返回的英文名称。

## 文件位置

- NetCDF：`NetCDF\来源或模型\产品或情景`
- GeoTIFF：`GeoTIFF\来源或模型\产品或情景`
- CSV：`Tables\来源或站点\产品`

所有目录都位于设置页选择的数据保存位置下，并会按数据组显示在“本地数据”页。

## 账号边界

CDS 与部分 NASA Earthdata 文件需要用户账号、许可接受或短期授权。当前版本不会把
浏览器登录信息写入程序，也不会把目录结果伪装成可直接下载文件；这类结果的勾选框
会禁用，选中结果后可使用“打开来源”进入官方页面。
