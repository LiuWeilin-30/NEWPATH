"""Path G-code生成

【功能】
根据连续路径及速度、温度和挤出参数生成打印指令。

【输入端】
Path（通用对象；数据树；必填）：含显式MotionKind的连续路径数据包。
FlowMultiplier（数值；单项；可选）：挤出倍率，默认1。
TravelSpeed（数值；单项；可选）：空走速度 mm/s，默认80。
PrintSpeed（数值；单项；可选）：打印速度 mm/s，默认20。
FirstLayerSpeed（数值；单项；可选）：首层速度 mm/s，默认10。
FanSpeed（数值；单项；可选）：非首层风扇0–400，乘2.55输出，默认100。
FirstLayerFanSpeed（数值；单项；可选）：首层风扇0–400，乘2.55输出，默认100。
ZHopHeight（数值；单项；可选）：世界Z抬升 mm，默认5。
RetractDist（数值；单项；可选）：回抽 mm，默认6。
PrimeDist（数值；单项；可选）：补料 mm，默认等于回抽。
RetractSpeed（数值；单项；可选）：回抽及原位补料速度 mm/s，默认25。
StartPause（数值；单项；可选）：每条路径开始停留秒数，默认0。
TopWipeDistance（数值；单项；可选）：闭合wall末端沿原方向空走距离mm，默认0。
BedTemp（数值；单项；可选）：热床温度摄氏度，0不设置。
Temperatures（通用对象；单项；可选）：T0至T2温度Panel文本，例如190 190 170；单值仅T0；空不设置。

【输出端】
GCode（通用对象；数据树）：按执行顺序排列的G-code文本树。

【详细用法与约束】
Path G-code：continuous毫米Path → GCode文本树。
宽度、终点局部高度、首层及运动类型来自Path；输入端口及默认值见上方输入说明。
逐Chunk执行：安全Z定位、独立原位补料、开始停留、打印、回抽；闭合wall末端可沿原方向空走擦拭。
跨路径安全Z=max(当前Z,目标Z)+ZHopHeight；首次定位先到目标Z+抬升量。
空驶G0无E；打印G1，自动识别段/块ArcSegments或几何Curve输出XY圆弧G2/G3，I/J为起点到圆心偏移。
圆弧端点须与Points保持对应；支持XY平面G2/G3，空间圆弧自动降级或报错。
E=实际直线/圆弧长度×终点Heights×段Width/3.67×FlowMultiplier；头部传递耗材截面积。
IsConnection连接段挤出量在上述计算基础上乘0.5；普通打印段保持原量。
TopWipeDistance仅作用于末端闭合wall轮廓，默认0mm；使用TravelSpeed空驶速度，不挤出；仅标注擦拭起止。
首层和非首层分别使用两档风扇值；停留默认0秒；Temperatures接受T0至T2顺序的Panel文本，未输入不设置热端温度。
两档风扇输入0–400、默认100，乘2.55输出；ZHopHeight默认5mm。
XYZ/E/F两位、I/J六位；数据错误清空整次输出。
"""

# Localized presentation only; keep required inputs, type hints and solver behavior.
def _newpath_zh_message(message):
    text = str(message)
    if any('\u4e00' <= char <= '\u9fff' for char in text):
        return text
    return '运行提示，请检查相关输入。原始信息：' + text


# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '根据连续路径及速度、温度和挤出参数生成打印指令。'
    ghenv.Component.Tooltip = '根据连续路径及速度、温度和挤出参数生成打印指令。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '含显式MotionKind的连续路径数据包', 'FlowMultiplier': '挤出倍率，默认1', 'TravelSpeed': '空走速度 mm/s，默认80', 'PrintSpeed': '打印速度 mm/s，默认20', 'FirstLayerSpeed': '首层速度 mm/s，默认10', 'FanSpeed': '非首层风扇0–400，乘2.55输出，默认100', 'FirstLayerFanSpeed': '首层风扇0–400，乘2.55输出，默认100', 'ZHopHeight': '世界Z抬升 mm，默认5', 'RetractDist': '回抽 mm，默认6', 'PrimeDist': '补料 mm，默认等于回抽', 'RetractSpeed': '回抽及原位补料速度 mm/s，默认25', 'StartPause': '每条路径开始停留秒数，默认0', 'TopWipeDistance': '闭合wall末端沿原方向空走距离mm，默认0', 'BedTemp': '热床温度摄氏度，0不设置', 'Temperatures': 'T0至T2温度Panel文本，例如190 190 170；单值仅T0；空不设置'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'GCode': '按执行顺序排列的G-code文本树'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description



PORT_ALIASES = {'Paths': 'Path'}
import math

COMPONENT_MARKER = 'path_gcode:20'
COMPONENT_MESSAGE = "Path → G-code\n逐点高度 · 分段速度"
FILAMENT_AREA = 3.67  # mm²; keep consistent with header metadata and E conversion.
DEFAULTS = dict(FlowMultiplier=1.0, TravelSpeed=80.0, PrintSpeed=20.0,
                FirstLayerSpeed=10.0, FanSpeed=100.0, FirstLayerFanSpeed=100.0, ZHopHeight=5.0,
                RetractDist=6.0, RetractSpeed=25.0, StartPause=0.0, TopWipeDistance=0.0,
                BedTemp=0.0)
