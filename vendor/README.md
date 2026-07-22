# Vendor

外部开源项目或源码归档统一放在这里，避免散落到系统其他目录。

第一阶段开发环境：

- Native Engine 是默认核心研究引擎；
- Freqtrade 是开发/验证环境中客户可自行安装的可选 external engine；
- 其余 Python 依赖安装到项目 `.venv/`；
- uv 管理的 Python 安装到项目 `.tools/python/`；
- uv 缓存写入项目 `.cache/uv/`。

当前不复制 Freqtrade 源码。开发环境可通过可选依赖安装 PyPI 固定发布版并写入
`uv.lock`，但商业包不捆绑、复制或修改 Freqtrade，核心业务代码也不直接 import
Freqtrade。正式收费前必须由开源许可证律师复核最终制品和交付方式；本记录不作法律
结论。直接依赖、工具、
来源、许可证、用途和已知校验和记录在 `vendor/manifest.json`。不要无版本地跟随
主分支。不要为商业交付把 Freqtrade 源码归档进本目录。
