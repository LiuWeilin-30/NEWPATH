"""G-code导出

【功能】
分析打印时间和材料用量，并按按钮将指令保存到指定目录。

【输入端】
GCode（通用对象；数据树；必填）：按执行顺序排列的 G-code 文本树。
Folder（通用对象；单项；可选）：保存目录；Save 时必填。
Name（通用对象；单项；可选）：文件名前缀，默认 Print。
Density（数值；单项；可选）：材料密度 g/cm³，默认 1.04。
Save（布尔值；单项；可选）：按钮上升沿保存一次；序号按目录现有文件递增。

【输出端】
Report（通用对象；单项）：时间、长度、重量、物体数及保存结果。

【详细用法与约束】
G-code 分析导出：读取 Path G-code 文本树，估算时间和材料并安全保存。
输入：GCode(Tree/object)；Folder(Item/object)；Name(Item/object，默认 Print)；
      Density(Item/number，g/cm³，默认1.04)；Save(Item/bool，按钮上升沿保存)。
输出：Report(Item/object)，包含打印长度、重量、预计时间和保存路径。
重量按有 XYZ 的正向 E × G-code 头部 FilamentArea × Density / 1000；
无截面积元数据时重量显示未知。估时仅含运动及 G4 停留，不含加热、归零和加速度。
"""

# Localized presentation only; keep required inputs, type hints and solver behavior.
def _newpath_zh_message(message):
    text = str(message)
    if any('\u4e00' <= char <= '\u9fff' for char in text):
        return text
    return '运行提示，请检查相关输入。原始信息：' + text


# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '分析打印时间和材料用量，并按按钮将指令保存到指定目录。'
    ghenv.Component.Tooltip = '分析打印时间和材料用量，并按按钮将指令保存到指定目录。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'GCode': '按执行顺序排列的 G-code 文本树', 'Folder': '保存目录；Save 时必填', 'Name': '文件名前缀，默认 Print', 'Density': '材料密度 g/cm³，默认 1.04', 'Save': '按钮上升沿保存一次；序号按目录现有文件递增'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Report': '时间、长度、重量、物体数及保存结果'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description


import math
import os
import re

COMPONENT_MARKER = 'gcode_export:3'
COMPONENT_MESSAGE = 'G-code 分析与导出\n自动命名 · 安全保存'
INPUT_SPECS = [
    ('GCode', 'GCode', '按执行顺序排列的 G-code 文本树', 'tree', 'object', False),
    ('Folder', 'Folder', '保存目录；Save 时必填', 'item', 'object', True),
    ('Name', 'Name', '文件名前缀，默认 Print', 'item', 'object', True),
    ('Density', 'Density', '材料密度 g/cm³，默认 1.04', 'item', 'number', True),
    ('Save', 'Save', '按钮上升沿保存一次；序号按目录现有文件递增', 'item', 'bool', True),
]
OUTPUT_SPECS = [
    ('Report', 'Report', '时间、长度、重量、物体数及保存结果', 'item', 'object', False),
]
WORD = re.compile(r'([A-Z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))', re.I)
AREA = re.compile(r'^;\s*FilamentArea\s*:\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*mm2\s*$', re.I)


def flatten_gcode(tree):
    if tree is None:
        raise ValueError('缺少 GCode')
    if hasattr(tree, 'BranchCount') and hasattr(tree, 'Branch'):
        return [str(line) for i in range(tree.BranchCount) for line in tree.Branch(i)]
    if isinstance(tree, (list, tuple)):
        return [str(line) for branch in tree for line in (branch if isinstance(branch, (list, tuple)) else [branch])]
    raise ValueError('GCode 应为文本树')


