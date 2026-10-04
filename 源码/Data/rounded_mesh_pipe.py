"""路径料条预览

【功能】
根据路径的逐点高度和料条宽度生成挤出料条网格预览。

【输入端】
Path（通用对象；数据树；必填）：带采样点、逐点高度和 Width 的 Path。

【输出端】
Mesh（网格；数据树）：逐点变截面料条网格。

【详细用法与约束】
连续块按MotionKind跳过空走；按段Width拆分料条，相邻同宽挤出段连续显示。
唯一输入 Path / Tree / object，读取智能切片或连续路径的数据包。
输出 Mesh / Tree；不输出或预览点。Width 从包内读取，Heights/GrowthVectors 与 Points 逐点对应。
新版连续路径PathMotionPolicy=extrusion_only沿当前段Curve与SampleParameters采样，不把稀疏Points连成弦线。
切片及旧包按真实SampleCurve与SampleParameters将弯曲段按约4模型单位弧长分段；旧含travel包仍按动作过滤。
显示侧按模型公差合并重复/过近点，压缩形状及属性近似共线点；保留端点与明显转角。新增点插值高度/方向，截面仍为16点。
采样路径为料条顶面，沿当前层法线向下延伸H；GrowthVectors须为新版切片提供的层法线；宽度始终保持输入Width。
变截面采用 section-first / corner-width 方法：不再修改中心线，也不再判断“圆角是否放得下”。
借鉴 Ovenbird corner-width + fillet points；在层法线的垂直平面内建立有向角平分横轴，截面保持固定宽度。
较长直线段会在转角附近自动插入 fillet/control sections，使直线段保持干净、转角过渡集中。
短线段不做特殊折角/圆角模式；没有空间就不插入 control section，直接连接相邻 corner sections。
侧面优先使用四边面；局部四边面发生折叠/退化风险时自动改为三角面，不缩小 Width/Height。
允许接触、自交和材料重叠；不做全局碰撞、布尔融合或实体朝向门槛。
平行层法线路径边无法定义横向宽度方向，会分开成管段并提示未模拟的边数。
侧面转接退化时，受影响路径回退为逐边独立封口料条并提示；保留所有边与宽高，接头允许重叠。
仅独立边自身仍退化时报告错误；不保证消除材料自交。
仅支持含逐点数据的新 Path；旧包请重新运行智能切片及 Continuous。

树路径统一为{物体;打印块;块内路径}；Role和真实LayerIndex独立保留。
ContinuousGroupId为独立连续组；同打印块内不同组不合并。
"""

# Localized presentation only; keep required inputs, type hints and solver behavior.
def _newpath_zh_message(message):
    text = str(message)
    if any('\u4e00' <= char <= '\u9fff' for char in text):
        return text
    return '运行提示，请检查相关输入。原始信息：' + text


# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '根据路径的逐点高度和料条宽度生成挤出料条网格预览。'
    ghenv.Component.Tooltip = '根据路径的逐点高度和料条宽度生成挤出料条网格预览。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '带采样点、逐点高度和 Width 的 Path'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Mesh': '逐点变截面料条网格'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description



PORT_ALIASES = {'Paths': 'Path'}

import math

COMPONENT_MARKER = 'RoundedMeshPipe:r24-path-preview'
COMPONENT_MESSAGE = "Path Preview\n轻量采样 · 挤出料条"

# Centralized settings require no new ports. Fillets use inscribed polyline approximations, not analytical arcs.
CORNER_SEGMENTS = 3
RADIUS_RATIO = 0.5  # Maximum fillet forming a capsule section.
BEND_MIN_DEGREES = 15.0
BEND_STEP_DEGREES = 30.0
BEND_RADIUS_RATIO = 1.0  # Centerline fillet radius / Width.
MITER_LIMIT = 2.0
ANGLE_DEGREES = 10.0
MAX_SECTIONS = 200000  # Prevent accidental excessive mesh sizes; this is not a reduced-precision limit.

# Variable-section, section-first parameters. A fillet point adds a section without changing the centerline path.
SECTION_SIDES = 16
PATH_SEGMENT_LENGTH = 4.0  # Model units; 4 mm in a millimeter model. Used only on curved segments.
FILLET_POINT_DISTANCE_RATIO = 0.75   # Distance from control section to corner / Width.
FILLET_POINT_MIN_ANGLE_DEGREES = 12.0  # Do not insert additional control sections below this corner angle.
FRAME_EPS = 1e-9

INPUT_SPECS = [("Path", "Path", '带采样点、逐点高度和 Width 的 Path', "tree", "object", False)]
OUTPUT_SPECS = [("Mesh", "Mesh", '逐点变截面料条网格', "tree")]
PATH_PROTOCOL = "codex.ghpython.Path"
PATH_SCHEMA = 1



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

def section_profile(width, height):
    'The section is counterclockwise in (left, up) coordinates, with its normal pointing along the path.'
    radius = min(width, height) * RADIUS_RATIO
    a, b = width * 0.5 - radius, height * 0.5 - radius
    result = []
    for cx, cy, start in ((a, b, 0), (-a, b, 90),
                          (-a, -b, 180), (a, -b, 270)):
        for j in range(CORNER_SEGMENTS + 1):
            t = math.radians(start + 90.0 * j / CORNER_SEGMENTS)
            ct, st = math.cos(t), math.sin(t)
            if abs(ct) < 1e-14: ct = 0.0
            if abs(st) < 1e-14: st = 0.0
            point = (cx + radius * ct, cy + radius * st - height*0.5)
            if not result or _distance(point, result[-1]) > min(width, height)*1e-12:
                result.append(point)
    if _distance(result[0], result[-1]) <= min(width, height)*1e-12:
        result.pop()
    return result


