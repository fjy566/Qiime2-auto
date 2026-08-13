# 一键分析工作台实施计划

**目标：** 把 QIIME2 Auto 从“能识别输入”升级为“选择数据后即可一键运行”的 Linux/Conda 工作台。

**设计：** 浏览器 picker 选择的 manifest、多个 FASTQ、分类器和 metadata 会合并到一个本地上传会话；manifest 的 filepath 会根据当前服务器文件自动校正，大型 FASTQ 仍可直接填写 Linux 服务器路径。后端使用 `conda env list` 枚举环境，并用 `conda run -n <name> qiime --version` 做能力探测。Pipeline 通过 `conda run` 前缀执行 QIIME2，避免依赖当前 shell 是否已经 activate。

## 已实现的交互

- 多选 FASTQ picker，并自动扫描 R1/R2、`_1/_2` 等常见命名。
- manifest picker 不依赖扩展名；扫描 manifest 内容并统计实际引用的 FASTQ，继续保留 manifest 生成按钮。
- manifest 与 FASTQ 分开选择时共用 session，自动生成服务器可用的规范化 manifest。
- 分类器 `.qza` picker、metadata picker 和即时校验。
- 参数面板：引物、截断长度、采样深度自动/自定义、质量阈值、过滤开关，并解释关键参数。
- Conda 环境刷新、QIIME2 版本探测和环境选择。
- 无 QIIME2 时的版本/分发版一键安装助手和后台任务状态。
- 主流程不要求复制命令；CLI 与日志作为备用和排错入口。

## 验证边界

当前开发环境不安装 QIIME2；这里验证 picker API、manifest 合并识别、环境扫描、CLI 参数、前端状态和一键任务边界。目标 Linux 机需要先有 Conda，完整分析才会调用选定环境里的 QIIME2；Windows 页面只用于前置准备和界面开发。
