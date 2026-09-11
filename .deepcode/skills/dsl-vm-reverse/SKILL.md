---
name: dsl-vm-reverse
description: >
  逆向基于 JavaScript 实现的自定义虚拟机/DSL 引擎（如 AWS WAF fireye、风控 JS、自定义 opcode 解释器）。
  当目标 JS 文件是 IIFE + 单字母变量 + DG() 解释器主循环 + 常量表、或疑似"JS 实现的 WASM 虚拟机"时使用。
  覆盖识别、opcode 提取、常量表分析、导出函数定位、运行时注入捕获的完整方法论。
  源自 zhaoxuya520/reverse-skill 的 dsl-vm-reverse skill，适配 DeepCode 工具链。
version: 1.0.0
author: DeepCode (adapted from zhaoxuya520/reverse-skill)
source: https://github.com/zhaoxuya520/reverse-skill
date: 2026-08-01
tags: [reverse, vm, js, opcode, wasm, deobfuscation, security]
---

# DSL 自定义虚拟机逆向（DSL VM Reverse Engineering）

> 用于逆向基于 JavaScript 实现的自定义 WASM 虚拟机 / 风控引擎 / 自定义 opcode 解释器。

---

## 1. 适用范围（识别特征）

目标文件符合以下 **任意特征** 时使用本 skill：

| # | 特征 | 说明 |
|---|------|------|
| 1 | IIFE 开头 + 大量单字母变量名 | `!function(){var U=void 0,y=parseInt,E0=Function,...}` |
| 2 | 包含 `DG()` 或类似函数含 switch-case 循环 | 解释器主循环，`d[7]&31` 解码 opcode |
| 3 | 大文件（500KB+）但零字节占比 < 1% | 非标准 WASM，纯 JS 实现 |
| 4 | 包含 `C[number]` 常量表引用 | `C[9][xxx]` 函数表/字符串表 |
| 5 | 单行压缩代码 | 583KB 单行，混淆变量名 |

### 排除规则（避免误用）

| 条件 | 非本 skill | 转至 |
|------|-----------|------|
| 文件以 `\x00asm` 开头 | 标准 WASM 二进制 | 提取后转 IDA/Ghidra 分析 |
| 含 WASM 魔术字 `Uint8Array([0,97,115,109])` | WASM 嵌入式 | 提取 .wasm 后转原生工具 |
| 标准 Webpack 打包（`function(e,t,n){...}`） | 普通 JS | js-reverse |
| 零字节占比 > 20% | WASM 二进制 | 原生反编译 |

---

## 2. 代码特征速记

```javascript
// 特征 1: IIFE 入口，单字母变量映射数字常量
!function(){ var U=void 0, y=parseInt, E0=Function, AN=Uint8Array;
    var E=15, l=10, m=12, x=16, S=13, $=11; /* 数字常量→变量名 */ }

// 特征 2: 解释器主循环 DG()
function DG(C, d, ...) {
    for (d[7] = x; d[7] !== U;) {
        var aE = d[7] & 31;        // 低 5 位 = opcode
        var O  = d[7] >> 5 & 31;   // 高 5 位 = sub-operation
        switch (aE) { case 0: d[7]=612; break; /* ...N 个 case */ }
    }
}

// 特征 3: 常量表 C[9] — C[9][0]=["pc"] 函数参数描述 / C[9][667]="string" / C[9][x]=number 函数索引
// 特征 4: W(C[index], null, ...) — W=Function.prototype.call.bind(call) 统一内置函数调用
// 特征 5: 指令编码 d[7] = opcode(bit0-4) | subop(bit5-9) | operand(bit10+)
```

**Opcode 编码格式**（32 位整数）：

```
bit 0-4:   opcode (0-N)
bit 5-9:   sub-operation (0-31)
bit 10-31: operand/立即数

解码:  aE = d[7] & 31;  O = d[7] >> 5 & 31;  operand = d[7] >> 10
```

---

## 3. 逆向工作流（6 阶段）

### Phase 1: 文件分类（5 分钟）
- 检查文件头是否 `\x00asm`（标准 WASM）
- 统计零字节占比：>20% → WASM；<1% + IIFE → 疑似 DSL VM
- 匹配 `var U=void 0` / `U=void 0,y=parseInt` → DSL VM

### Phase 2: 变量映射表提取（10 分钟）
- 用正则 `var\s+(\w+)\s*=\s*(\d+)` 提取文件前 2000 字符的常量映射
- 产出：`name → value(dec/hex)` 映射表，后续解读指令立即数

