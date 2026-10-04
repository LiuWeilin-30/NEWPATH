# NEWPATH

[中文](README.md) | **English**

A Grasshopper component library for parametric toolpaths in large-format pellet 3D printing. **1.0.0 · 21 components · Rhino 8 / Python 3**

Build an inspectable workflow from slicing and path editing to continuous paths, extrusion previews and G-code generation.

> **Hardware testing: tested only on a Jinshi (金石) FGF 1800 large-format pellet 3D printer. Not tested on robotic-arm 3D printing systems or desktop high-speed 3D printers such as Bambu Lab. Compatibility with other machines has not been established.**

## Core value

- **Keep geometry and process data together:** PATH packets carry curves, local heights, bead widths and motion types through editing and G-code generation.
- **Control large-format toolpaths:** combine grouping, continuous paths, double walls, end bearings, structural ribs and bottom infill to suit the design.
- **Inspect before output:** use print-bed, unpacking, pointwise-motion and extrusion-mesh components to examine geometry and path data.
- **Readable and extensible:** complete `.py` sources accompany `.ghuser` files with embedded code. Source headers document inputs, outputs, defaults and constraints.

## Download and installation

1. Install **Rhino 8** and use its **Grasshopper Python 3 Script** runtime.
2. Download and extract [NEWPATH_1.0.0.zip](NEWPATH_1.0.0.zip). This installation archive contains only **21 `.ghuser` files at its root**. For source code and documentation, use GitHub **Code → Download ZIP** to download the whole repository, or follow the source links below.
3. In Grasshopper, choose **File → Special Folders → User Objects Folder** and copy all extracted `.ghuser` files there. If you downloaded the whole repository, copy the `.ghuser` files inside the category folders in [UserObjects](UserObjects).
4. When upgrading, back up old components and check the User Objects folder and its subfolders for duplicates before replacing files with matching names.
5. Save your work, fully quit and restart Rhino, then open Grasshopper. Place new components from **NEWPATH → Slice / Path / Data / GCode**.

Each `.ghuser` embeds its code and icon; normal use does not require this repository's path, external `.py` files or the development skill. Existing canvas instances do not update automatically: place fresh instances and reconnect them after upgrading. This English README translates the documentation; component names and descriptions remain Chinese, with English port identifiers and categories.

## Workflow

Typical closed-contour workflow: **Smart slicer → Path grouping → optional infill/wall operations → Continuous path → optional PATH to G2/G3 → Path G-code → G-code export / upload**.

Use Open continuous path for suitable open contours, or UV slicer for surface-parameter slicing. These are branches selected according to each component's input contract, not interchangeable steps. Connect **Path** outputs between processing nodes. `Preview` geometry does not replace an attributed PATH packet. Slicing produces slices and pointwise attributes; grouping handles print blocks.

G-code generation requires millimetre paths. Check model units, machine travel, temperature, speed and extrusion settings before output. The current source uses machine-related extrusion conversion (a fixed `3.67 mm²` filament-area value) and fan ranges; defaults are not universal printer profiles. Arc output supports XY-plane G2/G3 and requires compatible firmware. Upload uses Moonraker: explicitly set your own `Host` (the built-in default is `http://192.168.1.114`) and `Key` if needed. Uploading does not start printing. Time estimates exclude heating, homing and acceleration. Consult source headers for complete parameter details.

## All components

Icons are transparent 80×80 PNGs rendered from the release SVGs. Source headers contain the complete port documentation in Chinese.

### Slice

| Icon | Component | Purpose | Files |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/readme/smart_slicer_path.png" width="80" height="80" alt="Smart slicer"> | Smart slicer<br>智能切片 | Slices geometry using layer heights and end planes, retaining bead width and pointwise attributes. | [.py](%E6%BA%90%E7%A0%81/Slice/smart_slicer_path.py) · [.ghuser](UserObjects/Slice/NEWPATH_%E6%99%BA%E8%83%BD%E5%88%87%E7%89%87.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/uv_slicer_path.png" width="80" height="80" alt="UV slicer"> | UV slicer<br>UV方向切片 | Creates conformal slices along a surface parameter direction with local heights and growth directions. | [.py](%E6%BA%90%E7%A0%81/Slice/uv_slicer_path.py) · [.ghuser](UserObjects/Slice/NEWPATH_UV%E6%96%B9%E5%90%91%E5%88%87%E7%89%87.ghuser) |

