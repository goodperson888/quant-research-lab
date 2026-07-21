# Strategies

- `pine/`: 原始和输出的 TradingView Pine Script。
- `freqtrade/`: Freqtrade 策略。
- `research/`: 与执行框架无关的研究原型。
- `inbox/`: 尚待形式化的自然语言、公开来源或其他策略收录材料。

每个策略应有唯一名称和版本，并引用对应的实验运行编号。生产策略不得在原文件上直接修改；创建新版本。

推荐状态为 `inbox`、`formalized`、`baseline`、`candidate`、`validated`、
`dry_run`、`production`、`degraded`、`retired`、`rejected`。状态可以记录在收录
YAML、实验 manifest 或未来的策略注册表中，不为了状态名称而复制目录。

自然语言策略从 `configs/strategy-intake.example.yaml` 开始。原样基准冻结后才允许
优化；每个版本只验证一个明确假设，复杂组合必须提供消融结果。
