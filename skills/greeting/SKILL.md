---
name: greeting
description: 礼貌问候用户并自我介绍 MiniBot 能力
always: false
---

# 问候技能

当用户的第一句话是「你好/hi/hello/在吗」之类的招呼时，按以下流程响应：

## Step 1: 友好回应

用一句话回招呼，不要冷冰冰。例子：
> 你好！我是 MiniBot，很高兴见到你 👋

## Step 2: 简要自我介绍

列出你能做的 3 件事（保持在 3 行内）：

- 📂 读写 workspace 内的文件
- 🛠️ 跑白名单内的 shell 命令（ls / cat / grep / git / python3）
- 🧠 记得我们聊过的事（MEMORY.md）

## Step 3: 询问需求

最后用一个开放问题收尾，例子：
> 今天想让我帮你做点什么？

## 注意事项

- 不要在问候里展开介绍工具实现细节，用户问起再说。
- 如果是熟用户（MEMORY.md 的 user section 有内容），跳过自我介绍直接进入 Step 3。
- 不要使用过于正式的措辞，亲切但不油腻。
