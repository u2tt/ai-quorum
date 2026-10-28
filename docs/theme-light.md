# 浅色主题变量表（AI 辩论台）

> 生成方式：`python3 /tmp/audit_light.py quorum/web/static/index.html /tmp/light_overrides.css`
> 审计脚本为 `/tmp/audit_contrast.py` 的改造版（支持把一组 `:root` 覆盖 CSS 注入到 `<style>` 末尾后做整页级联求值）。
> 本文件只描述 token，不修改 `index.html`。

## 1. 设计基调

**柔和纸感 + 高对比工作台**：底色用 #F6F7F9 暖灰纸面、面板纯白，文字用墨蓝黑（#1A2230 系），整体接近苹果风浅灰文档观感。取舍点：为满足非文本 3:1（WCAG 1.4.11），边框三兄弟（--line / --line-strong / --line-input）的 alpha 比典型浅色主题深（合成后 #7E-88 灰），视觉上更"硬"；阵营色全部调深到白底 4.5:1 以上（蓝/玫红/金棕），饱和度换可读性；阴影改为低透明大模糊，去掉深色版的发光感。

## 2. 完整变量表（深色 → 浅色，一一对应）

| 变量 | 深色值 | 浅色值 | 用途/说明 |
|---|---|---|---|
| `--bg` | `#0B0E14` | `#F6F7F9` | 页面底 |
| `--bg-glow-a` | `rgba(91,157,255,.10)` | `rgba(47,111,224,.07)` | 左上氛围光（蓝） |
| `--bg-glow-b` | `rgba(255,107,157,.07)` | `rgba(178,50,50,.04)` | 右下氛围光（随 danger 色相） |
| `--surface-1` | `#11151D` | `#FFFFFF` | 面板/卡 |
| `--surface-2` | `#161B24` | `#F2F4F8` | 次级面/气泡 |
| `--surface-3` | `#1C2230` | `#EBEEF4` | 三级面/按钮底 |
| `--line` | `rgba(255,255,255,.07)` | `rgba(16,24,40,.50)` | 发丝线（合成 ≥3:1） |
| `--line-strong` | `rgba(255,255,255,.16)` | `rgba(16,24,40,.60)` | 强边框 |
| `--line-input` | `rgba(255,255,255,.34)` | `rgba(16,24,40,.66)` | 表单控件边界 |
| `--text` | `#EDF1F7` | `#1A2230` | 主文字 |
| `--text-2` | `#A7B2C4` | `#454F5F` | 次文字 |
| `--text-3` | `#86909F` | `#576274` | 弱文字/meta |
| `--pro` | `#5B9DFF` | `#255DC1` | 正方（白底 6.15:1） |
| `--con` | `#FF6B9D` | `#AD305E` | 反方（白底 6.25:1） |
| `--judge` | `#E2B457` | `#855E0F` | 裁判（白底 5.82:1） |
| `--ok` | `#4CC38A` | `#1D7951` | 成功 |
| `--danger` | `#FF6B6B` | `#B23232` | 危险 |
| `--pro-12` | `rgba(91,157,255,.12)` | `rgba(37,93,193,.12)` | 正方 12% tint |
| `--pro-18` | `rgba(91,157,255,.18)` | `rgba(37,93,193,.18)` | 正方 tint（边） |
| `--pro-30` | `rgba(91,157,255,.30)` | `rgba(37,93,193,.30)` | 正方 tint（chip 边） |
| `--con-12` | `rgba(255,107,157,.12)` | `rgba(173,48,94,.12)` | 反方 tint |
| `--con-18` | `rgba(255,107,157,.18)` | `rgba(173,48,94,.18)` | 反方 tint（边） |
| `--con-30` | `rgba(255,107,157,.30)` | `rgba(173,48,94,.30)` | 反方 tint（chip 边） |
| `--judge-12` | `rgba(226,180,87,.12)` | `rgba(133,94,15,.12)` | 裁判 tint |
| `--shadow-card` | `…rgba(0,0,0,.8)` | `0 1px 2px rgba(16,24,40,.05), 0 16px 36px -18px rgba(16,24,40,.16)` | 卡片阴影 |
| `--shadow-pop` | `…rgba(0,0,0,.7)` | `0 24px 56px -16px rgba(16,24,40,.22)` | 弹层阴影 |
| `--inset` | `…rgba(255,255,255,.04)…` | `0 1px 0 rgba(255,255,255,.65) inset` | 内高光 |
| `--line-2` | `var(--line-strong)` | `var(--line-strong)` | 旧别名 |
| `--muted` | `var(--text-2)` | `var(--text-2)` | 旧别名 |
| `--dim` | `var(--text-3)` | `var(--text-3)` | 旧别名 |
| `--pro-soft` / `--con-soft` | `var(--pro-12)` 等 | 同左（继承新值） | 旧别名 |
| `--panel` / `--panel-2` | `var(--surface-1/2)` | `var(--surface-1/2)` | 旧别名 |
| `--border` | `var(--line)` | `var(--line)` | 旧别名 |
| `--float` | `var(--shadow-card)` | `var(--shadow-card)` | 旧别名 |
| `--radius-card/row/ctl`、`--r-*`、`--ease`、`--fast`、`--q-mono/sans` | 同深色 | **不变**（粘贴块中原样保留） | 非颜色 token |