### Path

| Icon | Component | Purpose | Files |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/readme/bottom_hatch_fill.png" width="80" height="80" alt="Zigzag infill"> | Zigzag infill<br>蛇形填充 | Adds bottom zigzag infill over a selected layer range with configurable spacing. | [.py](%E6%BA%90%E7%A0%81/Path/bottom_hatch_fill.py) · [.ghuser](UserObjects/Path/NEWPATH_%E8%9B%87%E5%BD%A2%E5%A1%AB%E5%85%85.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/brim_skirt_path.png" width="80" height="80" alt="Brim"> | Brim<br>生成brim | Adds outward brim loops around ground-level contours while retaining model paths. | [.py](%E6%BA%90%E7%A0%81/Path/brim_skirt_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E7%94%9F%E6%88%90brim.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/continuous_path.png" width="80" height="80" alt="Continuous path"> | Continuous path<br>连续路径 | Adjusts closed-loop seams using guide points and connects slices within each group. | [.py](%E6%BA%90%E7%A0%81/Path/continuous_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E8%BF%9E%E7%BB%AD%E8%B7%AF%E5%BE%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/double_wall_path.png" width="80" height="80" alt="Double wall"> | Double wall<br>双层墙路径 | Offsets closed wall curves inward and creates double-wall paths at selected connection points. | [.py](%E6%BA%90%E7%A0%81/Path/double_wall_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E5%8F%8C%E5%B1%82%E5%A2%99%E8%B7%AF%E5%BE%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/end_bearing_path.png" width="80" height="80" alt="End bearing"> | End bearing<br>端部承托 | Thickens double walls progressively over selected end layers to form bearing surfaces. | [.py](%E6%BA%90%E7%A0%81/Path/end_bearing_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E7%AB%AF%E9%83%A8%E6%89%BF%E6%89%98.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/end_offset_path.png" width="80" height="80" alt="End offset"> | End offset<br>端部偏移 | Offsets wall ends inward or outward progressively over selected layers. | [.py](%E6%BA%90%E7%A0%81/Path/end_offset_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E7%AB%AF%E9%83%A8%E5%81%8F%E7%A7%BB.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/open_continuous_path.png" width="80" height="80" alt="Open continuous path"> | Open continuous path<br>开放连续路径 | Alternates the direction of open curves across layers to form a back-and-forth path. | [.py](%E6%BA%90%E7%A0%81/Path/open_continuous_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E5%BC%80%E6%94%BE%E8%BF%9E%E7%BB%AD%E8%B7%AF%E5%BE%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/path_grouping.png" width="80" height="80" alt="Path grouping"> | Path grouping<br>路径分组 | Divides paths into print blocks by height, with optional block-color previews. | [.py](%E6%BA%90%E7%A0%81/Path/path_grouping.py) · [.ghuser](UserObjects/Path/NEWPATH_%E8%B7%AF%E5%BE%84%E5%88%86%E7%BB%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/skirt_path.png" width="80" height="80" alt="Skirt"> | Skirt<br>生成skirt | Adds a surrounding skirt based on the top-view convex hull while retaining model paths. | [.py](%E6%BA%90%E7%A0%81/Path/skirt_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E7%94%9F%E6%88%90skirt.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/spiral_infill_path.png" width="80" height="80" alt="Spiral infill"> | Spiral infill<br>螺旋填充 | Adds spiral infill to selected layers of the first model group, retaining all original paths. | [.py](%E6%BA%90%E7%A0%81/Path/spiral_infill_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E8%9E%BA%E6%97%8B%E5%A1%AB%E5%85%85.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/surface_rib_path.png" width="80" height="80" alt="Double-track rib"> | Double-track rib<br>双道结构肋 | Creates internal return-path ribs from control points, layer ranges and overhang angles. | [.py](%E6%BA%90%E7%A0%81/Path/surface_rib_path.py) · [.ghuser](UserObjects/Path/NEWPATH_%E5%8F%8C%E9%81%93%E7%BB%93%E6%9E%84%E8%82%8B.ghuser) |

### Data

| Icon | Component | Purpose | Files |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/readme/gcode_motion.png" width="80" height="80" alt="Pointwise motion"> | Pointwise motion<br>Path逐点运动 | Extracts original path points, indices and incoming motion types. | [.py](%E6%BA%90%E7%A0%81/Data/gcode_motion.py) · [.ghuser](UserObjects/Data/NEWPATH_Path%E9%80%90%E7%82%B9%E8%BF%90%E5%8A%A8.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/print_bed.png" width="80" height="80" alt="Print bed"> | Print bed<br>打印床 | Displays a named print bed of a given size and outputs the world-horizontal reference plane. | [.py](%E6%BA%90%E7%A0%81/Data/print_bed.py) · [.ghuser](UserObjects/Data/NEWPATH_%E6%89%93%E5%8D%B0%E5%BA%8A.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/rounded_mesh_pipe.png" width="80" height="80" alt="Extrusion preview"> | Extrusion preview<br>路径料条预览 | Builds mesh previews using pointwise path heights and bead widths. | [.py](%E6%BA%90%E7%A0%81/Data/rounded_mesh_pipe.py) · [.ghuser](UserObjects/Data/NEWPATH_%E8%B7%AF%E5%BE%84%E6%96%99%E6%9D%A1%E9%A2%84%E8%A7%88.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/unpack_path.png" width="80" height="80" alt="Unpack Path"> | Unpack Path<br>解包Path | Extracts layer planes, curves, nominal layer heights and original point sequences. | [.py](%E6%BA%90%E7%A0%81/Data/unpack_path.py) · [.ghuser](UserObjects/Data/NEWPATH_%E8%A7%A3%E5%8C%85Path.ghuser) |

### GCode

| Icon | Component | Purpose | Files |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/readme/curve_arc_classifier.png" width="80" height="80" alt="PATH to G2/G3"> | PATH to G2/G3<br>PATH转G2 G3 | Fits lines and arcs, rebuilding paths within tolerance and segment-length limits. | [.py](%E6%BA%90%E7%A0%81/GCode/curve_arc_classifier.py) · [.ghuser](UserObjects/GCode/NEWPATH_PATH%E8%BD%ACG2%20G3.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/gcode_export.png" width="80" height="80" alt="G-code export"> | G-code export<br>G-code导出 | Estimates print time and material usage, and saves G-code on a button trigger. | [.py](%E6%BA%90%E7%A0%81/GCode/gcode_export.py) · [.ghuser](UserObjects/GCode/NEWPATH_G-code%E5%AF%BC%E5%87%BA.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/gcode_upload.png" width="80" height="80" alt="G-code upload"> | G-code upload<br>G-code上传 | Saves G-code locally and uploads it to a Moonraker device without starting a print. | [.py](%E6%BA%90%E7%A0%81/GCode/gcode_upload.py) · [.ghuser](UserObjects/GCode/NEWPATH_G-code%E4%B8%8A%E4%BC%A0.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/readme/path_gcode.png" width="80" height="80" alt="Path G-code"> | Path G-code<br>Path G-code生成 | Generates printing commands from continuous paths, speed, temperature and extrusion settings. | [.py](%E6%BA%90%E7%A0%81/GCode/path_gcode.py) · [.ghuser](UserObjects/GCode/NEWPATH_Path%20G-code%E7%94%9F%E6%88%90.ghuser) |

## Developing components with the ghpython skill

This library was developed with assistance from the **`ghpython-component-workflow` skill** for Rhino 8 Grasshopper Python 3 components. The workflow maintains standalone Python source, iterates through a development loader, specifies typed ports and Item/List/Tree access, documents interfaces, validates changes and embeds standalone code in `.ghuser` files. The skill assists development and is not a runtime dependency.

Start from the matching file in [源码 (source)](%E6%BA%90%E7%A0%81). In Codex with the skill installed, invoke `ghpython-component-workflow` and provide the source and requested changes. The skill installer is not bundled here. Sources run inside Rhino 8 Python 3 Script, not as ordinary command-line Python programs. Editing an external `.py` does not update embedded `.ghuser` code; repackage and validate modified components.
