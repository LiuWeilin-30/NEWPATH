"""生成brim

【功能】
沿落地外轮廓向外生成多圈贴边裙边，并保留原模型路径。

【输入端】
Path（通用对象；数据树；必填）：完整sliced Path。
FirstOffset（数值；单项；可选）：首圈中心线距离；默认Width；模型单位。
GeneralOffset（数值；单项；可选）：圈间中心线距离；默认Width；模型单位。
OffsetCount（数值；单项；可选）：整数圈数0～1000；默认3；0关闭。

【输出端】
Path（通用对象；单项）：辅助环在前、原模型在后的完整PATH。
Preview（曲线；数据树）：完整路径曲线副本。

【详细用法与约束】
用途：选全局最低Z的水平首层外轮廓，向外生成brim；同一落地轮廓的多圈合为一组。
Path / Tree / object / 必填：完整sliced数据包；保留原模型路径，新增裙边块。
FirstOffset / Item / number / 可选：首圈与模型轮廓中心线距离，默认继承Width。
GeneralOffset / Item / number / 可选：相邻圈中心线间距，默认继承Width。
OffsetCount / Item / number / 可选=3：整数圈数，0关闭生成，最大1000；所有距离用模型单位。
输出 Path / Item / object：完整数据包；Preview / Tree：所有曲线副本；自动求解，无点预览。
每个独立落地外轮廓对应一个Chunk/PathGroupId，组内从外圈到内圈；三个脚各两圈即3组×2曲线。
段Role、PathGroupRole固定brim；每圈保留独立SegmentId；不同脚不合组，裙边先于模型。
树路径为{物体;打印块;块内路径}；辅助层按brim→infill→wall输出，原位置保留用于追溯。
下游路径分组保留brim功能组，连续化移缝裁短后将同组各圈由外到内挤出连接为一条路径。
仅水平真实首层/current_layer_normal；宽高和方向继承，内环跳过；拒绝自交、斜层和重复应用。
原始单墙边界适用；双墙/肋等组合轮廓不支持，输出接分组/连续，避免再被几何编辑器改写。
简化、圆弧拟合及Round向外偏移保留；按模型绝对公差处理，偏移失败或碎片报错并清空输出。
辅助圆弧按模型公差与0.1弧度角限制离散，同步几何/采样字段；OffsetCurve保留偏移原曲线。
距离允许大于Width；FirstOffset决定与模型间距，GeneralOffset决定圈距，料条是否接触由距离决定。
没有多岛并集、料条实体避碰或平台边界检查；几何中心线相交/包围报错，较高首层跳过。
失败时清空输出；不支持将路径数据内部化保存。
树路径统一为{物体;打印块;块内路径}；Role和真实LayerIndex独立保留。
ContinuousGroupId为独立连续组；同打印块内不同组不合并。
辅助路径按物体及真实层号排序；底部逐层brim→infill→wall，上部保留原连续组边界。
排序不运行几何分组算法；连续路径仍仅处理组内路径。
"""

# Localized presentation only; keep required inputs, type hints and solver behavior.
def _newpath_zh_message(message):
    text = str(message)
    if any('\u4e00' <= char <= '\u9fff' for char in text):
        return text
    return '运行提示，请检查相关输入。原始信息：' + text


# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '沿落地外轮廓向外生成多圈贴边裙边，并保留原模型路径。'
    ghenv.Component.Tooltip = '沿落地外轮廓向外生成多圈贴边裙边，并保留原模型路径。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '完整sliced Path', 'FirstOffset': '首圈中心线距离；默认Width；模型单位', 'GeneralOffset': '圈间中心线距离；默认Width；模型单位', 'OffsetCount': '整数圈数0～1000；默认3；0关闭'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '辅助环在前、原模型在后的完整PATH', 'Preview': '完整路径曲线副本'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description


import math
import bisect
import Rhino
import Rhino.Geometry as rg
import Grasshopper as gh
from Grasshopper import DataTree

