"""打印床

【功能】
按设备名称和长宽显示打印床，并输出世界水平基准面。

【输入端】
Device（通用对象；单项；可选）：任意中英文设备名称，仅用于床前文字显示。
Length（数值；单项；可选）：Y方向长度mm；默认256，与设备名称无关。
Width（数值；单项；可选）：X方向宽度mm；默认256，与设备名称无关。

【输出端】
BedPlane（平面；单项）：世界XY平面；接智能切片Planes，不含床尺寸。

【详细用法与约束】
用途：显示设备名称，生成世界XY基准面与加粗床框预览。
输入 Device / Item / object / 可选：任意中英文名称，仅作显示标签。
Length、Width / Item / number / 可选：Y、X方向尺寸mm，默认各256，与名称无关。
输出仅 BedPlane / Item：世界XY Plane，接智能切片Planes；Plane没有有限尺寸。
公差：不进行几何近似；仅允许毫米模型，非法输入清空输出和预览。
预览：3像素床框，Y负方向两行白色微软雅黑文字；不预览输出Plane。
床框与文字不受“仅预览选中对象”影响；仍遵守组件隐藏与全局关闭预览。
设备名称不选择G-code配置；尺寸始终由Length、Width独立决定。
"""

# Localized presentation only; keep required inputs, type hints and solver behavior.
def _newpath_zh_message(message):
    text = str(message)
    if any('\u4e00' <= char <= '\u9fff' for char in text):
        return text
    return '运行提示，请检查相关输入。原始信息：' + text


# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '按设备名称和长宽显示打印床，并输出世界水平基准面。'
    ghenv.Component.Tooltip = '按设备名称和长宽显示打印床，并输出世界水平基准面。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Device': '任意中英文设备名称，仅用于床前文字显示', 'Length': 'Y方向长度mm；默认256，与设备名称无关', 'Width': 'X方向宽度mm；默认256，与设备名称无关'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'BedPlane': '世界XY平面；接智能切片Planes，不含床尺寸'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description


import math

COMPONENT_MARKER = 'PrintBed:r4'
COMPONENT_MESSAGE = 'Print Bed\nNEWPATH · mm'
INPUT_SPECS = [
    ('Device', 'Device', '任意中英文设备名称，仅用于床前文字显示', 'item', 'object', True),
    ('Length', 'Length', 'Y方向长度mm；默认256，与设备名称无关', 'item', 'number', True),
    ('Width', 'Width', 'X方向宽度mm；默认256，与设备名称无关', 'item', 'number', True),
]
OUTPUT_SPECS = [('BedPlane', 'BedPlane', '世界XY平面；接智能切片Planes，不含床尺寸', 'item')]


def bed_dimensions(device=None, length=None, width=None):
    device = getattr(device, 'Value', device)
    name = ' '.join(str(device if device is not None else '').split())
    result = []
    for label, value in zip(('Length', 'Width'), (length, width)):
        value = 256.0 if value is None else value
        if value is None or isinstance(value, bool):
            raise ValueError(label + '需要正数毫米尺寸。')
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(label + '需要有限正数。')
        result.append(value)
    return name, result[0], result[1]


def preview_visible(component, document, canvas, enabled):
    return (enabled and component.OnPingDocument() == document
            and canvas is not None and canvas.Document == document
            and not component.Hidden and not component.Locked
            and str(document.PreviewMode) != 'Disabled')


def preview_lines(name, length, width):
    dimensions = '长 {:g} mm × 宽 {:g} mm'.format(length, width)
    return ['正在使用NEWPATH切片', '{}  {}'.format(name, dimensions) if name else dimensions]


def hide_plane_preview(component):
    # Hide only this component's standard output geometry preview; preserve Plane data and downstream wires.
    for param in component.Params.Output:
        if _port_name(param) == 'BedPlane':
            param.Hidden = True