INPUT_SPECS = [("Path", "Path", '含显式MotionKind的连续路径数据包', "tree", "object", False)]
INPUT_SPECS += [(name, name, description, "item", "number", True) for name, description in (
    ("FlowMultiplier", '挤出倍率，默认1'), ("TravelSpeed", '空走速度 mm/s，默认80'),
    ("PrintSpeed", '打印速度 mm/s，默认20'), ("FirstLayerSpeed", '首层速度 mm/s，默认10'),
    ("FanSpeed", '非首层风扇0–400，乘2.55输出，默认100'),
    ("FirstLayerFanSpeed", '首层风扇0–400，乘2.55输出，默认100'),
    ("ZHopHeight", '世界Z抬升 mm，默认5'),
    ("RetractDist", '回抽 mm，默认6'), ("PrimeDist", '补料 mm，默认等于回抽'))]
INPUT_SPECS += [(name, name, description, "item", "number", True) for name, description in (
    ("RetractSpeed", '回抽及原位补料速度 mm/s，默认25'),
    ("StartPause", '每条路径开始停留秒数，默认0'),
    ("TopWipeDistance", '闭合wall末端沿原方向空走距离mm，默认0'),
    ("BedTemp", '热床温度摄氏度，0不设置'))]
INPUT_SPECS += [("Temperatures", "Temperatures", 'T0至T2温度Panel文本，例如190 190 170；单值仅T0；空不设置', "item", "object", True)]
OUTPUT_SPECS = [("GCode", "GCode", '按执行顺序排列的G-code文本树', "tree", "object", False)]


def _has_wall(packets):
    """Editors require explicit purposes; never infer a missing role from geometry."""
    found = False
    for _, _, data in packets:
        for chunk in data.Chunks:
            for record in chunk.get('Segments', ()):
                if record.get('Role') not in ('wall', 'brim', 'infill'):
                    raise ValueError('编辑入口要求wall/brim/infill标记，请重新计算上游。')
                found = found or record['Role'] == 'wall'
    return found

def _passthrough_path(packets):
    """No target: retain full records/chunks, namespace only multiple input packets."""
    if len({p.Stage for _, _, p in packets}) != 1 or len({str(p.Metadata.get('Units')) for _, _, p in packets}) != 1:
        raise ValueError('透传的多个Path阶段或单位不一致。')
    chunks, empty = [], []
    for wire, item, data in packets:
        prefix = wire+(item,) if len(packets) > 1 else ()
        chunks.extend(dict(c, TreePath=prefix+tuple(c['TreePath'])) for c in data.Chunks)
        empty.extend(prefix+tuple(p) for p in data.Metadata.get('EmptyPaths', ()))
    meta = dict(packets[0][2].Metadata)
    if len(packets) > 1:
        meta.update(Inputs=tuple(dict(p.Metadata) for _, _, p in packets), EmptyPaths=tuple(empty))
    result = _PathData(packets[0][2].Stage, chunks, meta)
    _validate_path_contract(result)
    return result, ['没有wall目标，输入路径完整透传。']

def _refresh_role_chunk(chunk, records):
    """Rebuild edited sliced summaries without modifying any retained segment."""
    records = tuple(records)
    chunk.update(Segments=records, Curves=tuple(r['Curve'] for r in records))
    for key in ('Points', 'Heights', 'GrowthVectors'):
        if all(key in r for r in records):
            chunk[key] = tuple(v for r in records for v in r[key])
        else:
            chunk.pop(key, None)
    for key in ('SampleParameters', 'SampleCurve', 'PointRange', 'ArcSegments'):
        chunk.pop(key, None)
    roles = {r.get('Role') for r in records}
    if len(roles) != 1:
        for key in ('Role', 'PathGroupRole', 'PathGroupId'):
            chunk.pop(key, None)
    walls = [r for r in records if r.get('Role') == 'wall']
    if walls:
        counts = {r.get('WallCount', 1) for r in walls}
        chunk['WallCount'] = next(iter(counts)) if len(counts) == 1 else None
    return chunk

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

