# NEWPATH

**中文** | [English](README_EN.md)

面向大幅面颗粒 3D 打印的 Grasshopper 参数化路径组件库。**1.0.0 · 21 个组件 · Rhino 8 / Python 3**

从几何切片、路径编辑和连续化，到料条预览与 G-code 生成，在 Grasshopper 中构建可检查、可调整的打印流程。

> **设备测试范围：仅在金石 FGF 1800 大幅面颗粒 3D 打印机上测试。未在机械臂 3D 打印设备上测试，也未在拓竹（Bambu Lab）一类桌面级高速 3D 打印机上测试。不能据此推定其他设备兼容。**

## 核心价值

- **保留几何与工艺信息**：PATH 数据包在组件间传递曲线、逐点高度、料条宽度及运动类型，使路径编辑与 G-code 输出共享数据。
- **针对大幅面打印的路径控制**：组合分组、连续路径、双层墙、端部承托、结构肋和底部填充，按设计需要组织打印路径。
- **在输出前检查路径**：结合打印床、解包、逐点运动和料条网格预览，检查几何及路径数据。
- **可阅读、可继续开发**：同时提供完整 `.py` 源码和内嵌源码的 `.ghuser`；每份源码头部包含输入、输出、默认值及使用约束。

## 下载与安装

1. 安装 **Rhino 8**，使用其中的 **Grasshopper Python 3 Script** 运行环境。
2. 下载 [NEWPATH_1.0.0.zip](NEWPATH_1.0.0.zip) 并解压。这个安装包仅包含 **21 个平铺的 `.ghuser` 文件**。需要源码及文档时，通过 GitHub 的 **Code → Download ZIP** 下载整个仓库，或单独打开下方源码链接。
3. 在 Grasshopper 中选择 **File → Special Folders → User Objects Folder**，将安装包内全部 `.ghuser` 文件复制到该文件夹。若下载的是整个仓库，请复制 [UserObjects](UserObjects) 各分类目录中的 `.ghuser` 文件，而不是把仓库 ZIP 当作安装包。
4. 更新安装时先备份旧组件，检查 User Objects 目录及子目录，避免同一组件重复安装；再替换同名文件。
5. 保存工作并完整退出、重启 Rhino。打开 Grasshopper，在 **NEWPATH** 分类下找到 **Slice / Path / Data / GCode**，拖入新组件。

`.ghuser` 已内嵌源码和图标，正常使用不依赖本仓库路径、外部 `.py` 或 skill。已放在旧画布中的实例不会随安装文件自动更新，升级后需放置新实例并重新连接。英文 README 是文档翻译，当前组件界面仍为中文，端口标识与分类为英文。

## 使用流程

典型闭合轮廓流程：**智能切片 → 路径分组 → 按需添加填充/墙体等路径操作 → 连续路径 → 可选 PATH转G2 G3 → Path G-code生成 → G-code导出 / G-code上传**。

开放轮廓按输入条件使用“开放连续路径”；曲面参数方向切片可使用“UV方向切片”。这些是按数据契约选择的分支，并非所有组件都能任意串联。连接处理节点的 **Path** 输出；`Preview` 只用于观察，不能替代携带属性的 PATH 数据包。智能切片负责切片与逐点属性，打印分块交给路径分组。

G-code 生成要求毫米路径；建模及输出前核对单位、设备行程、温度、速度和挤出设置。当前源码使用设备相关的挤出换算（耗材截面积常量 `3.67 mm²`）和风扇参数范围，默认参数不是其他打印机的通用配置。圆弧输出支持 XY 平面 G2/G3，须确认控制器支持。上传使用 Moonraker：显式填写自己的 `Host`（内置默认地址为 `http://192.168.1.114`）及需要时的 `Key`；上传不会启动打印。估时不包含加热、归零及加速度。详细参数以各源码头部为准。

## 全部组件

图标为从本版 SVG 导出的 80×80 透明 PNG；点击源码查看完整端口说明。

### Slice

