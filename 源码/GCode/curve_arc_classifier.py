"""PATH转G2 G3

【功能】
将路径拟合为直线和圆弧，并按公差及段长限制重建路径。

【输入端】
Path（通用对象；数据树；必填）：同系列sliced/continuous Path包。
AllowSpatialArcs（布尔值；单项；可选）：Boolean Toggle：False仅保留XY平面圆弧(G2/G3)；True保留3D空间弧。
Tol（数值；单项；可选）：拟合公差及离散弦高；默认0.1，必须>0。
AngleTol（数值；单项；可选）：拟合/共线合并角度公差(度)；默认0.1。
MinLen（数值；单项；可选）：最小段长阈值(mm)，自动平滑吸收微小短段且不产生缺口；默认0关闭。
MaxLen（数值；单项；可选）：最大段长阈值(mm)，限制圆弧与折线分段及空间弧离散；默认0关闭。

【输出端】
Path（通用对象；数据树）：同系列Path包，保留输入包排列。
Preview（曲线；数据树）：拟合及离散后的曲线预览树。

【详细用法与约束】
将输入Path中的曲线拟合识别为G1直线与G2/G3圆弧，并按参数离散或分割。
Path：Tree / object / 必填；codex.ghpython.Path，SchemaVersion=1，支持sliced或continuous。
AllowSpatialArcs：Item / bool / 可选False；True保留3D空间弧，False仅保留XY平面圆弧(G2/G3)。
Tol：Item / number / 默认0.1；拟合距离公差、空间圆弧离散最大弦高。
AngleTol：Item / number / 默认0.1度；用于拟合与共线合并角度公差。
MinLen：Item / number / 默认0关闭；最小段长阈值，自动平滑合并吸收微小短段，保证无缝连接不产生缺口。
MaxLen：Item / number / 默认0关闭；最大段长阈值，>0时对圆弧与直线进行分段约束，限制长段与离散步长。
连续包同步重建采样曲线、参数、逐点属性与PointRange；保留MotionKind与来源。
输出：
Path：Tree / object；同系列Path数据包，保留阶段、树路径及来源元数据。
Preview：Tree / Curve；拟合及离散后的曲线预览树。

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
    ghenv.Component.Description = '将路径拟合为直线和圆弧，并按公差及段长限制重建路径。'
    ghenv.Component.Tooltip = '将路径拟合为直线和圆弧，并按公差及段长限制重建路径。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '同系列sliced/continuous Path包', 'AllowSpatialArcs': 'Boolean Toggle：False仅保留XY平面圆弧(G2/G3)；True保留3D空间弧', 'Tol': '拟合公差及离散弦高；默认0.1，必须>0', 'AngleTol': '拟合/共线合并角度公差(度)；默认0.1', 'MinLen': '最小段长阈值(mm)，自动平滑吸收微小短段且不产生缺口；默认0关闭', 'MaxLen': '最大段长阈值(mm)，限制圆弧与折线分段及空间弧离散；默认0关闭'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '同系列Path包，保留输入包排列', 'Preview': '拟合及离散后的曲线预览树'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description



PORT_ALIASES = {'Paths': 'Path'}
import math
import bisect
import Rhino
import Rhino.Geometry as rg
import System
from Grasshopper import DataTree
from Grasshopper.Kernel.Data import GH_Path

COMPONENT_MARKER = 'PathToG2G3:r13'
COMPONENT_MESSAGE = "PATH → G2/G3\n圆弧/直线拟合"
INPUT_SPECS = [
    ("Path", "Path", '同系列sliced/continuous Path包', "tree", "object", False),
    ("AllowSpatialArcs", "AllowSpatialArcs", 'Boolean Toggle：False仅保留XY平面圆弧(G2/G3)；True保留3D空间弧', "item", "bool", True),
    ("Tol", "Tol", '拟合公差及离散弦高；默认0.1，必须>0', "item", "number", True),
    ("AngleTol", "AngleTol", '拟合/共线合并角度公差(度)；默认0.1', "item", "number", True),
    ("MinLen", "MinLen", '最小段长阈值(mm)，自动平滑吸收微小短段且不产生缺口；默认0关闭', "item", "number", True),
    ("MaxLen", "MaxLen", '最大段长阈值(mm)，限制圆弧与折线分段及空间弧离散；默认0关闭', "item", "number", True),
]
OUTPUT_SPECS = [
    ("Path", "Path", '同系列Path包，保留输入包排列', "tree"),
    ("Preview", "Preview", '拟合及离散后的曲线预览树', "tree"),
]


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

def arc_is_horizontal(normal):
    length = math.sqrt(normal.X**2 + normal.Y**2 + normal.Z**2)
    return length > 0 and math.hypot(normal.X, normal.Y) / length <= 1e-10


def try_get_arc_from_curve(crv):
    if isinstance(crv, rg.ArcCurve):
        return True, crv.Arc
    if hasattr(crv, "TryGetArc"):
        try:
            ok, arc = crv.TryGetArc()
            if ok:
                return True, arc
        except Exception:
            pass
        try:
            ok, arc = crv.TryGetArc(0.01)
            if ok:
                return True, arc
        except Exception:
            pass
    return False, None


def create_arc_curve_3pt(p0, pm, p1):
    try:
        arc = rg.Arc(p0, pm, p1)
        if getattr(arc, "IsValid", False) and getattr(arc, "Radius", 0.0) > 1e-6:
            return rg.ArcCurve(arc)
    except Exception:
        pass
    if p0.DistanceTo(p1) > 1e-9:
        return rg.LineCurve(p0, p1)
    return None


def subdivision_count(radius, sweep, max_len, tolerance):
    if not all(math.isfinite(v) and v > 0 for v in (radius, sweep, max_len, tolerance)):
        raise ValueError("圆弧及离散参数必须为有限正数。")
    step = min(math.pi / 2, 4 * math.asin(math.sqrt(min(1.0, tolerance / (2 * radius)))))
    count = max(1, int(math.ceil(radius * sweep / max_len)), int(math.ceil(sweep / step)))
    if count > 200000:
        raise ValueError("单弧细分超过200000段，请检查MaxLen和Tol。")
    return count


def subdivide_line(line_curve, max_len):
    if max_len is not None and max_len > 0:
        length = line_curve.GetLength()
        if length > max_len * (1 + 1e-6):
            count = max(1, int(math.ceil(length / max_len)))
            p0 = line_curve.PointAtStart
            p1 = line_curve.PointAtEnd
            vec = p1 - p0
            return [rg.LineCurve(p0 + vec * (i / float(count)), p0 + vec * ((i + 1) / float(count)))
                    for i in range(count)]
    return [line_curve]


def subdivide_arc(arc_curve, max_len):
    count = 1
    if max_len is not None and max_len > 0:
        length = arc_curve.GetLength()
        if length > max_len * (1 + 1e-6):
            count = max(1, int(math.ceil(length / max_len)))
    if arc_curve.IsClosed:
        count = max(count, 2)
    if count > 1:
        parts = []
        for i in range(count):
            t0 = arc_curve.Domain.ParameterAt(i / float(count))
            t1 = arc_curve.Domain.ParameterAt((i + 1) / float(count))
            trimmed = arc_curve.Trim(t0, t1)
            if trimmed is not None and getattr(trimmed, "IsValid", False):
                ok, a = try_get_arc_from_curve(trimmed)
                parts.append(rg.ArcCurve(a) if ok else trimmed)
            else:
                p0 = arc_curve.PointAt(t0)
                pm = arc_curve.PointAt((t0 + t1) / 2.0)
                p1 = arc_curve.PointAt(t1)
                adjusted = create_arc_curve_3pt(p0, pm, p1)
                if adjusted is not None:
                    parts.append(adjusted)
        return parts if parts else [arc_curve]
    return [arc_curve]


def discretize_spatial_arc(arc_curve, max_len, tol):
    arc = arc_curve.Arc
    step_len = max_len if (max_len is not None and max_len > 0) else max(tol * 10, 1.0)
    count = subdivision_count(arc.Radius, abs(arc.Angle), step_len, tol)
    params = arc_curve.DivideByCount(count, True)
    if params is None or len(params) != count + 1:
        raise ValueError("空间圆弧等弧长细分失败。")
    points = [arc_curve.PointAt(t) for t in params]
    points[0], points[-1] = arc_curve.PointAtStart, arc_curve.PointAtEnd
    return [rg.LineCurve(points[i], points[i + 1]) for i in range(count)]


def fit_curve_to_primitives(curve, allow, tol, angle, min_len, max_len, depth=0):
    if depth > 32:
        raise ValueError("曲线分解层级过深。")
    if curve is None or not curve.IsValid or curve.GetLength() <= 1e-12:
        return []

    # 1. Detect a line.
    if curve.IsLinear(tol):
        line = rg.LineCurve(curve.PointAtStart, curve.PointAtEnd)
        return subdivide_line(line, max_len)

    # 2. Detect a single arc.
    ok, arc = curve.TryGetArc(tol)
    if ok:
        arc_crv = rg.ArcCurve(arc)
        if not allow and not arc_is_horizontal(arc.Plane.Normal):
            return discretize_spatial_arc(arc_crv, max_len, tol)
        else:
            return subdivide_arc(arc_crv, max_len)

    # 3. Recursively decompose PolyCurve segments.
    if isinstance(curve, rg.PolyCurve):
        result = []
        for i in range(curve.SegmentCount):
            child = curve.SegmentCurve(i)
            result.extend(fit_curve_to_primitives(child, allow, tol, angle, min_len, max_len, depth + 1))
        return result

    # 4. Preprocess PolylineCurve by removing tiny segments before fitting to avoid noise.
    if isinstance(curve, rg.PolylineCurve):
        poly = curve.ToPolyline()
        if poly is not None and poly.Count > 2 and min_len > 0:
            poly.DeleteShortSegments(min_len)
            if poly.Count >= 2:
                curve = rg.PolylineCurve(poly)

    # 5. Fit with Rhino's native ToArcsAndLines.
    min_l = min_len if min_len > 0 else 0.0
    max_l = max_len if (max_len is not None and max_len > 0) else 1e30
    fitted = curve.ToArcsAndLines(tol, math.radians(angle), min_l, max_l)
    if fitted is None and hasattr(curve, "ToNurbsCurve"):
        nurbs = curve.ToNurbsCurve()
        if nurbs is not None:
            fitted = nurbs.ToArcsAndLines(tol, math.radians(angle), min_l, max_l)

    if fitted is not None:
        result = []
        for segment in fitted.DuplicateSegments():
            result.extend(fit_curve_to_primitives(segment, allow, tol, angle, min_len, max_len, depth + 1))
        if result:
            return result

    # 6. Fall back if native ToArcsAndLines fails.
    if isinstance(curve, rg.PolylineCurve):
        poly = curve.ToPolyline() or [curve.Point(i) for i in range(curve.PointCount)]
        lines = []
        for i in range(len(poly) - 1):
            lines.extend(subdivide_line(rg.LineCurve(poly[i], poly[i + 1]), max_len))
        return lines

    step = max_len if (max_len is not None and max_len > 0) else max(tol * 10, 1.0)
    count = max(1, int(math.ceil(curve.GetLength() / step)))
    params = curve.DivideByCount(count, True)
    if params:
        pts = [curve.PointAt(t) for t in params]
        return [rg.LineCurve(pts[i], pts[i + 1]) for i in range(count)]
    return [rg.LineCurve(curve.PointAtStart, curve.PointAtEnd)]


def merge_collinear_lines(segments, tol, angle, max_len):
    result = []
    for seg in segments:
        if result and isinstance(result[-1], rg.LineCurve) and isinstance(seg, rg.LineCurve):
            a, b = result[-1], seg
            va = a.PointAtEnd - a.PointAtStart
            vb = b.PointAtEnd - b.PointAtStart
            if va.Unitize() and vb.Unitize():
                combined_len = a.GetLength() + b.GetLength()
                can_merge_len = (max_len is None or max_len <= 0) or (combined_len <= max_len * (1 + 1e-6))
                if (can_merge_len
                        and a.PointAtEnd.DistanceTo(b.PointAtStart) <= 1e-4
                        and rg.Vector3d.VectorAngle(va, vb) <= math.radians(angle)
                        and rg.Vector3d.CrossProduct(b.PointAtEnd - a.PointAtStart, va).Length <= tol):
                    result[-1] = rg.LineCurve(a.PointAtStart, b.PointAtEnd)
                    continue
        result.append(seg)
    return result


def eliminate_short_segments(segments, min_len):
    if min_len is None or min_len <= 0 or len(segments) <= 1:
        return segments

    for _ in range(6):
        modified = False
        new_segs = []
        i = 0
        while i < len(segments):
            curr = segments[i]
            length = curr.GetLength()
            if length < min_len and len(segments) > 1:
                modified = True
                p_start, p_end = curr.PointAtStart, curr.PointAtEnd
                if new_segs:
                    prev = new_segs[-1]
                    if isinstance(prev, rg.LineCurve):
                        new_segs[-1] = rg.LineCurve(prev.PointAtStart, p_end)
                    elif i + 1 < len(segments) and isinstance(segments[i + 1], rg.LineCurve):
                        nxt = segments[i + 1]
                        segments[i + 1] = rg.LineCurve(p_start, nxt.PointAtEnd)
                    else:
                        if i + 1 < len(segments):
                            nxt = segments[i + 1]
                            ok, a = try_get_arc_from_curve(nxt)
                            if ok:
                                mid = nxt.PointAt(nxt.Domain.ParameterAt(0.5))
                                adjusted = create_arc_curve_3pt(p_start, mid, nxt.PointAtEnd)
                                segments[i + 1] = adjusted if adjusted is not None else nxt
                            else:
                                segments[i + 1] = rg.LineCurve(p_start, nxt.PointAtEnd)
                        else:
                            ok, a = try_get_arc_from_curve(prev)
                            if ok:
                                mid = prev.PointAt(prev.Domain.ParameterAt(0.5))
                                adjusted = create_arc_curve_3pt(prev.PointAtStart, mid, p_end)
                                if adjusted is not None:
                                    new_segs[-1] = adjusted
                            else:
                                new_segs[-1] = rg.LineCurve(prev.PointAtStart, p_end)
                else:
                    if i + 1 < len(segments):
                        nxt = segments[i + 1]
                        if isinstance(nxt, rg.LineCurve):
                            segments[i + 1] = rg.LineCurve(p_start, nxt.PointAtEnd)
                        else:
                            ok, a = try_get_arc_from_curve(nxt)
                            if ok:
                                mid = nxt.PointAt(nxt.Domain.ParameterAt(0.5))
                                adjusted = create_arc_curve_3pt(p_start, mid, nxt.PointAtEnd)
                                segments[i + 1] = adjusted if adjusted is not None else nxt
                            else:
                                segments[i + 1] = rg.LineCurve(p_start, nxt.PointAtEnd)
                i += 1
                continue
            new_segs.append(curr)
            i += 1
        segments = [s for s in new_segs if s is not None and s.GetLength() > 1e-9]
        if not modified or len(segments) <= 1:
            break
    return segments


def assemble_curve(parts):
    valid_parts = [p for p in parts if p is not None and getattr(p, "IsValid", False) and p.GetLength() > 1e-9]
    if not valid_parts:
        raise ValueError("处理后曲线为空。")
    if len(valid_parts) == 1:
        return valid_parts[0].DuplicateCurve()
    curve = rg.PolyCurve()
    for part in valid_parts:
        if curve.SegmentCount:
            gap = curve.PointAtEnd.DistanceTo(part.PointAtStart)
            if gap > 1e-6:
                if gap <= 1e-2:
                    if isinstance(part, rg.LineCurve):
                        part = rg.LineCurve(curve.PointAtEnd, part.PointAtEnd)
                    else:
                        ok, a = try_get_arc_from_curve(part)
                        if ok:
                            mid = part.PointAt(part.Domain.ParameterAt(0.5))
                            adjusted = create_arc_curve_3pt(curve.PointAtEnd, mid, part.PointAtEnd)
                            part = adjusted if adjusted is not None else part
                        else:
                            part = rg.LineCurve(curve.PointAtEnd, part.PointAtEnd)
                else:
                    raise ValueError("曲线段存在缺口 ({:.4f}mm)，不能封装为连续Path。".format(gap))
        if not curve.AppendSegment(part.DuplicateCurve()):
            raise ValueError("组合曲线构建失败。")
    return curve


def curve_endpoints(curve, tolerance=1e-5, depth=0):
    if depth > 64:
        raise ValueError("组合曲线嵌套过深。")
    if isinstance(curve, rg.PolyCurve):
        points, parameters = [], []
        for i in range(curve.SegmentCount):
            child = curve.SegmentCurve(i)
            ps, ts = curve_endpoints(child, tolerance, depth + 1)
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


def classified_geometry(curve, options):
    allow, tol, angle, min_len, max_len = options
    raw_segments = fit_curve_to_primitives(curve, allow, tol, angle, min_len, max_len)
    if not raw_segments:
        raise ValueError("未能从输入曲线识别或拟合出有效线段。")
    merged = merge_collinear_lines(raw_segments, tol, angle, max_len)
    cleaned = eliminate_short_segments(merged, min_len)
    final_segments = merge_collinear_lines(cleaned, tol, angle, max_len)
    result = assemble_curve(final_segments)
    points, parameters = curve_endpoints(result)
    return result, tuple(points), tuple(parameters), tuple(final_segments)


def number_input(name, default, positive=True):
    value = globals().get(name)
    value = default if value is None else float(value)
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError(name + "必须为有限" + ("正数。" if positive else "非负数。"))
    return value


def unwrap_path(value):
    return _unwrap_path(value)


def path_curve_items(tree):
    if tree is None or not hasattr(tree, "BranchCount"):
        raise ValueError("Path必须为Tree Access，并连接同系列Path数据。")
    packets = []
    for i in range(tree.BranchCount):
        wire = tuple(int(v) for v in tree.Path(i).Indices)
        for j, value in enumerate(tree.Branch(i)):
            packets.append((wire, j, unwrap_path(value)))
    packets = _path_packets(packets)
    if not packets:
        raise ValueError("输入树中没有Path数据项。")
    multiple = len(packets) > 1
    items, empty, seen = [], set(), set()
    for wire, index, data in packets:
        prefix = ()
        for path in data.Metadata.get("EmptyPaths", ()):
            empty.add(prefix + tuple(path))
        for chunk in data.Chunks:
            key = prefix + tuple(chunk["TreePath"])
            if key in seen:
                raise ValueError("Path内存在重复分块树路径。")
            seen.add(key)
            curves = tuple(chunk["Curves"])
            if not curves:
                empty.add(key)
            for j, curve in enumerate(curves):
                items.append((key + (j,), curve))
    return items, tuple(sorted(empty))


def gh_path(indices):
    return GH_Path(System.Array[System.Int32](list(indices)))


PATH_PROTOCOL = "codex.ghpython.Path"
PATH_SCHEMA = 1


class _PathData:
    Protocol = PATH_PROTOCOL
    SchemaVersion = PATH_SCHEMA

    def __init__(self, stage, chunks, metadata=None):
        self.Stage = stage
        self.Chunks = tuple(chunks)
        self.Metadata = dict(metadata or {})

    @property
    def LayerHeight(self):
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


def prepare_sample_field(record):
    if record.get("Sampling") != "segment_endpoints":
        raise ValueError("请用当前智能切片重新生成端点高度 Path。")
    curve = record.get("SampleCurve", record.get("Curve"))
    parameters = tuple(record.get("SampleParameters", ()))
    heights = tuple(record.get("Heights", ()))
    vectors = tuple(record.get("GrowthVectors", ()))
    if curve is None or not parameters or not (len(parameters) == len(heights) == len(vectors)):
        raise ValueError("Path 缺少逐点高度数据，请用新版智能切片重新生成。")
    table = sorted(zip(parameters, heights, vectors), key=lambda row: row[0])
    for _, h, v in table:
        if not math.isfinite(h) or h <= 0 or v.Length <= 0:
            raise ValueError("Path 的逐点高度或方向无效。")
    if curve.IsClosed and table[-1][0] < curve.Domain.Max:
        table.append((curve.Domain.Max, table[0][1], table[0][2]))
    return curve, tuple(r[0] for r in table), table


def read_sample_field(field, point):
    curve, params, table = field
    ok, t = curve.ClosestPoint(point)
    if not ok:
        raise ValueError("无法将采样点匹配回上游曲线。")
    i = bisect.bisect_right(params, t) - 1
    i = max(0, min(i, len(table) - 1))
    j = min(i + 1, len(table) - 1)
    delta = params[j] - params[i]
    f = max(0.0, min(1.0, (t - params[i]) / delta)) if delta > 0 else 0.0
    h = table[i][1] * (1 - f) + table[j][1] * f
    v = rg.Vector3d(table[i][2]) * (1 - f) + rg.Vector3d(table[j][2]) * f
    v = rg.Vector3d(v)
    if not v.Unitize():
        raise ValueError("插值生长方向退化。")
    return h, v


def transform_record(record, options):
    source = record["Curve"]
    field = prepare_sample_field(record)
    curve, points, parameters, parts = classified_geometry(source, options)
    values = [read_sample_field(field, p) for p in points]
    values[0] = (record["Heights"][0], rg.Vector3d(record["GrowthVectors"][0]))
    values[-1] = (record["Heights"][-1], rg.Vector3d(record["GrowthVectors"][-1]))
    result = dict(record)
    result.update(Curve=curve, Points=points, SampleCurve=curve, SampleParameters=parameters,
                  Heights=tuple(h for h, v in values), GrowthVectors=tuple(v for h, v in values),
                  Sampling="segment_endpoints", ArcSegments=parts)
    return result


def _interpolate_attributes(orig_pts, orig_hs, orig_vs, new_pts):
    if len(orig_pts) == 0 or len(new_pts) == 0:
        return (), ()
    if len(orig_pts) == 1:
        return (orig_hs[0],) * len(new_pts), (orig_vs[0],) * len(new_pts)

    new_hs = []
    new_vs = []
    poly = rg.Polyline(list(orig_pts))

    for k, p in enumerate(new_pts):
        if k == 0:
            new_hs.append(orig_hs[0])
            new_vs.append(orig_vs[0])
            continue
        if k == len(new_pts) - 1:
            new_hs.append(orig_hs[-1])
            new_vs.append(orig_vs[-1])
            continue

        t = poly.ClosestParameter(p)
        seg_idx = max(0, min(int(math.floor(t)), len(orig_pts) - 2))
        f = max(0.0, min(1.0, t - seg_idx))

        h = orig_hs[seg_idx] * (1.0 - f) + orig_hs[seg_idx + 1] * f
        v = rg.Vector3d(orig_vs[seg_idx]) * (1.0 - f) + rg.Vector3d(orig_vs[seg_idx + 1]) * f
        if not v.Unitize():
            v = rg.Vector3d(orig_vs[seg_idx])
        new_hs.append(h)
        new_vs.append(v)
    return tuple(new_hs), tuple(new_vs)


def transform_packet(data, options):
    chunks = []
    tolerance = float(data.Metadata.get('Tolerance', 1e-06))
    for source in data.Chunks:
        chunk = dict(source)
        for key in ('SampleCurve', 'SampleParameters', 'PointRange'):
            chunk.pop(key, None)
        if data.Stage == 'sliced':
            originals = tuple(source.get('Segments', ()))
            if len(source['Curves']) != len(originals):
                raise ValueError('sliced曲线与段记录数量不一致。')
            records = [transform_record(record, options) for record in originals]
            for record in records:
                record.pop('PointRange', None)
            chunk.update(Segments=tuple(records), Curves=tuple((r['Curve'] for r in records)))
            for key in ('Points', 'Heights', 'GrowthVectors'):
                if key in chunk:
                    chunk[key] = tuple((v for r in records for v in r[key]))
            chunk.pop('ArcSegments', None)
        else:
            original_points = tuple(source['Points'])
            hs = tuple(source['Heights'])
            vs = tuple(source['GrowthVectors'])
            if not len(original_points) == len(hs) == len(vs):
                raise ValueError('continuous逐点属性长度不一致。')
            if not source['Curves'] and (not original_points):
                chunks.append(chunk)
                continue
            (new_records, all_points, all_heights, all_vectors, all_parts) = ([], [], [], [], [])
            previous_end = None
            for old in source['Segments']:
                rec = dict(old)
                (start, end) = rec['PointRange']
                if not 0 <= start < end <= len(original_points) or end - start < 2:
                    raise ValueError('连续段PointRange无效或不足两个点。')
                if previous_end is not None:
                    if start not in (previous_end - 1, previous_end):
                        raise ValueError('连续段PointRange未按运动顺序衔接。')
                    if start == previous_end and original_points[start - 1].DistanceTo(original_points[start]) > 0:
                        raise ValueError('连续段之间缺少非零运动边记录。')
                elif start != 0:
                    raise ValueError('连续段未覆盖块起点。')
                seg_pts = original_points[start:end]
                if rec.get('IsConnection') is True:
                    if end - start != 2:
                        raise ValueError('connection必须覆盖两个端点。')
                    fitted = rg.LineCurve(seg_pts[0], seg_pts[-1])
                    (pts, params) = curve_endpoints(fitted)
                    parts = (fitted,)
                    (seg_hs, seg_vs) = (hs[start:end], vs[start:end])
                else:
                    curve = rec.get('Curve')
                    if curve is None or not getattr(curve, 'IsValid', False):
                        curve = rg.PolylineCurve(list(seg_pts))
                    (fitted, pts, params, parts) = classified_geometry(curve, options)
                    (seg_hs, seg_vs) = _interpolate_attributes(seg_pts, hs[start:end], vs[start:end], pts)
                if pts[0].DistanceTo(seg_pts[0]) > tolerance or pts[-1].DistanceTo(seg_pts[-1]) > tolerance:
                    raise ValueError('圆弧拟合改变了段端点，无法保持连接。')
                share = previous_end is not None and start == previous_end - 1
                new_start = len(all_points) - 1 if share else len(all_points)
                begin = 1 if share else 0
                all_points.extend(pts[begin:])
                all_heights.extend(seg_hs[begin:])
                all_vectors.extend(seg_vs[begin:])
                all_parts.extend(parts)
                rec.update(PointRange=(new_start, len(all_points)), Curve=fitted, SampleCurve=fitted, SampleParameters=tuple(params), Points=tuple(pts), Heights=tuple(seg_hs), GrowthVectors=tuple(seg_vs), Sampling='segment_endpoints', ArcSegments=tuple(parts))
                new_records.append(rec)
                previous_end = end
            if previous_end != len(original_points):
                raise ValueError('连续段未覆盖块终点。')
            continuous_curve = assemble_curve(all_parts)
            chunk.update(Curves=(continuous_curve,), Points=tuple(all_points), Heights=tuple(all_heights), GrowthVectors=tuple(all_vectors), Segments=tuple(new_records), ArcSegments=tuple(all_parts))
        chunk['Sampling'] = 'segment_endpoints'
        chunks.append(chunk)
    metadata = dict(data.Metadata)
    metadata.update(ArcClassification=True, AllowSpatialArcs=bool(options[0]), ArcTolerance=options[1], ArcMaxLength=options[4], ArcMinLength=options[3], Sampling='segment_endpoints')
    return _path_output(data.Stage, chunks, metadata)


def build_outputs(tree, options):
    path_curve_items(tree)
    inputs = [(tuple(int(v) for v in tree.Path(i).Indices), j, value)
              for i in range(tree.BranchCount) for j, value in enumerate(tree.Branch(i))]
    result_path = DataTree[System.Object]()
    result_preview = DataTree[rg.Curve]()
    transformed = [(wire, index, transform_packet(unwrap_path(value), options))
                   for wire, index, value in inputs]
    for wire, index, data in _path_packets(transformed):
        packet = _PathData(data.Stage, data.Chunks, data.Metadata)
        path = gh_path(wire)
        result_path.EnsurePath(path)
        result_path.Add(packet, path)
        for chunk in packet.Chunks:
            chunk_path = gh_path(chunk['TreePath'])
            result_preview.EnsurePath(chunk_path)
            for crv in chunk.get('Curves', ()):
                if crv is not None and getattr(crv, 'IsValid', False):
                    result_preview.Add(crv.DuplicateCurve(), chunk_path)
    return result_path, result_preview


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


if 'ghenv' in globals():
    import Grasshopper as gh
    component = ghenv.Component
    component.Name = r'PATH转G2\G3'
    component.NickName = 'PathToG2G3'
    component.Message = COMPONENT_MESSAGE
    input_path = globals().get('Path')
    Path = DataTree[System.Object]()
    Preview = DataTree[rg.Curve]()
    if _ensure_ports(component):
        try:
            tol = number_input('Tol', 0.1)
            angle = number_input('AngleTol', 0.1)
            minimum = number_input('MinLen', 0.0, False)
            maximum = number_input('MaxLen', 0.0, False)
            if angle >= 90:
                raise ValueError('AngleTol必须小于90度。')
            allow = globals().get('AllowSpatialArcs')
            allow = False if allow is None else allow
            if not isinstance(allow, (bool, System.Boolean)):
                raise ValueError('AllowSpatialArcs请连接Boolean Toggle。')
            Path, Preview = build_outputs(input_path, (bool(allow), tol, angle, minimum, maximum))
        except Exception as exc:
            component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = 'PATH转G2 G3'
    ghenv.Component.NickName = 'PATH转G2 G3'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'GCode'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '将路径拟合为直线和圆弧，并按公差及段长限制重建路径。'
    ghenv.Component.Tooltip = '将路径拟合为直线和圆弧，并按公差及段长限制重建路径。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '同系列sliced/continuous Path包', 'AllowSpatialArcs': 'Boolean Toggle：False仅保留XY平面圆弧(G2/G3)；True保留3D空间弧', 'Tol': '拟合公差及离散弦高；默认0.1，必须>0', 'AngleTol': '拟合/共线合并角度公差(度)；默认0.1', 'MinLen': '最小段长阈值(mm)，自动平滑吸收微小短段且不产生缺口；默认0关闭', 'MaxLen': '最大段长阈值(mm)，限制圆弧与折线分段及空间弧离散；默认0关闭'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '同系列Path包，保留输入包排列', 'Preview': '拟合及离散后的曲线预览树'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
