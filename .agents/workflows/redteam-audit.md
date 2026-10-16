---
name: redteam-audit
description: 主 Agent SOP 结束后的收尾自查法官，执行六道物理绞杀线与一票否决裁决 (Post-SOP Final Audit)
---

# 红队收尾自查工作流 (Post-SOP Final Audit Protocol)

**触发时机**：主 Agent 执行完 SOP 流程、准备向用户宣称完成或提交发布时，**无条件强制触发**。  
**核心立场**：假定一切宣称皆为假象；只认物理事实，实行**一票否决制（VETO）**。

---

## 六道收尾物理绞杀线

1. **活体内核命令行查验**：
   - 查杀旧版 `TranslationDesktop.exe`、`llama.exe` 僵尸进程；
   - 运行 `python scripts/verify_live_llama_args.py`，核验 `Win32_Process` 真实物理命令行（必须包含 `--cache-ram 512 --ctx-checkpoints 1 --no-cache-idle-slots`）与 C++ 引擎启动日志。
2. **二进制编译与现场同步查验**：
   - 运行 `powershell -File scripts/build_desktop_exe.ps1`；
   - 确认 `D:\TranslationDesktop\TranslationDesktop.exe` 与 `Release.zip` 修改时间戳晚于代码修改。
3. **消费端真实上屏查验**：
   - 运行快速回放，核查日志中必须包含 `caption.paint > 0` 且有有效 `sig` 特征。
4. **SSOT 参数收口与垃圾清缴查验**：
   - 审查 Git Diff，确认零散弹式 hardcode，且清空调试 probe/scratch 临时文件。
5. **文档生态物理行数门禁**：
   - 检查 `AGENT.md < 120 行`、`STATE.md < 80 行`、`PLAN.md < 60 行`；运行 `python scripts/rag.py index`。
6. **全量破坏性自动化回归**：
   - 运行 `pytest tests/ -q`，确认 365+ 项用例 **100% 全绿（0 Fail, 0 Error）**。

---

## 终局裁决输出模版

自查完成后必须输出标准裁决，任一 FAIL 即熔断打回整改：

```text
======================= 🛡️ 红队收尾自查裁决书 =======================
[门禁 1] 活体进程内核命令行核验: [PASS / FAIL] (物理PID命令行完整对齐)
[门禁 2] 独立EXE编译与现场部署:  [PASS / FAIL] (时间戳对齐，D盘已同步)
[门禁 3] 消费端物理上屏与有效绘制: [PASS / FAIL] (caption.paint > 0)
[门禁 4] SSOT参数集中与反拉屎修改: [PASS / FAIL] (无散弹式硬编码，零残存探针)
[门禁 5] 文档生态物理行数与防漂移: [PASS / FAIL] (AGENT.md 47行 < 120行)
[门禁 6] 全量破坏性回归测试套件: [PASS / FAIL] (365/365 全绿 100%)
====================================================================
最终裁决: 【CLEARED 准予发布】 / 【VETO 驳回整改】
```
