# QIIME2 Auto

一个面向新手的 16S rRNA 测序分析助手：把输入扫描、manifest/metadata 准备、参数预览和 QIIME2 分析流程串成一条可解释的工作流。

> 重要：本项目不会替你安装 QIIME2。当前目录中的扫描、模板生成、metadata 校验和 Web 控制台不需要 QIIME2；真正执行 DADA2、分类、多样性和 ANCOM 时，仍必须在你自己的 QIIME2 环境中运行。

## 它能做什么

- 自动识别 EMP、Casava、manifest 和混样 FASTQ 输入。
- 从 FASTQ 文件名生成 QIIME2 manifest，修复单端/双端常见命名问题。
- 根据 manifest 生成 metadata 模板，并校验 `sample-id`、`#SampleID`、重复 ID、空值和分组列。
- 保留原来的命令行接口，并增加 `scan` 和 `serve` 两个更容易上手的入口。
- 使用统一的命令执行器记录日志、报告缺失工具和分析失败原因。
- 在本地 Web 控制台中完成“扫描 → 生成模板 → 校验 → 命令预览”。数据不会上传。
- 在 QIIME2 可用时执行导入、引物去除、质量过滤、Figaro、DADA2、物种分类、多样性、系统发育和 ANCOM 流程。

## 最快上手：不用 QIIME2 也能先准备数据

在项目目录打开终端：

```bash
python qiime2_auto.py --help
python qiime2_auto.py scan /path/to/raw_data
python qiime2_auto.py serve
# 也可以使用模块入口
python -m qiime2auto serve
```

浏览器打开 <http://127.0.0.1:8765>。默认只监听本机，适合处理本地测序数据。

控制台里可以：

1. 输入 FASTQ 目录或 manifest 路径。
2. 查看数据类型、FASTQ 数量和双端判断。
3. 生成 `manifest.tsv`。
4. 生成 `metadata.tsv` 模板。
5. 校验 metadata 并复制完整 CLI 命令。

## 命令行用法

### 生成双端 manifest

```bash
python qiime2_auto.py \
  -i raw_data \
  -o prepared \
  --generate-manifest \
  --paired-end
```

单端数据使用 `--single-end`。输出会写到 `prepared/manifest.tsv`。

### 生成 metadata 模板

```bash
python qiime2_auto.py \
  -i prepared/manifest.tsv \
  -o prepared \
  --generate-metadata \
  --metadata-columns group timepoint
```

生成后请打开 `prepared/metadata.tsv`，把示例值改成真实分组。至少应有：

```text
sample-id    group
sample01     control
sample02     treatment
```

QIIME2 常见的 `#SampleID` 表头也可以识别。

### 执行完整分析

下面示例假设你已经进入包含 `qiime` 命令的 QIIME2 环境，并准备好了分类器：

```bash
python qiime2_auto.py \
  -i prepared/manifest.tsv \
  -o results \
  --metadata prepared/metadata.tsv \
  --classifier /path/to/silva-138-99-nb-classifier.qza \
  --primer-f GTGCCAGCMGCCGCGGTAA \
  --primer-r GGACTACHVGGGTWTCTAAT \
  --sampling-depth 10000
```

如果想先检查参数和命令，不调用外部工具：

```bash
python qiime2_auto.py ... --dry-run
```

`--sampling-depth auto` 会尝试导出 feature table 并用 biom 计算；如果当前环境没有 biom，请手动传入正整数。

## 支持的输入

| 类型 | 识别方式 | 备注 |
| --- | --- | --- |
| EMP single | 目录含 `barcodes.fastq.gz` 与 `sequences.fastq.gz` | 单端 EMP |
| EMP paired | 目录含 `barcodes.fastq.gz`、`forward.fastq.gz`、`reverse.fastq.gz` | 双端 EMP |
| Casava | 例如 `SampleA_S1_L001_R1_001.fastq.gz` | 双端需要同时有 R1/R2 |
| manifest single | TSV 表头含 `sample-id` 与 `absolute-filepath` | 路径建议使用绝对路径 |
| manifest paired | TSV 表头含 `sample-id`、`forward-absolute-filepath`、`reverse-absolute-filepath` | 路径建议使用绝对路径 |
| 混样 FASTQ | 文件名含 `_R1` 或普通 FASTQ | 必须额外提供 `--barcodes` |

