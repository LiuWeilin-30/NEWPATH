"""Path逐点运动

【功能】
读取路径原始点列，输出逐点坐标、索引和入边运动类型。

【输入端】
Path（通用对象；数据树；必填）：完整 Path 数据包；直接读取内部 Points。

【输出端】
Motion（通用对象；数据树）：每个 Point 对应的原始坐标和入边运动类型；可接 Panel。
Index（通用对象；数据树）：块内 Point 索引，从 0 开始，与 Motion 和 Points 对应。
Points（通用对象；数据树）：Path 原始点列；保留顺序和重复点，与 Motion 严格对应。

【详细用法与约束】
Path 逐点运动：直接读取 Path 内部 Points，一点一条运动描述。
输入 Path：Tree / object / 必填，接完整 Path 数据包，不接 GCode 文本。
输出 Motion、Points、Index：均为 Tree；每分支同索引严格对应。
保持块 TreePath、原始点序、重复点和空分支；不采样、不合并、不插入机器动作。
Motion 显示原始 XYZ 与入边 MotionKind；无入边为 Start，缺失类型显示 Unspecified。
段 PointRange 只用于匹配入边，首点也保留；多包沿用公共物体编号映射。
坐标采用可回读原始双精度的表示，无几何公差；不计算 E/F 或猜测 G2/G3。
"""

# Localized presentation only; keep required inputs, type hints and solver behavior.
def _newpath_zh_message(message):
    text = str(message)
    if any('\u4e00' <= char <= '\u9fff' for char in text):
        return text
    return '运行提示，请检查相关输入。原始信息：' + text


# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '读取路径原始点列，输出逐点坐标、索引和入边运动类型。'
    ghenv.Component.Tooltip = '读取路径原始点列，输出逐点坐标、索引和入边运动类型。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '完整 Path 数据包；直接读取内部 Points'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Motion': '每个 Point 对应的原始坐标和入边运动类型；可接 Panel', 'Index': '块内 Point 索引，从 0 开始，与 Motion 和 Points 对应', 'Points': 'Path 原始点列；保留顺序和重复点，与 Motion 严格对应'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description


import math

COMPONENT_MARKER = 'gcode_motion:2'
COMPONENT_MESSAGE = 'Path 原始运动\n一点一项 · 同树对应'
PORT_ALIASES = {'Paths': 'Path'}
INPUT_SPECS = [('Path', 'Path', '完整 Path 数据包；直接读取内部 Points', 'tree', 'object', False)]
OUTPUT_SPECS = [
    ('Motion', 'Motion', '每个 Point 对应的原始坐标和入边运动类型；可接 Panel', 'tree', 'object', False),
    ('Index', 'Index', '块内 Point 索引，从 0 开始，与 Motion 和 Points 对应', 'tree', 'object', False),
    ('Points', 'Points', 'Path 原始点列；保留顺序和重复点，与 Motion 严格对应', 'tree', 'object', False),
]


def chunk_rows(chunk, stage):
    records = chunk.get('Segments', ())
    points = chunk.get('Points')
    if points is None:
        if stage == 'continuous':
            raise ValueError('连续 Path 缺少块 Points')
        points, ranges = [], []
        for record in records:
            if 'Points' not in record:
                raise ValueError('层段缺少 Points')
            start = len(points)
            points.extend(record['Points'])
            ranges.append((start, len(points), record))
    else:
        ranges = []
        for record in records:
            bounds = record.get('PointRange')
            if bounds is None:
                if points:
                    raise ValueError('段缺少 PointRange，无法严格对应原始运动')
                continue
            ranges.append((bounds[0], bounds[1], record))
    incoming = [None] * len(points)
    for start, end, record in ranges:
        if not 0 <= start <= end <= len(points):
            raise ValueError('PointRange 超出 Points 范围')
        for index in range(start + 1, end):
            if incoming[index] is not None:
                raise ValueError('PointRange 入边重复覆盖：Point {}'.format(index))
            incoming[index] = record
    rows = []
    starts = {start for start, end, record in ranges if start < end}
    for index, point in enumerate(points):
        coords = tuple(float(getattr(point, axis)) for axis in 'XYZ')
        if not all(math.isfinite(value) for value in coords):
            raise ValueError('Point {} 坐标无效'.format(index))
        record = incoming[index]
        if record is None and index not in starts:
            raise ValueError('Point {} 没有段归属，无法对应原始运动'.format(index))
        kind = 'Start' if record is None else record.get('MotionKind', 'Unspecified')
        text = '{} X{} Y{} Z{}'.format(kind, *(repr(value) for value in coords))
        rows.append((text, index, point))
    return rows