def make_preview(component, length, width, name=''):
    import Rhino
    import Rhino.Geometry as rg
    import Grasshopper as gh
    from System.Drawing import Color

    class BedDisplay(Rhino.Display.DisplayConduit):
        def __init__(self):
            super().__init__()
            self.owner = component
            self.document = component.OnPingDocument()
            self.curve = rg.Rectangle3d(rg.Plane.WorldXY, width, length).ToNurbsCurve()
            self.height = min(length, width) * 0.025
            self.lines = preview_lines(name, length, width)
            self.height = min(self.height, width * 0.8 / max(map(len, self.lines)))
            self.text_planes = [rg.Plane(rg.Point3d(width * 0.1,
                                -self.height * (2.5 + index * 1.8), 0), rg.Vector3d.ZAxis)
                                for index in range(len(self.lines))]
            self.bounds = rg.BoundingBox(rg.Point3d(0, -self.height * (3 + len(self.lines) * 1.8), 0), rg.Point3d(width, length, 0))
            self.document.ObjectsDeleted += self.deleted
            gh.Instances.DocumentServer.DocumentRemoved += self.removed
            self.Enabled = True

        def dispose(self):
            if not self.Enabled:
                return
            self.Enabled = False
            self.document.ObjectsDeleted -= self.deleted
            gh.Instances.DocumentServer.DocumentRemoved -= self.removed
            self.curve.Dispose()

        def deleted(self, sender, args):
            if self.owner.OnPingDocument() is None or self.owner in list(args.Objects):
                self.dispose()

        def removed(self, sender, document):
            if document == self.document:
                self.dispose()

        def visible(self):
            return preview_visible(self.owner, self.document, gh.Instances.ActiveCanvas, self.Enabled)

        def report_error(self, error):
            if not getattr(self, 'reported', False):
                self.reported = True
                self.owner.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error,
                                             _newpath_zh_message('打印床预览失败：' + str(error)))

        def CalculateBoundingBox(self, event):
            try:
                if self.visible():
                    event.IncludeBoundingBox(self.bounds)
            except Exception as exc:
                self.report_error(exc)

        def PostDrawObjects(self, event):
            try:
                if self.visible():
                    event.Display.DrawCurve(self.curve, Color.FromArgb(70, 190, 180), 3)
                    for line, plane in zip(self.lines, self.text_planes):
                        event.Display.Draw3dText(line, Color.White,
                                                 plane, self.height, 'Microsoft YaHei')
            except Exception as exc:
                self.report_error(exc)

    return BedDisplay()


def run_component():
    import Rhino
    import Rhino.Geometry as rg
    import Grasshopper as gh
    import scriptcontext as sc
    component = ghenv.Component
    key = ('NEWPATH.PrintBed', str(component.InstanceGuid))
    old = sc.sticky.pop(key, None)
    if old is not None and old.Enabled:
        old.dispose()
    component.Message = COMPONENT_MESSAGE
    try:
        if not _ensure_ports(component):
            return None
        hide_plane_preview(component)
        document = Rhino.RhinoDoc.ActiveDoc
        if document is None or document.ModelUnitSystem != Rhino.UnitSystem.Millimeters:
            raise ValueError('请将Rhino模型单位设为毫米，再生成打印床。')
        name, length, width = bed_dimensions(globals().get('Device'), globals().get('Length'), globals().get('Width'))
        sc.sticky[key] = make_preview(component, length, width, name)
        component.Message = '{}\n{:g} × {:g} mm'.format(name or 'Print Bed', length, width)
        document.Views.Redraw()
        return rg.Plane.WorldXY
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))
        return None


def _port_name(param):
    return str(getattr(param, "VariableName", param.NickName))

def _canonical_port_name(param):
    name = _port_name(param)
    return globals().get("PORT_ALIASES", {}).get(name, name)

def _validate_port_specs():
    import re
    for specs, is_input in ((INPUT_SPECS, True), (OUTPUT_SPECS, False)):
        seen = set()
        for spec in specs:
            if len(spec) < (6 if is_input else 4):
                raise ValueError("端口规格不完整。")
            name = spec[0]
            if not isinstance(name, str) or re.fullmatch(r"[A-Z][A-Za-z0-9]*", name) is None:
                raise ValueError("端口须用首字母大写的简短英文名称：" + str(name))
            if name in seen or spec[1] != name:
                raise ValueError("端口重名或Name/NickName不一致：" + name)
            seen.add(name)
            if spec[3] not in ("item", "list", "tree"):
                raise ValueError("端口Access无效：" + name)
            if is_input and (spec[4] not in ("object", "number", "bool", "point", "interval")
                             or not isinstance(spec[5], bool)):
                raise ValueError("端口Type Hint/Optional无效：" + name)