### Phase 3: Opcode 提取与分类（15 分钟）
- 提取全部 `case <n>:`，去重得到唯一 opcode 集
- 按 case 内代码片段分类：`d[7]=`→BRANCH / `return`→RETURN / `W(C[`→CALL / `new`→ALLOC / `try|catch`→EXCEPTION / 其他→ARITH/STORE

### Phase 4: 常量表分析（30 分钟）
- 提取 `C[9][<n>]` 全部引用索引，统计范围
- 对每个索引取上下文（±50 字符），判断是字符串/函数索引/参数描述

### Phase 5: 导出函数定位（1-2 小时）
- 找 `register()` / `AWSCInner.register()` 类注册调用
- 确定模块 + 工厂函数 → 导出对象定义位置
- 函数名不在 JS 中 → 在 C[9] 常量表按字节码索引查找
- 追踪调用链：`_modules['fy'].getToken() → W(C[idx],...) → DG() 解释器执行`

### Phase 6: 运行时注入（纯静态不够时）
- 注入最小兼容环境（fake `AWSCInner._modules` / `register` 桩）
- 执行 DSL VM 代码，读取导出函数返回值
- 或走浏览器方案捕获（见下）

---

## 4. 参考 Opcode 对照表（基于已有案例）

| Opcode | 类型 | 特征 |
|--------|------|------|
| 0 | BRANCH | `d[7]=xxx` 无条件跳转 |
| 1 | CALL | `W(C[Y],null,function(){...})` 嵌入函数调用 |
| 2-5 | ARITH | 赋值/比较/算术运算 |
| 6 | RETURN | `return gV` / `throw` |
| 7 | ALLOC | `d[6]=[]`, push 操作 |
| 8 | BRANCH | `d[7]=d[k]?512:425` 条件跳转 |
| 9-14 | STRING/STORE/CALL | 正则/字符串拼接/模块初始化 |
| 15 | RETURN | 函数返回 |
| 16-18 | ALLOC/TABLE | 局部变量/静态数组/函数表初始化 |
| 19 | EXCEPTION | try-catch 循环 |
| 20 | DOM | DOM 操作 |
| 23 | BRANCH | try-catch 安全获取 + 条件跳转 |
| 24 | CALL | 多参数函数调用 |
| 25 | EXCEPTION | 异常捕获 + 跳转 |

---

## 5. 运行时捕获方案（DeepCode 工具链适配）

### 方案 A: Playwright MCP（推荐，成功率最高）
使用 DeepCode 的 `mcp__playwright__*` 工具：
1. 注入反检测（`Page.addScriptToEvaluateOnNewDocument` 抹掉 webdriver/plugins 特征）
2. 拦截网络请求（`browser_network_requests` 观察 token 请求）
3. `browser_evaluate` 等待 `window.AWSCInner._modules` 就绪
4. 模拟鼠标/键盘动作（`browser_click`/`browser_press_key`）后捕获导出

### 方案 B: 运行时注入（Node 环境桩）
- 伪造 `AWSCInner.register/modules` 环境后 `vm.runInNewContext` 执行
- 读取导出函数返回值

### 方案 C: 纯协议验证（不推荐）
> DSL VM token 通常与浏览器上下文强绑定（TLS JA3 指纹、IP、Cookie），纯协议方案成功率极低。

---

## 6. 常见状态码

| Code | 含义 | 处理 |
|------|------|------|
| 0 | 验证通过 | 取出 sessionId + sig |
| 300 | 风控拦截 | 无法通过 |
| 8778 | 验证失败需重试 | 重试操作 |
| 8776 | 操作太快需重试 | 增加延迟 |
| 69634 | 通用失败 | 检查参数 |

---

## 7. 自检清单

- [ ] 完成 DSL VM 识别（IIFE + 单字母变量 + DG() 解释器）？
- [ ] 提取变量映射表（`var X=数字`）？
- [ ] 提取 opcode 列表并分类？
- [ ] 分析常量表 C[9] 引用范围？
- [ ] 定位导出函数注册点？
- [ ] 纯静态不够时尝试运行时注入？
- [ ] 结论沉淀（可存入 deepcode-knowledge 知识库）？

---

## 路径交叉

```
DSL VM 逆向 → 本 skill Phase 1-6
  ↓ 需要捕获运行时数据 → playwright MCP（浏览器注入）
  ↓ 需要 v8 字节码级分析（Bun/Node 打包的 VM）→ deepcode-decompiler / v8asm 工具链
  ↓ 需要分析 API 协议层 → js-reverse 方法论（Observe→Capture→Rebuild）
```