def motion_branches(packets):
    packets = list(packets)
    # Single packet retains its exact internal paths, including sparse/legacy paths.
    packets = ([(wire, index, _unwrap_path(raw)) for wire, index, raw in packets]
               if len(packets) <= 1 else _path_packets(packets))
    result = {}
    for _, _, data in packets:
        for chunk in data.Chunks:
            key = tuple(chunk['TreePath'])
            if key in result:
                raise ValueError('重复输出路径：{}'.format(key))
            result[key] = chunk_rows(chunk, data.Stage)
        for key in data.Metadata.get('EmptyPaths', ()):
            result.setdefault(tuple(key), [])
    return result


def run_component():
    import Grasshopper as gh
    import Rhino.Geometry as rg
    component = ghenv.Component
    component.Name, component.NickName = 'Path 逐点运动', 'PathMotion'
    component.Message = COMPONENT_MESSAGE
    empty = (gh.DataTree[str](), gh.DataTree[int](), gh.DataTree[rg.Point3d]())
    try:
        if not _ensure_ports(component):
            return empty
        packets = [(path, i, item) for path, items in _tree_items(globals().get('Path'))
                   for i, item in enumerate(items)]
        branches = motion_branches(packets)
        motion, index, points = empty
        for key, rows in branches.items():
            path = _gh_path(key)
            for tree in empty:
                tree.EnsurePath(path)
            for line, number, point in rows:
                motion.Add(line, path)
                index.Add(number, path)
                points.Add(point, path)
        return motion, index, points
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))
        return gh.DataTree[str](), gh.DataTree[int](), gh.DataTree[rg.Point3d]()


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

