"""智能切片

【功能】
按层高与端部平面切片几何，输出携带料条宽度和逐点属性的路径。

【输入端】
Geometry（通用对象；数据树；必填）：待切片几何树。
LayerHeight（数值；单项；必填）：名义层高，模型单位。
Width（数值；单项；必填）：料条宽度，随 Path 传递。
Planes（通用对象；列表；可选）：限定切片区间的平面或闭合平面边界列表。

【输出端】
Path（通用对象；单项）：一个携带曲线、层高、来源及层号的 Path 数据包。
Preview（曲线；数据树）：Path 内切片曲线的副本，可预览或接 Curve。

【详细用法与约束】
用途：保留 Smart Slicer 自动端面识别、PlaneToPlane / ZContour；只切片，不分束、不按高度分块、不做打印排序。
输入：Geometry / Tree / object / 必填，支持 Brep、Surface、Extrusion、SubD、Mesh、GUID。
LayerHeight、Width / Item / float / 必填；Planes / List / object / 可选。
端部允许多个 Plane 或闭合平面边界，自动识别最低为 bottom、最高为 top，留空自动判断；超距直接报错；上下按世界 Z。
输出：Path / Item / object，一个完整数据包；Preview / Tree，包内曲线副本可直接预览。
Path 保存每段 LayerNormal 单位上升法向量、名义层高、层号、端部平面、来源路径、GeoId、ChunkId、Role 和模型单位/公差。
仅取原始子段端点；矩形5点、8段组合线9点，不细分圆弧或光滑曲线。点仅存包内。
每段保存 Points/Heights/GrowthVectors/SampleParameters；首层名义高度，其余取当前端点与上一非空层最近对应点的当前层坐标系的局部Z差值，不截断。
对应点使用下层轮廓最近点，复杂分叉可能匹配到邻近轮廓，不代表挤出后实测厚度。
Role=wall，切片轮廓直接表示墙体路径。GeoId 按组件和输入树位置命名。
公差与切片几何行为沿用 smart_slicer.py；完整层高起切，无额外 Z 抬升。
每个输入几何一个传输Chunk，按真实层序及层内原顺序保存全部轮廓；ChunkId=0不是打印分组。需要打印分组时接路径分组电池。
自动端面不明确则回退并提示；PlaneToPlane 层高沿原点连线，不承诺法向等厚。
预览端可用于普通 Curve 电池；后续 Continuous Path 必须连接 Path 端，不经过 Panel。
这是 Python 自定义对象原型，不是已注册的 .gha 类型；不支持 Internalise 持久化。
保存重开后由上游重新计算 Path。

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
    ghenv.Component.Description = '按层高与端部平面切片几何，输出携带料条宽度和逐点属性的路径。'
    ghenv.Component.Tooltip = '按层高与端部平面切片几何，输出携带料条宽度和逐点属性的路径。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Geometry': '待切片几何树', 'LayerHeight': '名义层高，模型单位', 'Width': '料条宽度，随 Path 传递', 'Planes': '限定切片区间的平面或闭合平面边界列表'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '一个携带曲线、层高、来源及层号的 Path 数据包', 'Preview': 'Path 内切片曲线的副本，可预览或接 Curve'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description



PORT_ALIASES = {'Geo': 'Geometry'}
import math
import Rhino
import Rhino.Geometry as rg
import rhinoscriptsyntax as rs

COMPONENT_MARKER = 'SmartSlicer:r15'
COMPONENT_MESSAGE = "Smart Slicer\nPath + Preview"
MAX_LAYERS = 100000
MAX_SAMPLES = 200000
MAX_AUTO_MESH_FACES = 20000
INPUT_SPECS = [
    ("Geometry", "Geometry", '待切片几何树', "tree", "object", False),
    ("LayerHeight", "LayerHeight", '名义层高，模型单位', "item", "number", False),
    ("Width", "Width", '料条宽度，随 Path 传递', "item", "number", False),
    ("Planes", "Planes", '限定切片区间的平面或闭合平面边界列表', "list", "object", True),
]
OUTPUT_SPECS = [
    ("Path", "Path", '一个携带曲线、层高、来源及层号的 Path 数据包', "item"),
    ("Preview", "Preview", 'Path 内切片曲线的副本，可预览或接 Curve', "tree"),
]

# Keep the Path protocol consistent across both standalone components; do not use Python class identity for cross-component data.
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
        return sum(heights)/len(heights) if heights else 0.0

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


def boundary_plane(curve, tol):
    if not curve or not curve.IsValid or not curve.IsClosed:
        return None
    ok, plane = curve.TryGetPlane(tol)
    if not ok:
        return None
    caps = rg.Brep.CreatePlanarBreps(curve, tol)
    if not caps:
        return None
    try:
        if len(caps) != 1 or not caps[0].IsValid or caps[0].Faces.Count != 1 or caps[0].Loops.Count != 1:
            return None
        mass = rg.AreaMassProperties.Compute(caps[0])
        if mass is None:
            return None
        try:
            if mass.Area <= tol*tol:
                return None
            return rg.Plane(mass.Centroid, plane.Normal)
        finally:
            mass.Dispose()
    finally:
        for cap in caps:
            cap.Dispose()


def custom_plane(value, tol, name):
    value = unwrap(value)
    if value is None:
        return None
    plane = None
    if isinstance(value, rg.Plane):
        plane = rg.Plane(value)
    elif isinstance(value, rg.Curve):
        plane = boundary_plane(value, tol)
    elif isinstance(value, rg.Brep) and value.Faces.Count == 1 and value.Loops.Count == 1:
        plane = boundary_plane(value.Faces[0].OuterLoop.To3dCurve(), tol)
    elif isinstance(value, rg.Surface):
        ok, candidate = value.TryGetPlane(tol)
        if ok:
            plane = candidate
    if plane is None or not plane.IsValid:
        raise ValueError(name+" 必须是有效平面或能够封面的单一闭合平面边界。")
    return plane


def auto_ends(geo, tol):
    candidates = []
    temporary = None
    source = geo
    if isinstance(geo, rg.Mesh):
        for polyline in geo.GetNakedEdges() or []:
            p = boundary_plane(rg.PolylineCurve(polyline), tol)
            if p is not None:
                candidates.append(p)
        if geo.IsClosed and geo.Faces.Count <= MAX_AUTO_MESH_FACES:
            temporary = rg.Brep.CreateFromMesh(geo, True)
            source = temporary
    if isinstance(source, rg.Brep):
        try:
            # Merge coplanar split faces before checking the complete end boundary; do not treat individual mesh triangles as end faces.
            if temporary is None:
                temporary = source.DuplicateBrep()
            temporary.MergeCoplanarFaces(tol)
            for face in temporary.Faces:
                ok, _ = face.TryGetPlane(tol)
                if ok and face.Loops.Count == 1:
                    p = boundary_plane(face.OuterLoop.To3dCurve(), tol)
                    if p is not None:
                        candidates.append(p)
            naked = temporary.DuplicateNakedEdgeCurves(True, True)
            for curve in rg.Curve.JoinCurves(naked, tol) if naked else []:
                p = boundary_plane(curve, tol)
                if p is not None:
                    candidates.append(p)
        finally:
            if temporary is not None:
                temporary.Dispose()
    bottoms, tops = [], []
    for plane in candidates:
        normal = rg.Vector3d(plane.Normal)
        if abs(normal.Z) < 1e-6:
            continue
        if normal.Z < 0:
            normal = -normal
        plane = rg.Plane(plane.Origin, normal)
        bounds = geo.GetBoundingBox(plane)
        # The whole geometry must lie on one side of the candidate end plane, excluding internal steps and side faces.
        group = bottoms if bounds.Min.Z >= -tol and bounds.Max.Z > tol else (
            tops if bounds.Max.Z <= tol and bounds.Min.Z < -tol else None)
        if group is None:
            continue
        if not any(abs(p.DistanceTo(plane.Origin)) <= tol and
                   rg.Vector3d.CrossProduct(p.Normal, normal).Length < 1e-8 for p in group):
            group.append(plane)
    # Multiple distinct support end faces indicate ambiguity; do not arbitrarily choose the largest or smallest face.
    return (bottoms[0] if len(bottoms) == 1 else None,
            tops[0] if len(tops) == 1 else None)


def layer_offsets(length, height):
    'Place the first layer one full layer height above the bottom; retain an exact-multiple top layer and omit incomplete layers.'
    if not math.isfinite(height) or height <= 0:
        raise ValueError("LayerHeight 必须是有限正数。")
    if not math.isfinite(length):
        raise ValueError("切片范围必须是有限数。")
    if length <= 0:
        return []
    ratio = length/height
    if not math.isfinite(ratio) or ratio > MAX_LAYERS+1:
        raise ValueError("切片层数超过上限，请检查模型单位与层高。")
    count = int(math.floor(ratio+1e-12))
    if count > MAX_LAYERS:
        raise ValueError("切片层数超过上限，请检查模型单位与层高。")
    return [min(i*height, length) for i in range(1, count+1)]


def _plane_height_at_center(plane, center):
    normal = rg.Vector3d(plane.Normal)
    if normal.Z < 0:
        normal = -normal
    if abs(normal.Z) > 1e-6:
        return plane.Origin.Z - (normal.X * (center.X - plane.Origin.X) + normal.Y * (center.Y - plane.Origin.Y)) / normal.Z
    return plane.Origin.Z


def validate_and_resolve_planes(geo, planes_input, tol):
    if planes_input is None:
        return []
    raw_list = planes_input if isinstance(planes_input, (list, tuple)) else [planes_input]
    parsed = []
    for item in raw_list:
        if item is None:
            continue
        p = custom_plane(item, tol, "Planes")
        if p is not None and p.IsValid:
            parsed.append(p)
    if not parsed:
        return []

    box = geo.GetBoundingBox(True)
    if not box.IsValid:
        raise ValueError("几何包围盒无效。")
    diag = box.Diagonal.Length
    max_allowed = max(diag * 1.5, tol * 100.0)

    for p in parsed:
        origin_dist = box.ClosestPoint(p.Origin).DistanceTo(p.Origin)
        pbox = geo.GetBoundingBox(p)
        perp_dist = max(0.0, pbox.Min.Z, -pbox.Max.Z)
        if origin_dist > max_allowed or perp_dist > max_allowed:
            dist = max(origin_dist, perp_dist)
            raise ValueError("输入平面实际位置距离几何体太远（距几何体 {:.2f}，超出允许范围 {:.2f}）。".format(dist, max_allowed))

    center = box.Center
    if len(parsed) == 1:
        return [parsed[0]]

    # Include all input planes, sort by elevation at the geometry center and retain all intermediate transition planes.
    sorted_planes = sorted(parsed, key=lambda p: _plane_height_at_center(p, center))
    for i in range(len(sorted_planes) - 1):
        p_a, p_b = sorted_planes[i], sorted_planes[i+1]
        h_a = _plane_height_at_center(p_a, center)
        h_b = _plane_height_at_center(p_b, center)
        if (h_b - h_a) <= tol and p_b.Origin.DistanceTo(p_a.Origin) <= tol:
            raise ValueError("识别到的输入平面位置重合或间距过小。")
    return sorted_planes


def slice_geometry(geo, height, planes_chain=None, tol=0.001, angle=math.radians(1), *args):
    messages = []
    box = geo.GetBoundingBox(True)
    if not box.IsValid:
        raise ValueError("几何包围盒无效。")

    # Support the legacy six-argument call: (geo, height, top, bottom, tol, angle).
    if args:
        bottom = args[0]
        tol = args[1] if len(args) > 1 else tol
        angle = args[2] if len(args) > 2 else angle
        raw_chain = [p for p in (bottom, planes_chain) if p is not None]
    else:
        if isinstance(planes_chain, (list, tuple)):
            raw_chain = list(planes_chain)
        elif planes_chain is not None:
            raw_chain = [planes_chain]
        else:
            raw_chain = []

    for p in raw_chain:
        diag_check = box.Diagonal.Length
        max_check = max(diag_check * 1.5, tol * 100.0)
        o_dist = box.ClosestPoint(p.Origin).DistanceTo(p.Origin)
        pbox_check = geo.GetBoundingBox(p)
        p_dist = max(0.0, pbox_check.Min.Z, -pbox_check.Max.Z)
        if o_dist > max_check or p_dist > max_check:
            d_val = max(o_dist, p_dist)
            raise ValueError("输入平面实际位置距离几何体太远（距几何体 {:.2f}，超出允许范围 {:.2f}）。".format(d_val, max_check))

    center = box.Center
    if len(raw_chain) == 0:
        auto_bottom, auto_top = auto_ends(geo, tol)
        chain = [auto_bottom, auto_top] if (auto_bottom is not None and auto_top is not None) else []
    elif len(raw_chain) == 1:
        p = raw_chain[0]
        h = _plane_height_at_center(p, center)
        auto_bottom, auto_top = auto_ends(geo, tol)
        if h < center.Z:
            chain = [p, auto_top] if auto_top is not None else []
        else:
            chain = [auto_bottom, p] if auto_bottom is not None else []
    else:
        chain = sorted(raw_chain, key=lambda p: _plane_height_at_center(p, center))

    planes = []
    if len(chain) >= 2:
        stages = []
        for i in range(len(chain) - 1):
            p_start, p_end = chain[i], chain[i+1]
            d = p_end.Origin - p_start.Origin
            length = d.Length
            if length <= tol:
                raise ValueError("相邻输入平面位置重合或间距过小。")
            n_start, n_end = rg.Vector3d(p_start.Normal), rg.Vector3d(p_end.Normal)
            if n_start * d < 0: n_start = -n_start
            if n_end * d < 0: n_end = -n_end
            if min(n_start * d, n_end * d) <= tol:
                raise ValueError("相邻输入平面没有有效间距，或连线平行于端平面。")
            stages.append(dict(start=p_start, end=p_end, dir=d, length=length,
                               n_start=n_start, n_end=n_end))

        total_length = sum(s["length"] for s in stages)
        stage_cum = [0.0]
        for s in stages:
            stage_cum.append(stage_cum[-1] + s["length"])

        for offset in layer_offsets(total_length, height):
            idx = 0
            while idx < len(stages) - 1 and offset > stage_cum[idx + 1] + 1e-12:
                idx += 1
            st = stages[idx]
            s_local = max(0.0, min(offset - stage_cum[idx], st["length"]))
            t = s_local / st["length"] if st["length"] > 0 else 0.0
            normal = rg.Vector3d(st["n_start"] * (1.0 - t) + st["n_end"] * t)
            if not normal.Unitize():
                raise ValueError("插值法向退化。")
            origin = st["start"].Origin + st["dir"] * t
            planes.append(rg.Plane(origin, normal))

        mode = "PlaneToPlane"
        bot = chain[0]
        end = chain[-1]
    else:
        mode = "ZContour"
        messages.append("端部不明确，使用 ZContour。" + (
            "自定义端未应用：另一端无法识别。" if len(raw_chain) == 1 else ""))
        if isinstance(geo, rg.Mesh) and geo.IsClosed and geo.Faces.Count > MAX_AUTO_MESH_FACES:
            messages.append("闭合网格超过自动端面识别面数上限。")
        for offset in layer_offsets(box.Max.Z - box.Min.Z, height):
            planes.append(rg.Plane(rg.Point3d(0, 0, box.Min.Z + offset), rg.Vector3d.ZAxis))
        bot = rg.Plane(rg.Point3d(0, 0, box.Min.Z), rg.Vector3d.ZAxis)
        end = rg.Plane(rg.Point3d(0, 0, box.Max.Z), rg.Vector3d.ZAxis)

    layers = []
    for plane in planes:
        if hasattr(rg, "Mesh") and isinstance(geo, rg.Mesh):
            raw = rg.Mesh.CreateContourCurves(geo, plane)
        elif hasattr(rg, "Intersect"):
            ok, raw, points = rg.Intersect.Intersection.BrepPlane(geo, plane, tol)
            if not ok and not raw:
                raw = []
        else:
            raw = []
        joined = rg.Curve.JoinCurves(raw, tol) if raw else []
        layers.append(list(joined or []))
    if not any(layers):
        messages.append("所有切平面均未得到曲线。")
    return layers, mode, messages, planes, bot, end






def layer_normal(plane, direction):
    'Use the slicing-plane normal oriented toward print progression, independently of curve winding.'
    normal = rg.Vector3d(plane.Normal)
    rise = rg.Vector3d(direction)
    if not normal.Unitize() or not rise.Unitize():
        raise ValueError("切片法向或打印推进方向无效。")
    dot = normal * rise
    if abs(dot) <= 1e-12:
        raise ValueError("打印推进方向平行于切片平面，无法确定上升法向。")
    return normal * (-1.0 if dot < 0 else 1.0)


def curve_endpoints(curve, tolerance=1e-7, depth=0):
    'Use original segment endpoints only; include shared endpoints once and retain the closing endpoint.'
    if depth > 64:
        raise ValueError("组合曲线嵌套过深。")
    if isinstance(curve, rg.PolyCurve):
        points, parameters = [], []
        for i in range(curve.SegmentCount):
            child = curve.SegmentCurve(i)
            ps, ts = curve_endpoints(child, tolerance, depth+1)
            if points and points[-1].DistanceTo(ps[0]) > tolerance:
                raise ValueError("组合曲线子段不连续。")
            begin = 1 if points else 0
            points.extend(ps[begin:])
            parameters.extend(curve.PolyCurveParameter(i, t) for t in ts[begin:])
    elif isinstance(curve, rg.PolylineCurve):
        points = [curve.Point(i) for i in range(curve.PointCount)]
        parameters = [curve.Parameter(i) for i in range(curve.PointCount)]
    else:
        points = [curve.PointAtStart, curve.PointAtEnd]
        parameters = [curve.Domain.Min, curve.Domain.Max]
    if len(points) < 2 or len(points) > 200000:
        raise ValueError("曲线端点数量无效或超过上限。")
    parameters[0], parameters[-1] = curve.Domain.Min, curve.Domain.Max
    return points, parameters


def sample_layers(layers, normals, height, tolerance):
    'Sample in actual geometric layer order, independently of subsequent chunk ordering.'
    result = {}
    previous = []
    previous_index = None
    total_samples = 0
    for li, curves in enumerate(layers):
        if not curves:
            continue
        for curve in curves:
            points, parameters = curve_endpoints(curve, tolerance)
            total_samples += len(points)
            if total_samples > MAX_SAMPLES:
                raise ValueError("单几何采样点总数超过上限。")
            heights, vectors, support_indices = [], [], []
            for point_index, point in enumerate(points):
                if curve.IsClosed and point_index == len(points)-1:
                    heights.append(heights[0]); vectors.append(rg.Vector3d(vectors[0]))
                    support_indices.append(support_indices[0])
                    continue
                if not previous:
                    h, vector, support = height, rg.Vector3d(normals[li]), -1
                else:
                    nearest, support, distance = None, -1, float("inf")
                    for ci, lower in enumerate(previous):
                        success, parameter = lower.ClosestPoint(point)
                        if not success: continue
                        candidate = lower.PointAt(parameter)
                        current = point.DistanceTo(candidate)
                        if current < distance:
                            nearest, support, distance = candidate, ci, current
                    if nearest is None or not math.isfinite(distance) or distance <= tolerance:
                        raise ValueError("第{}层无法取得有效的局部层间距离。".format(li))
                    vector = rg.Vector3d(normals[li])
                    if not vector.Unitize():
                        raise ValueError("当前层法线无效。")
                    h = rg.Vector3d(point-nearest) * vector
                    if not math.isfinite(h) or h <= tolerance:
                        raise ValueError("第{}层局部层序倒置或相交，不能推定正打印高度。".format(li))
                if not vector.Unitize():
                    raise ValueError("局部生长方向退化。")
                heights.append(h); vectors.append(vector); support_indices.append(support)
            result[id(curve)] = dict(Points=tuple(points), SampleParameters=tuple(parameters),
                Heights=tuple(heights), GrowthVectors=tuple(vectors), SampleCurve=curve,
                IsFirstLayer=not bool(previous), SupportLayerIndex=previous_index,
                SupportCurveIndices=tuple(support_indices), Sampling="segment_endpoints",
                HeightMetric="current_layer_normal", HeightMethod="nominal_first_layer" if not previous else "previous_layer_local_z_difference")
        previous, previous_index = curves, li
    return result


def build_sliced_path(geo_tree, height, planes=None, *args, **kwargs):
    if not math.isfinite(height) or height <= 0:
        raise ValueError('LayerHeight 必须是有限正数。')
    if len(args) >= 5 and (not isinstance(args[0], str)) and isinstance(args[1], str):
        top_val = planes
        bot_val = args[0]
        producer_id = args[1]
        tol = args[2]
        angle = args[3]
        units = args[4]
        width = kwargs.get('width') if 'width' in kwargs else args[5] if len(args) > 5 else None
        planes_list = [p for p in (bot_val, top_val) if p is not None]
    else:
        producer_id = args[0] if len(args) > 0 else kwargs.get('producer_id', '')
        tol = args[1] if len(args) > 1 else kwargs.get('tol', 0.001)
        angle = args[2] if len(args) > 2 else kwargs.get('angle', math.radians(1))
        units = args[3] if len(args) > 3 else kwargs.get('units', 'None')
        width = kwargs.get('width') if 'width' in kwargs else args[4] if len(args) > 4 else None
        planes_list = planes
    if width is None or not math.isfinite(float(width)) or float(width) <= 0:
        raise ValueError('Width 必须是有限正数。')
    width = float(width)
    (chunks, notices, empty_paths) = ([], [], [])
    for (source_path, values) in _tree_items(geo_tree):
        if not values:
            empty_paths.append(source_path)
        for (index, value) in enumerate(values):
            geo_id = '{}:{}:{}'.format(producer_id, ';'.join(map(str, source_path)), index)
            try:
                geo = unwrap(value)
                if isinstance(geo, (rg.Extrusion, rg.SubD, rg.Surface)):
                    geo = geo.ToBrep()
                if not isinstance(geo, (rg.Brep, rg.Mesh)) or not geo.IsValid:
                    raise ValueError('需要有效 Brep、Surface、Extrusion、SubD 或 Mesh。')
                resolved_planes = validate_and_resolve_planes(geo, planes_list, tol)
                (layers, mode, messages, planes, bot, end) = slice_geometry(geo, height, resolved_planes, tol, angle)
                rise = end.Origin - bot.Origin if mode == 'PlaneToPlane' else rg.Vector3d.ZAxis
                normals = [layer_normal(plane, rise) for plane in planes]
                samples = sample_layers(layers, normals, height, tol)
                layer_lookup = {id(curve): (li, ci) for (li, layer) in enumerate(layers) for (ci, curve) in enumerate(layer)}
                raw_curves = [curve for layer in layers for curve in layer]
                ordered = [raw_curves] if raw_curves else []
                if not ordered:
                    empty_paths.append(source_path + (index, 0))
                for (chunk_index, curves) in enumerate(ordered):
                    records = []
                    for curve in curves:
                        (li, ci) = layer_lookup[id(curve)]
                        records.append(dict(Curve=curve, LayerIndex=li, LayerHeight=height, Plane=planes[li], LayerNormal=normals[li], GeoId=geo_id, ChunkId=chunk_index, SegmentId='{}:layer{}:curve{}'.format(geo_id, li, ci), Role='wall', SourcePath=source_path, GeoIndex=index))
                        records[-1].update(samples[id(curve)])
                        records[-1]['Width'] = width
                    chunks.append(dict(TreePath=source_path + (index, chunk_index), SourcePath=source_path, GeoIndex=index, GeoId=geo_id, ChunkId=chunk_index, LayerHeight=height, Width=width, SlicingMode=mode, BottomPlane=bot, TopPlane=end, Curves=tuple(curves), Segments=tuple(records), Points=tuple((p for r in records for p in r['Points'])), Heights=tuple((h for r in records for h in r['Heights'])), GrowthVectors=tuple((v for r in records for v in r['GrowthVectors']))))
                notices.extend((('warning', '{}[{}]：{}'.format(source_path, index, m)) for m in messages))
            except Exception as exc:
                empty_paths.append(source_path + (index, 0))
                notices.append(('error', '{}[{}]：{}'.format(source_path, index, exc)))
    data = _path_output('sliced', chunks, dict(Producer=producer_id, Units=units, Tolerance=tol, AngleTolerance=angle, EmptyPaths=tuple(empty_paths), NominalLayerHeight=height, Width=width, Sampling='segment_endpoints', HeightMetric='current_layer_normal'))
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
            if getattr(param,"VariableName",param.NickName)=="Path" and hasattr(param,"Hidden"):
                param.Hidden=True
        component.Name, component.NickName = "智能切片", "SmartSlicer"
        component.Message = COMPONENT_MESSAGE
        geometry_val = globals().get("Geometry")
        if geometry_val is None:
            geometry_val = globals().get("Geo")
        lh_val = globals().get("LayerHeight")
        width_val = globals().get("Width")
        if geometry_val is None or lh_val is None or width_val is None:
            return
        doc = Rhino.RhinoDoc.ActiveDoc
        tol = doc.ModelAbsoluteTolerance if doc else 0.001
        angle = doc.ModelAngleToleranceRadians if doc else math.radians(1)
        units = str(doc.ModelUnitSystem) if doc else "None"
        planes_val = globals().get("Planes")
        if planes_val is None:
            old_top = globals().get("TopPlane")
            old_bot = globals().get("BottomPlane")
            if old_top is not None or old_bot is not None:
                planes_val = [p for p in (old_bot, old_top) if p is not None]
        data, notices = build_sliced_path(geometry_val, float(lh_val),
                         planes_val,
                         str(component.InstanceGuid), tol, angle, units, width_val)
        globals()["Path"] = GH_ObjectWrapper(data)
        globals()["Preview"] = _preview_tree(data)
        component.Message = "Smart Slicer\n{} layers | {} curves".format(data.LayerCount, data.CurveCount)
        for level, message in notices:
            component.AddRuntimeMessage(getattr(gh.Kernel.GH_RuntimeMessageLevel, level.title()), _newpath_zh_message(message))
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))


if "ghenv" in globals():
    _run_component()


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = '智能切片'
    ghenv.Component.NickName = '智能切片'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'Slice'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '按层高与端部平面切片几何，输出携带料条宽度和逐点属性的路径。'
    ghenv.Component.Tooltip = '按层高与端部平面切片几何，输出携带料条宽度和逐点属性的路径。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Geometry': '待切片几何树', 'LayerHeight': '名义层高，模型单位', 'Width': '料条宽度，随 Path 传递', 'Planes': '限定切片区间的平面或闭合平面边界列表'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '一个携带曲线、层高、来源及层号的 Path 数据包', 'Preview': 'Path 内切片曲线的副本，可预览或接 Curve'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
