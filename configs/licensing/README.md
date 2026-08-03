# 商业授权配置

商业安装包在此目录放置 Ed25519 公钥：

```text
configs/licensing/public-key.pem
```

公钥可以随程序分发，不是秘密。签发许可证的私钥绝对不能放入本目录、项目仓库、安装包
或 Git；管理工具会拒绝在项目目录内生成或读取私钥。

源码开发环境默认使用 `development_disabled`，不会因为缺少许可证阻断研究。测试商业
门禁时显式设置：

```bash
export QUANT_LAB_LICENSE_ENFORCEMENT=required
export QUANT_LAB_LICENSE_PUBLIC_KEY_PATH=/absolute/path/to/public-key.pem
```

环境变量只是当前源码阶段的测试/装配入口，不是最终防破解边界。未来商业构建必须把
`commercial_required` 和公钥装配进签名后的安装包与后端二进制，不能让前端决定授权。
