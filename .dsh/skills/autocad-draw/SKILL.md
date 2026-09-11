---
name: autocad-draw
description: 控制 AutoCAD 2020 绘制工程图纸。当用户要求画图、绘制CAD、设计图纸、出图时使用。支持直线/圆/矩形/多边形/文字/标注/图层管理等所有基础绘图操作。
---

# AutoCAD 绘图 Skill

你现在可以通过 MCP 工具直接控制 AutoCAD 2020 进行绘图。

## 使用方式

调用 MCP 工具 `autocad_*` 系列函数。每次绘图前先确认 AutoCAD 已启动（调用 `autocad_status`）。

## 绘图原则

1. **先规划后绘制**：收到用户需求后，先口头说明绘图计划（尺寸、比例、图层规划），再逐条发送命令
2. **使用图层**：不同类型的对象放在不同图层（如 轮廓线/标注/文字/中心线）
3. **统一单位**：默认毫米(mm)，1单位 = 1mm
4. **合理配色**：
   - 轮廓线: 白色(7) / 粗实线层
   - 标注: 青色(4) / 标注层
   - 中心线: 红色(1) / 中心线层（线型 CENTER）
   - 文字: 黄色(2) / 文字层
   - 虚线: 绿色(3) / 虚线层（线型 HIDDEN）
5. **先画完再调用 `autocad_zoom_extents`** 查看全图
6. **出错时说 `autocad_undo`** 撤销

## 可用工具

| 工具 | 用途 | 参数 |
|------|------|------|
| `autocad_draw_line` | 画直线 | x1,y1, x2,y2 |
| `autocad_draw_circle` | 画圆 | cx,cy, radius |
| `autocad_draw_rectangle` | 画矩形 | x1,y1, x2,y2 |
| `autocad_draw_polygon` | 画正多边形 | cx,cy, radius, sides |
| `autocad_draw_arc` | 画圆弧 | cx,cy, radius, start_angle, end_angle |
| `autocad_draw_text` | 单行文字 | x,y, text, height, rotation |
| `autocad_send_command` | 执行任意命令 | cmd (如 "_OFFSET 10 ...") |
| `autocad_set_layer` | 创建/切换图层 | name, color, linetype |
| `autocad_set_color` | 设置颜色 | color (1-7) |
| `autocad_zoom_extents` | 缩放至全图 | — |
| `autocad_save` | 保存图纸 | filepath(可选) |
| `autocad_dimension_linear` | 线性标注 | x1,y1, x2,y2, x3,y3 |
| `autocad_move` | 移动对象 | x1,y1, x2,y2 |
| `autocad_copy` | 复制对象 | x1,y1, x2,y2 |
| `autocad_rotate` | 旋转对象 | bx,by, angle |
| `autocad_scale` | 缩放对象 | bx,by, factor |
| `autocad_mirror` | 镜像对象 | x1,y1, x2,y2 |
| `autocad_undo` | 撤销 | — |
| `autocad_erase_last` | 删除最后对象 | — |
| `autocad_status` | 检查状态 | — |

## 典型绘图流程

```
用户: "画一个100x80的矩形板，中间有一个直径20的圆孔"

1. autocad_set_layer("轮廓线", 7)          # 白色轮廓层
2. autocad_draw_rectangle(0, 0, 100, 80)   # 底板
3. autocad_set_layer("中心线", 1)          # 红色中心线
4. autocad_draw_line(40, 40, 60, 40)       # 水平中心线
5. autocad_draw_line(50, 30, 50, 50)       # 垂直中心线
6. autocad_set_layer("轮廓线", 7)          # 切回轮廓层
7. autocad_draw_circle(50, 40, 10)         # 圆孔
8. autocad_zoom_extents()                   # 查看全图
```

## 注意事项

- **AutoCAD 必须保持运行状态**——所有命令通过模拟键盘输入发送到 AutoCAD 命令行
- 命令之间需要短暂延迟，MCP 服务器已自动处理
- 复杂图形建议先生成 .scr 脚本文件，再用 `autocad_send_command("_SCRIPT ...")` 批量执行
- 如果 AutoCAD 无响应，先调用 `autocad_status` 检查状态