def clean_points(points, closed, epsilon):
    'Remove only numerically duplicate or collinear points; do not treat small turns as straight lines.'
    result = []
    for point in points:
        if result and _distance(point, result[-1]) <= epsilon:
            continue
        while len(result) >= 2 and _straight(result[-2], result[-1], point, epsilon):
            result.pop()
        result.append(point)
    if closed:
        if len(result) > 1 and _distance(result[-1], result[0]) <= epsilon:
            result.pop()
        while len(result) > 2 and _straight(result[-2], result[-1], result[0], epsilon):
            result.pop()
        # Use indices to avoid quadratic complexity from repeated pop(0).
        start = 0
        while len(result)-start > 2 and _straight(result[-1], result[start], result[start+1], epsilon):
            start += 1
        if start:
            result = result[start:]
    if len(result) < (3 if closed else 2):
        raise ValueError("路径有效点不足或长度退化。")
    return result


def _distance(a, b):
    return math.sqrt(sum((x-y)*(x-y) for x, y in zip(a, b)))


def _straight(a, b, c, epsilon):
    ux, uy = b[0]-a[0], b[1]-a[1]
    vx, vy = c[0]-b[0], c[1]-b[1]
    uz, vz = (b[2]-a[2], c[2]-b[2]) if len(a) == 3 else (0.0, 0.0)
    # Also require angles near machine precision; do not accumulate model tolerance to reduce curved face counts.
    cross = math.sqrt((uy*vz-uz*vy)**2 + (uz*vx-ux*vz)**2 + (ux*vy-uy*vx)**2)
    lengths = math.sqrt((ux*ux+uy*uy+uz*uz)*(vx*vx+vy*vy+vz*vz))
    return ux*vx+uy*vy+uz*vz > 0 and cross <= min(epsilon*math.sqrt((ux+vx)**2+(uy+vy)**2+(uz+vz)**2), lengths*1e-12)


def build_frames(points, closed, width, tolerance):
    'Return a lateral vector for each point; mitering expands only the width direction without changing height.'
    count = len(points)
    edges = count if closed else count-1
    directions = []
    for i in range(edges):
        p, q = points[i], points[(i+1) % count]
        dx, dy = q[0]-p[0], q[1]-p[1]
        length = math.hypot(dx, dy)
        if length <= tolerance:
            raise ValueError("第 {} 段的 XY 投影长度不大于模型公差（短段或竖直抬升）；竖直截面会退化，请清理短段或分开纯 Z 空走路径。".format(i+1))
        directions.append((dx/length, dy/length))
    frames = []
    limited = 0
    for i in range(count):
        if not closed and i in (0, count-1):
            tx, ty = directions[0 if i == 0 else -1]
            frames.append((-ty, tx))
            continue
        ax, ay = directions[(i-1) % edges]
        bx, by = directions[i % edges]
        sx, sy = ax+bx, ay+by
        norm = math.hypot(sx, sy)
        if norm <= 1e-8:
            raise ValueError("第 {} 个截面的 XY 方向接近 180° 回折，无法构造稳定竖直截面。".format(i+1))
        cosine_half = norm*0.5
        scale = 1.0/cosine_half
        if scale > MITER_LIMIT:
            scale = MITER_LIMIT
            limited += 1
        frames.append((-sy/norm*scale, sx/norm*scale))
    # Both outer and inner edges must advance along the segment, including all narrower filleted sections.
    half = width*0.5
    for i in range(edges):
        j = (i+1) % count
        dx, dy = points[j][0]-points[i][0], points[j][1]-points[i][1]
        tx, ty = directions[i]
        shift = half*((frames[j][0]-frames[i][0])*tx + (frames[j][1]-frames[i][1])*ty)
        if dx*tx+dy*ty-abs(shift) <= tolerance:
            raise ValueError("第 {} 段内侧发生折叠或退化；需减小宽度、延长短段或先圆角路径。".format(i+1))
    return frames, limited


def mesh_vertices(points, frames, profile):
    'Stream spatial vertices; each ring uses its own Z while sections preserve world-Z height.'
    for p, u in zip(points, frames):
        for lateral, vertical in profile:
            yield p[0]+u[0]*lateral, p[1]+u[1]*lateral, p[2]+vertical


def mesh_faces(count, sides, closed):
    'Stream indices without storing additional face arrays; use convex polygon quad fans for end caps.'
    for i in range(count if closed else count-1):
        a, b = i*sides, ((i+1) % count)*sides
        for j in range(sides):
            k = (j+1) % sides
            yield a+j, a+k, b+k, b+j
    if not closed:
        last = (count-1)*sides
        for j in range(1, sides-2, 2):
            yield 0, j+2, j+1, j
            yield last, last+j, last+j+1, last+j+2


def round_path(points, closed, width, tolerance):
    'Sample analytical XY arcs locally without creating Rhino curves or globally resampling.'
    count = len(points)
    result = []
    rounded = 0
    for i, p in enumerate(points):
        if not closed and i in (0, count-1):
            result.append(p)
            continue
        a, b = points[(i-1) % count], points[(i+1) % count]
        ax, ay, bx, by = p[0]-a[0], p[1]-a[1], b[0]-p[0], b[1]-p[1]
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if min(la, lb) <= tolerance:
            raise ValueError("第 {} 个转角邻接短段或竖直段，请分开纯 Z 空走路径。".format(i+1))
        ax, ay, bx, by = ax/la, ay/la, bx/lb, by/lb
        angle = math.atan2(ax*by-ay*bx, ax*bx+ay*by)
        theta = abs(angle)
        if theta < math.radians(BEND_MIN_DEGREES):
            result.append(p)
            continue
        if math.pi-theta < 1e-8:
            raise ValueError("第 {} 个转角 XY 接近180°回折，请修改路径。".format(i+1))
        tanhalf = math.tan(theta*0.5)
        trim = min(width*BEND_RADIUS_RATIO*tanhalf, 0.45*la, 0.45*lb)
        radius = trim/tanhalf
        if radius <= width*0.5+tolerance:
            raise ValueError("第 {} 个转角邻边过短，容不下当前宽度的圆角；请延长邻边或减小宽度。".format(i+1))
        start = (p[0]-ax*trim, p[1]-ay*trim, p[2]+(a[2]-p[2])*trim/la)
        end = (p[0]+bx*trim, p[1]+by*trim, p[2]+(b[2]-p[2])*trim/lb)
        sign = 1.0 if angle > 0 else -1.0
        cx, cy = start[0]-ay*sign*radius, start[1]+ax*sign*radius
        rx, ry = start[0]-cx, start[1]-cy
        steps = max(2, int(math.ceil(theta/math.radians(BEND_STEP_DEGREES))))
        for j in range(steps+1):
            if j == 0: q = start
            elif j == steps: q = end
            else:
                t = j/float(steps)
                co, si = math.cos(angle*t), math.sin(angle*t)
                q = (cx+rx*co-ry*si, cy+rx*si+ry*co, start[2]+(end[2]-start[2])*t)
            result.append(q)
        rounded += 1
        if len(result) > MAX_SECTIONS:
            raise ValueError("修圆后截面数量超过上限。")
    if len(result) > MAX_SECTIONS:
        raise ValueError("修圆后截面数量超过上限。")
    return result, rounded