def _path_order_auxiliaries(chunks, metadata):
    """Order existing sliced groups around auxiliary layers; never join geometry/groups."""
    chunks = list(chunks)
    meta = dict(metadata or {})
    limits = {}
    for c in chunks:
        key = _path_object_key(c)
        for r in c.get('Segments', ()):
            if r.get('Role') in ('brim', 'infill'):
                layer = r.get('LayerIndex')
                if type(layer) is not int or layer < 0:
                    raise ValueError('辅助路径排序需要真实非负LayerIndex。')
                limits[key] = max(limits.get(key, -1), layer)
    if not limits:
        return tuple(chunks), meta
    objects = {}
    for c in sorted(chunks, key=lambda c: c.get('ObjectIndex', 0)):
        objects.setdefault(_path_object_key(c), len(objects))
    entries = []
    for index, source in enumerate(chunks):
        key = _path_object_key(source)
        origin = source.get('OrderingSourceGroupId', source.get('ContinuousGroupId', 'source:{}'.format(index)))
        buckets = {}
        for r in source.get('Segments', ()):
            role, layer = r.get('Role'), r.get('LayerIndex')
            if role not in ('brim', 'infill', 'wall'):
                raise ValueError('辅助路径排序需要有效Role。')
            if key in limits and (type(layer) is not int or layer < 0):
                raise ValueError('辅助路径排序需要wall的真实非负LayerIndex。')
            bottom = key in limits and layer <= limits[key]
            bucket = (0, layer, {'brim': 0, 'infill': 1, 'wall': 2}[role]) if bottom else (1, 0, 2)
            buckets.setdefault(bucket, []).append(r)
        if not buckets:
            buckets[(1, 0, 2)] = []
        for bucket, records in buckets.items():
            c = dict(source)
            if tuple(records) != tuple(source.get('Segments', ())):
                _refresh_role_chunk(c, records)
                roles = {r['Role'] for r in records}
                if len(roles) == 1:
                    c['Role'] = c['PathGroupRole'] = next(iter(roles))
                    if 'GroupingRole' in c:
                        c['GroupingRole'] = c['Role']
                ids = {r.get('PathGroupId') for r in records}
                if len(ids) == 1 and next(iter(ids)) is not None:
                    c['PathGroupId'] = next(iter(ids))
                else:
                    c.pop('PathGroupId', None)
            # Lineage lets a later fill select all pieces of the original model group.
            c['OrderingSourceGroupId'] = origin
            c.setdefault('OriginalTreePath', tuple(source['TreePath']))
            group = source.get('ContinuousGroupId', 'source:{}'.format(index))
            if len(buckets) > 1:
                group = '{}:order:{}:{}:{}'.format(group, *bucket)
            c['ContinuousGroupId'] = group
            entries.append((objects[key], bucket, index, c))
    entries.sort(key=lambda e: (e[0], e[1], e[2]))
    blocks, result = {}, []
    for obj, bucket, index, c in entries:
        # Bottom roles share a layer block, but each input group stays independent.
        token = ('layer', bucket[1]) if bucket[0] == 0 else ('upper', index)
        mapping = blocks.setdefault(obj, {})
        block = mapping.setdefault(token, len(mapping))
        c.update(ObjectIndex=obj, PrintBlockIndex=block)
        result.append(c)
    meta.update(PreservePathGroups=True, AuxiliaryOrderComplete=True)
    return tuple(result), meta

def _path_object_key(chunk):
    records = tuple(chunk.get('Segments', ()))
    keys = []
    for record in records:
        if record.get('IsConnection'):
            continue
        key = record.get('SourceObjectKey', (tuple(record.get('SourcePath', ())), record.get('GeoId')))
        if key not in keys:
            keys.append(key)
    if len(keys) > 1:
        raise ValueError('一个路径组不能包含多个来源物体。')
    return keys[0] if keys else chunk.get('SourceObjectKey', (
        tuple(chunk.get('SourcePath', ())), chunk.get('GeoId', ('empty', tuple(chunk['TreePath'])))))

