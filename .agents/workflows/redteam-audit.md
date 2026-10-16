---
name: redteam-audit
description: 发布前与提交前的红队对抗自检工作流 (Red Team Pre-Commit & Release Audit Workflow)
---

# 红队自检标准工作流 (Red Team Self-Audit Workflow)

在任何向 `git` 提交变更、向 `GitHub` 推送代码、打包发行版 `TranslationDesktop.exe` 或产出 `TranslationDesktop-Release.zip` 之前，**必须严格按序执行以下五级红队自检防线**：

---

### 第一级：契约与配置中枢审计 (SSOT & Silent Degradation Audit)
1. **SSOT 唯一真相核验**：
   - 核查 `server/serve_config.py`，确认 ASR (`-c 2048 -np 4`) 与 MT (`-c 2048 -np 3`) 的显存预算未被各子模块硬编码颠覆；
   - 检查启动脚本（`scripts/start.ps1`, `scripts/start_llama_backends.ps1`, `scripts/daemon_llama.ps1`），确保无任何参数漂移与静默降级（严禁把 Qwen 降级为 whisper，严禁把独显降级为核显）。
2. **物理铁笼与连续密度曲线审计**：
   - 核验 VAD `_take()` 处是否严格执行 `limit(ratio)`；
   - 核验 UI `_start_dismiss_timer()` 是否根据字符数自适应伸缩 ($1.5\text{s} \sim 6.0\text{s}$)；
   - 检查是否残留任何脱离物理生理极限的"拍脑袋"硬阈值（如死硬的 8000ms/25000ms）。

---

### 第二级：全量自动化回归测试 (100% Green Regressions)
运行全局回归套件：
```bash
python -m pytest tests/ -q
```
* **硬性通过指标**：测试用例通过率必须为 **100%**（当前基线 359+ 用例全绿，0 失败，0 错误）；
* **重点回归覆盖**：
  - `tests/test_vad.py` & `tests/test_firered_vad.py`：连续静音调控单调性、短对话保留、前音节保护、FireRedVAD 纯 NumPy Fbank 特征提取、DFSMN 缓存管理与 UI 切换持久化；
  - `tests/test_nemotron_gpu.py`：Vulkan 独显优先级与状态重置；
  - `tests/test_ui_language.py`：语言切换信号贯通、菜单状态同步与持久化；
  - `tests/test_audio_pipeline.py` / `tests/test_caption_renderer.py`：渲染稳定性与防抖。

---

### 第三级：真实音频链路穿透自检 (Real Pipeline Interception)
运行红队快速拦截矩阵（Layer 1）：
```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_redteam_matrix.ps1 -Layer 1
```
* **L1.1 密集对白暴击 (ds2_dense_855)**：验证密集轰炸下无死锁、无 OOM、无吞字；
* **L1.2 长停顿与断点唤醒 (tbbt_3min)**：验证 30 秒静音后缓存正常释放、重新来声无爆破音与漏字；
* **L1.3 高动态电影对白与 BGM 干扰 (ds2_action_115 / bgm_movie_trailer)**：验证强背景音乐干扰下无持续性幻觉复读；
* **L1.4 双 VAD 引擎 A/B 矩阵评测**：`python scripts/compare_vad_engines.py`，验证 Silero（<0.2ms，低误报）与 FireRedVAD（<1.0ms，微弱气音高召回）运行正常。

---

### 第四级：独立构建与压缩包封包验证 (Packaging & ZIP Artifact Audit)
运行自动化打包与桌面发布同步：
1. **Windows 独立 EXE 与 Release ZIP**：
```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_desktop_exe.ps1
```
* **产物必须完整存在且非空**：
  - `dist/TranslationDesktop/TranslationDesktop.exe`（单体独立可执行文件）；
  - `dist/TranslationDesktop-Release.zip`（桌面绿色发布压缩包）；
  - 自动部署镜像目录 `D:\TranslationDesktop`（包含同步后的 exe、`desktop_config.json` 与 `TranslationDesktop-Release.zip`）。

2. **跨平台轻量源码与桌面分发 ZIP 同步**：
```bash
python scripts/build_distribution_zip.py
```
* 自动同步至当前用户桌面：`~/Desktop/translation-ubuntu-24.04.zip`（保证体积在 5MB 以内）。

---

### 第五级：清剿“只加不删”的垃圾补丁与冗余债务 (Dead Cruft & Debt Audit)
* **严禁“只加不删”的反模式 (Accumulator Anti-Pattern)**：
  1. **排查临时探针与孤儿脚本**：审查 `scripts/` 与根目录，清理已完成历史使命、一次性试错遗留的临时测试脚本；
  2. **排查幽灵与失效测试用例**：审查 `tests/`，若某个旧机制已被高层统一架构取代，坚决重构或彻底删除失效的死测试断言，严禁用 `skip` 或在死用例上无休止贴胶水层；
  3. **排查防御性垃圾补丁 (Garbage Shims)**：严禁在核心流水线（`server/`）中无休止堆砌针对偶发 BUG 的 ad-hoc `try-except pass` 补丁或废弃兼容 alias，每一个保留的代码必须有确凿调用方，无用者坚决拔除；
  4. **严查未清理的大型临时资产**：严禁提交测试过程中产生的中间转录缓存、未压缩大尺寸 WAV/MP4 片段，确保代码库精炼纯洁。

---

### 第六级：文档台账与版本控制发布 (VCS Sync & Ledger Trail)
1. 更新 `STATE.md` 与 `PLAN.md`，记录本次交付的客观证据与最新测试数据；
2. 严禁提交本地临时垃圾文件（如 `.log`、临时音频切片、未压缩测试中间件）；
3. 提交 git commit 并推送至远程分支。
