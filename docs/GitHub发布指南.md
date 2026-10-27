# GitHub 发布指南

## 仓库信息建议

- 仓库名：`shicai-resume-agent`
- 简介：基于大语言模型与向量检索的招聘辅助工作台，支持简历管理、宽松匹配、候选人分析与历史任务。
- Topics：`python`、`fastapi`、`langchain`、`llm`、`agent`、`resume-screening`、`chromadb`、`zhipu-ai`

名称和可见性由仓库所有者决定，以上仅为建议。

## 源码包与本地项目的区别

发布源码包采用文件白名单，只包含应用、前端、配置模板、测试与整理后的文档及虚构示例。不包含 `.env`、真实简历、数据库、向量索引、缓存、日志、虚拟环境、历史截图与笔记本输出。

请上传解压后的源码内容，不要把整个运行目录直接拖进 GitHub。上传 zip 文件本身不能替代仓库中的可浏览源码。

## 发布步骤

1. 先核实原项目的授权范围，保留原项目链接及 `README_UPSTREAM.md`；不要自行标注为 MIT 等未确认的许可证。
2. 确定目标仓库和公开或私有属性。
3. 解压干净源码包，检查文件清单及 `.env.example`。
4. 在 GitHub 创建空仓库，然后通过 Git 推送源码，或通过网页上传解压后的文件与目录。
5. 打开仓库首页检查 README、Logo、文档链接与 Mermaid 流程图。
6. 依据演示指南添加脱敏截图；按需创建首个 Release，并记录实际测试范围。

如果使用 Git，可在干净源码目录执行：

```powershell
git init
git add .
git diff --cached --stat
git commit -m "Prepare Shicai resume agent source and documentation"
git branch -M main
# 将下面地址替换为你自己创建的真实仓库地址
git remote add origin https://github.com/YOUR_ACCOUNT/shicai-resume-agent.git
git push -u origin main
```

不要覆盖已有仓库历史，也不要使用强制推送解决不明冲突。

## 发布前检查

- `.env` 和真实密钥未出现在提交中。
- 没有真实简历、业务数据库、向量库、日志或个人信息截图。
- README 说明原项目来源、本版本贡献和当前限制。
- 新环境安装依赖后按 `local_app:app` 启动。
- 实际测试结果与发布说明一致，不把历史测试结果写成当前全量通过。

## 当前限制

主要验证环境是 Windows、Python 3.12、本机单进程。原项目保留的 Dockerfile 不是本版本完整部署验证结果；PDF 中文字体仍使用 Windows 字体路径。跨平台、容器和公网场景需要额外适配与验证。