def _validate_path_contract(data):
    """Read-only minimum contract; geometric and machine capabilities remain consumer checks."""
    import math
    from collections.abc import Mapping
    def fail(where, message):
        raise ValueError(where + ": " + message)
    def number(value, where, positive=False):
        if isinstance(value, (bool, str, bytes)):
            fail(where, "必须为有限数值，不能使用布尔值或文本。")
        try:
            valid = math.isfinite(value) and (not positive or value > 0)
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            fail(where, "必须为有限正数。" if positive else "必须为有限数值。")
    def record_fields(record, where, summary=False):
        for field in ("Width", "LayerHeight"):
            if field in record and not (summary and record[field] is None):
                number(record[field], where+"."+field, True)
        if "LayerIndex" in record:
            value = record["LayerIndex"]
            if not (value is None and record.get("IsConnection") is True):
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    fail(where+".LayerIndex", "真实层号必须为非负整数；仅IsConnection连接可为None。")
        if "IsFirstLayer" in record and not isinstance(record["IsFirstLayer"], bool):
            fail(where+".IsFirstLayer", "必须为布尔值。")
        if "MotionKind" in record and record["MotionKind"] not in ("extrude", "travel"):
            fail(where+".MotionKind", "只接受extrude或travel。")
        if "IsConnection" in record and not isinstance(record["IsConnection"], bool):
            fail(where+".IsConnection", "必须为布尔值。")
        for role_field in ("Role", "FromRole", "ToRole"):
            if role_field in record and record[role_field] not in ("wall", "brim", "infill"):
                fail(where+"."+role_field, "仅接受wall/brim/infill；旧数据请重新计算上游。")
        if record.get("IsConnection"):
            if "MotionKind" in record and record.get("MotionKind") not in ("extrude", "travel"):
                fail(where+".MotionKind", "连接必须明确挤出或空走。")
            if record.get("MotionKind") == "extrude" and record.get("Role") not in ("wall", "brim", "infill"):
                fail(where+".Role", "挤出连接必须继承用途。")
            if "FromRole" in record and "ToRole" in record:
                if record["FromRole"] != record["ToRole"]:
                    if record.get("MotionKind") != "travel" or "Role" in record:
                        fail(where+".Role", "跨用途连接只能空走，不能覆盖两端用途。")
                elif "Role" in record and record["Role"] != record["ToRole"]:
                    fail(where+".Role", "同类连接必须继承两端用途。")
        for field in ("Role", "HeightMetric", "Sampling"):
            if field in record and (not isinstance(record[field], str) or not record[field].strip()):
                fail(where+"."+field, "必须为非空语义标识；支持范围由消费端判断。")
        for field in ("Points", "Heights", "GrowthVectors", "SampleParameters"):
            if field not in record:
                continue
            values = record[field]
            if not isinstance(values, (tuple, list)):
                fail(where+"."+field, "必须为有序序列。")
            if field != "Points":
                if "Points" not in record:
                    fail(where+"."+field, "逐点属性必须有同记录Points。")
                if len(values) != len(record["Points"]):
                    fail(where+"."+field, "与Points数量不对应。")
            if field in ("Heights", "SampleParameters"):
                for index, value in enumerate(values):
                    number(value, where+"."+field+"["+str(index)+"]", field == "Heights")
            # Geometry object type/validity and unit-vector tests belong to Rhino consumers.
    if not isinstance(getattr(data, "Metadata", None), Mapping):
        fail("Path.Metadata", "必须为映射。")
    record_fields(data.Metadata, "Path.Metadata", summary=True)
    if "Tolerance" in data.Metadata:
        number(data.Metadata["Tolerance"], "Path.Metadata.Tolerance", True)
    chunks = getattr(data, "Chunks", None)
    if not isinstance(chunks, (tuple, list)):
        fail("Path.Chunks", "必须为有序序列。")
    seen = set()
    for ci, chunk in enumerate(chunks):
        where = "Path.Chunks["+str(ci)+"]"
        if not isinstance(chunk, Mapping):
            fail(where, "块必须为映射。")
        path = chunk.get("TreePath")
        if (not isinstance(path, (tuple, list)) or any(not isinstance(v, int) or isinstance(v, bool) for v in path)):
            fail(where+".TreePath", "必须为整数序列。")
        if tuple(path) in seen:
            fail(where+".TreePath", "包内重复TreePath。")
        seen.add(tuple(path))
        segments = chunk.get("Segments")
        if segments is None and "Segments" not in chunk and chunk.get("Curves") in ((), []):
            segments = ()
        if not isinstance(segments, (tuple, list)):
            fail(where+".Segments", "必须为有序段映射序列。")
        record_fields(chunk, where, summary=True)
        for si, record in enumerate(segments):
            loc = where+".Segments["+str(si)+"]"
            if not isinstance(record, Mapping):
                fail(loc, "段必须为映射。")
            record_fields(record, loc)
            if "PointRange" in record:
                bounds = record["PointRange"]
                if (not isinstance(bounds, (tuple, list)) or len(bounds) != 2
                        or any(not isinstance(v, int) or isinstance(v, bool) for v in bounds)
                        or not 0 <= bounds[0] < bounds[1]):
                    fail(loc+".PointRange", "必须为非空左闭右开整数范围。")
                if "Points" in chunk and bounds[1] > len(chunk["Points"]):
                    fail(loc+".PointRange", "超出块Points范围。")
                if "Points" in record and bounds[1]-bounds[0] != len(record["Points"]):
                    fail(loc+".PointRange", "范围长度与段Points不对应。")
    return data

