# Pipeline Profiles

`smoke`、`fast_screen` 和 `full_validation` 定义阶段、门禁、输出、审批与停止规则。
阈值按 profile、市场族和策略目标配置，不是跨市场通用结论。当前只为
`crypto_perpetual` 提供第一阶段示例；新市场必须增加自己的 AcceptancePolicy。

固定顺序是：correctness → fast screen → viability → cheap sensitivity →
regime/Pine → full validation/locked/full stress → dry-run。失败策略可以进入廉价归因，
也可以把有样本外增量的局部规则保存为 diagnostic/component candidate，但不能把
“少亏”当作完整可交易策略候选。