def _ports_match(component):
    import Grasshopper as gh
    for ports, specs, is_input in ((component.Params.Input, INPUT_SPECS, True),
                                   (component.Params.Output, OUTPUT_SPECS, False)):
        if len(list(ports)) != len(specs):
            return False
        for param, spec in zip(ports, specs):
            if (_port_name(param) != spec[0] or param.Name != spec[0]
                    or param.NickName != spec[1]
                    or param.Access != getattr(gh.Kernel.GH_ParamAccess, spec[3])
                    or param.Optional != (spec[5] if is_input else False)):
                return False
    return True

def _port_has_data(param):
    persistent = getattr(param, "PersistentData", None)
    return bool(getattr(param, "SourceCount", 0)
                or getattr(getattr(param, "Recipients", None), "Count", 0)
                or (persistent is not None and getattr(persistent, "DataCount", 0)))

def _plan_port_side(ports, templates):
    existing = {}
    for param in ports:
        name = _canonical_port_name(param)
        if name in existing:
            raise ValueError("旧端口映射重名，停止迁移：" + name)
        existing[name] = param
    names = [_port_name(p) for p in templates]
    if len(set(names)) != len(names):
        raise ValueError("目标端口重名。")
    removed = [p for name, p in existing.items() if name not in names]
    for param in removed:
        if _port_has_data(param):
            raise ValueError("待删除端口仍有接线或持久数据，请保留旧组件并在空白组件加载：" + _port_name(param))
    return [existing.get(_port_name(p), p) for p in templates], removed

def _migrate_port_side(ports, templates, unregister, register):
    desired, removed = _plan_port_side(list(ports), templates)
    for param in removed:
        unregister(param, True)
    for index, (param, template) in enumerate(zip(desired, templates)):
        current = list(ports)
        if index >= len(current) or current[index] is not param:
            if any(p is param for p in current):
                unregister(param, False)
            if register(param, index) is False:
                raise ValueError("端口注册失败：" + _port_name(template))
        param.VariableName = template.VariableName
        param.Name, param.NickName = template.Name, template.NickName
        param.Description, param.Access = template.Description, template.Access
        param.Optional = template.Optional

def _apply_port_spec(param, spec, is_input):
    import Grasshopper as gh
    param.VariableName = param.Name = spec[0]
    param.NickName, param.Description = spec[1], spec[2]
    param.Access = getattr(gh.Kernel.GH_ParamAccess, spec[3])
    param.Optional = spec[5] if is_input else False
    if is_input:
        import System
        import clr
        if spec[4] in ("point", "interval"):
            import Rhino.Geometry as geometry
            hint = geometry.Point3d if spec[4] == "point" else geometry.Interval
        else:
            hint = {"object": System.Object, "number": System.Double, "bool": System.Boolean}[spec[4]]
        if param.TypeHints.Select(clr.GetClrType(hint)) is None:
            raise ValueError("无法设置Type Hint：" + spec[0])

def _port_stamp(component):
    return (tuple(tuple(s) for s in INPUT_SPECS), tuple(tuple(s) for s in OUTPUT_SPECS),
            tuple(str(getattr(p, "InstanceGuid", id(p)))
                  for p in list(component.Params.Input) + list(component.Params.Output)))

def _ensure_ports(component):
    return True

if 'ghenv' in globals():
    BedPlane = run_component()


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = '打印床'
    ghenv.Component.NickName = '打印床'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'Data'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '按设备名称和长宽显示打印床，并输出世界水平基准面。'
    ghenv.Component.Tooltip = '按设备名称和长宽显示打印床，并输出世界水平基准面。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Device': '任意中英文设备名称，仅用于床前文字显示', 'Length': 'Y方向长度mm；默认256，与设备名称无关', 'Width': 'X方向宽度mm；默认256，与设备名称无关'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'BedPlane': '世界XY平面；接智能切片Planes，不含床尺寸'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