def _unwrap_path(value):
    for _ in range(8):
        if getattr(value, "Protocol", None) == "codex.ghpython.Path":
            break
        other = getattr(value, "Value", value)
        if other is value:
            break
        value = other
    if getattr(value, "Protocol", None) != "codex.ghpython.Path":
        raise ValueError("请接完整Path，不能连接Preview曲线或Panel文本。")
    if type(getattr(value, "SchemaVersion", None)) is not int or value.SchemaVersion != 1:
        raise ValueError("Path结构版本不兼容，请更新上下游。")
    if getattr(value, "Stage", None) not in ("sliced", "continuous"):
        raise ValueError("Path阶段标识无效。")
    return _validate_path_contract(value)

def positive(value, name, allow_zero=False):
    value = float(value)
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        raise ValueError(name + " 必须是有限" + ("非负数" if allow_zero else "正数"))
    return value


def unwrap(value):
    value = _unwrap_path(value)
    if value.Stage != "continuous":
        raise ValueError("仅接受continuous Path，请先连接连续路径电池")
    if str(value.Metadata.get("Units")) != "Millimeters":
        raise ValueError("G21要求毫米，请在上游统一模型单位后重算")
    return value


def prepare(packets, wipe_distance=0):
    'Each packets element is (input tree path, item index, packet); preserve upstream order.'
    result = []
    for wire, item, raw in packets:
        data = unwrap(raw)
        seen = set()
        for chunk in data.Chunks:
            key = tuple(chunk["TreePath"])
            if key in seen:
                raise ValueError("包内重复TreePath")
            seen.add(key)
            if chunk.get("Sampling") != "segment_endpoints":
                raise ValueError("需要最新端点采样包，请重算上游并点击Run")
            pts = tuple(tuple(float(getattr(p, a)) for a in "XYZ") for p in chunk["Points"])
            hs = tuple(positive(h, "Heights") for h in chunk["Heights"])
            if len(pts) != len(hs) or any(not math.isfinite(v) for p in pts for v in p):
                raise ValueError("Points/Heights数量不匹配或坐标无效")
            if not pts:
                if chunk.get("Segments") or chunk.get("Curves"):
                    raise ValueError("非空分块缺少点")
                continue
            if len(pts) < 2:
                raise ValueError("打印分块至少需要两个点")
            base_width = chunk.get("Width")
            records = tuple(chunk["Segments"])
            layers = {}
            edges = [None] * (len(pts)-1)
            for s in records:
                if s.get("MotionKind") not in ("extrude", "travel"):
                    raise ValueError("段缺少有效MotionKind，请重算连续路径并点击Run")
                if s.get("IsConnection") is True:
                    continue
                if s.get("HeightMetric") != "current_layer_normal":
                    raise ValueError("需要当前层法线高度数据，请重算上游并点击Run")
                if not isinstance(s.get("IsFirstLayer"), bool) or s.get("LayerIndex") is None:
                    raise ValueError("层段缺少首层标识或真实层号")
                sid = s["SegmentId"]
                if sid in layers:
                    raise ValueError("重复SegmentId")
                layers[sid] = s
            chunk_arcs = chunk.get("ArcSegments")
            for s in records:
                start, end = s["PointRange"]
                if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(pts):
                    raise ValueError("PointRange无效（结束索引不包含）")
                owner = s
                if s.get("IsConnection") is True:
                    owner = layers.get(s.get("ToSegmentId"))
                    if owner is None or s.get("FromSegmentId") not in layers or end-start != 2:
                        raise ValueError("connection来源/目标段或范围无效")
                motion = s["MotionKind"]
                if motion == "travel":
                    state = (motion, None, None)
                else:
                    if s.get("HeightMetric") != "current_layer_normal" or not isinstance(s.get("IsFirstLayer"), bool):
                        raise ValueError("挤出段必须显式提供HeightMetric和IsFirstLayer")
                    width = s.get("Width") if s.get("IsConnection") is True else s.get("Width", base_width)
                    if width is None:
                        raise ValueError("挤出段缺少显式Width，请重算连续路径")
                    state = (motion, s["IsFirstLayer"], positive(width, "段Width"))
                edge_count = end - start - 1
                parts = s.get("ArcSegments")
                if parts is None and chunk_arcs is not None and len(chunk_arcs) == len(pts) - 1:
                    parts = chunk_arcs[start:end-1]
                if parts is None and not s.get("IsConnection"):
                    crv = s.get("Curve")
                    if crv is not None:
                        parts = _extract_curve_parts(crv, chunk['Points'][start:end], edge_count)
                if parts is not None and len(parts) != edge_count:
                    raise ValueError("ArcSegments数量与运动边不对应")
                if parts is not None and any(part is None for part in parts):
                    raise ValueError("已有几何提取失败：运动边图元为空，请重算上游")
                for edge in range(start, end-1):
                    if edges[edge] is not None:
                        raise ValueError("PointRange重复覆盖运动边")
                    geometry = primitive(parts[edge-start] if parts is not None else None, pts[edge], pts[edge+1])
                    edges[edge] = state + (geometry, s.get('Role', owner.get('Role')), s.get('IsConnection') is True)
            # Upstream omits coincident or extremely short connections; only strictly zero-length edges may remain unassigned.
            for i, state in enumerate(edges):
                if state is None and pts[i] != pts[i+1]:
                    raise ValueError("存在未被Segments覆盖的非零运动边")
            if not any(state is not None and state[3][1] > 0 for state in edges):
                raise ValueError("打印分块没有非零运动边")
            geo = chunk.get("GeoId")
            if geo is None:
                raise ValueError("分块缺少GeoId")
            terminal = next((s for s in reversed(records)
                             if not s.get('IsConnection') and s.get('PointRange', (None, None))[1] == len(pts)), None)
            wipe_start = None
            if wipe_distance > 0 and terminal and terminal.get('Role') == 'wall' and terminal.get('MotionKind') == 'extrude':
                seam = terminal.get('SeamDistance', 0)
                if seam is not None and positive(seam, 'SeamDistance', True) > 0:
                    wipe_start = terminal['PointRange'][0]
                    gap = math.dist(pts[-1], pts[wipe_start])
                    if gap > seam + 0.05:
                        raise ValueError('闭合wall接缝距离与端点不一致，请重算连续路径')
            result.append(((tuple(wire), item, str(geo)), key, pts, hs, edges,
                           chunk_kinds(records), wipe_start))
    return result


