# CMIP Climate Data Explorer

面向 Windows 10/11 x64 中文用户的 CMIP 数据下载与 NetCDF 转 GeoTIFF 工具。安装版
不要求用户预装 Python，也不需要管理员权限。

项目主页：<https://github.com/NOOB304/cmip-climate-data-explorer>

Windows 正式版通过 [GitHub Releases](https://github.com/NOOB304/cmip-climate-data-explorer/releases)
发布。设置页的“检查更新”会读取最新 Release，下载 Windows 安装包并验证
`SHA256SUMS.txt`，校验通过后才启动安装程序。

## 功能

- 通过图标标签切换 ESGF、Copernicus CDS、AWS Open Data、Microsoft
  Planetary Computer、NASA POWER、NASA Earthdata 和 NOAA NCEI。
- 从 ORNL、DKRZ、IPSL、CEDA 等 ESGF 节点分页检索 CMIP6 文件。
- 新数据源变量目录支持中文名称/别名搜索；API 新增且尚未翻译的变量保留英文。
- Planetary Computer、AWS、POWER 与 NCEI 结果可进入现有下载任务；CDS 和
  Earthdata 受保护数据会明确显示账号授权要求并提供官方来源入口。
- 变量、模型、情景、数据表、频率和网格均使用联动下拉选择。
- 查询年份按时间范围是否相交匹配，例如查询 2020-2100 会显示覆盖 2000-2100 的文件。
- 支持断点续传、同任务自动重连、镜像切换、校验和验证和可见的任务进度。
- 月、年和固定场数据下载后可自动转换为 GeoTIFF；高频数据保留 NetCDF，避免生成海量文件。
- 本地月数据可选择月份，并按平均、总和、最大、最小或众数合成为年度 GeoTIFF。
- 本地数据列表支持右键在资源管理器中显示，操作日志会记录主要用户操作。
- 任务列表支持右键定位文件，失败或意外中断任务可在原任务上重新连接。
- 气候文件存储位置可在设置中修改；有 D 盘时首次运行优先使用
`D:\CMIP Climate Explorer`。

安装器默认建议 `D:\Programs\CMIP Climate Explorer`，并始终显示安装目录选择页。

## 使用

安装后从开始菜单打开 **CMIP Climate Explorer**。详细流程见
[中文用户指南](docs/USER_GUIDE.zh-CN.md)。
[数据源与访问方式](docs/DATA_SOURCES.zh-CN.md)列出了各 API 的产品和鉴权边界。

发布诊断：

```powershell
CMIPClimateExplorer.exe --self-test D:\CMIP-Self-Test
```

## 开发

需要 Python 3.12：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m ruff check src tests tools
.venv\Scripts\python -m pytest
.venv\Scripts\cmip-climate-explorer.exe
```

构建 Windows portable ZIP 与 Inno Setup 安装器：

```powershell
.\tools\build_windows.ps1
```

应用配置、任务数据库和日志位于 `%LOCALAPPDATA%\CMIPClimateExplorer`；体积较大的
NetCDF 与 GeoTIFF 存放在设置页指定的位置。