def analyze(lines, density=1.04):
    density = float(density)
    if not math.isfinite(density) or density <= 0:
        raise ValueError('Density 必须为有限正数')
    pos = dict(X=0.0, Y=0.0, Z=0.0)
    epos, feed = 0.0, None
    xyz_relative = e_relative = False
    area = None
    seconds = length = extrusion = 0.0
    objects = None
    has_motion = False
    for raw in lines:
        raw = str(raw).strip()
        match = AREA.fullmatch(raw)
        if match:
            value = float(match.group(1))
            if not math.isfinite(value) or value <= 0 or (area is not None and area != value):
                raise ValueError('FilamentArea 元数据无效或冲突')
            area = value
        if raw.upper().startswith('M117'):
            match = re.search(r'\[(\d+)\s+Objects\]', raw, re.I)
            if match:
                objects = int(match.group(1))
        code = raw.split(';', 1)[0].strip().upper()
        if not code:
            continue
        words = WORD.findall(code)
        if not words:
            continue
        command = words[0][0] + str(int(float(words[0][1]))) if words[0][0] in 'GM' else ''
        values = {letter: float(value) for letter, value in words[1:]}
        if any(not math.isfinite(v) for v in values.values()):
            raise ValueError('G-code 包含非有限数值')
        if command == 'G90': xyz_relative = False
        elif command == 'G91': xyz_relative = True
        elif command == 'M82': e_relative = False
        elif command == 'M83': e_relative = True
        elif command == 'G92':
            for axis in pos:
                if axis in values: pos[axis] = values[axis]
            if 'E' in values: epos = values['E']
        elif command == 'G4':
            seconds += values.get('P', 0.0)/1000.0 + values.get('S', 0.0)
        elif command in ('G0', 'G1', 'G2', 'G3'):
            if 'F' in values: feed = values['F']
            target = {axis: (pos[axis]+values[axis] if xyz_relative else values[axis])
                      if axis in values else pos[axis] for axis in pos}
            has_xyz = any(axis in values for axis in pos)
            distance = math.dist(tuple(pos.values()), tuple(target.values())) if has_xyz else 0.0
            if command in ('G2', 'G3') and has_xyz:
                cx, cy = pos['X']+values.get('I', 0.0), pos['Y']+values.get('J', 0.0)
                radius = math.hypot(pos['X']-cx, pos['Y']-cy)
                if radius <= 0: raise ValueError('圆弧 I/J 半径无效')
                a0 = math.atan2(pos['Y']-cy, pos['X']-cx)
                a1 = math.atan2(target['Y']-cy, target['X']-cx)
                angle = (a1-a0) % (2*math.pi) if command == 'G3' else (a0-a1) % (2*math.pi)
                if angle < 1e-10: angle = 2*math.pi
                distance = math.hypot(radius*angle, target['Z']-pos['Z'])
            delta_e = 0.0
            if 'E' in values:
                delta_e = values['E'] if e_relative else values['E']-epos
                epos = epos+values['E'] if e_relative else values['E']
            if distance > 0 and (feed is None or feed <= 0):
                raise ValueError('运动指令缺少有效 F，无法估算时间')
            if has_xyz:
                has_motion = True
                if feed is not None and feed > 0: seconds += distance*60.0/feed
                if delta_e > 0:
                    length += distance
                    extrusion += delta_e
            elif 'E' in values and feed is not None and feed > 0:
                seconds += abs(delta_e)*60.0/feed
            pos = target
    if not has_motion:
        raise ValueError('GCode 没有可分析的运动指令')
    return dict(seconds=seconds, length=length, weight=(extrusion*area*density/1000.0 if area else None),
                area=area, objects=objects)


def duration_label(seconds):
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, second = divmod(remainder, 60)
    return '{:02d}h{:02d}m{:02d}s'.format(hours, minutes, second)


def save_gcode(lines, folder, name, seconds):
    folder = str(folder or '').strip()
    if not folder:
        raise ValueError('Folder 不能为空')
    folder = os.path.abspath(os.path.expanduser(folder))
    name = str(name or 'Print').strip()
    if not name or name in ('.', '..') or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith(('.', ' ')):
        raise ValueError('Name 含无效文件名字符')
    if not os.path.isdir(folder): os.makedirs(folder)
    duration = duration_label(seconds)
    # Include existing files from the former Name_Time_Sequence layout so numbering continues.
    current_pattern = re.compile(r'^'+re.escape(name)+r'_(\d+)_\d+h\d{2}m\d{2}s\.gcode$', re.I)
    former_pattern = re.compile(r'^'+re.escape(name)+r'_\d+h\d{2}m\d{2}s_(\d+)\.gcode$', re.I)
    used = [int(match.group(1)) for item in os.listdir(folder)
            for match in [current_pattern.fullmatch(item) or former_pattern.fullmatch(item)] if match]
    index = max(used, default=0)+1
    while True:
        path = os.path.join(folder, '{}_{:03d}_{}.gcode'.format(name, index, duration))
        try:
            with open(path, 'x', encoding='utf-8', newline='\n') as stream:
                stream.write('\n'.join(lines)+'\n')
            return path
        except FileExistsError:
            index += 1


def _port_name(param):
    return str(getattr(param, 'VariableName', param.NickName))


def _ensure_ports(component):
    return True


def run_component():
    import Grasshopper as gh
    component = ghenv.Component
    component.Name, component.NickName = 'G-code 分析导出', 'GCodeExport'
    component.Message = COMPONENT_MESSAGE
    saved = ''
    try:
        if not _ensure_ports(component): return '等待端口配置'
        lines = flatten_gcode(globals().get('GCode'))
        density = globals().get('Density')
        info = analyze(lines, 1.04 if density is None else density)
        key = str(getattr(component, 'InstanceGuid', id(component)))
        states = globals().setdefault('_EXPORT_SAVE_STATES', {})
        pressed = bool(globals().get('Save'))
        rising = pressed and not states.get(key, False)
        states[key] = pressed
        if rising:
            saved = save_gcode(lines, globals().get('Folder'), globals().get('Name'), info['seconds'])
        weight = '{:.2f} g'.format(info['weight']) if info['weight'] is not None else '未知（G-code 无 FilamentArea）'
        report = '打印长度: {:.2f} mm\n重量: {}\n预计时间: {}\n物体数: {}'.format(
            info['length'], weight, duration_label(info['seconds']),
            info['objects'] if info['objects'] is not None else '未知')
        if saved: report += '\n已保存: '+saved
        return report
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))
        return '分析或保存失败: '+str(exc)


if 'ghenv' in globals():
    Report = run_component()


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = 'G-code导出'
    ghenv.Component.NickName = 'G-code导出'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'GCode'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '分析打印时间和材料用量，并按按钮将指令保存到指定目录。'
    ghenv.Component.Tooltip = '分析打印时间和材料用量，并按按钮将指令保存到指定目录。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'GCode': '按执行顺序排列的 G-code 文本树', 'Folder': '保存目录；Save 时必填', 'Name': '文件名前缀，默认 Print', 'Density': '材料密度 g/cm³，默认 1.04', 'Save': '按钮上升沿保存一次；序号按目录现有文件递增'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Report': '时间、长度、重量、物体数及保存结果'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