def chunk_kinds(records):
    """Labels use existing role and explicit cross-layer extrusion, not Stage."""
    by_id = {s.get('SegmentId'): s for s in records if not s.get('IsConnection')}
    continuous_wall = False
    for s in records:
        if not s.get('IsConnection') or s.get('MotionKind') != 'extrude':
            continue
        a, b = by_id.get(s.get('FromSegmentId')), by_id.get(s.get('ToSegmentId'))
        if a and b and a.get('Role') == b.get('Role') == 'wall' and a.get('LayerIndex') != b.get('LayerIndex'):
            continuous_wall = True
    kinds = []
    for s in records:
        if s.get('IsConnection') or s.get('MotionKind') != 'extrude':
            continue
        role = s.get('Role')
        if role not in ('wall', 'brim', 'infill'):
            raise ValueError('打印提示需要wall/brim/infill用途，请重算上游')
        kind = 'continuous wall' if role == 'wall' and continuous_wall else role
        if kind not in kinds:
            kinds.append(kind)
    return tuple(kinds) or ('travel',)


def xyz(point):
    result = tuple(float(getattr(point, axis)) for axis in 'XYZ')
    if not all(math.isfinite(v) for v in result):
        raise ValueError('坐标必须为有限数值')
    return result


def _extract_curve_parts(crv, seg_pts, edge_count):
    "Safely extract curve primitives for each motion edge from the segment's geometric Curve."
    if crv is None:
        return None
    if edge_count <= 0:
        raise ValueError('已有几何提取失败：运动边数量无效')
    if edge_count == 1:
        return (crv,)
    if hasattr(crv, "SegmentCount") and crv.SegmentCount == edge_count:
        try:
            parts = tuple(crv.SegmentCurve(i) for i in range(edge_count))
            if any(p is None or not getattr(p, 'IsValid', True) for p in parts):
                raise ValueError('子曲线为空或无效')
            return parts
        except Exception as exc:
            raise ValueError('已有几何提取失败：SegmentCurve：'+str(exc)) from exc
    if hasattr(crv, "ClosestPoint") and hasattr(crv, "Trim") and hasattr(crv, "Domain"):
        try:
            params = []
            for p in seg_pts:
                if hasattr(p, 'X'):
                    pt = p
                else:
                    import Rhino.Geometry as rg
                    pt = rg.Point3d(p[0], p[1], p[2])
                ok, t = crv.ClosestPoint(pt)
                if not ok:
                    raise ValueError('ClosestPoint未找到采样点参数')
                if not math.isfinite(t):
                    raise ValueError('ClosestPoint返回非有限参数')
                params.append(t)
            sub_parts = []
            for i in range(edge_count):
                t0, t1 = params[i], params[i+1]
                if abs(t1 - t0) < 1e-9:
                    raise ValueError('相邻采样点参数重合，无法确定曲线区间')
                else:
                    sub = crv.Trim(t0, t1)
                    if sub is None or not getattr(sub, "IsValid", True):
                        raise ValueError('Trim返回空或无效曲线')
                    sub_parts.append(sub)
            if len(sub_parts) == edge_count:
                return tuple(sub_parts)
        except Exception as exc:
            raise ValueError('已有几何提取失败：'+str(exc)) from exc
    raise ValueError('已有几何提取失败：Curve缺少拆分所需接口')


