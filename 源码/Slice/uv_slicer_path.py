"""UV方向切片

【功能】
沿曲面的指定参数方向生成保形切片，并记录逐点高度和生长方向。

【输入端】
Geometry（通用对象；数据树；必填）：待切片曲面/Brep几何树。
LayerHeight（数值；单项；必填）：名义层高/步长，模型单位。
Width（数值；单项；必填）：料条宽度，随 Path 传递。
Direction（数值；单项；可选）：UV切片方向：0 为沿 U 方向曲线（沿 V 步进），1 为沿 V 方向曲线（沿 U 步进），默认 0。

【输出端】
Path（通用对象；单项）：一个携带曲线、层高、来源及层号的 Path 数据包。
Preview（曲线；数据树）：Path 内切片曲线的副本，可预览或接 Curve。

【详细用法与约束】
用途：从 Brep / Surface / Extrusion / SubD 几何体提取 UV 方向 IsoCurve（等参数线）生成保形表面切片路径，支持多面 Brep 与拓扑分束排序，接入 PATH 工作流。
输入：Geometry / Tree / object / 必填，待切片几何树；
LayerHeight / Item / float / 必填，名义层高/步长（模型单位）；
Width / Item / float / 必填，料条宽度，随 Path 传递；
Direction / Item / number / 可选，默认 0：0 为沿 U 方向生成 IsoCurve（沿 V 方向步进），1 为沿 V 方向生成 IsoCurve（沿 U 方向步进）。
输出：Path / Item / object，携带曲线、层高、来源及层号的 Path 数据包；
Preview / Tree，Path 内切片曲线的副本，可直接预览或接 Curve。
切片规则：
1. 去除紧贴底面/打印平面的第0层路径（切片从 1 * LayerHeight 完整层高起步），底线作为计算首层高度的支撑参照。
2. 沿 IsoCurve 离散采样点，根据与上一层对应轮廓的真实 3D 空间层间距计算逐点高度 Heights 与局部生长向量 GrowthVectors，真实呈现各层内不同点根据局部曲面几何变化而形成的非均匀高度。
3. 内部 ChunkHeight=10，保留完整拓扑分析分束与喷嘴就近排序。
4. Role=wall，切片轮廓直接表示墙体路径。
这是 Python 自定义对象原型，非已注册 .gha 类型；不支持 Internalise 持久化，重开由上游重算。

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
    ghenv.Component.Description = '沿曲面的指定参数方向生成保形切片，并记录逐点高度和生长方向。'
    ghenv.Component.Tooltip = '沿曲面的指定参数方向生成保形切片，并记录逐点高度和生长方向。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Geometry': '待切片曲面/Brep几何树', 'LayerHeight': '名义层高/步长，模型单位', 'Width': '料条宽度，随 Path 传递', 'Direction': 'UV切片方向：0 为沿 U 方向曲线（沿 V 步进），1 为沿 V 方向曲线（沿 U 步进），默认 0'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '一个携带曲线、层高、来源及层号的 Path 数据包', 'Preview': 'Path 内切片曲线的副本，可预览或接 Curve'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description



PORT_ALIASES = {}
import math
import Rhino
import Rhino.Geometry as rg
import rhinoscriptsyntax as rs

COMPONENT_MARKER = 'UVIsoSlicer:r8'
COMPONENT_MESSAGE = "UV Iso Slicer\nPath + Preview"
CHUNK_HEIGHT = 10.0
MAX_LAYERS = 100000
MAX_SAMPLES = 200000

INPUT_SPECS = [
    ("Geometry", "Geometry", '待切片曲面/Brep几何树', "tree", "object", False),
    ("LayerHeight", "LayerHeight", '名义层高/步长，模型单位', "item", "number", False),
    ("Width", "Width", '料条宽度，随 Path 传递', "item", "number", False),
    ("Direction", "Direction", 'UV切片方向：0 为沿 U 方向曲线（沿 V 步进），1 为沿 V 方向曲线（沿 U 步进），默认 0', "item", "number", True),
]
OUTPUT_SPECS = [
    ("Path", "Path", '一个携带曲线、层高、来源及层号的 Path 数据包', "item"),
    ("Preview", "Preview", 'Path 内切片曲线的副本，可预览或接 Curve', "tree"),
]

# Keep the Path protocol consistent across standalone components; do not use Python class identity for cross-component data.
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

class _PathData:
    'Do not implement iteration; pass as one data item and treat geometry/metadata as read-only.'
    Protocol = PATH_PROTOCOL
    SchemaVersion = PATH_SCHEMA

    def __init__(self, stage, chunks, metadata=None):
        self.Stage = stage
        self.Chunks = tuple(chunks)
        self.Metadata = dict(metadata or {})

    @property
    def LayerHeight(self):
        "Return the common nominal layer height, or None for mixed heights; downstream consumers must read each chunk's LayerHeight."
        values = {float(c["LayerHeight"]) for c in self.Chunks}
        if len(values) == 1:
            return next(iter(values))
        return self.Metadata.get("NominalLayerHeight") if not values else None

    @property
    def Curves(self):
        return tuple(curve for c in self.Chunks for curve in c["Curves"])

    @property
    def CurveCount(self):
        return sum(len(c["Curves"]) for c in self.Chunks)

    @property
    def LayerCount(self):
        return len({(s["GeoId"], s["LayerIndex"]) for c in self.Chunks
                    for s in c["Segments"] if s.get("LayerIndex") is not None})

    @property
    def AverageLayerHeight(self):
        heights = [s["LayerHeight"] for c in self.Chunks for s in c["Segments"]
                   if s.get("LayerIndex") is not None]
        return sum(heights) / len(heights) if heights else 0.0

    def __str__(self):
        return "Path with {} layers, {} curves, {:g} average nominal layer height [{}]".format(
            self.LayerCount, self.CurveCount, self.AverageLayerHeight, self.Stage)

    def __repr__(self):
        return str(self)

    def ToString(self):
        return str(self)


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


def _preview_tree(data):
    from Grasshopper import DataTree
    result = DataTree[rg.Curve]()
    for indices in data.Metadata.get("EmptyPaths", ()):
        result.EnsurePath(_gh_path(indices))
    for chunk in data.Chunks:
        path = _gh_path(chunk["TreePath"])
        result.EnsurePath(path)
        # Output copies so downstream preview edits cannot modify internal Path data.
        for curve in chunk["Curves"]:
            result.Add(curve.DuplicateCurve(), path)
    return result


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


def unwrap(value):
    for _ in range(4):
        other = getattr(value, "Value", value)
        if other is value:
            break
        value = other
    types = tuple(t for t in (getattr(rg, "GeometryBase", None), getattr(rg, "Plane", None)) if t is not None)
    if (types and isinstance(value, types)) or value is None:
        return value
    try:
        return rs.coercegeometry(value) or value
    except Exception:
        return value


def sample_curve_points(curve, max_step=4.0, min_points=4):
    'Sample points along curve arc length with sufficient interior points to capture local changes in layer spacing.'
    length = curve.GetLength()
    if not math.isfinite(length) or length <= 1e-6:
        pts = [curve.PointAtStart, curve.PointAtEnd]
        ts = [curve.Domain.Min, curve.Domain.Max]
        return pts, ts

    count = max(8 if curve.IsClosed else min_points, int(math.ceil(length / max_step)))
    count = min(count, 500)

    divided = None
    if hasattr(curve, "DivideByCount"):
        try:
            divided = curve.DivideByCount(count, True)
        except Exception:
            divided = None

    if divided is not None and len(divided) >= 2:
        params = list(divided)
        points = [curve.PointAt(t) for t in params]
    else:
        d_min, d_max = curve.Domain.Min, curve.Domain.Max
        params = [d_min + (d_max - d_min) * (k / float(count)) for k in range(count + 1)]
        points = [curve.PointAt(t) for t in params]

    if hasattr(curve, "TryGetPolyline"):
        try:
            ok, polyline = curve.TryGetPolyline()
            if ok and polyline and getattr(polyline, "Count", 0) >= 2:
                vert_params = []
                for k in range(polyline.Count):
                    ok_cp, t_cp = curve.ClosestPoint(polyline[k])
                    if ok_cp:
                        vert_params.append(t_cp)
                if vert_params:
                    all_params = sorted(set(params + vert_params))
                    params = all_params
                    points = [curve.PointAt(t) for t in params]
        except Exception:
            pass

    return points, params


def slice_uv_geometry(geo, height, direction=0, tol=0.001, ang_tol=0.01745):
    'Extract UV IsoCurve from Brep/Surface geometry and organize curves by layer, excluding layer 0 on the bottom face.'
    if not math.isfinite(height) or height <= 0:
        raise ValueError("LayerHeight 必须是有限正数。")

    # Convert and validate geometry types.
    if isinstance(geo, rg.SubD):
        geo = rg.Brep.CreateFromSubD(geo, True)
    elif isinstance(geo, (rg.Extrusion, rg.Surface)):
        geo = geo.ToBrep()
    elif isinstance(geo, rg.Mesh):
        raise ValueError("UV方向切片需要 Brep、Surface、Extrusion 或 SubD 几何体，网格 (Mesh) 无 UV 参数域。")

    if not isinstance(geo, rg.Brep) or not geo.IsValid:
        raise ValueError("需要有效的 Brep、Surface、Extrusion 或 SubD 几何体。")

    dir_code = int(direction) if direction is not None else 0
    # crv_dir: IsoCurve direction (0=along U, 1=along V).
    # step_dir: slice growth/step direction (0=step along U, 1=step along V).
    if dir_code == 0:
        crv_dir = 0   # U-direction IsoCurve (constant V).
        step_dir = 1  # Advance along V.
    else:
        crv_dir = 1   # V-direction IsoCurve (constant U).
        step_dir = 0  # Advance along U.

    iso_curves_all = []
    base_curves_raw = []
    messages = []

    for fi, face in enumerate(geo.Faces):
        step_domain = face.Domain(step_dir)
        other_domain = face.Domain(crv_dir)
        other_mid = 0.5 * (other_domain.Min + other_domain.Max)

        # Try computing physical surface arc length from a guide isocurve.
        guide_crv = face.IsoCurve(step_dir, other_mid)
        has_3d_guide = False
        step_len = step_domain.Max - step_domain.Min

        if guide_crv and guide_crv.IsValid:
            crv_len = guide_crv.GetLength()
            if math.isfinite(crv_len) and crv_len > tol:
                step_len = crv_len
                has_3d_guide = True

        ratio = step_len / height
        if not math.isfinite(ratio) or ratio > MAX_LAYERS:
            raise ValueError("切片层数超过上限，请检查模型单位与层高。")

        # Extract the baseline on the print plane (s = 0.0) as support for first-layer height calculation; exclude it from output slices.
        base_param = step_domain.Min
        raw_base = face.IsoCurve(crv_dir, base_param)
        if raw_base:
            base_list = raw_base if isinstance(raw_base, (list, tuple)) else [raw_base]
            for c in base_list:
                if c and c.IsValid:
                    sim = c.Simplify(rg.CurveSimplifyOptions.All, 0.1, ang_tol)
                    base_curves_raw.append(sim if sim else c)

        # Start slices at i = 1 (one full LayerHeight), omitting layer 0 on the print plane.
        v_count = int(math.floor(ratio + 1e-12))
        face_isos = []

        for i in range(1, v_count + 1):
            s = min(i * height, step_len)

            if has_3d_guide:
                ok, param = guide_crv.LengthParameter(s)
                if not ok:
                    t_frac = s / step_len if step_len > 0 else 0.0
                    param = step_domain.Min + t_frac * (step_domain.Max - step_domain.Min)
            else:
                param = step_domain.Min + min(i * height, step_domain.Max - step_domain.Min)

            if param < step_domain.Min - 1e-9 or param > step_domain.Max + 1e-9:
                continue
            param = max(step_domain.Min, min(step_domain.Max, param))

            raw_crv = face.IsoCurve(crv_dir, param)
            if raw_crv:
                crv_list = raw_crv if isinstance(raw_crv, (list, tuple)) else [raw_crv]
                for c in crv_list:
                    if c and c.IsValid:
                        sim = c.Simplify(rg.CurveSimplifyOptions.All, 0.1, ang_tol)
                        # Assign zero-based layer indices.
                        face_isos.append((i - 1, sim if sim else c))

        iso_curves_all.append(face_isos)

    # Organize baselines.
    base_curves = []
    if base_curves_raw:
        if len(base_curves_raw) > 1:
            joined_base = rg.Curve.JoinCurves(base_curves_raw, tol * 2.0)
            base_curves = list(joined_base) if joined_base else base_curves_raw
        else:
            base_curves = base_curves_raw
    base_curves = [c for c in base_curves if c and c.IsValid]

    # Organize the hierarchy by grouping curves by layer index.
    max_layers = 0
    for face_isos in iso_curves_all:
        for idx, _ in face_isos:
            if idx + 1 > max_layers:
                max_layers = idx + 1

    layers_raw = [[] for _ in range(max_layers)]
    for face_isos in iso_curves_all:
        for idx, crv in face_isos:
            layers_raw[idx].append(crv)

    # Try joining curves within each layer across faces of multi-face Breps.
    layers = []
    for raw in layers_raw:
        if not raw:
            layers.append([])
            continue
        if len(raw) > 1:
            joined = rg.Curve.JoinCurves(raw, tol * 2.0)
            curves = list(joined) if joined else raw
        else:
            curves = raw
        valid_curves = [c for c in curves if c and c.IsValid]
        layers.append(valid_curves)

    if not any(layers):
        messages.append("切片范围不足一个 LayerHeight 或未得到有效切片曲线。")

    return layers, base_curves, messages


def compute_uv_layer_planes_and_normals(layers, tol):
    planes_dict = {}
    normals_list = []

    for li, curves in enumerate(layers):
        norm = None
        if li + 1 < len(layers) and layers[li + 1]:
            if curves:
                c_curr = curves[0]
                t_mid = 0.5 * (c_curr.Domain.Min + c_curr.Domain.Max)
                p_curr = c_curr.PointAt(t_mid)
                nearest_next = None
                min_d = float("inf")
                for c_next in layers[li + 1]:
                    ok, t_next = c_next.ClosestPoint(p_curr)
                    if ok:
                        p_next = c_next.PointAt(t_next)
                        d = p_curr.DistanceTo(p_next)
                        if d < min_d:
                            min_d = d
                            nearest_next = p_next
                if nearest_next is not None:
                    v = nearest_next - p_curr
                    if v.Unitize():
                        norm = v
        if norm is None and li > 0 and layers[li - 1]:
            if curves:
                c_curr = curves[0]
                t_mid = 0.5 * (c_curr.Domain.Min + c_curr.Domain.Max)
                p_curr = c_curr.PointAt(t_mid)
                nearest_prev = None
                min_d = float("inf")
                for c_prev in layers[li - 1]:
                    ok, t_prev = c_prev.ClosestPoint(p_curr)
                    if ok:
                        p_prev = c_prev.PointAt(t_prev)
                        d = p_curr.DistanceTo(p_prev)
                        if d < min_d:
                            min_d = d
                            nearest_prev = p_prev
                if nearest_prev is not None:
                    v = p_curr - nearest_prev
                    if v.Unitize():
                        norm = v
        if norm is None:
            norm = rg.Vector3d.ZAxis
        normals_list.append(norm)

        for curve in curves:
            ok, plane = curve.TryGetPlane(tol)
            if not ok or not plane.IsValid:
                t_mid = 0.5 * (curve.Domain.Min + curve.Domain.Max)
                p_mid = curve.PointAt(t_mid)
                t_tan = curve.TangentAt(t_mid)
                pts = [curve.PointAt(curve.Domain.Min + (curve.Domain.Max - curve.Domain.Min) * (k / 10.0)) for k in range(11)]
                ok_fit, fit_plane = rg.Plane.FitPlaneToPoints(pts)
                if ok_fit and fit_plane.IsValid:
                    plane = fit_plane
                else:
                    ref_vec = norm if abs(norm * t_tan) < 0.99 else rg.Vector3d.ZAxis
                    plane_norm = rg.Vector3d.CrossProduct(t_tan, ref_vec)
                    if not plane_norm.Unitize():
                        plane_norm = rg.Vector3d.ZAxis
                    plane = rg.Plane(p_mid, plane_norm)
            planes_dict[id(curve)] = plane

    return planes_dict, normals_list


def sample_uv_layers(layers, normals, height, tolerance, base_curves=None):
    'Sample endpoints and interior points in actual geometric layer order; compute per-point heights and growth directions from actual spacing to the previous layer.'
    result = {}
    previous = list(base_curves) if base_curves else []
    previous_index = -1 if base_curves else None
    total_samples = 0
    sample_step = max(1.0, min(4.0, height / 2.0 if height > 0 else 2.0))

    for li, curves in enumerate(layers):
        if not curves:
            continue
        for curve in curves:
            points, parameters = sample_curve_points(curve, max_step=sample_step)
            total_samples += len(points)
            if total_samples > MAX_SAMPLES:
                raise ValueError("单几何采样点总数超过上限。")
            heights, vectors, support_indices = [], [], []

            for point_index, point in enumerate(points):
                if curve.IsClosed and point_index == len(points) - 1:
                    heights.append(heights[0])
                    vectors.append(rg.Vector3d(vectors[0]))
                    support_indices.append(support_indices[0])
                    continue

                if not previous:
                    h, vector, support = height, rg.Vector3d(normals[li]), -1
                else:
                    nearest, support, distance = None, -1, float("inf")
                    for ci, lower in enumerate(previous):
                        success, parameter = lower.ClosestPoint(point)
                        if not success:
                            continue
                        candidate = lower.PointAt(parameter)
                        current = point.DistanceTo(candidate)
                        if current < distance:
                            nearest, support, distance = candidate, ci, current

                    if nearest is None or not math.isfinite(distance) or distance <= tolerance:
                        h = height
                        vector = rg.Vector3d(normals[li])
                    else:
                        disp = point - nearest
                        vector = rg.Vector3d(disp)
                        if vector.Unitize():
                            h = distance
                        else:
                            vector = rg.Vector3d(normals[li])
                            h = height

                if not vector.Unitize():
                    vector = rg.Vector3d.ZAxis
                heights.append(h)
                vectors.append(vector)
                support_indices.append(support)

            result[id(curve)] = dict(
                Points=tuple(points),
                SampleParameters=tuple(parameters),
                Heights=tuple(heights),
                GrowthVectors=tuple(vectors),
                SampleCurve=curve,
                IsFirstLayer=(previous_index == -1 or previous_index is None),
                SupportLayerIndex=previous_index,
                SupportCurveIndices=tuple(support_indices),
                Sampling="segment_endpoints",
                HeightMetric="interlayer_distance",
                HeightMethod="interlayer_local_distance" if previous else "nominal_first_layer"
            )
        previous, previous_index = curves, li
    return result


def sequence_layers(temp_results, layer_height, last_nozzle_pos):
    d_merge = layer_height * 12.0
    merge_layer_limit = max(1, int(CHUNK_HEIGHT * 2.0 / layer_height))
    step_limit = max(1, int(CHUNK_HEIGHT / layer_height))
    all_final_chunks = []
    raw_layers = temp_results
    if not any(raw_layers):
        return [], last_nozzle_pos
    first_split, last_split = len(raw_layers), -1
    for i in range(len(raw_layers)):
        if len(raw_layers[i]) > 1:
            if i < first_split:
                first_split = i
            if i > last_split:
                last_split = i

    effective_last_split = last_split if last_split != -1 else len(raw_layers) - 1
    cap_start_idx = last_split + 1 if last_split != -1 else len(raw_layers)
    cap_curves = []
    if cap_start_idx < len(raw_layers):
        for i in range(cap_start_idx, len(raw_layers)):
            cap_curves.extend(raw_layers[i])

    strands = []
    for layer_idx in range(effective_last_split + 1):
        curves = raw_layers[layer_idx]
        used_indices = [False] * len(curves)

        # Try connecting existing strands.
        for s in strands:
            if s["last_layer"] == layer_idx - 1:
                best_idx, min_d = -1, layer_height * 15
                for i, crv in enumerate(curves):
                    if not used_indices[i]:
                        d = crv.GetBoundingBox(True).Center.DistanceTo(s["last_cp"])
                        if d < min_d:
                            min_d, best_idx = d, i
                if best_idx != -1:
                    s["curves"].append(curves[best_idx])
                    s["last_cp"] = curves[best_idx].GetBoundingBox(True).Center
                    s["last_layer"] = layer_idx
                    used_indices[best_idx] = True

        # Create new strands.
        for i, crv in enumerate(curves):
            if not used_indices[i]:
                strands.append({
                    "curves": [crv],
                    "start_layer": layer_idx,
                    "last_layer": layer_idx,
                    "last_cp": crv.GetBoundingBox(True).Center
                })

    is_base_boosted = False
    while True:
        remaining = [s for s in strands if len(s["curves"]) > 0]
        if not remaining:
            break

        current_chunk = []
        min_z_in_remaining = min(s["start_layer"] for s in remaining)
        candidates = [s for s in remaining if s["start_layer"] <= min_z_in_remaining + 1]

        # Sort by proximity to the nozzle.
        candidates.sort(key=lambda s: s["curves"][0].PointAtStart.DistanceTo(last_nozzle_pos))

        current_strand = candidates[0]
        chunk_base_z = current_strand["start_layer"]

        while current_strand:
            if not is_base_boosted and current_strand["start_layer"] < first_split:
                h_take = (first_split - current_strand["start_layer"]) + step_limit
                is_base_boosted = True
            else:
                h_take = min(step_limit, merge_layer_limit - (current_strand["start_layer"] - chunk_base_z))

            h_take = min(h_take, len(current_strand["curves"]))
            if h_take <= 0:
                break

            seg = current_strand["curves"][:h_take]
            current_chunk.extend(seg)
            last_nozzle_pos = seg[-1].PointAtEnd
            current_strand["curves"] = current_strand["curves"][h_take:]
            current_strand["start_layer"] += h_take

            next_strand = None
            if (current_strand["start_layer"] - chunk_base_z) < merge_layer_limit:
                potential_neighbors = [s for s in strands if len(s["curves"]) > 0 and abs(s["start_layer"] - current_strand["start_layer"]) <= 1]
                if potential_neighbors:
                    potential_neighbors.sort(key=lambda s: s["curves"][0].PointAtStart.DistanceTo(last_nozzle_pos))
                    if potential_neighbors[0]["curves"][0].PointAtStart.DistanceTo(last_nozzle_pos) < d_merge:
                        next_strand = potential_neighbors[0]
            current_strand = next_strand

        if len([s for s in strands if len(s["curves"]) > 0]) == 0:
            current_chunk.extend(cap_curves)

        if current_chunk:
            all_final_chunks.append(current_chunk)
            last_nozzle_pos = current_chunk[-1].PointAtEnd

    return all_final_chunks, last_nozzle_pos


def build_uv_sliced_path(geo_tree, height, direction=0, *args, **kwargs):
    if not math.isfinite(height) or height <= 0:
        raise ValueError('LayerHeight 必须是有限正数。')
    producer_id = args[0] if len(args) > 0 else kwargs.get('producer_id', '')
    tol = args[1] if len(args) > 1 else kwargs.get('tol', 0.001)
    ang_tol = args[2] if len(args) > 2 else kwargs.get('angle', math.radians(1))
    units = args[3] if len(args) > 3 else kwargs.get('units', 'None')
    width = kwargs.get('width') if 'width' in kwargs else args[4] if len(args) > 4 else None
    if width is None or not math.isfinite(float(width)) or float(width) <= 0:
        raise ValueError('Width 必须是有限正数。')
    width = float(width)
    (chunks, notices, empty_paths) = ([], [], [])
    nozzle = rg.Point3d.Origin
    for (source_path, values) in _tree_items(geo_tree):
        if not values:
            empty_paths.append(source_path)
        for (index, value) in enumerate(values):
            geo_id = '{}:{}:{}'.format(producer_id, ';'.join(map(str, source_path)), index)
            try:
                geo = unwrap(value)
                if geo is None:
                    raise ValueError('几何体为空。')
                (layers, base_curves, messages) = slice_uv_geometry(geo, height, direction, tol, ang_tol)
                if not any(layers):
                    empty_paths.append(source_path + (index, 0))
                    notices.extend((('warning', '{}[{}]：{}'.format(source_path, index, m)) for m in messages))
                    continue
                (planes_dict, normals) = compute_uv_layer_planes_and_normals(layers, tol)
                samples = sample_uv_layers(layers, normals, height, tol, base_curves)
                layer_lookup = {id(curve): (li, ci) for (li, layer) in enumerate(layers) for (ci, curve) in enumerate(layer)}
                (ordered, nozzle) = sequence_layers(layers, height, nozzle)
                if not ordered:
                    empty_paths.append(source_path + (index, 0))
                for (chunk_index, curves) in enumerate(ordered):
                    records = []
                    for curve in curves:
                        (li, ci) = layer_lookup[id(curve)]
                        curve_plane = planes_dict.get(id(curve), rg.Plane.WorldXY)
                        seg_samples = samples[id(curve)]
                        seg_heights = seg_samples['Heights']
                        seg_h_avg = sum(seg_heights) / len(seg_heights) if seg_heights else height
                        rec = dict(Curve=curve, LayerIndex=li, LayerHeight=seg_h_avg, Plane=curve_plane, LayerNormal=normals[li], GeoId=geo_id, ChunkId=chunk_index, SegmentId='{}:layer{}:curve{}'.format(geo_id, li, ci), Role='wall', SourcePath=source_path, GeoIndex=index, Width=width)
                        rec.update(seg_samples)
                        records.append(rec)
                    first_plane = records[0]['Plane'] if records else rg.Plane.WorldXY
                    last_plane = records[-1]['Plane'] if records else rg.Plane.WorldXY
                    chunk_h_avg = sum((r['LayerHeight'] for r in records)) / len(records) if records else height
                    chunks.append(dict(TreePath=source_path + (index, chunk_index), SourcePath=source_path, GeoIndex=index, GeoId=geo_id, ChunkId=chunk_index, LayerHeight=chunk_h_avg, Width=width, SlicingMode='UVIso', BottomPlane=first_plane, TopPlane=last_plane, Curves=tuple(curves), Segments=tuple(records), Points=tuple((p for r in records for p in r['Points'])), Heights=tuple((h for r in records for h in r['Heights'])), GrowthVectors=tuple((v for r in records for v in r['GrowthVectors']))))
                notices.extend((('warning', '{}[{}]：{}'.format(source_path, index, m)) for m in messages))
            except Exception as exc:
                empty_paths.append(source_path + (index, 0))
                notices.append(('error', '{}[{}]：{}'.format(source_path, index, exc)))
    data = _path_output('sliced', chunks, dict(Producer=producer_id, Units=units, Tolerance=tol, AngleTolerance=ang_tol, EmptyPaths=tuple(empty_paths), NominalLayerHeight=height, Width=width, Direction=int(direction) if direction is not None else 0, Sampling='segment_endpoints', HeightMetric='interlayer_distance', SlicingMode='UVIso'))
    return (data, notices)


def _run_component():
    import Grasshopper as gh
    from Grasshopper.Kernel.Types import GH_ObjectWrapper
    component = ghenv.Component
    globals()["Path"] = None
    globals()["Preview"] = gh.DataTree[rg.Curve]()
    try:
        if not _ensure_ports(component):
            return
        for param in component.Params.Output:
            if getattr(param, "VariableName", param.NickName) == "Path" and hasattr(param, "Hidden"):
                param.Hidden = True
        component.Name, component.NickName = "UV方向切片", "UVIsoSlicer"
        component.Message = COMPONENT_MESSAGE
        geometry_val = globals().get("Geometry")
        lh_val = globals().get("LayerHeight")
        width_val = globals().get("Width")
        dir_val = globals().get("Direction")
        if geometry_val is None or lh_val is None or width_val is None:
            return
        doc = Rhino.RhinoDoc.ActiveDoc
        tol = doc.ModelAbsoluteTolerance if doc else 0.001
        angle = doc.ModelAngleToleranceRadians if doc else math.radians(1)
        units = str(doc.ModelUnitSystem) if doc else "None"
        direction = int(dir_val) if dir_val is not None else 0
        data, notices = build_uv_sliced_path(
            geometry_val, float(lh_val), direction,
            str(component.InstanceGuid), tol, angle, units, float(width_val)
        )
        globals()["Path"] = GH_ObjectWrapper(data)
        globals()["Preview"] = _preview_tree(data)
        component.Message = "UV Iso Slicer\n{} layers | {} curves".format(data.LayerCount, data.CurveCount)
        for level, message in notices:
            component.AddRuntimeMessage(getattr(gh.Kernel.GH_RuntimeMessageLevel, level.title()), _newpath_zh_message(message))
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))


if "ghenv" in globals():
    _run_component()


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = 'UV方向切片'
    ghenv.Component.NickName = 'UV方向切片'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'Slice'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '沿曲面的指定参数方向生成保形切片，并记录逐点高度和生长方向。'
    ghenv.Component.Tooltip = '沿曲面的指定参数方向生成保形切片，并记录逐点高度和生长方向。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Geometry': '待切片曲面/Brep几何树', 'LayerHeight': '名义层高/步长，模型单位', 'Width': '料条宽度，随 Path 传递', 'Direction': 'UV切片方向：0 为沿 U 方向曲线（沿 V 步进），1 为沿 V 方向曲线（沿 U 步进），默认 0'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '一个携带曲线、层高、来源及层号的 Path 数据包', 'Preview': 'Path 内切片曲线的副本，可预览或接 Curve'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
