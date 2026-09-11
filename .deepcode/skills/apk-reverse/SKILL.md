---
name: apk-reverse
description: >
  Android APK 逆向分析方法论（适配 DeepCode 工具链）。当需要 APK 解包、Java 反编译、smali 修改、
  重打包、Frida 动态 Hook，或 APK 内 so 库分析时使用。工具链以 jadx/apktool/frida/adb CLI 为主，
  so 层转 ida-reverse。
  源自 zhaoxuya520/reverse-skill 的 apk-reverse skill，适配 DeepCode。
version: 1.0.0
author: DeepCode (adapted from zhaoxuya520/reverse-skill)
source: https://github.com/zhaoxuya520/reverse-skill
date: 2026-08-01
tags: [apk, android, jadx, apktool, frida, smali, mobile, security]
---

# APK 逆向 CLI 作业规范

> 动态分析（Frida/Hook）须在授权设备上进行。

## 适用范围

- 分析 APK 的 Java 业务逻辑
- 定位登录、签名、风控、证书校验、root 检测
- 查看与修改 `AndroidManifest.xml`
- 查看与修改 smali
- 重打包 APK
- 用 Frida 做 Java/native 动态 Hook
- APK 内含 `.so` 时切到 native 分析

> 核心逻辑在 `.so` → 转 `ida-reverse` skill。

## 工具分工（CLI，经 DeepCode Bash 工具执行）

### jadx — Java 反编译
本地已装 v1.5.6：`tools/jadx/`（依赖 JDK 21），启动器 `bash tools/pentest/bin/jadx.sh`（等价于下方 `jadx` 命令）。
```bash
jadx -d jadx_out app.apk                        # 全量反编译
jadx --single-class com.example.LoginActivity -d jadx_out app.apk
jadx --deobf -d jadx_out app.apk                # 混淆还原
```
用于：包名/类名/方法名搜索，先从高层逻辑理解 APK。

### apktool — 解包/改/重建
本地已装 v3.0.3：`tools/apktool/apktool_3.0.3.jar`（依赖 JDK），启动器 `bash tools/pentest/bin/apktool.sh`（等价于下方 `apktool` 命令）。
```bash
apktool d app.apk -o apktool_out               # 解包（manifest + smali + res）
apktool b apktool_out -o rebuilt.apk           # 重建
```
用于：查看/修改 `AndroidManifest.xml`、smali、重打包。

### frida — 动态 Hook
本地已装 frida-tools 17.16.4（pip）：frida/frida-ps/frida-trace 等 CLI 已可用，启动器 `bash tools/pentest/bin/frida.sh`。
```bash
frida-ps -U                                    # 列出 USB 设备进程
frida -U -f com.example.app -l hook.js         # spawn 注入
frida-trace -U -f com.example.app -j '*!*certificate*'
```
用于：观察 Java 方法调用、Hook native 导出、绕过 root/证书/调试检测。

### objection — Frida 运行时探索（免写脚本）
本地已装 v1.12.5（pip）：objection 基于 frida，启动器 `bash tools/pentest/bin/objection.sh`（等价于下方 `objection` 命令）。
```bash
objection -g com.example.app explore            # 进入 REPL
android sslpinning disable                       # 绕过 SSL pinning
android root disable                             # 绕过 root 检测
android keystore list                            # 枚举 AndroidKeystore
patchapk --source app.apk                        # 注入 Frida Gadget 重打包
```
用于：动态分析时免写 Hook 脚本快速绕过/枚举。

### adb — 设备/安装/日志
```bash
adb devices
adb install -r app.apk
adb logcat
adb pull /data/local/tmp/file .
```

## 推荐工作流

### 1. Triage（先看构成，不改包不 Hook）
1. `jadx -d jadx_out app.apk` 导出 Java 代码
2. `apktool d app.apk -o apktool_out` 导出 smali + 资源
3. 先看：`AndroidManifest.xml`、主 package、application/activity/service/receiver、`lib/` 下是否有 `.so`

### 2. Java 逻辑观察
优先从 `jadx_out` 读：MainActivity、Application、登录/网络/加密/风控相关类。
常见关键词：`login` `sign` `encrypt` `cipher` `token` `root` `certificate` `trust` `okhttp` `retrofit` `webview`。

### 3. Smali 与资源层确认
当 jadx 结果不完整、混淆重、或需要实际 patch 时，切到 `apktool_out`：
- 看 `smali*/`、`res/values/strings.xml`、`AndroidManifest.xml`
- 优先 patch：`android:exported`、调试标记、root 检测返回值、登录验证逻辑、证书校验分支

### 4. 重建与安装
```bash
apktool b apktool_out -o rebuilt.apk
# 签名：apksigner sign --ks debug.keystore rebuilt.apk
# 对齐：zipalign -f 4 rebuilt.apk rebuilt-aligned.apk
adb install -r rebuilt-aligned.apk
```

### 5. 动态 Hook
静态不足时用 Frida：Hook 登录函数、OkHttp/Retrofit/WebView 关键点、`javax.crypto`/`MessageDigest`、root 检测、SSL pinning。
原则：先 Hook Java 层再看 native；先打印参数/返回值，再决定是否改返回值。

### 6. Native `.so` 分流
遇到以下信号尽快切 native（转 `ida-reverse`）：
- Java 层只是 JNI 包装
- 核心签名逻辑不在 Java
- `System.loadLibrary()` 后关键逻辑消失
- 证书校验/风控在 `.so` 中

## 输出要求

最终至少说明：入口组件与关键类、关键逻辑在 Java/smali/`.so`、已确认敏感点（登录/签名/root/SSL/WebView/JNI）、patch 内容、Hook 的类/方法/导出函数。

## 禁止事项

- 不要一开始就盲目改 smali
- 不要在没看 manifest 和主入口前就写 Hook
- 不要把 Java 反编译不完整直接等同于"逻辑不可分析"
- 不要在 `.so` 明显承载核心逻辑时继续死磕 Java 层

## 快速命令备忘

```bash
jadx -d jadx_out app.apk          # 反编译 Java
apktool d app.apk -o apktool_out  # 解包
apktool b apktool_out -o rebuilt.apk  # 重建
adb devices && frida-ps -U        # 设备与进程
frida -U -f com.example.app -l hook.js   # 注入
```

## 任务完成自检（声称完成前 MUST 通过）

- [ ] 是否完成 Triage（manifest + 主入口 + lib/ 检查）？
- [ ] 是否产出可复现证据（命令/输出/修改点/Hook 脚本）？
- [ ] 动态 Hook 是否在授权设备上执行？
- [ ] 结论是否沉淀（可存入 deepcode-knowledge 知识库）？
