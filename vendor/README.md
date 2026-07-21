# Vendor

外部开源项目或源码归档统一放在这里，避免散落到系统其他目录。

第一阶段计划：

- Freqtrade 基础包：历史数据工具、回测和后续模拟盘；
- 其余 Python 依赖安装到项目 `.venv/`；
- uv 管理的 Python 安装到项目 `.tools/python/`；
- uv 缓存写入项目 `.cache/uv/`。

当前不复制 Freqtrade 源码，使用 PyPI 固定发布版和 `uv.lock`。直接依赖、工具、
来源、许可证、用途和已知校验和记录在 `vendor/manifest.json`。不要无版本地跟随
主分支；若未来归档源码，应作为新的 manifest 条目保存并校验。