def _path_layout(chunks, metadata, preserve_order=False):
    """Copy and index containers; never merge groups or change segment ordering."""
    meta = dict(metadata or {})
    chunks = list(chunks)
    objects = {}
    # Preserve upstream object order even when a producer places auxiliaries first.
    for chunk in sorted(chunks, key=lambda c: c.get('ObjectIndex', 0)):
        key = _path_object_key(chunk)
        objects.setdefault(key, len(objects))
    planned = meta.get('GroupingComplete') is True
    preserve_groups = planned or meta.get('PreservePathGroups') is True
    block_counts, path_counts = {}, {}
    result = []
    for source in chunks:
        key = _path_object_key(source)
        obj = objects[key]
        chunk = dict(source)
        old_path = tuple(source['TreePath'])
        if preserve_groups:
            block = source.get('PrintBlockIndex')
            if type(block) is not int or block < 0 or not source.get('ContinuousGroupId'):
                raise ValueError('已分组数据缺少打印块或独立连续组身份，请重新分组。')
        else:
            block = block_counts.get(obj, 0)
            block_counts[obj] = block + 1
        group = source.get('ContinuousGroupId') if preserve_groups else 'container:{}:{}'.format(obj, block)
        count_key = (obj, block)
        path_index = path_counts.get(count_key, 0)
        path_counts[count_key] = path_index + 1
        chunk.update(TreePath=(obj, block, path_index), ObjectIndex=obj,
                     PrintBlockIndex=block, SourceObjectKey=key, ContinuousGroupId=group)
        chunk.setdefault('OriginalTreePath', old_path)
        records = []
        for record in source.get('Segments', ()):
            original_record = record
            record = dict(record)
            default_key = (tuple(record.get('SourcePath', ())), record.get('GeoId'))
            if key != default_key or 'SourceObjectKey' in record:
                record['SourceObjectKey'] = key
            if preserve_groups:
                record.update(PrintGroupId='object:{}:block:{}'.format(obj, block),
                              ContinuousGroupId=group)
            else:
                record.pop('PrintGroupId', None)
                record.pop('ContinuousGroupId', None)
            records.append(original_record if record == original_record else record)
        if 'Segments' in source:
            chunk['Segments'] = tuple(records)
        if preserve_groups:
            chunk['PrintGroupId'] = 'object:{}:block:{}'.format(obj, block)
        else:
            chunk.pop('PrintGroupId', None)
        result.append(chunk)
    if not preserve_order and preserve_groups:
        result.sort(key=lambda c: c['TreePath'])
    # Empty input branches remain explicit, outside occupied object indices.
    # Their original positions remain available for tracing, not guessed as geometry.
    empty = tuple(tuple(p) for p in meta.get('EmptyPaths', ()))
    originals = tuple(meta.get('EmptyPathSources', empty))
    if len(originals) != len(empty):
        originals = empty
    meta.update(TreeLayout='object_block_path', GroupingComplete=planned,
                EmptyPaths=tuple((len(objects)+i, 0, 0) for i in range(len(empty))),
                EmptyPathSources=originals,
                ObjectSources=tuple(objects),
                PathGroups=tuple(dict(TreePath=c['TreePath'],
                    PrintGroupId=c.get('PrintGroupId'), ContinuousGroupId=c['ContinuousGroupId'],
                    PathGroupId=c.get('PathGroupId'),
                    Roles=tuple(dict.fromkeys(r.get('Role') for r in c.get('Segments', ())))) for c in result))
    return tuple(result), meta

def _path_output(stage, chunks, metadata=None):
    metadata = dict(metadata or {})
    if stage == 'sliced' and metadata.pop('AuxiliaryOrderDirty', False):
        chunks, metadata = _path_order_auxiliaries(chunks, metadata)
    if stage == 'continuous' and all('ContinuousGroupId' in c for c in chunks):
        metadata['PreservePathGroups'] = True
    chunks, metadata = _path_layout(chunks, metadata)
    return _PathData(stage, chunks, metadata)