## 3. 对比度验证记录（脚本实测，保留两位小数）

整页级联求值结果：**文本 160 项全部达标（0 不达标）、非文本 0 项不达标**。以下为 44 组指定组合：

| 组合 | 前景 | 背景 | 比值 | 要求 | 结论 |
|---|---|---|---|---|---|
| --text × --bg | #1a2230 | #f6f7f9 | 14.89 | 4.5 | ✓ |
| --text × --surface-1 | #1a2230 | #ffffff | 15.96 | 4.5 | ✓ |
| --text × --surface-2 | #1a2230 | #f2f4f8 | 14.50 | 4.5 | ✓ |
| --text × --surface-3 | #1a2230 | #ebeef4 | 13.74 | 4.5 | ✓ |
| --text-2 × --bg | #454f5f | #f6f7f9 | 7.72 | 4.5 | ✓ |
| --text-2 × --surface-1 | #454f5f | #ffffff | 8.28 | 4.5 | ✓ |
| --text-2 × --surface-2 | #454f5f | #f2f4f8 | 7.52 | 4.5 | ✓ |
| --text-2 × --surface-3 | #454f5f | #ebeef4 | 7.12 | 4.5 | ✓ |
| --text-3 × --bg | #576274 | #f6f7f9 | 5.75 | 4.5 | ✓ |
| --text-3 × --surface-1 | #576274 | #ffffff | 6.17 | 4.5 | ✓ |
| --text-3 × --surface-2 | #576274 | #f2f4f8 | 5.60 | 4.5 | ✓ |
| --text-3 × --surface-3 | #576274 | #ebeef4 | 5.31 | 4.5 | ✓ |
| --pro × --surface-1（白底） | #255dc1 | #ffffff | 6.15 | 4.5 | ✓ |
| --pro × --pro-12⊃--surface-1 | #255dc1 | #e5ecf8 | 5.16 | 4.5 | ✓ |
| --pro × --pro-12⊃--surface-2 | #255dc1 | #d9e2f1 | 4.72 | 4.5 | ✓ |
| --con × --surface-1（白底） | #ad305e | #ffffff | 6.25 | 4.5 | ✓ |
| --con × --con-12⊃--surface-1 | #ad305e | #f5e6ec | 5.19 | 4.5 | ✓ |
| --con × --con-12⊃--surface-2 | #ad305e | #eadce6 | 4.73 | 4.5 | ✓ |
| --judge × --surface-1（白底） | #855e0f | #ffffff | 5.82 | 4.5 | ✓ |
| --judge × --judge-12⊃--surface-1 | #855e0f | #f0ece2 | 4.93 | 4.5 | ✓ |
| --judge × --judge-12⊃--surface-2 | #855e0f | #e5e2dc | 4.50 | 4.5 | ✓ |
| --ok × --surface-1 | #1d7951 | #ffffff | 5.38 | 4.5 | ✓ |
| --danger × --surface-1 | #b23232 | #ffffff | 6.19 | 4.5 | ✓ |
| --text × --pro-12 底 | #1a2230 | #e5ecf8 | 13.40 | 4.5 | ✓ |
| --text-2 × --pro-12 底 | #454f5f | #e5ecf8 | 6.95 | 4.5 | ✓ |
| --text × --con-12 底 | #1a2230 | #f5e6ec | 13.25 | 4.5 | ✓ |
| 白字 × CTA 渐变起点 #2F6FE0 | #ffffff | #2f6fe0 | 4.70 | 4.5 | ✓ |
| 白字 × --pro | #ffffff | #255dc1 | 6.15 | 4.5 | ✓ |
| 白字 × --con | #ffffff | #ad305e | 6.25 | 4.5 | ✓ |
| 白字 × --ok | #ffffff | #1d7951 | 5.38 | 4.5 | ✓ |
| 白字 × --danger | #ffffff | #b23232 | 6.19 | 4.5 | ✓ |
| 白字 × CTA 渐变终点 #2456B8 | #ffffff | #2456b8 | 6.78 | 4.5 | ✓ |
| disabled 字 #5D6879 × disabled 底 #E8EBF0 | #5d6879 | #e8ebf0 | 4.72 | 4.5 | ✓ |
| disabled 图标 #76808F × 白底 | #76808f | #ffffff | 4.00 | 3.0 | ✓ |
| placeholder #576274 × 各输入框白底 | #576274 | #ffffff | 6.17 | 4.5 | ✓ |
| --line ⊃ --bg | #838890 | #f6f7f9 | 3.34 | 3.0 | ✓ |
| --line ⊃ --surface-1 | #888c94 | #ffffff | 3.40 | 3.0 | ✓ |
| --line ⊃ --surface-2 | #818690 | #f2f4f8 | 3.32 | 3.0 | ✓ |
| --line ⊃ --surface-3 | #7e838e | #ebeef4 | 3.28 | 3.0 | ✓ |
| --line-strong ⊃ --surface-1 | #70747e | #ffffff | 4.66 | 3.0 | ✓ |
| --line-strong ⊃ --surface-2 | #6a707b | #f2f4f8 | 4.52 | 3.0 | ✓ |
| --line-strong ⊃ --surface-3 | #686e7a | #ebeef4 | 4.43 | 3.0 | ✓ |
| --line-input ⊃ 输入框白底 | #616771 | #ffffff | 5.72 | 3.0 | ✓ |
| --line-input ⊃ --surface-2 | #5d636f | #f2f4f8 | 5.50 | 3.0 | ✓ |

