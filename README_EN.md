# NEWPATH

[中文](README.md) | **English**

A Grasshopper component library for parametric toolpaths in large-format pellet 3D printing. **1.0.2 · 25 components · Rhino 8 / Python 3**

Build an inspectable workflow from slicing and path editing to continuous paths, extrusion previews and G-code generation.

> **Hardware testing: tested only on a Jinshi (金石) FGF 1800 large-format pellet 3D printer. Not tested on robotic-arm 3D printing systems or desktop high-speed 3D printers such as Bambu Lab. Compatibility with other machines has not been established.**

## Core value

- **Keep geometry and process data together:** PATH packets carry curves, local heights, bead widths and motion types through editing and G-code generation.
- **Control large-format toolpaths:** combine grouping, continuous paths, double walls, end bearings, structural ribs and bottom infill to suit the design.
- **Inspect before output:** use print-bed, unpacking, pointwise-motion and extrusion-mesh components to examine geometry and path data.
- **Ready to use:** `.ghuser` files embed their code and icons. This repository does not distribute standalone Python source files.

## Version 1.0.2

Released on 2026-10-10. Compared with 1.0.1, this release replaces the icons of all 25 components with a coordinated color-block set. Code, ports, names, categories and component identities are unchanged. High-resolution transparent 512×512 PNG icons are included.

## Download and installation

1. Install **Rhino 8** with its **Grasshopper Python 3 Script** runtime.
2. Select **Code → Download ZIP** on GitHub and extract the repository.
3. Open Grasshopper → **File → Special Folders → User Objects Folder** and copy all **25 `.ghuser` files** from [UserObjects](UserObjects) into it.
4. Back up and remove older NEWPATH components before upgrading, including duplicates in subfolders. Some old files have been renamed, so replacing matching filenames alone can leave obsolete components installed.
5. Save your work and fully restart Rhino. Place fresh components from **NEWPATH → 1 Slice / 2 Path Edit / 3 Path Assembly / 4 Export / 5 Analyze / 6 Utilities**.

Each `.ghuser` embeds its code and icon, with no dependency on external `.py` files, repository paths or the development skill. Existing canvas instances do not update automatically; place new instances and reconnect them. Component names and descriptions are Chinese, while port identifiers and categories are English.

## Repository structure

```text
NEWPATH/
├── UserObjects/    # 25 .ghuser
├── 图标/           # 25 PNG, 512 × 512
├── .gitignore
├── LICENSE
├── README.md
└── README_EN.md
```

## Workflow

Typical closed-contour workflow: **Smart slicer → Path grouping → optional infill/wall operations → Continuous path → optional PATH to G2/G3 → Path G-code → G-code export / upload**.

Use Open continuous path for suitable open contours, or UV slicer for surface-parameter slicing. These are branches selected according to each component's input contract, not interchangeable steps. Connect **Path** outputs between processing nodes. `Preview` geometry does not replace an attributed PATH packet. Slicing produces slices and pointwise attributes; grouping handles print blocks.

G-code generation requires millimetre paths. Check model units, machine travel, temperature, speed and extrusion settings before output. The current source uses machine-related extrusion conversion (a fixed `3.67 mm²` filament-area value) and fan ranges; defaults are not universal printer profiles. Arc output supports XY-plane G2/G3 and requires compatible firmware. Upload uses Moonraker: explicitly set your own `Host` (the built-in default is `http://192.168.1.114`) and `Key` if needed. Uploading does not start printing. Time estimates exclude heating, homing and acceleration. Consult component tooltips and embedded code headers for complete parameter details.


## All components

Icons are displayed at 80×80; original files are transparent 512×512 PNGs. File links open the corresponding components.

### 1 Slice