def make_pipe(value, width, height):
    import Rhino
    import Rhino.Geometry as rg
    import rhinoscriptsyntax as rs
    width, height = float(width), float(height)
    if (not isinstance(CORNER_SEGMENTS, int) or CORNER_SEGMENTS < 1
            or not 0 < RADIUS_RATIO <= 0.5 or not math.isfinite(MITER_LIMIT)
            or MITER_LIMIT < 1):
        raise ValueError("代码顶部圆角或斜接设置无效。")
    doc = Rhino.RhinoDoc.ActiveDoc
    tolerance = doc.ModelAbsoluteTolerance if doc else 0.001
    if not all(math.isfinite(v) and v > 2*tolerance for v in (width, height)):
        raise ValueError("宽度和高度必须是有限正数，且大于两倍模型绝对公差。")
    if not isinstance(value, rg.Curve):
        value = getattr(value, "Value", value)
    curve = value if isinstance(value, rg.Curve) else rs.coercecurve(value)
    if curve is None or not curve.IsValid:
        raise ValueError("Curve 输入不是有效曲线。")
    bbox = curve.GetBoundingBox(True)
    warnings = []
    scale = max(abs(bbox.Min.X), abs(bbox.Max.X), abs(bbox.Min.Y), abs(bbox.Max.Y),
                abs(bbox.Min.Z), abs(bbox.Max.Z), 1.0)
    epsilon = max(1e-12, scale*2e-15)
    ok, polyline = curve.TryGetPolyline()
    if not ok:
        approximation = curve.ToPolyline(tolerance, math.radians(ANGLE_DEGREES), 0.0, 0.0)
        if approximation is None:
            raise ValueError("曲线折线化失败。")
        try:
            ok, polyline = approximation.TryGetPolyline()
        finally:
            approximation.Dispose()
        if not ok:
            raise ValueError("无法读取曲线折线化结果。")
    if polyline.Count > MAX_SECTIONS+1:
        raise ValueError("截面超过 {} 个；请先检查路径精度和模型单位。".format(MAX_SECTIONS))
    closed = bool(curve.IsClosed)
    points = clean_points(((p.X, p.Y, p.Z) for p in polyline), closed, epsilon)
    del polyline
    points, rounded = round_path(points, closed, width, tolerance)
    frames, limited = build_frames(points, closed, width, tolerance)
    if limited:
        warnings.append("{} 处急转角达到斜接上限 {}；已限制尖角，邻近管身局部宽度小于输入宽度。".format(limited, MITER_LIMIT))
    profile = section_profile(width, height)
    n, m = len(points), len(profile)
    mesh = rg.Mesh()
    try:
        # Use double precision for large coordinates or small dimensions; retain single precision at ordinary scales to save memory.
        coordinate = max(scale, width, height)
        mesh.Vertices.UseDoublePrecisionVertices = coordinate*1.2e-7 > min(tolerance*0.1, min(width, height)*1e-5)
        mesh.Vertices.Capacity = n*m
        mesh.Faces.Capacity = m*(n if closed else n-1)+(0 if closed else m-2)
        add_vertex, add_face = mesh.Vertices.Add, mesh.Faces.AddFace
        for vertex in mesh_vertices(points, frames, profile):
            add_vertex(*vertex)
        for face in mesh_faces(n, m, closed):
            add_face(*face)
        if collapsed_faces:
            raise ValueError("局部截面连接退化；未输出缺面的网格。请检查短回折路径。")
        if not mesh.IsValid:
            raise ValueError("网格存在无效或退化面。")
        # mesh_faces are closed by index and consistently oriented. Contact locations in Rhino topology
        # may merge into non-manifold edges; do not reject material strands using SolidOrientation/IsClosed.
        # Do not call Weld/CombineIdentical or flip local faces based on overall solid orientation.
        mesh.FaceNormals.ComputeFaceNormals()
        mesh.Normals.ComputeNormals()
        return mesh, warnings
    except Exception:
        mesh.Dispose()
        raise



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


def _ensure_ports(component):
    return True