文件名如果包含空格，程序会在命令执行时安全处理；但为了兼容第三方工具，仍建议使用字母、数字、下划线和短横线。

## 输出结构

分析结果默认写入 `qiime2_analysis/` 或你通过 `-o` 指定的目录：

```text
results/
├─ 00_raw_data/              导入后的 qza
├─ 01_demultiplexed/         拆分、去引物、过滤结果
├─ 02_quality_control/       qzv 质控报告
├─ 03_denoised/              DADA2 feature table 与代表序列
├─ 04_taxonomy/              分类与属水平表
├─ 05_diversity/              core metrics
├─ 06_phylogeny/              比对、树和 Newick
├─ 07_visualization/          条形图、热图和统计可视化
├─ 08_rarefaction_curves/     稀疏曲线
├─ 09_differential_abundance/ ANCOM 输出
├─ logs/                      每一步的命令与日志
└─ analysis_report.md         实际生成文件和参数报告
```

报告只列出实际存在的文件，不再列出一堆可能并未生成的“假清单”。`.qzv` 可以上传到 <https://view.qiime2.org> 查看。

## 参数分组

- 输入：`-i/--input`、`-o/--output`、`--barcodes`、`--metadata`
- 模板：`--generate-manifest`、`--paired-end`、`--single-end`、`--generate-metadata`、`--metadata-columns`
- 引物：`--primer-f`、`--primer-r`、`--primer-metadata`
- 质量与 DADA2：`--trim-left-f/r`、`--trunc-len-f/r`、`--max-ee`、`--trunc-q`、`--min-quality`、`--min-frequency`
- 采样深度：`--sampling-depth`、`--min-sample-retain`、`--min-depth-percent`、`--min-absolute-depth`
- 开关：`--no-trim`、`--no-filter`、`--no-figaro`、`--skip-taxonomy`、`--skip-diversity`、`--skip-ancom`
- 调试：`--dry-run`、`--version`

## 环境说明

### 前置准备阶段

Python 3.9+ 即可运行核心功能。当前环境是否安装 pandas、numpy、biom、matplotlib，不会影响 `--help`、`scan`、manifest 和 metadata 工作流。

### 真正分析阶段

你需要自行准备：

- 可用的 QIIME2 环境与 `qiime` 命令。
- 与当前 QIIME2 版本匹配的分类器 `.qza`。
- 分析所需的 QIIME2 plugins；Figaro 和 biom 是可选但会影响自动优化/导出。

程序发现缺少 `qiime` 时会直接说明，而不是假装分析成功。

## 常见问题

**为什么打开 `--help` 不需要 QIIME2？**

因为外部依赖已经延迟到真正执行分析时检查，这是为了让你在准备数据阶段不被环境卡住。

**为什么 metadata 校验不通过？**

优先检查：第一列是不是 `sample-id`/`#SampleID`，样本 ID 是否重复，是否有空值，以及是否至少存在一个分组列。

**为什么 `sampling-depth auto` 没有结果？**

自动模式需要 QIIME2 导出的 feature table 和 biom。没有 biom 时请改用 `--sampling-depth 10000` 这样的明确值。

**能否只用 Web 控制台运行全部分析？**

控制台提供执行接口和任务状态，但当前设计优先让你先看到完整命令；复制命令到 QIIME2 环境执行最透明，也方便复现。默认页面不会把本地路径发送到外部服务。

## 目录说明

- `qiime2_auto.py`：兼容 CLI 入口。
- `qiime2auto/io.py`：输入识别、manifest、metadata、报告。
- `qiime2auto/config.py`：配置模型与参数校验。
- `qiime2auto/runner.py`：外部命令、日志和缺失工具检查。
- `qiime2auto/pipeline.py`：QIIME2 分析流程编排。
- `qiime2auto/web.py`：本地 HTTP API 和任务服务。
- `web/`：本地控制台前端。
- `tests/`：无 QIIME2 环境也能运行的回归测试。

## 验证

```bash
python -m unittest discover -s tests -v
python -m py_compile qiime2_auto.py qiime2auto/config.py qiime2auto/io.py qiime2auto/runner.py qiime2auto/pipeline.py qiime2auto/web.py
python qiime2_auto.py --help
```

本项目目录当前不包含 Git 元数据；如需提交版本，请在你的仓库中初始化或放入对应 Git 工作区后再操作。
