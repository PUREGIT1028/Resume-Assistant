# 参与改进

本项目面向本机单进程使用。提交问题或修改建议时，请提供可复现的最小示例。

## 提交问题

请说明运行系统、Python 版本、复现步骤、期望行为与实际行为。模型问题可提供型号、错误类别和脱敏后的响应结构。不要提交 API Key、真实简历、完整数据库或带个人信息的日志截图。

## 开发与验证

1. 按 README 创建环境并安装 `requirements-local.txt`。
2. 对修改的行为补充必要测试，使用虚构简历和模拟模型。
3. 执行与变更相关的测试。真实模型测试需要有效额度，不能作为默认自动测试。
4. 说明功能变化、验证方式和已知限制；界面修改可附脱敏截图。

快速回归：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_glm53_config tests.test_metadata_nulls tests.test_relaxed_matching -v
```

完整测试入口为 `python -m pytest tests -q`；完整测试与快速回归的覆盖范围不同，请如实报告。

## 来源与授权

请保留原项目来源和本版本定制说明。当前材料未自行补设开源许可证；采用和再发布前应核实上游授权条件。