def _vadd(a,b): return tuple(x+y for x,y in zip(a,b))
def _vsub(a,b): return tuple(x-y for x,y in zip(a,b))
def _vmul(a,s): return tuple(x*s for x in a)
def _vdot(a,b): return sum(x*y for x,y in zip(a,b))
def _vcross(a,b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def _vunit(a):
    length=math.sqrt(_vdot(a,a))
    if not math.isfinite(length) or length<=1e-12: raise ValueError("局部方向退化。")
    return _vmul(a,1.0/length)


def variable_profile(width,height,sides=SECTION_SIDES):
    "Create a capsule section with a fixed point count. The path point is at the strand's top center; extend the section by height along -up."
    radius=min(width,height)*0.5
    a,b=width*0.5-radius,height*0.5-radius
    result=[]
    for i in range(sides):
        angle=2*math.pi*i/sides
        c,t=math.cos(angle),math.sin(angle)
        ac,at=abs(c),abs(t)
        if ac>1e-12 and width*0.5*at/ac<=b:
            distance=width*0.5/ac
        elif at>1e-12 and height*0.5*ac/at<=a:
            distance=height*0.5/at
        else:
            dot=a*ac+b*at
            distance=dot+math.sqrt(max(0.0,dot*dot-a*a-b*b+radius*radius))
        result.append((distance*c,distance*t-height*0.5))
    return result


def _vlength(a):
    return math.sqrt(_vdot(a,a))


def _clamp(value,lo=-1.0,hi=1.0):
    return lo if value<lo else hi if value>hi else value


def _project_to_growth_plane(direction,up):
    'Project the path direction onto the local plane perpendicular to GrowthVector; return None for degeneracy.'
    projected=_vsub(direction,_vmul(up,_vdot(direction,up)))
    length=_vlength(projected)
    if not math.isfinite(length) or length<=FRAME_EPS:
        return None
    return _vmul(projected,1.0/length)


def _blend_up(a,b,t):
    'Interpolate GrowthVector along the segment using normalized linear interpolation normally; avoid zero vectors near opposite directions.'
    mixed=_vadd(_vmul(a,1.0-t),_vmul(b,t))
    length=_vlength(mixed)
    if math.isfinite(length) and length>FRAME_EPS:
        return _vmul(mixed,1.0/length)
    # Near 180 degrees, no unique interpolation plane exists; resolving this is outside the corner algorithm's scope.
    # Choose the endpoint nearest the current parameter to keep data defined; subsequent frame continuity stabilizes lateral direction.
    return a if t<=0.5 else b


def _corner_angle(points,ups,index,closed):
    "Compute the path turn in the point's own GrowthVector plane, only to decide whether to add a control section."
    n=len(points)
    if not closed and index in (0,n-1):
        return 0.0
    up=ups[index]
    prev=(index-1)%n
    nxt=(index+1)%n
    incoming=_project_to_growth_plane(_vunit(_vsub(points[index],points[prev])),up)
    outgoing=_project_to_growth_plane(_vunit(_vsub(points[nxt],points[index])),up)
    if incoming is None or outgoing is None:
        return 0.0
    return math.acos(_clamp(_vdot(incoming,outgoing)))


def make_section_samples(points,heights,width,ups,closed,tolerance):
    '\n    Preserve all original centerline points and insert control / fillet sections only on long straight segments near significant turns.\n\n    Constraints:\n    - Do not change the centerline; inserted points lie strictly on the segment.\n    - Require no fillet radius and do not compare Width with segment length.\n    - If a short segment lacks space, omit the control section; retain the corner section itself.\n    '
    n=len(points)
    if n<(3 if closed else 2):
        raise ValueError("有效采样点不足。")

    corner_threshold=math.radians(FILLET_POINT_MIN_ANGLE_DEGREES)
    corners=[_corner_angle(points,ups,i,closed)>=corner_threshold for i in range(n)]
    control_distance=max(width*FILLET_POINT_DISTANCE_RATIO,tolerance*4.0)
    edge_count=n if closed else n-1
    samples=[]
    added=0

    # sample = (point, height, width, up, is_control)
    samples.append((points[0],heights[0],width,ups[0],False))

    for i in range(edge_count):
        j=(i+1)%n
        p0,p1=points[i],points[j]
        edge=_vsub(p1,p0)
        length=_vlength(edge)
        if not math.isfinite(length) or length<=tolerance:
            raise ValueError("采样点重复或过近，请检查上游采样。")

        start_corner=corners[i]
        end_corner=corners[j]
        ts=[]

        # For an edge with corners at both ends, add two control sections only when both fit independently.
        # For short edges, connect the original two corner sections directly without entering a special mode.
        if start_corner and end_corner:
            if length>2.0*control_distance+4.0*tolerance:
                ts=(control_distance/length,1.0-control_distance/length)
        elif start_corner:
            if length>control_distance+4.0*tolerance:
                ts=(control_distance/length,)
        elif end_corner:
            if length>control_distance+4.0*tolerance:
                ts=(1.0-control_distance/length,)

        for t in ts:
            p=_vadd(p0,_vmul(edge,t))
            h=heights[i]*(1.0-t)+heights[j]*t
            up=_blend_up(ups[i],ups[j],t)
            samples.append((p,h,width,up,True))
            added+=1
            if len(samples)>MAX_SECTIONS:
                raise ValueError("加入 control sections 后截面数量超过上限。")

        # Do not duplicate point 0 on the closing edge.
        if not (closed and j==0):
            samples.append((points[j],heights[j],width,ups[j],False))
            if len(samples)>MAX_SECTIONS:
                raise ValueError("加入 control sections 后截面数量超过上限。")

    return samples,added


def build_section_frames(samples,closed):
    '\n    Corner-width frame:\n    1. Project adjacent path directions onto the plane perpendicular to the current layer normal.\n    2. Compute the bisector of the two unit projected directions.\n    3. lateral = up × bisector.\n\n    Section width always equals input Width, without miter expansion.\n    A control section lies on a straight segment, so its incoming/outgoing directions are collinear and yield a standard straight section.\n    '
    count=len(samples)
    if count<(3 if closed else 2):
        raise ValueError("有效截面数量不足。")

    laterals=[]
    previous=None

    for i,sample in enumerate(samples):
        p,h,w,up,is_control=sample
        up=_vunit(up)

        if not closed and i==0:
            raw=_vunit(_vsub(samples[1][0],p))
            tangent=_project_to_growth_plane(raw,up)
        elif not closed and i==count-1:
            raw=_vunit(_vsub(p,samples[i-1][0]))
            tangent=_project_to_growth_plane(raw,up)
        else:
            incoming=_vunit(_vsub(p,samples[i-1][0]))
            outgoing=_vunit(_vsub(samples[(i+1)%count][0],p))
            pin=_project_to_growth_plane(incoming,up)
            pout=_project_to_growth_plane(outgoing,up)

            if pin is not None and pout is not None:
                bisector=_vadd(pin,pout)
                if _vlength(bisector)>FRAME_EPS:
                    tangent=_vunit(bisector)
                else:
                    # An exact 180-degree reversal has no unique bisector; the section plane itself can still continue.
                    # Use outgoing as the local section normal without Width- or radius-dependent conditions.
                    tangent=pout
            else:
                tangent=pin if pin is not None else pout

        if tangent is None:
            # split_normal_edges should already split these edges; this is only a final fallback.
            if previous is not None:
                candidate=_vsub(previous,_vmul(up,_vdot(previous,up)))
                if _vlength(candidate)>FRAME_EPS:
                    lateral=_vunit(candidate)
                else:
                    raise ValueError("局部生长方向与路径方向平行，无法定义横截面。")
            else:
                raise ValueError("局部生长方向与路径方向平行，无法定义横截面。")
        else:
            cross=_vcross(up,tangent)
            if _vlength(cross)<=FRAME_EPS:
                if previous is not None:
                    candidate=_vsub(previous,_vmul(up,_vdot(previous,up)))
                    if _vlength(candidate)<=FRAME_EPS:
                        raise ValueError("局部生长方向与路径方向平行，无法定义横截面。")
                    lateral=_vunit(candidate)
                else:
                    raise ValueError("局部生长方向与路径方向平行，无法定义横截面。")
            else:
                lateral=_vunit(cross)

        # Section vertices have a fixed winding order. The transverse axis must follow the directed path, not flip to align with the previous axis.
        laterals.append(lateral)
        previous=lateral

    return laterals


def section_ring(sample,lateral):
    'Keep section width fixed and descend by the local height along the current layer normal.'
    p,h,w,up,is_control=sample
    # The new Path direction is the current layer normal and H is local height along it; do not shear using nearest-point connections.
    growth=_vunit(up)
    return [_vadd(p,_vadd(_vmul(lateral,x),_vmul(growth,y)))
            for x,y in variable_profile(w,h,SECTION_SIDES)]



def _triangle_area2(a,b,c):
    'Return twice the triangle area, equal to the cross-product magnitude.'
    return _vlength(_vcross(_vsub(b,a),_vsub(c,a)))


def _add_strip_cell(mesh,indices_a,indices_b,ring_a,ring_b,j,area_epsilon):
    '\n    Connect one side-face unit between two adjacent sections.\n    Normally retain a quad; use more stable triangulation when a sharp turn risks folding or degeneracy.\n    '
    k=(j+1)%SECTION_SIDES
    ia,ib,ic,id_=indices_a[j],indices_a[k],indices_b[k],indices_b[j]
    a,b,c,d=ring_a[j],ring_a[k],ring_b[k],ring_b[j]

    # First check both quad halves across diagonal a-c for sufficient area and consistent orientation.
    n1=_vcross(_vsub(b,a),_vsub(c,a))
    n2=_vcross(_vsub(c,a),_vsub(d,a))
    a1=_vlength(n1)
    a2=_vlength(n2)
    if a1>area_epsilon and a2>area_epsilon and _vdot(n1,n2)>=0.0:
        mesh.Faces.AddFace(ia,ib,ic,id_)
        return 0

    # For risky quads, compare both triangulations and maximize the minimum triangle area.
    ac_areas=(_triangle_area2(a,b,c),_triangle_area2(a,c,d))
    bd_areas=(_triangle_area2(a,b,d),_triangle_area2(b,c,d))
    ac_score=min(ac_areas)
    bd_score=min(bd_areas)

    collapsed=0
    if ac_score>=bd_score:
        if ac_areas[0]>area_epsilon: mesh.Faces.AddFace(ia,ib,ic)
        else: collapsed+=1
        if ac_areas[1]>area_epsilon: mesh.Faces.AddFace(ia,ic,id_)
        else: collapsed+=1
    else:
        if bd_areas[0]>area_epsilon: mesh.Faces.AddFace(ia,ib,id_)
        else: collapsed+=1
        if bd_areas[1]>area_epsilon: mesh.Faces.AddFace(ib,ic,id_)
        else: collapsed+=1
    return collapsed


def _add_cap(mesh,indices,ring,reverse):
    "Create a triangle fan from the section's interior center to avoid collinear degenerate triangles along straight edges."
    center=tuple(sum(p[k] for p in ring)/len(ring) for k in range(3))
    root=mesh.Vertices.Add(*center)
    for j in range(len(indices)):
        k=(j+1)%len(indices)
        if reverse: mesh.Faces.AddFace(root,indices[k],indices[j])
        else: mesh.Faces.AddFace(root,indices[j],indices[k])


class _DegenerateSectionError(ValueError):
    pass


def mesh_sample_run(points,heights,width,ups,closed,tolerance):
    'Normally build a continuous tube; cap each original edge at degenerate transitions while preserving all edges and per-point widths/heights.'
    try:
        return _mesh_sample_run(points,heights,width,ups,closed,tolerance)
    except _DegenerateSectionError:
        import Rhino.Geometry as rg
        result=rg.Mesh()
        try:
            count=len(points) if closed else len(points)-1
            for i in range(count):
                j=(i+1)%len(points)
                try:
                    part,_,_= _mesh_sample_run([points[i],points[j]],
                        [heights[i],heights[j]],width,[ups[i],ups[j]],False,tolerance)
                except _DegenerateSectionError:
                    raise ValueError(
                        "第{}条边独立截面退化。诊断：P0={}；P1={}；H0={:.12g}；H1={:.12g}；"
                        "Width={:.12g}；G0={}；G1={}；Tolerance={:.12g}。".format(
                            i+1, points[i], points[j], heights[i], heights[j], width,
                            ups[i], ups[j], tolerance))
                try:
                    result.Append(part)
                finally:
                    part.Dispose()
            if not result.IsValid:
                raise ValueError("分段封口后的网格无效。")
            result.FaceNormals.ComputeFaceNormals()
            result.Normals.ComputeNormals()
            return result,0,count
        except Exception:
            result.Dispose()
            raise


def clean_preview_samples(points, heights, ups, closed, tolerance):
    """O(n) display-only cleanup; keep endpoints, corners and attribute changes."""
    if not (len(points) == len(heights) == len(ups)):
        raise ValueError("Path 逐点数组数量不一致。")
    epsilon = max(float(tolerance), 1e-12)
    kept = []
    def redundant(a, b, c):
        ac = _vsub(c[0], a[0]); ab = _vsub(b[0], a[0])
        length2 = _vdot(ac, ac)
        if length2 <= epsilon*epsilon: return False
        f = _vdot(ab, ac)/length2
        if not 0 < f < 1: return False
        if _distance(b[0], _vadd(a[0], _vmul(ac, f))) > epsilon: return False
        if abs(b[1] - (a[1]*(1-f)+c[1]*f)) > epsilon: return False
        return _distance(b[2], _blend_up(a[2], c[2], f)) <= 1e-3
    total = len(points)
    for i, (p, h, up) in enumerate(zip(points, heights, ups)):
        p = tuple(p); up = tuple(up)
        if not all(math.isfinite(v) for v in p) or not math.isfinite(h):
            raise ValueError("预览点坐标或高度无效。")
        item = (p, h, up)
        if kept and _distance(kept[-1][0], p) <= epsilon:
            # Open end must stay at its original endpoint, where possible.
            if not closed and i == total-1:
                while len(kept) > 1 and _distance(kept[-1][0], p) <= epsilon:
                    kept.pop()
                if _distance(kept[-1][0], p) > epsilon: kept.append(item)
            continue
        while len(kept) >= 2 and redundant(kept[-2], kept[-1], item):
            kept.pop()
        kept.append(item)
    if closed:
        while len(kept) > 1 and _distance(kept[-1][0],kept[0][0]) <= epsilon:
            kept.pop()
    return ([r[0] for r in kept], [r[1] for r in kept], [r[2] for r in kept])


def _mesh_sample_run(points,heights,width,ups,closed,tolerance):
    import Rhino.Geometry as rg
    points,heights,ups=clean_preview_samples(points,heights,ups,closed,tolerance)
    n=len(points)
    if n<(3 if closed else 2): raise ValueError("有效采样点不足。")
    if not (n==len(heights)==len(ups)): raise ValueError("Path 逐点数组数量不一致。")
    if not math.isfinite(width) or width<=2*tolerance or any(not math.isfinite(h) or h<=2*tolerance for h in heights):
        raise ValueError("Width 和每点 Height 必须大于两倍公差。")
    if n>MAX_SECTIONS: raise ValueError("截面数量超过上限。")

    samples,added_controls=make_section_samples(points,heights,width,ups,closed,tolerance)
    laterals=build_section_frames(samples,closed)
    count=len(samples)
    mesh=rg.Mesh()
    collapsed_faces=0

    try:
        mesh.Vertices.UseDoublePrecisionVertices=True
        # Use the area threshold only to detect numerically degenerate triangles, independently of Width or corner feasibility.
        area_epsilon=max(tolerance*tolerance*1e-4,1e-18)

        first_ring=None
        first_indices=None
        prev_ring=None
        prev_indices=None

        for i,(sample,lateral) in enumerate(zip(samples,laterals)):
            ring=section_ring(sample,lateral)
            indices=[]
            for vertex in ring:
                indices.append(mesh.Vertices.Add(*vertex))

            if i==0:
                first_ring=ring
                first_indices=indices
            else:
                for j in range(SECTION_SIDES):
                    collapsed_faces+=_add_strip_cell(mesh,prev_indices,indices,prev_ring,ring,j,area_epsilon)

            prev_ring=ring
            prev_indices=indices

        if closed:
            for j in range(SECTION_SIDES):
                collapsed_faces+=_add_strip_cell(mesh,prev_indices,first_indices,prev_ring,first_ring,j,area_epsilon)
        else:
            _add_cap(mesh,first_indices,first_ring,True)
            _add_cap(mesh,prev_indices,prev_ring,False)

        # Do not CombineIdentical / Weld; allow contact, self-intersection and local material overlap.
        if collapsed_faces:
            raise _DegenerateSectionError("局部截面连接退化。")
        if not mesh.IsValid:
            raise ValueError("Section-first 网格仍存在无效拓扑；请检查是否有完全重复采样点或异常 GrowthVector。")

        mesh.FaceNormals.ComputeFaceNormals()
        mesh.Normals.ComputeNormals()
        return mesh,added_controls,collapsed_faces
    except Exception:
        mesh.Dispose()
        raise


def split_normal_edges(points,heights,ups,closed):
    n=len(points)
    bad=[]
    for i in range(n if closed else n-1):
        delta=_vsub(points[(i+1)%n],points[i])
        if _distance(points[i],points[(i+1)%n])<=1e-12:
            bad.append(i); continue
        direction=_vunit(delta)
        if min(_vlength(_vcross(direction,ups[j])) for j in (i,(i+1)%n))<1e-6:
            bad.append(i)
    if not bad: return [(points,heights,ups,closed)],0
    order=list(range(n))
    if closed:
        start=(bad[0]+1)%n
        order=[(start+i)%n for i in range(n)]
    runs=[]; current=[]
    for index in order:
        current.append(index)
        if index in bad:
            if len(current)>1: runs.append(current)
            current=[]
    if len(current)>1: runs.append(current)
    return [([points[i] for i in r],[heights[i] for i in r],[ups[i] for i in r],False) for r in runs],len(bad)


def _xyz(value):
    return (value.X,value.Y,value.Z)


def preview_samples(source,width,tolerance):
    'Resample actual curves for display only; do not modify upstream data or extrusion paths.'
    points=[_xyz(p) for p in source.get("Points",())]
    heights=list(source.get("Heights",()))
    ups=[_vunit(_xyz(v)) for v in source.get("GrowthVectors",())]
    if not points or not (len(points)==len(heights)==len(ups)):
        raise ValueError("Path 逐点数组缺失或不对应。")
    if len(points)>MAX_SECTIONS:
        raise ValueError("截面数量超过上限。")
    if not math.isfinite(width) or width<=2*tolerance or any(
            not math.isfinite(h) or h<=2*tolerance for h in heights):
        raise ValueError("Width 和每点 Height 必须大于两倍公差。")
    curve=source.get("SampleCurve",source.get("Curve"))
    parameters=source.get("SampleParameters")
    if curve is None or parameters is None:
        if source.get("Sampling")=="segment_endpoints":
            raise ValueError("端点 Path 缺少真实采样曲线或参数，请重新生成上游。")
        return points,heights,ups
    parameters=list(parameters)
    if len(parameters)!=len(points) or any(not math.isfinite(t) for t in parameters):
        raise ValueError("Path 曲线参数与逐点数据不对应。")
    if any(b<a or (b==a and _distance(points[i],points[i+1])>tolerance)
           for i,(a,b) in enumerate(zip(parameters,parameters[1:]))):
        raise ValueError("Path 曲线参数顺序或点位无效。")
    if any(_distance(_xyz(curve.PointAt(t)),p)>tolerance for t,p in zip(parameters,points)):
        raise ValueError("Path 采样曲线与点位不一致，无法安全恢复弧线。")
    out_p,out_h,out_u=[points[0]],[heights[0]],[ups[0]]
    for i,(a,b) in enumerate(zip(parameters,parameters[1:])):
        if b==a:
            continue
        part=curve.Trim(a,b)
        if part is None:
            raise ValueError("无法提取预览曲线区间。")
        try:
            # Exact polylines need no further sampling; preserve internal corners instead of connecting only interval endpoints.
            ok,polyline=part.TryGetPolyline()
            if ok:
                count=polyline.Count-1
                interior=None
            else:
                length=part.GetLength()
                if not math.isfinite(length) or length<=0:
                    raise ValueError("预览曲线长度无效。")
                # Use at least two segments for short arcs to preserve curvature and four for full circles to prevent closure degeneracy.
                count=max(4 if part.IsClosed else 2,int(math.ceil(length/PATH_SEGMENT_LENGTH)))
                if len(out_p)+count>MAX_SECTIONS:
                    raise ValueError("曲线预览截面数量超过上限；未截断路径。")
                divided=part.DivideByCount(count,True)
                if divided is None:
                    raise ValueError("曲线按弧长分段失败。")
                interior=[t for t in divided if a<t<b]
                if len(interior)!=count-1:
                    raise ValueError("曲线分段参数数量异常。")
            if len(out_p)+count>MAX_SECTIONS:
                raise ValueError("曲线预览截面数量超过上限；未降低精度或截断路径。")
            previous=a
            for k in range(1,count):
                if interior is None:
                    p=polyline[k]
                    ok,t=part.ClosestPoint(p)
                    if not ok: raise ValueError("折线预览参数回映失败。")
                else:
                    t=interior[k-1]
                    p=part.PointAt(t)
                if not previous<t<b:
                    raise ValueError("曲线预览参数回映失败，不能保证属性对应。")
                previous=t
                f=(t-a)/(b-a)
                out_p.append(_xyz(p))
                out_h.append(heights[i]*(1-f)+heights[i+1]*f)
                out_u.append(_blend_up(ups[i],ups[i+1],f))
            out_p.append(points[i+1]); out_h.append(heights[i+1]); out_u.append(ups[i+1])
        finally:
            part.Dispose()
    return out_p,out_h,out_u


def preview_chunk_samples(chunk,width,tolerance):
    'Restore actual layer segments in continuous chunks using PointRange; preserve original connection edges and order.'
    if any(record.get("MotionKind") == "travel" for record in chunk.get("Segments", ())):
        raise ValueError("单条采样接口不能合并travel空走；请使用分段料条预览。")
    points=[_xyz(p) for p in chunk.get("Points",())]
    heights=list(chunk.get("Heights",()))
    ups=[_vunit(_xyz(v)) for v in chunk.get("GrowthVectors",())]
    if not points or not (len(points)==len(heights)==len(ups)):
        raise ValueError("连续 Path 逐点数组缺失或不对应。")
    out_p,out_h,out_u=[],[],[]
    cursor=0
    for record in chunk.get("Segments",()):
        if record.get("IsConnection") is True: continue
        if record.get("SampleCurve") is None or record.get("PointRange") is None: continue
        start,end=record["PointRange"]
        if not (cursor<=start<end<=len(points)):
            raise ValueError("连续 Path 层段 PointRange 无效或重叠。")
        original=[_xyz(p) for p in record.get("Points",())]
        if len(original)!=end-start or any(_distance(p,q)>tolerance
                for p,q in zip(original,points[start:end])):
            raise ValueError("连续 Path 层段与块点位不一致。")
        ps,hs,vs=preview_samples(record,width,tolerance)
        out_p.extend(points[cursor:start]); out_h.extend(heights[cursor:start]); out_u.extend(ups[cursor:start])
        out_p.extend(ps); out_h.extend(hs); out_u.extend(vs)
        cursor=end
        if len(out_p)>MAX_SECTIONS: raise ValueError("连续预览截面数量超过上限。")
    out_p.extend(points[cursor:]); out_h.extend(heights[cursor:]); out_u.extend(ups[cursor:])
    if len(out_p)>MAX_SECTIONS: raise ValueError("连续预览截面数量超过上限。")
    return out_p,out_h,out_u


def preview_chunk_runs(chunk, tolerance):
    """Read-only material runs: split at travel/width changes; never bridge air moves."""
    records = tuple(chunk.get("Segments", ()))
    if not records or all("MotionKind" not in r for r in records):
        width = float(chunk.get("Width"))
        ps, hs, vs = preview_chunk_samples(chunk, width, tolerance)
        return [(ps, hs, vs, width)]
    points = list(chunk.get("Points", ()))
    heights = list(chunk.get("Heights", ()))
    vectors = list(chunk.get("GrowthVectors", ()))
    if not (len(points) == len(heights) == len(vectors)):
        raise ValueError("连续 Path 逐点数组缺失或不对应。")
    edges = [False] * max(0, len(points)-1)
    runs, current = [], None
    last_end = None
    budget = 0
    for record in records:
        motion = record.get("MotionKind")
        if motion not in ("extrude", "travel"):
            raise ValueError("连续 Path 动作不完整，请重算连续路径并点击Run。")
        span = record.get("PointRange", ())
        if len(span) != 2:
            raise ValueError("连续 Path 段缺少PointRange。")
        start, end = span
        if (any(isinstance(i,bool) or not isinstance(i,int) for i in span)
                or not 0 <= start < end <= len(points) or end-start < 2):
            raise ValueError("连续 Path 段PointRange无效。")
        if last_end is not None and start < last_end-1:
            raise ValueError("连续 Path 段顺序或范围重叠。")
        for i in range(start,end-1):
            if edges[i]: raise ValueError("连续 Path 重复覆盖运动边。")
            edges[i] = True
        if motion == "travel":
            current = None
            last_end = end
            continue
        width = float(record.get("Width",chunk.get("Width")))
        local = dict(record)
        if chunk.get('PathMotionPolicy') == 'extrusion_only' and not record.get('IsConnection'):
            # Parameters belong to the current, seam-adjusted/trimmed Curve.
            # Never restore historical geometry or replace an arc with its chord.
            if record.get('Curve') is None or record.get('SampleParameters') is None:
                raise ValueError('连续 Path 缺少当前曲线或采样参数，请重算连续路径并点击Run。')
            local['SampleCurve'] = record['Curve']
        for key, values in (("Points",points),("Heights",heights),("GrowthVectors",vectors)):
            expected = values[start:end]
            if key in record:
                actual = record[key]
                if len(actual) != len(expected):
                    raise ValueError("连续 Path 段与块的{}数量不一致。".format(key))
                if key == "Points":
                    mismatch = any(_distance(_xyz(a),_xyz(b)) > tolerance for a,b in zip(actual,expected))
                elif key == "Heights":
                    mismatch = any(abs(a-b) > 1e-9*max(1,abs(b)) for a,b in zip(actual,expected))
                else:
                    mismatch = any(_distance(_vunit(_xyz(a)),_vunit(_xyz(b))) > 1e-9 for a,b in zip(actual,expected))
                if mismatch: raise ValueError("连续 Path 段与块的{}不对应。".format(key))
            local[key] = expected
        # Older planned connection records contain block ranges only.
        if record.get("IsConnection") is True and record.get("SampleCurve") is None:
            local.pop("Sampling",None)
            local.pop("Curve",None)
            local.pop("SampleParameters",None)
        ps, hs, vs = preview_samples(local,width,tolerance)
        budget += len(ps)
        if budget > MAX_SECTIONS:
            raise ValueError("连续预览截面数量超过上限。")
        join = (current is not None and last_end is not None and start in (last_end-1,last_end)
                and current[3] == width and _distance(current[0][-1],ps[0]) <= 1e-12
                and abs(current[1][-1]-hs[0]) <= 1e-12
                and _distance(current[2][-1],vs[0]) <= 1e-12)
        if join:
            current[0].extend(ps[1:]); current[1].extend(hs[1:]); current[2].extend(vs[1:])
        else:
            current = (ps,hs,vs,width)
            runs.append(current)
        last_end = end
    for i,covered in enumerate(edges):
        if not covered and _distance(_xyz(points[i]),_xyz(points[i+1])) > 0:
            raise ValueError("连续 Path 存在未定义动作的非零运动边。")
    return runs


def _run_component():
    global rg
    import Rhino.Geometry as rg
    import Grasshopper as gh
    component = ghenv.Component
    result = gh.DataTree[rg.Mesh]()
    globals()['Mesh'] = result
    try:
        if not _ensure_ports(component):
            return
        for param in component.Params.Input:
            if hasattr(param, 'Hidden'):
                param.Hidden = True
        (component.Name, component.NickName) = ('路径预览', 'PathPreview')
        component.Message = COMPONENT_MESSAGE
        for (wire, pi, data) in _path_packets([(w, i, v) for (w, values) in _tree_items(globals().get('Path')) for (i, v) in enumerate(values)]):
            tolerance = float(data.Metadata.get('Tolerance', 0.001))
            for (ci, chunk) in enumerate(data.Chunks):
                sources = [chunk] if data.Stage == 'continuous' else chunk.get('Segments', ())
                for (si, source) in enumerate(sources):
                    path = _gh_path(tuple(chunk['TreePath']) + (si,))
                    result.EnsurePath(path)
                    try:
                        if data.Stage == 'continuous':
                            if not chunk.get('Points') and (not chunk.get('Segments')):
                                continue
                            material_runs = preview_chunk_runs(chunk, tolerance)
                        else:
                            width = float(source.get('Width', chunk.get('Width')))
                            (points, heights, ups) = preview_samples(source, width, tolerance)
                            material_runs = [(points, heights, ups, width)]
                        control_total = collapsed_total = skipped_total = degenerate_total = 0
                        for (points, heights, ups, width) in material_runs:
                            closed = data.Stage == 'sliced' and source['Curve'].IsClosed
                            if closed and len(points) > 1 and (_distance(points[0], points[-1]) <= tolerance):
                                points.pop()
                                heights.pop()
                                ups.pop()
                            (points, heights, ups) = clean_preview_samples(points, heights, ups, closed, tolerance)
                            if len(points) < (3 if closed else 2):
                                degenerate_total += 1
                                continue
                            (runs, skipped) = split_normal_edges(points, heights, ups, closed)
                            skipped_total += skipped
                            for (pts, hs, vs, is_closed) in runs:
                                (generated, control_count, collapsed_count) = mesh_sample_run(pts, hs, width, vs, is_closed, tolerance)
                                result.Add(generated, path)
                                control_total += control_count
                                collapsed_total += collapsed_count
                        if degenerate_total:
                            component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Warning, _newpath_zh_message('{} 条预览路径清理后无有效长度，已跳过；其余料条正常生成。'.format(degenerate_total)))
                        if collapsed_total:
                            component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Warning, _newpath_zh_message('局部转接退化，已将受影响路径的 {} 条边改为独立封口料条；所有边均保留，接头允许重叠，未做布尔融合。'.format(collapsed_total)))
                        if skipped_total:
                            component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Warning, _newpath_zh_message('{} 条重复或平行层法线边无法定义横向宽度方向；已断开，未模拟这些边。'.format(skipped_total)))
                    except Exception as exc:
                        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))


if "ghenv" in globals():
    _run_component()


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = '路径料条预览'
    ghenv.Component.NickName = '路径料条预览'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'Data'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '根据路径的逐点高度和料条宽度生成挤出料条网格预览。'
    ghenv.Component.Tooltip = '根据路径的逐点高度和料条宽度生成挤出料条网格预览。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '带采样点、逐点高度和 Width 的 Path'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Mesh': '逐点变截面料条网格'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