PORT_ALIASES = {'Paths': 'Path'}
COMPONENT_MARKER = 'BrimSkirtPath:r7'
COMPONENT_MESSAGE = '生成brim\n按落地轮廓分组'
PATH_PROTOCOL = 'codex.ghpython.Path'
PATH_SCHEMA = 1
INPUT_SPECS = [('Path', 'Path', '完整sliced Path', 'tree', 'object', False), ('FirstOffset', 'FirstOffset', '首圈中心线距离；默认Width；模型单位', 'item', 'number', True), ('GeneralOffset', 'GeneralOffset', '圈间中心线距离；默认Width；模型单位', 'item', 'number', True), ('OffsetCount', 'OffsetCount', '整数圈数0～1000；默认3；0关闭', 'item', 'number', True)]
OUTPUT_SPECS = [
    ('Path','Path','辅助环在前、原模型在后的完整PATH','item'),
    ('Preview','Preview','完整路径曲线副本','tree'),
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

def prepare_sample_field(record):
    if record.get("Sampling") != "segment_endpoints":
        raise ValueError("请用当前智能切片重新生成端点高度 Path。")
    curve = record.get("SampleCurve", record.get("Curve"))
    parameters = tuple(record.get("SampleParameters", ()))
    heights = tuple(record.get("Heights", ()))
    vectors = tuple(record.get("GrowthVectors", ()))
    if curve is None or not parameters or not (len(parameters)==len(heights)==len(vectors)):
        raise ValueError("Path 缺少逐点高度数据，请用新版智能切片重新生成。")
    table = sorted(zip(parameters, heights, vectors), key=lambda row: row[0])
    for _, h, v in table:
        if not math.isfinite(h) or h <= 0 or v.Length <= 0:
            raise ValueError("Path 的逐点高度或方向无效。")
    if curve.IsClosed and table[-1][0] < curve.Domain.Max:
        table.append((curve.Domain.Max, table[0][1], table[0][2]))
    return curve, tuple(r[0] for r in table), table

def _set_records(chunk, records):
    chunk.update(Segments=tuple(records), Curves=tuple(s["Curve"] for s in records))
    for key in ("Points", "Heights", "GrowthVectors"):
        chunk[key] = tuple(v for record in records for v in record.get(key, ()))

def _value(value):
    for _ in range(8):
        other = getattr(value, 'Value', value)
        if other is value:
            break
        value = other
    return value


def _number(value, default, name, positive=False):
    value = _value(value)
    value = default if value is None else value
    if isinstance(value, bool):
        raise ValueError(name + ' 不能是布尔值。')
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(name + ' 必须为数值。')
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(name + ' 必须为有限' + ('正数。' if positive else '数。'))
    return value





def _simple_closed(curve, tolerance):
    if curve is None or not curve.IsValid or not curve.IsClosed:
        raise ValueError('辅助路径需要有效闭合轮廓。')
    box = curve.GetBoundingBox(True)
    if box.Max.Z - box.Min.Z > tolerance:
        raise ValueError('裙边 仅支持水平首层，不自动投影斜层。')
    events = rg.Intersect.Intersection.CurveSelf(curve, tolerance)
    if events is None or events.Count:
        raise ValueError('首层或偏移轮廓存在自交/重叠，不能生成可靠的辅助环。')
    if curve.GetLength() <= 4 * tolerance:
        raise ValueError('闭合轮廓过短。')


def preprocess_curve(curve, tolerance, notices):
    # Retain original simplify/arc fitting, using model tolerance instead of 0.1 units.
    target = curve.DuplicateCurve()
    nurbs = target.ToNurbsCurve()
    if nurbs is not None and nurbs.Points.Count > 3000:
        notices.append('控制点超过3000，跳过该轮廓的简化/圆弧拟合，继续用原轮廓偏移。')
        return target
    simple = target.Simplify(rg.CurveSimplifyOptions.All, tolerance, 0.1)
    if simple is not None:
        target = simple
    fitted = target.ToArcsAndLines(tolerance, 0.1, 0.0, 0.0)
    return fitted if fitted is not None else target


def _relation(a, b, plane, tolerance):
    return rg.Curve.PlanarClosedCurveRelationship(a, b, plane, tolerance)


def get_outward_offset_sign(curve, plane, sample_distance, tolerance):
    # Test both signs and require containment; never guess after a failed offset.
    valid = []
    for sign in (1.0, -1.0):
        results = curve.Offset(plane, sample_distance * sign, tolerance,
                               rg.CurveOffsetCornerStyle.Round)
        if results is None or len(results) != 1:
            continue
        candidate = results[0]
        if candidate is not None and candidate.IsValid and candidate.IsClosed:
            if _relation(curve, candidate, plane, tolerance) == rg.RegionContainment.AInsideB:
                valid.append(sign)
    if len(valid) != 1:
        raise ValueError('无法唯一确定向外偏移方向；请检查首层轮廓与偏移距离。')
    return valid[0]


def _outer_sources(sources, plane, tolerance):
    nested = set()
    for i, a in enumerate(sources):
        for j in range(i):
            b = sources[j]
            relation = _relation(a['record']['Curve'], b['record']['Curve'], plane, tolerance)
            if relation == rg.RegionContainment.AInsideB:
                nested.add(i)
            elif relation == rg.RegionContainment.BInsideA:
                nested.add(j)
            elif relation != rg.RegionContainment.Disjoint:
                raise ValueError('首层轮廓相交、相切或重复，请先提供互不交叠的外轮廓。')
    return [s for i, s in enumerate(sources) if i not in nested], len(nested)


def _validate_source(record, chunk, tolerance):
    record = dict(record)
    for key in ('Width', 'LayerHeight'):
        if key not in record:
            record[key] = chunk.get(key)
        record[key] = _number(record[key], None, key, True)
    required = ('GeoId', 'SourcePath', 'SegmentId', 'LayerIndex', 'IsFirstLayer',
                'HeightMetric', 'Plane', 'LayerNormal')
    if any(key not in record for key in required):
        raise ValueError('首层缺少来源、层号、平面或工艺字段，请重算上游。')
    if record['IsFirstLayer'] is not True or record['HeightMetric'] != 'current_layer_normal':
        raise ValueError('裙边 需要真实首层及 current_layer_normal 高度；不支持中间层或UV高度。')
    if record.get('WallCount', 1) != 1 or record.get('ConnectionPoints') or record.get('RibCount', 0):
        raise ValueError('请在双层墙/结构肋等改造之前生成辅助环，使用原始外轮廓。')
    _simple_closed(record['Curve'], tolerance)
    prepare_sample_field(record)
    heights, vectors = record['Heights'], record['GrowthVectors']
    for h, v in zip(heights, vectors):
        if abs(h - record['LayerHeight']) > tolerance:
            raise ValueError('水平首层高度不一致；辅助环需要已确认的平台首层高度。')
        if not all(math.isfinite(x) for x in (v.X, v.Y, v.Z)):
            raise ValueError('首层方向无效。')
        if abs(v.X) > 1e-6 or abs(v.Y) > 1e-6 or abs(v.Z - 1) > 1e-6:
            raise ValueError('辅助环需要世界Z向上的水平首层方向。')
    return record


def _ring_parameters(record, first, step, tolerance):
    width = record['Width']
    first = _number(first, width, 'FirstOffset', True)
    step = _number(step, width, 'GeneralOffset', True)
    if min(first, step) <= 2 * tolerance:
        raise ValueError('偏移距离须大于两倍模型公差，避免重复/退化路径。')
    return first, step


def create_optimized_brim(source, plane, first, step, count, tolerance, notices):
    curve = preprocess_curve(source, tolerance, notices)
    _simple_closed(curve, tolerance)
    sign = get_outward_offset_sign(curve, plane, min(first, step) * .5, tolerance)
    rings = []
    for index in range(count - 1, -1, -1):
        distance = first + index * step
        results = curve.Offset(plane, distance * sign, tolerance, rg.CurveOffsetCornerStyle.Round)
        if results is None or len(results) != 1:
            raise ValueError('第{}圈向外偏移失败或产生多个碎片；停止输出，避免缺圈。'.format(index + 1))
        offset = results[0]
        _simple_closed(offset, tolerance)
        if _relation(source, offset, plane, tolerance) != rg.RegionContainment.AInsideB:
            raise ValueError('偏移结果未完整包围原轮廓。')
        # Current continuous planner transports point lists. Sample arcs here explicitly.
        polyline = offset.ToPolyline(tolerance, 0.1, 0.0, 0.0)
        _simple_closed(polyline, tolerance)
        if _relation(source, polyline, plane, tolerance) != rg.RegionContainment.AInsideB:
            raise ValueError('辅助环离散后未完整包围原轮廓；请检查偏移与公差。')
        points, parameters = curve_endpoints(polyline, tolerance)
        if len(points) < 4 or len(points) > 200000:
            raise ValueError('辅助环离散点数无效或超过200000上限。')
        if rings and _relation(polyline, rings[-1]['curve'], plane, tolerance) != rg.RegionContainment.AInsideB:
            raise ValueError('相邻辅助环相交或顺序异常。')
        rings.append(dict(curve=polyline, offset_curve=offset, points=tuple(points),
                          parameters=tuple(parameters), index=index, distance=distance))
    return rings


def _aux_record(source, ring, group_id, segment_id, tolerance):
    # Preserve extension fields, removing fields whose meaning depends on old geometry/planning.
    record = dict(source)
    stale = ('Curve', 'SampleCurve', 'Points', 'Heights', 'GrowthVectors', 'SampleParameters',
             'PointRange', 'ArcSegments', 'SeamPoint', 'SeamHeight', 'SeamDistance', 'MotionKind',
             'PrintGroupId', 'SupportLayerIndex', 'SupportCurveIndices')
    for key in stale:
        record.pop(key, None)
    record.update(Curve=ring['curve'], SampleCurve=ring['curve'], Points=ring['points'],
                  SampleParameters=ring['parameters'],
                  Heights=tuple(source['Heights'][0] for _ in ring['points']),
                  GrowthVectors=tuple(rg.Vector3d(source['GrowthVectors'][0]) for _ in ring['points']),
                  Role='brim', PathGroupRole='brim', PathGroupId=group_id, SegmentId=segment_id,
                  SourceSegmentId=source['SegmentId'], SourceCurve=source['Curve'],
                  SourceRecord=dict(source), OffsetCurve=ring['offset_curve'],
                  AuxStrategy='outer_first_layer', AuxRingIndex=ring['index'],
                  AuxOffset=ring['distance'], Sampling='segment_endpoints',
                  ApproximationTolerance=tolerance, HeightTransfer='validated_horizontal_first_layer')
    return record


def build_brim_path(input_tree, first_offset=None, general_offset=None, offset_count=None, doc_tolerance=None):
    count = _number(offset_count, 3, 'OffsetCount')
    if count != int(count) or not 0 <= count <= 1000:
        raise ValueError('OffsetCount 必须是0到1000的整数；0表示不生成。')
    count = int(count)
    for (value, name) in ((first_offset, 'FirstOffset'), (general_offset, 'GeneralOffset')):
        if _value(value) is not None:
            _number(value, None, name, True)
    packets = [(wire, item, _unwrap_path(value)) for (wire, values) in _tree_items(input_tree) for (item, value) in enumerate(values)]
    packets = _path_packets(packets)
    if not packets or any((p.Stage != 'sliced' for (_, _, p) in packets)):
        raise ValueError('请连接连续路径之前的完整 sliced Path。')
    _has_wall(packets)
    units = {str(p.Metadata.get('Units')) for (_, _, p) in packets}
    if len(units) != 1 or next(iter(units)) in ('None', ''):
        raise ValueError('输入Path单位缺失或不一致。')
    if doc_tolerance is None:
        doc = Rhino.RhinoDoc.ActiveDoc
        doc_tolerance = doc.ModelAbsoluteTolerance if doc else max((_number(p.Metadata.get('Tolerance'), None, 'Tolerance', True) for (_, _, p) in packets))
    tolerance = _number(doc_tolerance, None, 'Tolerance', True)
    (original, candidates, empty) = ([], [], [])
    notices = []
    for (pi, (wire, item, packet)) in enumerate(packets):
        for path in packet.Metadata.get('EmptyPaths', ()):
            empty.append((1, pi) + tuple(path))
        for (ci, chunk) in enumerate(packet.Chunks):
            records = tuple(chunk.get('Segments', ()))
            if len(records) != len(chunk['Curves']):
                raise ValueError('sliced 曲线与段记录数量不一致。')
            copied = dict(chunk, TreePath=(1, pi) + tuple(chunk['TreePath']), AuxInputTreePath=tuple(chunk['TreePath']), AuxInputPacket=pi)
            if not records:
                copied['Segments'] = ()
            original.append(copied)
            for (si, record) in enumerate(records):
                if count and record.get('Role') == 'brim':
                    raise ValueError('输入已含裙边；请从辅助环生成前的Path重算，避免重复。')
                if record.get('Role') in ('wall',):
                    curve = record.get('Curve')
                    if curve is None or not curve.IsValid:
                        raise ValueError('输入包含无效模型轮廓。')
                    candidates.append(dict(record=record, chunk=chunk, pi=pi, ci=ci, si=si, z=curve.GetBoundingBox(True).Min.Z))
    auxiliary = []
    skipped = 0
    if count and candidates:
        minimum = min((s['z'] for s in candidates))
        selected = [s for s in candidates if abs(s['z'] - minimum) <= tolerance]
        for entry in selected:
            entry['record'] = _validate_source(entry['record'], entry['chunk'], tolerance)
        plane = rg.Plane(rg.Point3d(0, 0, minimum), rg.Vector3d.ZAxis)
        (selected, skipped) = _outer_sources(selected, plane, tolerance)
        used_ids = {r.get('SegmentId') for c in original for r in c.get('Segments', ())}
        envelopes = []
        for entry in selected:
            source = entry['record']
            (first, step) = _ring_parameters(source, first_offset, general_offset, tolerance)
            rings = create_optimized_brim(source['Curve'], plane, first, step, count, tolerance, notices)
            envelope = rings[0]['curve']
            for other in envelopes:
                if _relation(envelope, other, plane, tolerance) != rg.RegionContainment.Disjoint:
                    raise ValueError('不同模型的辅助环相交或互相包围；请减小圈数/距离，或先合并首层边界。')
            for other in selected:
                if other is not entry and _relation(envelope, other['record']['Curve'], plane, tolerance) != rg.RegionContainment.Disjoint:
                    raise ValueError('辅助环进入另一模型的首层区域；请调整距离或先合并边界。')
            envelopes.append(envelope)
            order = len(auxiliary)
            gid = 'brim:{}:{}:{}'.format(entry['pi'], entry['ci'], entry['si'])
            records = []
            for ring in rings:
                sid = '{}:ring:{}'.format(gid, ring['index'])
                while sid in used_ids:
                    sid += ':new'
                used_ids.add(sid)
                record = _aux_record(source, ring, gid, sid, tolerance)
                record['ChunkId'] = order
                records.append(record)
            chunk = dict(ObjectIndex=entry['chunk']['ObjectIndex'], TreePath=(0, order), ChunkId=order, GeoId=source['GeoId'], Width=source['Width'], LayerHeight=source['LayerHeight'], PathGroupId=gid, PathGroupRole='brim', AuxStrategy='outer_first_layer', SourceTreePath=tuple(entry['chunk']['TreePath']), Sampling='segment_endpoints')
            _set_records(chunk, records)
            auxiliary.append(chunk)
    if skipped:
        notices.append('已跳过{}条被外轮廓包围的内环；仅在模型外侧生成辅助环。'.format(skipped))
    if count and (not auxiliary):
        notices.append('没有可生成辅助环的首层轮廓，保留输入Path。')
    metadata = dict(packets[0][2].Metadata) if len(packets) == 1 else {}
    for key in ('PathGroups',):
        metadata.pop(key, None)
    metadata.update(Inputs=tuple((dict(p.Metadata) for (_, _, p) in packets)), Units=next(iter(units)), Tolerance=tolerance, EmptyPaths=tuple(empty), BrimGroupCount=len(auxiliary), BrimRingCount=sum((len(c['Segments']) for c in auxiliary)), BrimSelection='global_min_z', BrimOrder='landing_groups_outer_to_inner_before_model')
    metadata.update(GroupingComplete=all(p.Metadata.get('GroupingComplete') is True for _, _, p in packets), AuxiliaryOrderDirty=True)
    result = _path_output('sliced', auxiliary + original, metadata)
    _validate_path_contract(result)
    return (result, notices)


def _retire_mode_input(component):
    """Explicitly requested removal of the obsolete selector; other ports keep normal protection."""
    old = [p for p in component.Params.Input if _port_name(p) == 'Mode']
    if not old:
        return True
    document = component.OnPingDocument()
    if document is None:
        return False
    pending = globals().setdefault('_BRIM_RETIRE_MODE', set())
    key = str(getattr(component, 'InstanceGuid', id(component)))
    if key in pending:
        return False
    pending.add(key)
    def remove_mode(doc):
        try:
            if component.OnPingDocument() != doc:
                return
            obsolete = [p for p in component.Params.Input if _port_name(p) == 'Mode']
            if obsolete:
                component.RecordUndoEvent('生成brim：移除Mode输入')
                for param in obsolete:
                    component.Params.UnregisterInputParameter(param, True)
                component.Params.OnParametersChanged()
                component.VariableParameterMaintenance()
                component.ExpireSolution(False)
        except Exception as exc:
            component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))
        finally:
            pending.discard(key)
    try:
        document.ScheduleSolution(1, remove_mode)
    except Exception:
        pending.discard(key)
        raise
    return False