def primitive(part, start, end):
    """Consume a mapped primitive, never silently replace a spatial arc by its chord."""
    distance = math.dist(start, end)
    if part is None:
        return ('G1', distance, None)
    
    p_start = xyz(part.PointAtStart) if hasattr(part, "PointAtStart") else None
    p_end = xyz(part.PointAtEnd) if hasattr(part, "PointAtEnd") else None
    if p_start is not None and p_end is not None:
        if math.dist(p_start, start) > 0.05 or math.dist(p_end, end) > 0.05:
            raise ValueError('ArcSegments端点与Points不一致，请重算圆弧处理')

    arc = None
    if hasattr(part, "Arc") and getattr(part.Arc, "IsValid", True):
        arc = part.Arc
    elif hasattr(part, "TryGetArc"):
        for tol in (1e-6, 1e-4, 1e-3, 0.01, 0.05):
            try:
                res = part.TryGetArc(tol)
                if isinstance(res, tuple) and res[0] and getattr(res[1], "IsValid", True):
                    arc = res[1]
                    break
                elif res is True and hasattr(part, "Arc"):
                    arc = part.Arc
                    break
            except Exception:
                pass
        if arc is None:
            try:
                res = part.TryGetArc()
                if isinstance(res, tuple) and res[0] and getattr(res[1], "IsValid", True):
                    arc = res[1]
                elif res is True and hasattr(part, "Arc"):
                    arc = part.Arc
            except Exception:
                pass

    if arc is None:
        if not hasattr(part, "IsLinear"):
            raise ValueError('已有几何识别失败：无法确认圆弧或直线')
        if hasattr(part, "IsLinear"):
            is_linear = False
            for tol in (1e-6, 1e-4, 1e-3, 0.01, 0.05):
                try:
                    if part.IsLinear(tol):
                        is_linear = True
                        break
                except Exception:
                    pass
            if not is_linear:
                raise ValueError('ArcSegments含非直线非圆弧，请先运行PATH转G2/G3')
        return ('G1', distance, None)

    normal = xyz(arc.Plane.Normal)
    if abs(normal[0]) > 1e-4 or abs(normal[1]) > 1e-4 or abs(abs(normal[2])-1) > 1e-4 or abs(start[2]-end[2]) > 0.05:
        raise ValueError('仅支持XY平面G2/G3；请在上游关闭空间圆弧并重新离散')

    center = xyz(arc.Center)
    length = positive(part.GetLength() if hasattr(part, "GetLength") else getattr(arc, "Length", distance), '圆弧长度')
    radius = positive(arc.Radius, '圆弧半径')
    if abs(math.hypot(start[0]-center[0], start[1]-center[1])-radius) > 0.05 or abs(math.hypot(end[0]-center[0], end[1]-center[1])-radius) > 0.05:
        raise ValueError('圆弧半径与端点不一致')

    # Compare exactly the numeric XY coordinates that the two-decimal output sends.
    same_output_xy = all(float(format(a, '.2f')) == float(format(b, '.2f'))
                         for a, b in zip(start[:2], end[:2]))
    full_circle = (math.isclose(length, 2*math.pi*radius, rel_tol=1e-12, abs_tol=1e-12)
                   and math.dist(start, end) <= 1e-9*max(1.0, radius))
    if same_output_xy and not full_circle:
        raise ValueError('圆弧在XYZ两位小数输出下起终点重合，不能输出G2/G3；请调整上游分段')

    command = 'G3' if normal[2] > 0 else 'G2'
    mid_pt = None
    if hasattr(arc, "MidPoint"):
        mid_pt = xyz(arc.MidPoint)
    elif hasattr(part, "PointAt") and hasattr(part, "Domain"):
        try:
            mid_pt = xyz(part.PointAt(part.Domain.Mid))
        except Exception:
            pass
    if mid_pt is not None:
        cross = (mid_pt[0] - start[0]) * (end[1] - mid_pt[1]) - (mid_pt[1] - start[1]) * (end[0] - mid_pt[0])
        if abs(cross) > 1e-8:
            command = 'G3' if cross > 0 else 'G2'

    return (command, length, (center[0]-start[0], center[1]-start[1]))


def wipe_path(pts, edges, start_index, distance, feed):
    'For a closed wall, cross the seam from the final point, then repeat the beginning of the same contour in its original direction.'
    lines, current, remaining = [], pts[-1], distance
    seam_start = pts[start_index]
    gap = math.dist(current, seam_start)
    if gap > 0:
        step = min(remaining, gap)
        target = tuple(current[axis]+(seam_start[axis]-current[axis])*step/gap for axis in range(3))
        lines.append('G1 X{:.2f} Y{:.2f} Z{:.2f} F{:.2f}'.format(*target, feed))
        current, remaining = target, remaining-step
    if remaining <= 1e-9:
        return lines, current
    for index in range(start_index, len(edges)):
        state = edges[index]
        if state is None:
            if pts[index] == pts[index+1]:
                continue
            break
        motion, _, _, geometry, role, _ = state
        command, length, offsets = geometry
        if motion != 'extrude' or role != 'wall':
            break
        if length <= 0:
            continue
        step = min(remaining, length)
        start, end = pts[index], pts[index+1]
        if step == length:
            target = end
        elif command == 'G1':
            fraction = step/length
            target = tuple(start[axis]+(end[axis]-start[axis])*fraction for axis in range(3))
        else:
            cx, cy = start[0]+offsets[0], start[1]+offsets[1]
            radius = math.hypot(start[0]-cx, start[1]-cy)
            angle = math.atan2(start[1]-cy, start[0]-cx)
            sign = 1 if command == 'G3' else -1
            target = (cx+radius*math.cos(angle+sign*step/radius),
                      cy+radius*math.sin(angle+sign*step/radius),
                      start[2]+(end[2]-start[2])*step/length)
        if command != 'G1' and step < length and all(
                round(a, 2) == round(b, 2) for a, b in zip(current[:2], target[:2])):
            break  # Prevent firmware from interpreting tiny partial arcs as full circles.
        line = '{} X{:.2f} Y{:.2f} Z{:.2f}'.format(command, *target)
        if offsets is not None:
            cx, cy = start[0]+offsets[0], start[1]+offsets[1]
            line += ' I{:.6f} J{:.6f}'.format(cx-current[0], cy-current[1])
        lines.append(line+' F{:.2f}'.format(feed))
        current, remaining = target, remaining-step
        if remaining <= 1e-9:
            break
    return lines, current


