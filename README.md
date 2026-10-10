# NEWPATH

**中文** | [English](README_EN.md)

面向大幅面颗粒 3D 打印的 Grasshopper 参数化路径组件库。**1.0.2 · 25 个组件 · Rhino 8 / Python 3**

从几何切片、路径编辑和连续化，到料条预览与 G-code 生成，在 Grasshopper 中构建可检查、可调整的打印流程。

> **设备测试范围：仅在金石 FGF 1800 大幅面颗粒 3D 打印机上测试。未在机械臂 3D 打印设备上测试，也未在拓竹（Bambu Lab）一类桌面级高速 3D 打印机上测试。不能据此推定其他设备兼容。**

## 核心价值

- **保留几何与工艺信息**：PATH 数据包在组件间传递曲线、逐点高度、料条宽度及运动类型，使路径编辑与 G-code 输出共享数据。
- **针对大幅面打印的路径控制**：组合分组、连续路径、双层墙、端部承托、结构肋和底部填充，按设计需要组织打印路径。
- **在输出前检查路径**：结合打印床、解包、逐点运动和料条网格预览，检查几何及路径数据。
- **开箱使用**：`.ghuser` 已内嵌代码与图标，本仓库不分发独立 Python 源码。

## 1.0.2 更新

2026-10-10 发布。相对 1.0.1，本版更新全部 25 个组件的撞色图标，代码、端口、名称、分类及组件身份保持一致。仓库提供 512×512 透明 PNG 清晰版图标。

## 下载与安装

1. 安装 **Rhino 8**，使用其中的 **Grasshopper Python 3 Script** 运行环境。
2. 在 GitHub 选择 **Code → Download ZIP**，解压仓库。
3. 打开 Grasshopper → **File → Special Folders → User Objects Folder**，将 [UserObjects](UserObjects) 中全部 **25 个 `.ghuser`** 复制进去。
4. 升级前备份并移走旧版 NEWPATH 组件，检查子目录中的重复项；部分旧文件已更名，仅覆盖同名文件可能遗留旧组件。
5. 保存工作并完整退出、重启 Rhino。在 **NEWPATH** 下的六个子分类中放置新组件：**1 Slice / 2 Path Edit / 3 Path Assembly / 4 Export / 5 Analyze / 6 Utilities**。

`.ghuser` 已内嵌源码和图标，使用时不依赖外部 `.py`、仓库路径或开发 skill。已有画布实例不会自动更新，升级后需放置新实例并重新连接。组件名称和说明为中文，端口标识与分类为英文；英文 README 为文档翻译。

## 仓库结构

```text
NEWPATH/
├── UserObjects/    # 25 .ghuser
├── 图标/           # 25 PNG, 512 × 512
├── .gitignore
├── LICENSE
├── README.md
└── README_EN.md
```

## 使用流程

典型闭合轮廓流程：**智能切片 → 路径分组 → 按需添加填充/墙体等路径操作 → 连续路径 → 可选 PATH转G2 G3 → Path G-code生成 → G-code导出 / G-code上传**。

开放轮廓按输入条件使用“开放连续路径”；曲面参数方向切片可使用“UV方向切片”。这些是按数据契约选择的分支，并非所有组件都能任意串联。连接处理节点的 **Path** 输出；`Preview` 只用于观察，不能替代携带属性的 PATH 数据包。智能切片负责切片与逐点属性，打印分块交给路径分组。

G-code 生成要求毫米路径；建模及输出前核对单位、设备行程、温度、速度和挤出设置。当前源码使用设备相关的挤出换算（耗材截面积常量 `3.67 mm²`）和风扇参数范围，默认参数不是其他打印机的通用配置。圆弧输出支持 XY 平面 G2/G3，须确认控制器支持。上传使用 Moonraker：显式填写自己的 `Host`（内置默认地址为 `http://192.168.1.114`）及需要时的 `Key`；上传不会启动打印。估时不包含加热、归零及加速度。详细参数请查看组件端口提示及组件内嵌代码头部说明。


## 全部组件

图标以 80×80 显示，原图为 512×512 透明 PNG；点击文件链接下载对应组件。

### 1 Slice