disabled 状态显式配对：`#btnStart:disabled` 前景 #5d6879 / 底 #e8ebf0 = **4.72:1** ✓（`.icon-btn:disabled` 4.00:1，WCAG 1.4.3 豁免项，且高于图标 3:1 线）。

调色过程（不达标→达标的关键调整）：--pro #2F6FE0→#2763CE→#255DC1（12% tint 上 4.01→4.72）；--con #D6457E→#B93364→#AD305E；--judge #A97B18→#8A6210→#855E0F；--ok #1E7F55→#1D7951；--danger #C93A3A→#B23232（失败气泡 tint 上 4.44→4.76）；--text-3 #5F6B7E→#576274（反方气泡 meta 4.12→4.67）；--line .46→.50（surface-3 上 2.9→3.28）。

## 4. 粘贴块

组件级覆盖清单（浅色下必须翻转/调色的写死值）：`.q-top`/`.q-foot` 深色玻璃→白玻璃；表单控件 `#0E131A` 底→白（含新增的 `#pickSearch`）；disabled 配对 `#1B2431/#8B95A6/#58616F`→`#E8EBF0/#5D6879/#76808F`；`#topicInput` 白色底线与 placeholder；`.md strong`(#fff)、`.vc-label` 系(#66acfe/#fd75ac/#eac46e/rgba(232,184,75,.8))、`.q-judge em` opacity .85；错误系浅红字 `.fail-tag/#ff9d9d`、`.stage-error/#ffc9c9`、`.form-error/#ffb4b4`、`.msg.failed .body/#ffc9c9` 及其 tint；`.btn.primary`/`.q-start` 渐变与深字→深蓝渐变白字；hover 白叠加→黑低透明（q-btn/icon-btn/stepper/seg/add-member/q-step/roundInput/configPane/judgeSlot）；滚动条、开关选中态、`::selection`、stopped 状态条。

```css
/* ================================================================
   浅色主题（docs/theme-light.md）—— 整块追加到 index.html <style> 末尾
   验证：python3 /tmp/audit_light.py index.html <本块>
   ================================================================ */

:root[data-theme="light"] {
  --bg: #F6F7F9;
  --bg-glow-a: rgba(47, 111, 224, .07);
  --bg-glow-b: rgba(178, 50, 50, .04);
  --surface-1: #FFFFFF;
  --surface-2: #F2F4F8;
  --surface-3: #EBEEF4;
  --line: rgba(16, 24, 40, .50);
  --line-strong: rgba(16, 24, 40, .60);
  --line-input: rgba(16, 24, 40, .66);
  --text: #1A2230;
  --text-2: #454F5F;
  --text-3: #576274;
  --pro: #255DC1;
  --con: #AD305E;
  --judge: #855E0F;
  --ok: #1D7951;
  --danger: #B23232;
  --radius-card: 18px;
  --radius-row: 12px;
  --radius-ctl: 10px;
  --shadow-card: 0 1px 2px rgba(16, 24, 40, .05), 0 16px 36px -18px rgba(16, 24, 40, .16);
  --shadow-pop: 0 24px 56px -16px rgba(16, 24, 40, .22);
  --ease: cubic-bezier(.22, .8, .28, 1);
  --fast: 150ms cubic-bezier(.22, .8, .28, 1);

  /* 旧变量映射（与深色版同名同结构） */
  --line-2: var(--line-strong);
  --muted: var(--text-2);
  --dim: var(--text-3);
  --pro-12: rgba(37, 93, 193, .12);
  --pro-18: rgba(37, 93, 193, .18);
  --pro-30: rgba(37, 93, 193, .30);
  --con-12: rgba(173, 48, 94, .12);
  --con-18: rgba(173, 48, 94, .18);
  --con-30: rgba(173, 48, 94, .30);
  --judge-12: rgba(133, 94, 15, .12);
  --inset: 0 1px 0 rgba(255, 255, 255, .65) inset;
  --float: var(--shadow-card);
  --r-panel: var(--radius-card);
  --r-row: var(--radius-row);
  --r-btn: var(--radius-ctl);
  --r-bubble: 16px;
  --panel: var(--surface-1);
  --panel-2: var(--surface-2);
  --border: var(--line);
  --pro-soft: var(--pro-12);
  --con-soft: var(--con-12);
  --radius-bubble: var(--r-bubble);
  --radius-btn: var(--r-btn);

  --q-mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, monospace;
  --q-sans: -apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI", system-ui, sans-serif;
}

/* ---- 毛玻璃底：深色玻璃 → 白玻璃 ---- */
:root[data-theme="light"] .q-top { background: rgba(255, 255, 255, .78); }
:root[data-theme="light"] .q-foot { background: rgba(255, 255, 255, .88); }

/* ---- 表单控件底：#0E131A → 白 ---- */
:root[data-theme="light"] .field input,
:root[data-theme="light"] .field select,
:root[data-theme="light"] .field textarea,
:root[data-theme="light"] #slotModel,
:root[data-theme="light"] #slotCustomProvider,
:root[data-theme="light"] #slotCustomProviderName,
:root[data-theme="light"] #slotCustomModel,
:root[data-theme="light"] #slotCustomBase,
:root[data-theme="light"] #slotCustomKey,
:root[data-theme="light"] #slotKey,
:root[data-theme="light"] #slotPrompt,
:root[data-theme="light"] #roundInput,
:root[data-theme="light"] #mProvider,
:root[data-theme="light"] #mModel,
:root[data-theme="light"] #mBase,
:root[data-theme="light"] #mKey,
:root[data-theme="light"] #mLabel,
:root[data-theme="light"] #pickSearch {
  background: #FFFFFF;
}

:root[data-theme="light"] .field input:focus-visible,
:root[data-theme="light"] .field select:focus-visible,
:root[data-theme="light"] .field textarea:focus-visible {
  border-color: var(--pro) !important;
  box-shadow: 0 0 0 3px rgba(37, 93, 193, .18);
}

/* ---- disabled 显式配对（WCAG 1.4.3 豁免，但仍给 4.72:1） ---- */
:root[data-theme="light"] #btnStart:disabled,
:root[data-theme="light"] .q-btn:disabled,
:root[data-theme="light"] .btn.primary:disabled {
  background: #E8EBF0 !important;
  color: #5D6879 !important;
  border: 1px solid var(--line-strong) !important;
  box-shadow: none !important;
}
:root[data-theme="light"] .icon-btn:disabled { color: #76808F !important; }

/* ---- 辩题输入 ---- */
:root[data-theme="light"] #topicInput { border-bottom: 2px solid rgba(16, 24, 40, .55) !important; }
:root[data-theme="light"] #topicInput::placeholder { color: #576274 !important; }

/* ---- 裁决卡：标签色随 token，strong 不再用白 ---- */
:root[data-theme="light"] .md strong { color: var(--text); }
:root[data-theme="light"] .vc-cell.pro .vc-label { color: var(--pro); }
:root[data-theme="light"] .vc-cell.con .vc-label { color: var(--con); }
:root[data-theme="light"] .vc-block .vc-label { color: var(--judge); }
:root[data-theme="light"] .vc-cell.pro { box-shadow: 0 0 0 1px rgba(37, 93, 193, .16) inset; }
:root[data-theme="light"] .vc-cell.con { box-shadow: 0 0 0 1px rgba(173, 48, 94, .16) inset; }
:root[data-theme="light"] .md .vc-empty { color: #576274 !important; }

/* ---- 失败 / 错误态：浅红字 → token danger ---- */
:root[data-theme="light"] .msg.failed .bubble { border-color: rgba(178, 50, 50, .3); background: rgba(178, 50, 50, .05); }
:root[data-theme="light"] .msg.failed .body { color: var(--danger); }
:root[data-theme="light"] .fail-tag { background: rgba(255, 255, 255, .6); color: var(--danger); }
:root[data-theme="light"] .err-reason { border-top-color: rgba(178, 50, 50, .3); color: var(--danger); }
:root[data-theme="light"] .stage-error { border-color: rgba(178, 50, 50, .4); background: rgba(178, 50, 50, .06); color: var(--danger); }
:root[data-theme="light"] .form-error { color: var(--danger); background: rgba(178, 50, 50, .06); border-color: rgba(178, 50, 50, .4); }
:root[data-theme="light"] .btn.danger { background: rgba(178, 50, 50, .08); border-color: rgba(178, 50, 50, .75); }
:root[data-theme="light"] .btn.danger:hover:not(:disabled) { background: rgba(178, 50, 50, .14); border-color: var(--danger); }
:root[data-theme="light"] #btnStop { border-color: rgba(178, 50, 50, .75); background: rgba(178, 50, 50, .06); }
:root[data-theme="light"] #btnStop:hover:not(:disabled) { background: rgba(178, 50, 50, .12); border-color: var(--danger); }
:root[data-theme="light"] .q-btn-danger { border-color: rgba(178, 50, 50, .75); background: rgba(178, 50, 50, .06); }
:root[data-theme="light"] .q-btn-danger:hover:not(:disabled) { background: rgba(178, 50, 50, .12); border-color: var(--danger); }
:root[data-theme="light"] .chip.st-error { border-color: rgba(178, 50, 50, .4); background: rgba(178, 50, 50, .08); }
:root[data-theme="light"] .icon-btn.dangerish:hover:not(:disabled) { background: rgba(178, 50, 50, .1); }
:root[data-theme="light"] #configPane .q-side .slot .icon-btn.dangerish:hover:not(:disabled) { background: rgba(178, 50, 50, .1); }
:root[data-theme="light"] .toast.error { border-color: rgba(178, 50, 50, .4); background: rgba(178, 50, 50, .07); }

/* ---- 旧层通用按钮 hover：白描边 → 黑低透明 ---- */
:root[data-theme="light"] .btn:hover:not(:disabled) { border-color: rgba(16, 24, 40, .3); }

/* ---- judge 侧徽标：去掉 opacity 压暗 ---- */
:root[data-theme="light"] .q-judge .q-side-name em { opacity: 1; }

/* ---- 滚动条 / 选区 / 状态条 ---- */
:root[data-theme="light"] ::-webkit-scrollbar-thumb { background: rgba(16, 24, 40, .22); background-clip: padding-box; }
:root[data-theme="light"] ::-webkit-scrollbar-thumb:hover { background: rgba(16, 24, 40, .32); background-clip: padding-box; }
:root[data-theme="light"] ::selection { background: rgba(37, 93, 193, .22); }
:root[data-theme="light"] .hist-item[data-status="stopped"]::before { background: rgba(16, 24, 40, .25); }

/* ---- 开关选中态 ---- */
:root[data-theme="light"] .q-switch input:checked + span {
  background: rgba(133, 94, 15, .18);
  border-color: rgba(133, 94, 15, .45);
}

/* ---- hover 白叠加 → 黑低透明 ---- */
:root[data-theme="light"] .q-btn:hover:not(:disabled) { border-color: rgba(16, 24, 40, .28); }
:root[data-theme="light"] .icon-btn:hover:not(:disabled) { background: rgba(16, 24, 40, .06); }
:root[data-theme="light"] .stepper button:hover:not(:disabled) { background: rgba(16, 24, 40, .05); }
:root[data-theme="light"] .seg button:hover { background: rgba(16, 24, 40, .04); }
:root[data-theme="light"] .add-member:hover { border-color: rgba(16, 24, 40, .28); background: rgba(16, 24, 40, .025); }
:root[data-theme="light"] .q-step button:hover:not(:disabled) { background: rgba(16, 24, 40, .07); }
:root[data-theme="light"] #roundInput:focus { background: rgba(16, 24, 40, .03); }
:root[data-theme="light"] #configPane .q-side .slot .icon-btn:hover:not(:disabled) { background: rgba(16, 24, 40, .08); }
:root[data-theme="light"] #judgeSlot .icon-btn:hover { background: rgba(16, 24, 40, .08); }

/* ---- 气泡层：阵营 tint 随 token ---- */
:root[data-theme="light"] .q-stream .bubble { box-shadow: 0 1px 0 rgba(255, 255, 255, .6) inset; }
:root[data-theme="light"] .q-stream .msg.pro .bubble {
  border-color: rgba(37, 93, 193, .28);
  background: linear-gradient(135deg, rgba(37, 93, 193, .08), rgba(37, 93, 193, .03));
}
:root[data-theme="light"] .q-stream .msg.con .bubble {
  border-color: rgba(173, 48, 94, .28);
  background: linear-gradient(135deg, rgba(173, 48, 94, .08), rgba(173, 48, 94, .03));
}
:root[data-theme="light"] .q-stream .msg.pro .avatar { background: rgba(37, 93, 193, .08); }
:root[data-theme="light"] .q-stream .msg.con .avatar { background: rgba(173, 48, 94, .08); }
:root[data-theme="light"] .q-stream .body { color: var(--text); }

/* ---- 模态主按钮（旧层 .btn.primary）与主 CTA：深蓝渐变 + 白字 ---- */
:root[data-theme="light"] .btn.primary {
  background: linear-gradient(135deg, #2F6FE0, #2456B8);
  border-color: #2456B8;
  color: #FFFFFF;
  box-shadow: 0 1px 0 rgba(255, 255, 255, .2) inset, 0 6px 18px rgba(47, 111, 224, .25);
}
:root[data-theme="light"] .btn.primary:hover:not(:disabled) { border-color: #2F6FE0; }
:root[data-theme="light"] .q-start {
  background: linear-gradient(135deg, #2F6FE0, #2456B8);
  color: #FFFFFF;
  box-shadow: 0 1px 0 rgba(255, 255, 255, .2) inset, 0 10px 24px -12px rgba(47, 111, 224, .55);
}
:root[data-theme="light"] .q-start:hover:not(:disabled) {
  box-shadow: 0 1px 0 rgba(255, 255, 255, .25) inset, 0 14px 28px -12px rgba(47, 111, 224, .6);
}
:root[data-theme="light"] .q-start.danger {
  background: linear-gradient(135deg, #C93A3A, #A32E2E);
  color: #FFFFFF;
  box-shadow: 0 1px 0 rgba(255, 255, 255, .18) inset, 0 10px 24px -12px rgba(178, 50, 50, .5);
}
```

## 5. 风险与边界

1. **空状态插画（JS 内联 SVG）**：`empty-art` 的弧线描边写死 `rgba(255,255,255,.08/.16)`（JS 字符串，约在 index.html 3500 行附近），浅色下几乎不可见。CSS 属性可覆盖内联 attribute：加 `.empty-art svg [stroke] { stroke: rgba(16,24,40,.18); }` 之类，或改 JS。本次未覆盖（探针不测装饰图形）。
2. **装饰性光晕**：`.judge-head .side-dot`、`.phase-dot`、`.vdot` 等的 `box-shadow` 光晕仍引用旧色值 rgba(226,180,87,.4) 等。属纯装饰（元素本身另有实色），不影响对比度，但色相与新 --judge 略偏；建议后续 token 化为 `--judge-12`。
3. **残留旧 tint 字面量**：`rgba(77,159,255,…)`/`rgba(255,95,158,…)`（旧 pro/con）出现在少量装饰 inset/阴影里（如 `.slot.flash`、`.switch`）。浅色下色相略偏但可读性不受影响（文字/边框均已走 var）。
4. **边框偏重**：为满足非文本 3:1，--line 合成后约 #838890，比常见浅色主题的分隔线明显。若视觉验收觉得过硬，可把纯装饰性分隔降回低 alpha，但 **--line-input 必须保持 ≥3:1**（表单控件边界，WCAG 1.4.11）。
5. **毛玻璃**：`.q-top`/`.q-foot` 已换白玻璃，`backdrop-filter` 保留；若低端设备发灰可退化为不透明 #FFFFFF/#F6F7F9。
6. **深色遮罩保留**：模态遮罩 rgba(6,7,10,.9)、抽屉遮罩 rgba(3,4,8,.58/.6) 故意保留深色（浅色主题下标准做法，压暗背景突出弹层）。
7. **index.html 是活动文件**：fe-settings 正在同步修改。若表单控件选择器清单再扩（如新增 input id），需同步加进粘贴块的表单白底覆盖列表；新组件若写死深色字面量会直接不达标，应走 var。
