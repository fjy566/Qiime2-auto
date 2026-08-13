# QIIME2 Auto 产品化重构实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在不安装 QIIME2 的前提下，把现有单文件脚本升级为可维护、可自检、保留命令行入口并带有本地 Web 控制台的 QIIME2 16S 分析工具。

**Architecture:** 保留 `qiime2_auto.py` 作为兼容入口，将输入扫描、manifest/metadata、配置校验、QIIME2 命令执行和流程编排拆到 `qiime2auto/` 包中。Web 控制台使用 Python 标准库 HTTP 服务与静态 HTML/CSS/JS，核心的扫描与模板功能不依赖 QIIME2；真正执行分析前做依赖检查并给出可读错误。

**Tech Stack:** Python 3.9+, argparse, pathlib, subprocess, dataclasses, stdlib `http.server`, HTML/CSS/vanilla JavaScript；可选 pandas/numpy/biom/matplotlib 与本机 QIIME2。

---

### Task 1: 建立核心模块与可测试输入层

**Files:**
- Create: `qiime2auto/__init__.py`
- Create: `qiime2auto/config.py`
- Create: `qiime2auto/io.py`
- Create: `tests/test_io.py`
- Modify: `qiime2_auto.py`

**Step 1: Write tests** for filename pairing, data type detection, manifest headers, metadata validation, and empty/invalid inputs.

**Step 2: Implement** dataclass-backed configuration, robust UTF-8 file handling, correct `forward-absolute-filepath` spelling, Casava paired detection, and metadata parsing that supports QIIME2 `#sample-id` headers.

**Step 3: Verify** with `python -m unittest discover -s tests -v` and `python -m py_compile qiime2_auto.py qiime2auto/*.py`.

### Task 2: 封装命令执行与流程编排

**Files:**
- Create: `qiime2auto/runner.py`
- Create: `qiime2auto/pipeline.py`
- Modify: `qiime2_auto.py`
- Create: `tests/test_runner.py`

**Step 1: Write tests** for command construction, missing executable reporting, dry-run behavior, and configuration validation.

**Step 2: Implement** a `CommandRunner` that logs commands, captures failures, checks `qiime`/`biom`/`figaro` only when needed, and a pipeline service that returns structured results instead of mutating CLI arguments.

**Step 3: Fix regressions** including undefined primer variables, wrong adapter option names, invalid barcode flag construction, incorrect sampling-depth output location, stale file lists, duplicate report entries, and `auto` depth conversion errors.

**Step 4: Verify** with unit tests and `python qiime2_auto.py --help` in the current environment without biom/QIIME2.

### Task 3: 新增本地 Web 控制台

**Files:**
- Create: `qiime2auto/web.py`
- Create: `web/index.html`
- Create: `web/styles.css`
- Create: `web/app.js`
- Create: `tests/test_web.py`

**Step 1: Implement** `serve` command and JSON endpoints for health, input scan, manifest generation, metadata generation, metadata validation, and CLI preview.

**Step 2: Build** a responsive dashboard with an editorial laboratory aesthetic: dark ink background, warm ivory panels, cyan/amber accents, readable typography, clear step status, busy states, toast errors, and mobile layout.

**Step 3: Verify** endpoint responses through the standard library test client and manually open the local page if a browser is available.

### Task 4: 同步文档

**Files:**
- Create: `README.md`
- Replace: `README.html`

**Step 1: Document** scope, no-QIIME2 local setup, quick CLI commands, Web console usage, supported input types, output structure, dependency checks, troubleshooting, and limitations.

**Step 2: Ensure** README HTML is valid UTF-8, has no broken tags/encoding, mirrors README.md, and includes copy buttons plus responsive navigation.

### Task 5: 最终验证

**Files:**
- Modify: `.gitignore` only if needed

**Step 1:** Run unit tests, compile checks, CLI help, scan/template smoke tests, and web server smoke tests.

**Step 2:** Run `git diff --check` only if Git metadata becomes available; otherwise report that this folder is not a repository and do not claim a commit.

**Step 3:** Confirm no QIIME2 installation was attempted and summarize remaining environment-dependent checks.