| 图标 | 组件 | 功能 | 文件 |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/uv_slicer_path.png" width="80" height="80" alt="UV slicer"> | UV方向切片 | 沿曲面的指定参数方向生成保形切片，并记录逐点高度和生长方向。 | [.ghuser](UserObjects/NEWPATH_UV%E6%96%B9%E5%90%91%E5%88%87%E7%89%87.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/smart_slicer_path.png" width="80" height="80" alt="Smart slicer"> | 智能切片 | 按层高与端部平面切片几何，输出携带料条宽度和逐点属性的路径。 | [.ghuser](UserObjects/NEWPATH_%E6%99%BA%E8%83%BD%E5%88%87%E7%89%87.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/pack_path.png" width="80" height="80" alt="Pack Path"> | 打包Path | 把手绘平行层线打包为保留实际层高和来源的 Path 数据。 | [.ghuser](UserObjects/NEWPATH_%E6%89%93%E5%8C%85Path.ghuser) |

### 2 Path Edit

| 图标 | 组件 | 功能 | 文件 |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/double_wall_path.png" width="80" height="80" alt="Double wall"> | 双层墙路径 | 向闭合墙线内侧偏移，并在指定连接点生成双层墙路径。 | [.ghuser](UserObjects/NEWPATH_%E5%8F%8C%E5%B1%82%E5%A2%99%E8%B7%AF%E5%BE%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/surface_rib_path.png" width="80" height="80" alt="Double-track rib"> | 双道结构肋 | 按定位点、层范围和悬垂角，在墙体内侧生成双道往返结构肋。 | [.ghuser](UserObjects/NEWPATH_%E5%8F%8C%E9%81%93%E7%BB%93%E6%9E%84%E8%82%8B.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/brim_skirt_path.png" width="80" height="80" alt="Brim"> | 生成brim | 沿落地外轮廓向外生成多圈贴边裙边，并保留原模型路径。 | [.ghuser](UserObjects/NEWPATH_%E7%94%9F%E6%88%90brim.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/skirt_path.png" width="80" height="80" alt="Surrounding brim"> | 生成包围brim | 在全部模型底部生成闭合的包围 brim。 | [.ghuser](UserObjects/NEWPATH_%E7%94%9F%E6%88%90%E5%8C%85%E5%9B%B4brim.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/end_offset_path.png" width="80" height="80" alt="End offset"> | 端部偏移 | 按指定层范围逐层向内或向外偏移墙体端部。 | [.ghuser](UserObjects/NEWPATH_%E7%AB%AF%E9%83%A8%E5%81%8F%E7%A7%BB.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/end_bearing_path.png" width="80" height="80" alt="End bearing"> | 端部承托 | 在指定端部层范围内逐层加厚双层墙，形成承托面。 | [.ghuser](UserObjects/NEWPATH_%E7%AB%AF%E9%83%A8%E6%89%BF%E6%89%98.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/bottom_hatch_fill.png" width="80" height="80" alt="Zigzag infill"> | 蛇形填充 | 按指定层范围和扫描间距，为切片路径添加蛇形底部填充。 | [.ghuser](UserObjects/NEWPATH_%E8%9B%87%E5%BD%A2%E5%A1%AB%E5%85%85.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/spiral_infill_path.png" width="80" height="80" alt="Spiral infill"> | 螺旋填充 | 在首个模型组的指定层添加螺旋填充，并保留全部原路径。 | [.ghuser](UserObjects/NEWPATH_%E8%9E%BA%E6%97%8B%E5%A1%AB%E5%85%85.ghuser) |

### 3 Path Assembly

| 图标 | 组件 | 功能 | 文件 |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/open_continuous_path.png" width="80" height="80" alt="Open continuous path"> | 开放连续路径 | 将同组逐层开放曲线交替反向连接为往返打印路径。 | [.ghuser](UserObjects/NEWPATH_%E5%BC%80%E6%94%BE%E8%BF%9E%E7%BB%AD%E8%B7%AF%E5%BE%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/path_grouping.png" width="80" height="80" alt="Path grouping"> | 路径分组 | 按指定高度重新划分打印块，并可按块显示不同颜色。 | [.ghuser](UserObjects/NEWPATH_%E8%B7%AF%E5%BE%84%E5%88%86%E7%BB%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/continuous_path.png" width="80" height="80" alt="Continuous path"> | 连续路径 | 按引导点调整闭环接缝，将同组切片连接为连续挤出路径。 | [.ghuser](UserObjects/NEWPATH_%E8%BF%9E%E7%BB%AD%E8%B7%AF%E5%BE%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/curve_arc_classifier.png" width="80" height="80" alt="PATH to G2/G3"> | PATH转G2 G3 | 将路径拟合为直线和圆弧，并按公差及段长限制重建路径。 | [.ghuser](UserObjects/NEWPATH_PATH%E8%BD%ACG2%20G3.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/path_to_g1.png" width="80" height="80" alt="PATH to G1"> | PATH转G1 | 按长度或特征采样将 Path 曲线离散为 G1 路径，保留逐点属性。 | [.ghuser](UserObjects/NEWPATH_PATH%E8%BD%ACG1.ghuser) |

### 4 Export

| 图标 | 组件 | 功能 | 文件 |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/gcode_upload.png" width="80" height="80" alt="G-code upload"> | G-code上传 | 将 G-code 保存到本地并上传至指定打印设备。 | [.ghuser](UserObjects/NEWPATH_G-code%E4%B8%8A%E4%BC%A0.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/gcode_export.png" width="80" height="80" alt="G-code export"> | G-code导出 | 分析打印时间和材料用量，并按按钮将指令保存到指定目录。 | [.ghuser](UserObjects/NEWPATH_G-code%E5%AF%BC%E5%87%BA.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/path_gcode.png" width="80" height="80" alt="Path G-code"> | Path G-code生成 | 根据连续路径及速度、温度和挤出参数生成打印指令。 | [.ghuser](UserObjects/NEWPATH_Path%20G-code%E7%94%9F%E6%88%90.ghuser) |

### 5 Analyze

| 图标 | 组件 | 功能 | 文件 |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/gcode_motion.png" width="80" height="80" alt="Pointwise motion"> | Path逐点运动 | 读取路径原始点列，输出逐点坐标、索引和入边运动类型。 | [.ghuser](UserObjects/NEWPATH_Path%E9%80%90%E7%82%B9%E8%BF%90%E5%8A%A8.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/unpack_path.png" width="80" height="80" alt="Unpack Path"> | 解包Path | 将路径数据包拆为层平面、曲线、名义层高和原始点列。 | [.ghuser](UserObjects/NEWPATH_%E8%A7%A3%E5%8C%85Path.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/rounded_mesh_pipe.png" width="80" height="80" alt="Path preview"> | 路径预览 | 按 Path 的逐点宽度和高度预览打印料条网格。 | [.ghuser](UserObjects/NEWPATH_%E8%B7%AF%E5%BE%84%E9%A2%84%E8%A7%88.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/Gcode%E8%BF%90%E5%8A%A8%E6%A8%A1%E6%8B%9F_gcode_simulator.png" width="80" height="80" alt="G-code motion simulator"> | Gcode运动模拟 | 解析 G-code 并按速度播放打印头运动，显示已走路径。 | [.ghuser](UserObjects/NEWPATH_Gcode%E8%BF%90%E5%8A%A8%E6%A8%A1%E6%8B%9F.ghuser) |

### 6 Utilities

| 图标 | 组件 | 功能 | 文件 |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/print_bed.png" width="80" height="80" alt="Print bed"> | 打印床 | 按设备名称和长宽显示打印床，并输出世界水平基准面。 | [.ghuser](UserObjects/NEWPATH_%E6%89%93%E5%8D%B0%E5%BA%8A.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/%E6%B0%B4%E5%B9%B3%E5%81%8F%E7%A7%BB%E6%9B%B2%E9%9D%A2_horizontal_offset_surface.png" width="80" height="80" alt="Horizontal surface offset"> | 水平偏移曲面 | 按水平距离偏移曲面，可封闭为墙体。 | [.ghuser](UserObjects/NEWPATH_%E6%B0%B4%E5%B9%B3%E5%81%8F%E7%A7%BB%E6%9B%B2%E9%9D%A2.ghuser) |

## 验证与许可

本版完成离线 ghuser 归档回读、图标像素及文件哈希检查；未进行本版实时 Grasshopper 新实例求解验收。历史设备测试范围见文首说明。

采用 [MIT License](LICENSE)。本仓库不附带独立 `.py` 源码文件；`.ghuser` 内嵌代码是组件正常运行所需内容。