def parse_temperatures(value):
    value = getattr(value, 'Value', value)
    if value is None:
        return ()
    if isinstance(value, str):
        values = value.split()
    else:
        values = [value]
    if len(values) > 3:
        raise ValueError('Temperatures只接受T0至T2的1至3个温度')
    if any(isinstance(v, bool) for v in values):
        raise ValueError('Temperatures不能是布尔值')
    return tuple(positive(v, 'Temperatures', True) for v in values)


def generate(packets, settings=None):
    settings = settings or {}
    zeros = ('FanSpeed', 'FirstLayerFanSpeed', 'ZHopHeight', 'RetractDist', 'StartPause', 'TopWipeDistance', 'BedTemp')
    cfg = {k: positive(settings.get(k) if settings.get(k) is not None else v, k, k in zeros)
           for k, v in DEFAULTS.items()}
    cfg['PrimeDist'] = positive(settings.get('PrimeDist') if settings.get('PrimeDist') is not None else cfg['RetractDist'], 'PrimeDist', True)
    for name in ('FanSpeed', 'FirstLayerFanSpeed'):
        if cfg[name] > 400:
            raise ValueError(name+'必须在0–400之间')
    temperatures = parse_temperatures(settings.get('Temperatures'))
    chunks = prepare(packets, cfg['TopWipeDistance'])
    if not chunks:
        return []
    objects = {}
    for geo, _, _, _, _, _, _ in chunks:
        if geo not in objects:
            objects[geo] = len(objects)+1
    fan = int(round(cfg['FanSpeed']*2.55))
    first_fan = int(round(cfg['FirstLayerFanSpeed']*2.55))
    header = [';HEADER_START', '; FilamentArea: {:.2f} mm2'.format(FILAMENT_AREA),
              'M221 S100 ; Reset Flow Rate', 'M220 S100 ; Reset Speed Factor',
              'G21', 'G90', 'M83', 'M106 S{}'.format(first_fan)]
    if any(state and state[3][0] in ('G2', 'G3') for _, _, _, _, edges, _, _ in chunks for state in edges):
        header.append('G17 ; XY arc plane, incremental I/J')
    if cfg['BedTemp'] > 0:
        header.append('M190 S{:.0f} ; Wait for bed'.format(cfg['BedTemp']))
    for tool, temperature in enumerate(temperatures):
        if temperature > 0:
            header.append('M109 S{:.0f} T{} ; Wait for tool'.format(temperature, tool))
    header.extend(['G28', 'G92 E0.00', 'M117 [{} Objects] Generated by Caddisfly'.format(len(objects)), ';HEADER_END'])
    output = [header]
    travel = cfg['TravelSpeed']*60
    retract_feed = cfg['RetractSpeed']*60
    current = None
    retracted = False

    def retract(lines):
        nonlocal retracted
        if not retracted and cfg['RetractDist'] > 0:
            lines.append('G1 E-{:.2f} F{:.2f} ; Retract'.format(cfg['RetractDist'], retract_feed))
            retracted = True

    def move(lines, target, first=False):
        nonlocal current, retracted
        # First move uses absolute target Z + hop: homing Z is machine-defined.
        safe = target[2]+cfg['ZHopHeight'] if current is None else max(current[2], target[2])+cfg['ZHopHeight']
        lines.append('G0 Z{:.2f} F{:.2f} ; Z-Hop'.format(safe, travel))
        lines.append('G0 X{:.2f} Y{:.2f} Z{:.2f} F{:.2f} ; Travel'.format(target[0], target[1], safe, travel))
        lines.append('G0 Z{:.2f} F{:.2f} ; Land'.format(target[2], travel))
        if (first or retracted) and cfg['PrimeDist'] > 0:
            lines.append('G1 E{:.2f} F{:.2f} ; Prime'.format(cfg['PrimeDist'], retract_feed))
        retracted = False
        current = target

    def pause(lines, name):
        milliseconds = int(round(cfg[name]*1000))
        if milliseconds:
            lines.append('G4 P{} ; {}'.format(milliseconds, name))

    kind_counts = {}
    for index, (geo, key, pts, hs, edges, kinds, wipe_start) in enumerate(chunks, 1):
        labels = []
        for kind in kinds:
            count_key = (geo, kind)
            kind_counts[count_key] = kind_counts.get(count_key, 0)+1
            labels.append('{} {}'.format(kind, kind_counts[count_key]))
        lines = ['; geometry {} chunk {} ({})'.format(objects[geo], index, ', '.join(labels))]
        move(lines, pts[0], first=index == 1)
        pause(lines, 'StartPause')
        previous_first = None
        for i, state in enumerate(edges):
            if state is None:
                continue
            motion, first, width, geometry, _, is_connection = state
            command, distance, offsets = geometry
            if distance == 0:
                continue
            if motion == 'travel':
                retract(lines)
                move(lines, pts[i+1])
                previous_first = None
                continue
            if first != previous_first:
                lines.append('M106 S{}'.format(first_fan if first else fan))
                lines.append('G1 F{:.2f}'.format(cfg['FirstLayerSpeed' if first else 'PrintSpeed']*60))
                previous_first = first
            extrusion = distance*hs[i+1]*width/FILAMENT_AREA*cfg['FlowMultiplier']
            if is_connection:
                extrusion *= 0.5
            if not math.isfinite(extrusion):
                raise ValueError('挤出量溢出')
            line = '{} X{:.2f} Y{:.2f} Z{:.2f}'.format(command, *pts[i+1])
            if offsets is not None:
                line += ' I{:.6f} J{:.6f}'.format(*offsets)
            lines.append(line+' E{:.2f}'.format(extrusion))
            current = pts[i+1]
        retract(lines)
        if cfg['TopWipeDistance'] > 0 and wipe_start is not None:
            wipe_lines, current = wipe_path(pts, edges, wipe_start, cfg['TopWipeDistance'], travel)
            if wipe_lines:
                lines.extend(['; Top Wipe Start'] + wipe_lines + ['; Top Wipe End'])
        # Cross-chunk hop is deferred until the next target is known.
        output.append(lines)
    footer = [';FOOTER_START']
    if cfg['ZHopHeight'] > 0:
        footer.append('G0 Z{:.2f} F{:.2f} ; End Z-Hop'.format(current[2]+cfg['ZHopHeight'], travel))
    footer.extend(['M221 S100 ; Reset Flow Rate', 'M220 S100 ; Reset Speed Factor'])
    footer.extend('M104 S0 T{}'.format(tool) for tool in range(len(temperatures)))
    footer.extend(['M140 S0', 'M107', 'G28 X0.00 Y0.00 ; Home XY', ';FOOTER_END'])
    output.append(footer)
    return output



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