| 图标 | 组件 | 功能 | 文件 |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/readme/smart_slicer_path.png" width="80" height="80" alt="Smart slicer"> | 智能切片 | 按层高与端部平面切片几何，输出携带料条宽度和逐点属性的路径。 | [.py](%E6%BA%90%E7%A0%81/Slice/smart_slicer_path.py) · [.ghuser](UserObjects/Slice/NEWPATH_%E6%99%BA%E8%83%BD%E5%88%87%E7%89%87.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/uv_slicer_path.png" width="80" height="80" alt="UV slicer"> | UV方向切片 | 沿曲面的指定参数方向生成保形切片，并记录逐点高度和生长方向。 | [.py](%E6%BA%90%E7%A0%81/Slice/uv_slicer_path.py) · [.ghuser](UserObjects/Slice/NEWPATH_UV%E6%96%B9%E5%90%91%E5%88%87%E7%89%87.ghuser) |

### Path

| 图标 | 组件 | 功能 | 文件 |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/readme/bottom_hatch_fill.png" width="80" height="80" alt="Zigzag infill"> | 蛇形填充 | 按指定层范围和扫描间距，为切片路径添加蛇形底部填充。 | [.py](%E6%BA%90%E7%A0%81/Path/bottom_hatch_fill.py) · [.ghuser](UserObjects/Path/NEWPATH_%E8%9B%87%E5%BD%A2%E5%A1%AB%E5%85%85.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/brim_skirt_path.png" width="80" height="80" alt="Brim"> | 生成brim | 沿落地外轮廓向外生成多圈贴边裙边，并保留原模型路径。 | [.py](%E6%BA%90%E7%A0%81/Path/brim_skirt_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E7%94%9F%E6%88%90brim.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/continuous_path.png" width="80" height="80" alt="Continuous path"> | 连续路径 | 按引导点调整闭环接缝，将同组切片连接为连续挤出路径。 | [.py](%E6%BA%90%E7%A0%81/Path/continuous_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E8%BF%9E%E7%BB%AD%E8%B7%AF%E5%BE%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/double_wall_path.png" width="80" height="80" alt="Double wall"> | 双层墙路径 | 向闭合墙线内侧偏移，并在指定连接点生成双层墙路径。 | [.py](%E6%BA%90%E7%A0%81/Path/double_wall_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E5%8F%8C%E5%B1%82%E5%A2%99%E8%B7%AF%E5%BE%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/end_bearing_path.png" width="80" height="80" alt="End bearing"> | 端部承托 | 在指定端部层范围内逐层加厚双层墙，形成承托面。 | [.py](%E6%BA%90%E7%A0%81/Path/end_bearing_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E7%AB%AF%E9%83%A8%E6%89%BF%E6%89%98.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/end_offset_path.png" width="80" height="80" alt="End offset"> | 端部偏移 | 按指定层范围逐层向内或向外偏移墙体端部。 | [.py](%E6%BA%90%E7%A0%81/Path/end_offset_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E7%AB%AF%E9%83%A8%E5%81%8F%E7%A7%BB.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/open_continuous_path.png" width="80" height="80" alt="Open continuous path"> | 开放连续路径 | 将同组逐层开放曲线交替反向连接为往返打印路径。 | [.py](%E6%BA%90%E7%A0%81/Path/open_continuous_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E5%BC%80%E6%94%BE%E8%BF%9E%E7%BB%AD%E8%B7%AF%E5%BE%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/path_grouping.png" width="80" height="80" alt="Path grouping"> | 路径分组 | 按指定高度重新划分打印块，并可按块显示不同颜色。 | [.py](%E6%BA%90%E7%A0%81/Path/path_grouping.py) · [.ghuser](UserObjects/Path/NEWPATH_%E8%B7%AF%E5%BE%84%E5%88%86%E7%BB%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/skirt_path.png" width="80" height="80" alt="Skirt"> | 生成skirt | 按模型俯视凸包生成整体包围裙边，并保留原模型路径。 | [.py](%E6%BA%90%E7%A0%81/Path/skirt_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E7%94%9F%E6%88%90skirt.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/spiral_infill_path.png" width="80" height="80" alt="Spiral infill"> | 螺旋填充 | 在首个模型组的指定层添加螺旋填充，并保留全部原路径。 | [.py](%E6%BA%90%E7%A0%81/Path/spiral_infill_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E8%9E%BA%E6%97%8B%E5%A1%AB%E5%85%85.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/surface_rib_path.png" width="80" height="80" alt="Double-track rib"> | 双道结构肋 | 按定位点、层范围和悬垂角，在墙体内侧生成双道往返结构肋。 | [.py](%E6%BA%90%E7%A0%81/Path/surface_rib_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E5%8F%8C%E9%81%93%E7%BB%93%E6%9E%84%E8%82%8B.ghuser) |

### Data

| 图标 | 组件 | 功能 | 文件 |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/readme/gcode_motion.png" width="80" height="80" alt="Pointwise motion"> | Path逐点运动 | 读取路径原始点列，输出逐点坐标、索引和入边运动类型。 | [.py](%E6%BA%90%E7%A0%81/Data/gcode_motion.py) · [.ghuser](UserObjects/Data/NEWPATH_Path%E9%80%90%E7%82%B9%E8%BF%90%E5%8A%A8.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/print_bed.png" width="80" height="80" alt="Print bed"> | 打印床 | 按设备名称和长宽显示打印床，并输出世界水平基准面。 | [.py](%E6%BA%90%E7%A0%81/Data/print_bed.py) · [.ghuser](UserObjects/Data/NEWPATH_%E6%89%93%E5%8D%B0%E5%BA%8A.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/rounded_mesh_pipe.png" width="80" height="80" alt="Extrusion preview"> | 路径料条预览 | 根据路径的逐点高度和料条宽度生成挤出料条网格预览。 | [.py](%E6%BA%90%E7%A0%81/Data/rounded_mesh_pipe.py) · [.ghuser](UserObjects/Data/NEWPATH_%E8%B7%AF%E5%BE%84%E6%96%99%E6%9D%A1%E9%A2%84%E8%A7%88.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/unpack_path.png" width="80" height="80" alt="Unpack Path"> | 解包Path | 将路径数据包拆为层平面、曲线、名义层高和原始点列。 | [.py](%E6%BA%90%E7%A0%81/Data/unpack_path.py) · [.ghuser](UserObjects/Data/NEWPATH_%E8%A7%A3%E5%8C%85Path.ghuser) |

### GCode

| 图标 | 组件 | 功能 | 文件 |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/readme/curve_arc_classifier.png" width="80" height="80" alt="PATH to G2/G3"> | PATH转G2 G3 | 将路径拟合为直线和圆弧，并按公差及段长限制重建路径。 | [.py](%E6%BA%90%E7%A0%81/GCode/curve_arc_classifier.py) · [.ghuser](UserObjects/GCode/NEWPATH_PATH%E8%BD%ACG2%20G3.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/gcode_export.png" width="80" height="80" alt="G-code export"> | G-code导出 | 分析打印时间和材料用量，并按按钮将指令保存到指定目录。 | [.py](%E6%BA%90%E7%A0%81/GCode/gcode_export.py) · [.ghuser](UserObjects/GCode/NEWPATH_G-code%E5%AF%BC%E5%87%BA.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/gcode_upload.png" width="80" height="80" alt="G-code upload"> | G-code上传 | 将 G-code 保存到本地并上传至指定打印设备。 | [.py](%E6%BA%90%E7%A0%81/GCode/gcode_upload.py) · [.ghuser](UserObjects/GCode/NEWPATH_G-code%E4%B8%8A%E4%BC%A0.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/path_gcode.png" width="80" height="80" alt="Path G-code"> | Path G-code生成 | 根据连续路径及速度、温度和挤出参数生成打印指令。 | [.py](%E6%BA%90%E7%A0%81/GCode/path_gcode.py) · [.ghuser](UserObjects/GCode/NEWPATH_Path%20G-code%E7%94%9F%E6%88%90.ghuser) |

## 使用 ghpython skill 开发电池

本库使用 **`ghpython-component-workflow` skill** 辅助开发 Rhino 8 Grasshopper Python 3 电池。工作方式是维护独立 Python 源码，通过开发期 loader 迭代，并规范端口类型、Item/List/Tree 访问方式、输入输出说明与验证，再将独立源码封装进 `.ghuser`。skill 是开发辅助工具，不是使用本库必须安装的运行依赖。

二次开发可从 [源码](源码) 中选择对应文件；在已安装该 skill 的 Codex 中明确调用 `ghpython-component-workflow`，提供目标源码及修改需求。本仓库未附带 skill 安装包。源码应在 Rhino 8 的 Python 3 Script 环境运行，不能直接作为普通命令行 Python 程序使用。修改外部 `.py` 不会自动修改现有 `.ghuser` 的内嵌代码，需重新封装并验证。
