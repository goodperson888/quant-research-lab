# Market Profiles

Market profile 是行情下载、数据语义、成本模型和验证状态的门禁。模板默认
`enabled: false`，只描述需要用户确认的字段，不会触发下载。

使用方式：

1. 选择与目标市场最接近的 `*.example.yaml`；
2. 复制为新的、非 example 文件并填写 venue、symbols、timeframes、成本和规则；
3. 人工确认数据可得性与风险后，将 `enabled` 改为 `true`；
4. 在策略收录、数据 manifest、因子验证和实验 manifest 中引用 profile ID；
5. 不同 profile 的 `validated`/`production` 状态独立保存。

个人第一阶段只启用一个主市场。模板存在不代表对应市场已经实现或验证。