def _ensure_ports(component):
    return True




def run_component():
    import Grasshopper as gh
    from Grasshopper.Kernel.Data import GH_Path
    component = ghenv.Component
    component.Name, component.NickName = "Path G-code生成", "PathGCode"
    component.Message = COMPONENT_MESSAGE
    empty = gh.DataTree[str]()
    try:
        if not _ensure_ports(component):
            return empty
        tree = globals().get("Path")
        if tree is None:
            return empty
        packets = [(tuple(int(v) for v in tree.Path(i).Indices), j, value)
                   for i in range(tree.BranchCount) for j, value in enumerate(tree.Branch(i))]
        lines = generate(packets, globals())
        result = gh.DataTree[str]()
        for i, branch in enumerate(lines):
            for line in branch:
                result.Add(line, GH_Path(i))
        return result
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))
        return empty


if "ghenv" in globals():
    GCode = run_component()


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = 'Path G-code生成'
    ghenv.Component.NickName = 'Path G-code生成'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'GCode'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '根据连续路径及速度、温度和挤出参数生成打印指令。'
    ghenv.Component.Tooltip = '根据连续路径及速度、温度和挤出参数生成打印指令。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '含显式MotionKind的连续路径数据包', 'FlowMultiplier': '挤出倍率，默认1', 'TravelSpeed': '空走速度 mm/s，默认80', 'PrintSpeed': '打印速度 mm/s，默认20', 'FirstLayerSpeed': '首层速度 mm/s，默认10', 'FanSpeed': '非首层风扇0–400，乘2.55输出，默认100', 'FirstLayerFanSpeed': '首层风扇0–400，乘2.55输出，默认100', 'ZHopHeight': '世界Z抬升 mm，默认5', 'RetractDist': '回抽 mm，默认6', 'PrimeDist': '补料 mm，默认等于回抽', 'RetractSpeed': '回抽及原位补料速度 mm/s，默认25', 'StartPause': '每条路径开始停留秒数，默认0', 'TopWipeDistance': '闭合wall末端沿原方向空走距离mm，默认0', 'BedTemp': '热床温度摄氏度，0不设置', 'Temperatures': 'T0至T2温度Panel文本，例如190 190 170；单值仅T0；空不设置'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'GCode': '按执行顺序排列的G-code文本树'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