def _path_packets(packets):
    """Namespace object identity across packets without altering source GeoId."""
    from types import SimpleNamespace
    packets = list(packets)
    result = []
    offset = 0
    for wire, item, raw in packets:
        data = _unwrap_path(raw)
        if data.Metadata.get('TreeLayout') == 'object_block_path':
            keys = []
            for c in data.Chunks:
                p = tuple(c['TreePath'])
                if (len(p) != 3 or any(type(v) is not int or v < 0 for v in p)
                        or c.get('ObjectIndex') != p[0] or c.get('PrintBlockIndex') != p[1]):
                    raise ValueError('三位树路径与物体/打印块字段不一致，请重算上游。')
                keys.append(p)
            if data.Metadata.get('GroupingComplete') and keys != sorted(keys):
                raise ValueError('已分组Chunks执行顺序与TreePath不一致，请重新分组。')
        chunks = []
        for source in data.Chunks:
            key = _path_object_key(source)
            if len(packets) > 1:
                key = (tuple(wire), item, key)
            chunk = dict(source, SourceObjectKey=key)
            if len(packets) > 1 and 'OrderingSourceGroupId' in chunk:
                chunk['OrderingSourceGroupId'] = '{}:{}:{}'.format(tuple(wire), item, chunk['OrderingSourceGroupId'])
            chunk['Segments'] = (tuple(dict(r, SourceObjectKey=key) for r in source.get('Segments', ()))
                                 if len(packets) > 1 else source.get('Segments', ()))
            chunks.append(chunk)
        chunks, meta = _path_layout(chunks, data.Metadata, preserve_order=True)
        shifted = []
        for c in chunks:
            c = dict(c)
            obj, block, path = c['TreePath']
            c.update(TreePath=(obj+offset, block, path), ObjectIndex=obj+offset)
            if len(packets) > 1:
                c['ContinuousGroupId'] = '{}:{}:{}'.format(tuple(wire), item, c['ContinuousGroupId'])
            if meta.get('GroupingComplete') or meta.get('PreservePathGroups'):
                c['PrintGroupId'] = 'object:{}:block:{}'.format(obj+offset, block)
                c['Segments'] = tuple(dict(r, PrintGroupId=c['PrintGroupId'],
                    ContinuousGroupId=c['ContinuousGroupId']) for r in c.get('Segments', ()))
            shifted.append(c)
        meta['EmptyPaths'] = tuple((p[0]+offset, p[1], p[2]) for p in meta['EmptyPaths'])
        meta['PathGroups'] = tuple(dict(TreePath=c['TreePath'], PrintGroupId=c.get('PrintGroupId'),
            ContinuousGroupId=c['ContinuousGroupId'], PathGroupId=c.get('PathGroupId')) for c in shifted)
        offset += len(meta['ObjectSources']) + len(meta['EmptyPaths'])
        result.append((wire, item, SimpleNamespace(Protocol=data.Protocol,
            SchemaVersion=data.SchemaVersion, Stage=data.Stage, Chunks=tuple(shifted), Metadata=meta)))
    return result

def _path_passthrough(packets):
    packets = _path_packets(packets)
    if len({p.Stage for _, _, p in packets}) != 1 or len({str(p.Metadata.get('Units')) for _, _, p in packets}) != 1:
        raise ValueError('透传的多个Path阶段或单位不一致。')
    meta = dict(packets[0][2].Metadata)
    meta.update(GroupingComplete=all(p.Metadata.get('GroupingComplete') for _, _, p in packets),
                EmptyPaths=tuple(e for _, _, p in packets for e in p.Metadata['EmptyPaths']),
                EmptyPathSources=tuple(e for _, _, p in packets for e in p.Metadata['EmptyPathSources']))
    return _path_output(packets[0][2].Stage, [c for _, _, p in packets for c in p.Chunks], meta), []

def _tree_items(tree):
    'Expand only at the GH Tree Access input boundary; retain complete paths internally.'
    if tree is None:
        return
    if not hasattr(tree, "BranchCount"):
        raise ValueError("输入端必须使用 Tree Access。")
    for i in range(tree.BranchCount):
        yield tuple(int(v) for v in tree.Path(i).Indices), list(tree.Branch(i))

def _gh_path(indices):
    import System
    from Grasshopper.Kernel.Data import GH_Path
    return GH_Path(System.Array[System.Int32](list(indices)))

if 'ghenv' in globals():
    Motion, Index, Points = run_component()


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = 'Path逐点运动'
    ghenv.Component.NickName = 'Path逐点运动'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'Data'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '读取路径原始点列，输出逐点坐标、索引和入边运动类型。'
    ghenv.Component.Tooltip = '读取路径原始点列，输出逐点坐标、索引和入边运动类型。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '完整 Path 数据包；直接读取内部 Points'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Motion': '每个 Point 对应的原始坐标和入边运动类型；可接 Panel', 'Index': '块内 Point 索引，从 0 开始，与 Motion 和 Points 对应', 'Points': 'Path 原始点列；保留顺序和重复点，与 Motion 严格对应'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