def _run_component():
    input_path = globals().get('Path')
    from Grasshopper.Kernel.Types import GH_ObjectWrapper
    component = ghenv.Component
    globals()['Path'] = None
    globals()['Preview'] = DataTree[rg.Curve]()
    try:
        if not _retire_mode_input(component):
            return
        if not _ensure_ports(component):
            return
        component.Name, component.NickName = '生成brim', '生成brim'
        component.Message = COMPONENT_MESSAGE
        if input_path is None or (hasattr(input_path, 'BranchCount') and input_path.BranchCount == 0):
            component.Message = '生成brim\n等待 Path'
            return
        result, notices = build_brim_path(input_path, globals().get('FirstOffset'),
            globals().get('GeneralOffset'), globals().get('OffsetCount'))
        preview = _preview_tree(result)
        globals()['Path'] = GH_ObjectWrapper(result)
        globals()['Preview'] = preview
        for notice in dict.fromkeys(notices):
            component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Warning, _newpath_zh_message(notice))
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))


if 'ghenv' in globals():
    _run_component()


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = '生成brim'
    ghenv.Component.NickName = '生成brim'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'Path'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '沿落地外轮廓向外生成多圈贴边裙边，并保留原模型路径。'
    ghenv.Component.Tooltip = '沿落地外轮廓向外生成多圈贴边裙边，并保留原模型路径。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '完整sliced Path', 'FirstOffset': '首圈中心线距离；默认Width；模型单位', 'GeneralOffset': '圈间中心线距离；默认Width；模型单位', 'OffsetCount': '整数圈数0～1000；默认3；0关闭'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '辅助环在前、原模型在后的完整PATH', 'Preview': '完整路径曲线副本'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