| Icon | Component | Purpose | File |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/uv_slicer_path.png" width="80" height="80" alt="UV slicer"> | UV slicer<br>UV方向切片 | Creates conformal slices along a surface parameter direction with local heights and growth directions. | [.ghuser](UserObjects/NEWPATH_UV%E6%96%B9%E5%90%91%E5%88%87%E7%89%87.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/smart_slicer_path.png" width="80" height="80" alt="Smart slicer"> | Smart slicer<br>智能切片 | Slices geometry using layer heights and end planes, retaining bead width and pointwise attributes. | [.ghuser](UserObjects/NEWPATH_%E6%99%BA%E8%83%BD%E5%88%87%E7%89%87.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/pack_path.png" width="80" height="80" alt="Pack Path"> | Pack Path<br>打包Path | Packs hand-drawn parallel layer curves into Path data while retaining actual layer heights and provenance. | [.ghuser](UserObjects/NEWPATH_%E6%89%93%E5%8C%85Path.ghuser) |

### 2 Path Edit

| Icon | Component | Purpose | File |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/double_wall_path.png" width="80" height="80" alt="Double wall"> | Double wall<br>双层墙路径 | Offsets closed wall curves inward and creates double-wall paths at selected connection points. | [.ghuser](UserObjects/NEWPATH_%E5%8F%8C%E5%B1%82%E5%A2%99%E8%B7%AF%E5%BE%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/surface_rib_path.png" width="80" height="80" alt="Double-track rib"> | Double-track rib<br>双道结构肋 | Creates internal return-path ribs from control points, layer ranges and overhang angles. | [.ghuser](UserObjects/NEWPATH_%E5%8F%8C%E9%81%93%E7%BB%93%E6%9E%84%E8%82%8B.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/brim_skirt_path.png" width="80" height="80" alt="Brim"> | Brim<br>生成brim | Adds outward brim loops around ground-level contours while retaining model paths. | [.ghuser](UserObjects/NEWPATH_%E7%94%9F%E6%88%90brim.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/skirt_path.png" width="80" height="80" alt="Surrounding brim"> | Surrounding brim<br>生成包围brim | Creates a closed surrounding brim at the bottom of all models. | [.ghuser](UserObjects/NEWPATH_%E7%94%9F%E6%88%90%E5%8C%85%E5%9B%B4brim.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/end_offset_path.png" width="80" height="80" alt="End offset"> | End offset<br>端部偏移 | Offsets wall ends inward or outward progressively over selected layers. | [.ghuser](UserObjects/NEWPATH_%E7%AB%AF%E9%83%A8%E5%81%8F%E7%A7%BB.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/end_bearing_path.png" width="80" height="80" alt="End bearing"> | End bearing<br>端部承托 | Thickens double walls progressively over selected end layers to form bearing surfaces. | [.ghuser](UserObjects/NEWPATH_%E7%AB%AF%E9%83%A8%E6%89%BF%E6%89%98.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/bottom_hatch_fill.png" width="80" height="80" alt="Zigzag infill"> | Zigzag infill<br>蛇形填充 | Adds bottom zigzag infill over a selected layer range with configurable spacing. | [.ghuser](UserObjects/NEWPATH_%E8%9B%87%E5%BD%A2%E5%A1%AB%E5%85%85.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/spiral_infill_path.png" width="80" height="80" alt="Spiral infill"> | Spiral infill<br>螺旋填充 | Adds spiral infill to selected layers of the first model group, retaining all original paths. | [.ghuser](UserObjects/NEWPATH_%E8%9E%BA%E6%97%8B%E5%A1%AB%E5%85%85.ghuser) |

### 3 Path Assembly

| Icon | Component | Purpose | File |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/open_continuous_path.png" width="80" height="80" alt="Open continuous path"> | Open continuous path<br>开放连续路径 | Alternates the direction of open curves across layers to form a back-and-forth path. | [.ghuser](UserObjects/NEWPATH_%E5%BC%80%E6%94%BE%E8%BF%9E%E7%BB%AD%E8%B7%AF%E5%BE%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/path_grouping.png" width="80" height="80" alt="Path grouping"> | Path grouping<br>路径分组 | Divides paths into print blocks by height, with optional block-color previews. | [.ghuser](UserObjects/NEWPATH_%E8%B7%AF%E5%BE%84%E5%88%86%E7%BB%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/continuous_path.png" width="80" height="80" alt="Continuous path"> | Continuous path<br>连续路径 | Adjusts closed-loop seams using guide points and connects slices within each group. | [.ghuser](UserObjects/NEWPATH_%E8%BF%9E%E7%BB%AD%E8%B7%AF%E5%BE%84.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/curve_arc_classifier.png" width="80" height="80" alt="PATH to G2/G3"> | PATH to G2/G3<br>PATH转G2 G3 | Fits lines and arcs, rebuilding paths within tolerance and segment-length limits. | [.ghuser](UserObjects/NEWPATH_PATH%E8%BD%ACG2%20G3.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/path_to_g1.png" width="80" height="80" alt="PATH to G1"> | PATH to G1<br>PATH转G1 | Discretizes Path curves by length or feature sampling into G1 paths while preserving pointwise attributes. | [.ghuser](UserObjects/NEWPATH_PATH%E8%BD%ACG1.ghuser) |

### 4 Export

| Icon | Component | Purpose | File |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/gcode_upload.png" width="80" height="80" alt="G-code upload"> | G-code upload<br>G-code上传 | Saves G-code locally and uploads it to a Moonraker device without starting a print. | [.ghuser](UserObjects/NEWPATH_G-code%E4%B8%8A%E4%BC%A0.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/gcode_export.png" width="80" height="80" alt="G-code export"> | G-code export<br>G-code导出 | Estimates print time and material usage, and saves G-code on a button trigger. | [.ghuser](UserObjects/NEWPATH_G-code%E5%AF%BC%E5%87%BA.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/path_gcode.png" width="80" height="80" alt="Path G-code"> | Path G-code<br>Path G-code生成 | Generates printing commands from continuous paths, speed, temperature and extrusion settings. | [.ghuser](UserObjects/NEWPATH_Path%20G-code%E7%94%9F%E6%88%90.ghuser) |

### 5 Analyze

| Icon | Component | Purpose | File |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/gcode_motion.png" width="80" height="80" alt="Pointwise motion"> | Pointwise motion<br>Path逐点运动 | Extracts original path points, indices and incoming motion types. | [.ghuser](UserObjects/NEWPATH_Path%E9%80%90%E7%82%B9%E8%BF%90%E5%8A%A8.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/unpack_path.png" width="80" height="80" alt="Unpack Path"> | Unpack Path<br>解包Path | Extracts layer planes, curves, nominal layer heights and original point sequences. | [.ghuser](UserObjects/NEWPATH_%E8%A7%A3%E5%8C%85Path.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/rounded_mesh_pipe.png" width="80" height="80" alt="Path preview"> | Path preview<br>路径预览 | Previews extrusion meshes using pointwise Path widths and heights. | [.ghuser](UserObjects/NEWPATH_%E8%B7%AF%E5%BE%84%E9%A2%84%E8%A7%88.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/Gcode%E8%BF%90%E5%8A%A8%E6%A8%A1%E6%8B%9F_gcode_simulator.png" width="80" height="80" alt="G-code motion simulator"> | G-code motion simulator<br>Gcode运动模拟 | Parses G-code, animates print-head motion according to speed and displays the travelled path. | [.ghuser](UserObjects/NEWPATH_Gcode%E8%BF%90%E5%8A%A8%E6%A8%A1%E6%8B%9F.ghuser) |

### 6 Utilities

| Icon | Component | Purpose | File |
|---|---|---|---|
| <img src="%E5%9B%BE%E6%A0%87/print_bed.png" width="80" height="80" alt="Print bed"> | Print bed<br>打印床 | Displays a named print bed of a given size and outputs the world-horizontal reference plane. | [.ghuser](UserObjects/NEWPATH_%E6%89%93%E5%8D%B0%E5%BA%8A.ghuser) |
| <img src="%E5%9B%BE%E6%A0%87/%E6%B0%B4%E5%B9%B3%E5%81%8F%E7%A7%BB%E6%9B%B2%E9%9D%A2_horizontal_offset_surface.png" width="80" height="80" alt="Horizontal surface offset"> | Horizontal surface offset<br>水平偏移曲面 | Offsets surfaces by a horizontal distance and optionally closes them into walls. | [.ghuser](UserObjects/NEWPATH_%E6%B0%B4%E5%B9%B3%E5%81%8F%E7%A7%BB%E6%9B%B2%E9%9D%A2.ghuser) |

## Validation and license

This release passed offline ghuser archive readback, icon-pixel and file-hash checks. Live Grasshopper solving of fresh instances was not performed for this release. Historical hardware testing is described above.

Distributed under the [MIT License](LICENSE). Standalone `.py` source files are not included; embedded code inside `.ghuser` files is required for the components to run.
