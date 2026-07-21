# Factor Library

因子源码按状态分类保存，权威状态和实验关系写入 `registry.sqlite3`。

```text
candidates/   候选
validated/    已验证
production/   经人工批准使用
degraded/     衰减中
retired/      已停用
rejected/     验证失败
```

使用命令：

```bash
PYTHONPATH=src python3 -m quant_lab.cli init
PYTHONPATH=src python3 -m quant_lab.cli register-factor \
  --factor-id trend.ema_slope.20 \
  --name "EMA 20 slope" \
  --category trend \
  --status candidate
PYTHONPATH=src python3 -m quant_lab.cli list-factors
```
