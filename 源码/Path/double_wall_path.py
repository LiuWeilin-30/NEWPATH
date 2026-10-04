"""双层墙路径

【功能】
向闭合墙线内侧偏移，并在指定连接点生成双层墙路径。

【输入端】
Path（通用对象；数据树；必填）：闭合单层 sliced Path；仅编辑wall，brim/infill透传。
ConnectPoints（点；列表；必填）：向所属几何各层最近轮廓投影的连接点。
OffsetFactor（数值；单项；可选）：内偏移中心距/Width，默认1，至少1。
GapFactor（数值；单项；可选）：连接线中心距/Width，默认1，至少1。
Run（布尔值；单项；可选）：按钮重算=False。

【输出端】
Path（通用对象；单项）：双层 sliced Path；仅编辑wall，brim/infill透传。
Preview（曲线；数据树）：双层路径曲线副本。

【详细用法与约束】
用途：闭合单层 sliced Path 向区域内部偏移，在选点处断开两圈并成对跨接。
Path / Tree / object / 必填：接智能切片 Path；放在连续路径之前。
ConnectPoints / List / Point3d / 必填：连接引导点；先归属最近来源几何，再投影到该几何每层最近轮廓。
OffsetFactor / Item / number / 可选=1：内偏移中心距 = Path.Width × 此倍率。
GapFactor / Item / number / 可选=1：两条平行连接线中心距 = Path.Width × 此倍率。
两倍率不得小于1；1表示名义料条刚好相接，中心距减Width为理论净隙。
Run / Item / bool / 可选=False：Button重算；失败保留旧完整缓存并明确报错。
输出 Path / Item / object；Preview / Tree / Curve，仅曲线副本，隐藏点预览。
公差继承包的Tolerance；偏移坍塌、多解、自交、开曲线、嵌套孔环或断口相撞时报错，不缩小工艺尺寸。
在每层Plane内检查包含关系，不以世界XY或曲线方向猜测内外；只处理无孔简单闭合区域。
一个连接位置生成一个闭合往返环；同轮廓N个连接位置生成N个独立闭合环；未分配选点的轮廓保留内外两环。
完整曲线保存在Curve；Points按现行原始子段端点约定，弧/光滑段在后续连续路径和料条预览中仍为端点弦线。
新增点高度/方向按原单层曲线最近参数插值继承，不重算内层实际支承高度。
新Path保留sliced阶段及来源/层号/平面/法向/Width，加入WallCount、OffsetDistance、BridgeCenterDistance。
重新打开文件后，上游重新计算并点击 Run；不要将路径数据内部化保存。

用途规则：只编辑Role=wall；brim/infill的几何、属性、功能组完整透传。无wall时原样输出，无需墙体参数。

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
    ghenv.Component.Description = '向闭合墙线内侧偏移，并在指定连接点生成双层墙路径。'
    ghenv.Component.Tooltip = '向闭合墙线内侧偏移，并在指定连接点生成双层墙路径。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '闭合单层 sliced Path；仅编辑wall，brim/infill透传', 'ConnectPoints': '向所属几何各层最近轮廓投影的连接点', 'OffsetFactor': '内偏移中心距/Width，默认1，至少1', 'GapFactor': '连接线中心距/Width，默认1，至少1', 'Run': '按钮重算=False'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '双层 sliced Path；仅编辑wall，brim/infill透传', 'Preview': '双层路径曲线副本'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description



PORT_ALIASES = {'Paths': 'Path'}
import math
import bisect
import Rhino.Geometry as rg
from Grasshopper import DataTree

COMPONENT_MARKER = 'DoubleWallPath:r8'
COMPONENT_MESSAGE = "Double Wall\nInward + Bridges"
PATH_PROTOCOL = "codex.ghpython.Path"
PATH_SCHEMA = 1
INPUT_SPECS = [('Path', 'Path', '闭合单层 sliced Path；仅编辑wall，brim/infill透传', 'tree', 'object', False), ('ConnectPoints', 'ConnectPoints', '向所属几何各层最近轮廓投影的连接点', 'list', 'point', False), ('OffsetFactor', 'OffsetFactor', '内偏移中心距/Width，默认1，至少1', 'item', 'number', True), ('GapFactor', 'GapFactor', '连接线中心距/Width，默认1，至少1', 'item', 'number', True), ('Run', 'Run', '按钮重算=False', 'item', 'bool', True)]
OUTPUT_SPECS = [('Path', 'Path', '双层 sliced Path；仅编辑wall，brim/infill透传', 'item'), ('Preview', 'Preview', '双层路径曲线副本', 'tree')]

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

def read_sample_field(field, point):
    curve, params, table = field
    ok, t = curve.ClosestPoint(point)
    if not ok:
        raise ValueError("无法将连续路径采样点匹配回上游曲线。")
    i = bisect.bisect_right(params, t)-1
    i = max(0, min(i, len(table)-1))
    j = min(i+1, len(table)-1)
    delta = params[j]-params[i]
    f = max(0.0,min(1.0,(t-params[i])/delta)) if delta>0 else 0.0
    h = table[i][1]*(1-f)+table[j][1]*f
    v = rg.Vector3d(table[i][2])*(1-f)+rg.Vector3d(table[j][2])*f
    v = rg.Vector3d(v)
    if not v.Unitize(): raise ValueError("插值生长方向退化。")
    return h,v

def _cache_result(cache, run, compute):
    'Transactional button cache: preserve the last complete result on failure and replace it only on the first Run after structural changes.'
    if cache is not None and cache.get("Marker") != COMPONENT_MARKER:
        cache = None
    if not run:
        return cache, None
    try:
        data, notices = compute()
        return dict(Marker=COMPONENT_MARKER, Data=data, Notices=tuple(notices)), None
    except Exception as exc:
        return cache, str(exc)

def _factor(value, name):
    value = 1.0 if value is None else float(value)
    if not math.isfinite(value) or value < 1:
        raise ValueError(name+" 必须是至少1的有限数。")
    return value


def _closest(curve, point):
    ok, t = curve.ClosestPoint(point)
    if not ok:
        raise ValueError("选点无法投影到轮廓。")
    return t, curve.PointAt(t)


def _station(curve, t):
    if t <= curve.Domain.Min:
        return 0.0
    return curve.GetLength(rg.Interval(curve.Domain.Min, t))


def _simple(curve, tolerance):
    if curve is None or not curve.IsValid or not curve.IsClosed:
        raise ValueError("需要有效闭合曲线。")
    events = rg.Intersect.Intersection.CurveSelf(curve, tolerance)
    if events is None:
        raise ValueError("无法检查曲线自交。")
    if events.Count:
        raise ValueError("轮廓或连接结果自交/重叠。")


def _inner_curve(outer, plane, distance, tolerance):
    # Solve both signs; determine the interior from containment of complete closed regions.
    candidates = []
    for sign in (1.0, -1.0):
        pieces = outer.Offset(plane, sign*distance, tolerance, rg.CurveOffsetCornerStyle.Sharp)
        if pieces is None or len(pieces) != 1:
            continue
        inner = pieces[0]
        try:
            _simple(inner, tolerance)
        except ValueError:
            continue
        relation = rg.Curve.PlanarClosedCurveRelationship(inner, outer, plane, tolerance)
        if relation == rg.RegionContainment.AInsideB:
            candidates.append(inner)
    if len(candidates) != 1:
        raise ValueError("内偏移坍塌、分裂或不能唯一确认内部；请检查轮廓及打印宽度。")
    inner = candidates[0]
    if inner.ClosedCurveOrientation(plane) != outer.ClosedCurveOrientation(plane):
        if not inner.Reverse():
            raise ValueError("内圈方向统一失败。")
    return inner


def _plane_hit(curve, cut_plane, anchor, tolerance):
    events = rg.Intersect.Intersection.CurvePlane(curve, cut_plane, tolerance)
    hits = []
    if events is not None:
        for event in events:
            if event.IsPoint:
                if not any(event.PointA.DistanceTo(p) <= tolerance for _, p in hits):
                    hits.append((event.ParameterA, event.PointA))
            elif event.IsOverlap:
                raise ValueError("断口平面与轮廓重叠，请将选点移离转角。")
    if not hits:
        raise ValueError("断口无法同时截到内外两圈。")
    hits.sort(key=lambda pair: pair[1].DistanceTo(anchor))
    if len(hits) > 1 and abs(hits[0][1].DistanceTo(anchor)-hits[1][1].DistanceTo(anchor)) <= tolerance:
        raise ValueError("断口位置存在等距歧义，请移动选点。")
    return hits[0]


def _check_bridge(a, b, outer, inner, plane, tolerance):
    if a.DistanceTo(b) <= tolerance:
        raise ValueError("连接线长度退化。")
    bridge = rg.LineCurve(a, b)
    for boundary in (outer, inner):
        events = rg.Intersect.Intersection.CurveCurve(bridge, boundary, tolerance, tolerance)
        if events is None:
            raise ValueError("连接线碰撞检查失败。")
        for event in events:
            if event.IsOverlap or min(event.PointA.DistanceTo(a), event.PointA.DistanceTo(b)) > tolerance*2:
                raise ValueError("连接线穿越其他轮廓边，请移动选点。")
    middle = a+(b-a)*0.5
    if (outer.Contains(middle, plane, tolerance) != rg.PointContainment.Inside or
            inner.Contains(middle, plane, tolerance) != rg.PointContainment.Outside):
        raise ValueError("连接线未处于内外圈之间。")
    return bridge


def _cut_pair(outer, inner, plane, point, gap, tolerance):
    t, anchor = _closest(outer, point)
    tangent = rg.Vector3d(outer.TangentAt(t))
    if not tangent.Unitize():
        raise ValueError("选点处切线退化。")
    pair = {}
    for key, sign in (("minus", -1.0), ("plus", 1.0)):
        target = anchor+tangent*(sign*gap/2.0)
        cut_plane = rg.Plane(target, tangent)
        ot, op = _plane_hit(outer, cut_plane, target, tolerance)
        it, ip = _plane_hit(inner, cut_plane, op, tolerance)
        bridge = _check_bridge(op, ip, outer, inner, plane, tolerance)
        pair[key] = dict(outer=ot, inner=it, bridge=bridge)
    # Select the nearby local gap; do not mistake distant contours for missing local intersections.
    center = _station(outer, t)
    length = outer.GetLength()
    before = (center-_station(outer, pair["minus"]["outer"])) % length
    after = (_station(outer, pair["plus"]["outer"])-center) % length
    if before <= tolerance or after <= tolerance or before+after >= length/2:
        raise ValueError("选点处不能构造局部断口，请移离尖角或狭窄区域。")
    pair["Point"] = anchor
    return pair


def _ordered_cuts(cuts, curve, side, tolerance):
    length = curve.GetLength()
    events = []
    for index, cut in enumerate(cuts):
        for key in ("minus", "plus"):
            events.append((_station(curve, cut[key][side]) % length, index, key))
    events.sort()
    for i, event in enumerate(events):
        following = events[(i+1) % len(events)]
        if (following[0]-event[0]) % length <= tolerance:
            raise ValueError("选点重复或断口端点重合。")
        if event[2] == "minus" and following[1:] != (event[1], "plus"):
            raise ValueError("连接断口相交或覆盖；请拉开选点距离。")
        if event[2] == "plus" and following[2] != "minus":
            raise ValueError("内外圈连接顺序不一致。")
    return [e[1] for e in events if e[2] == "minus"]


def _same_cycle(a, b):
    return len(a) == len(b) and any(a == b[i:]+b[:i] for i in range(len(b)))


def _join_ordered(parts, tolerance):
    result = rg.PolyCurve()
    for part in parts:
        if part is None or part.GetLength() <= tolerance:
            raise ValueError("裁切后的墙段过短或退化。")
        if result.SegmentCount and result.PointAtEnd.DistanceTo(part.PointAtStart) > tolerance:
            raise ValueError("内外圈拼接端点不一致。")
        if not result.Append(part):
            raise ValueError("路径拼接失败。")
    _simple(result, tolerance)
    return result


def _trim_forward(curve, start, end, tolerance):
    if start < end:
        return curve.Trim(start, end)
    result = rg.PolyCurve()
    for a, b in ((start, curve.Domain.Max), (curve.Domain.Min, end)):
        if b > a:
            part = curve.Trim(a, b)
            if part is None:
                raise ValueError("跨原接缝裁切失败。")
            if part.GetLength() > tolerance and not result.Append(part):
                raise ValueError("跨原接缝拼接失败。")
    return result


def _wall_loops(outer, inner, plane, points, gap, tolerance):
    if not points:
        return [outer.DuplicateCurve(), inner.DuplicateCurve()], []
    cuts = [_cut_pair(outer, inner, plane, p, gap, tolerance) for p in points]
    order = _ordered_cuts(cuts, outer, "outer", tolerance)
    inner_order = _ordered_cuts(cuts, inner, "inner", tolerance)
    if not _same_cycle(order, inner_order):
        raise ValueError("内外圈的断口次序不一致，不能安全跨接。")
    loops = []
    for i, index in enumerate(order):
        start = cuts[index]["plus"]
        end = cuts[order[(i+1) % len(order)]]["minus"]
        outer_arc = _trim_forward(outer, start["outer"], end["outer"], tolerance)
        inner_arc = _trim_forward(inner, start["inner"], end["inner"], tolerance)
        if inner_arc is None or not inner_arc.Reverse():
            raise ValueError("内圈裁切失败。")
        back = start["bridge"].DuplicateCurve()
        if not back.Reverse():
            raise ValueError("连接线反向失败。")
        loops.append(_join_ordered([outer_arc, end["bridge"], inner_arc, back], tolerance))
    for i, loop in enumerate(loops):
        for other in loops[:i]:
            events = rg.Intersect.Intersection.CurveCurve(loop, other, tolerance, tolerance)
            if events is None or events.Count:
                raise ValueError("双层连接结果相交，未输出。")
    return loops, [cut["Point"] for cut in cuts]


def _make_record(source, curve, index, width, offset, gap, selected, tolerance):
    field = prepare_sample_field(source)
    points, parameters = curve_endpoints(curve, tolerance)
    samples = [read_sample_field(field, p) for p in points]
    record = dict(source)
    for key in ("PointRange", "SeamHeight", "SeamDistance", "SeamPoint"):
        record.pop(key, None)
    record.update(Curve=curve, SourceCurve=source["Curve"], SourceSegmentId=source["SegmentId"],
                  SegmentId=source["SegmentId"]+":double:"+str(index), Role="wall",
                  WallCount=2, WallLoopIndex=index, Width=width, OffsetDistance=offset,
                  BridgeCenterDistance=gap, BridgeClearance=gap-width,
                  WallClearance=offset-width, ConnectionPoints=tuple(selected),
                  Sampling="segment_endpoints", SampleCurve=curve,
                  SampleParameters=tuple(parameters), Points=tuple(points),
                  Heights=tuple(h for h, _ in samples), GrowthVectors=tuple(v for _, v in samples),
                  HeightTransfer="nearest_source_parameter")
    return record


def build_double_wall(input_tree, connect_points, offset_factor=None, gap_factor=None):
    packets = [(wire, i, _unwrap_path(v)) for (wire, items) in _tree_items(input_tree) for (i, v) in enumerate(items)]
    packets = _path_packets(packets)
    if packets and (not _has_wall(packets)):
        return _path_passthrough(packets)
    offset_factor = _factor(offset_factor, 'OffsetFactor')
    gap_factor = _factor(gap_factor, 'GapFactor')
    points = [getattr(p, 'Value', p) for p in (connect_points if connect_points is not None else ())]
    if not points or any((not isinstance(p, rg.Point3d) or not p.IsValid for p in points)):
        raise ValueError('请提供有效的 ConnectPoints 连接点。')
    packets = [(wire, i, _unwrap_path(value)) for (wire, items) in _tree_items(input_tree) for (i, value) in enumerate(items)]
    packets = _path_packets(packets)
    if not packets:
        raise ValueError('请连接智能切片的 Path。')
    if len({str(data.Metadata.get('Units')) for (_, _, data) in packets}) != 1:
        raise ValueError('多个 Path 单位不同。')
    (entries, empty_paths, chunks, groups) = ([], [], [], {})
    for (pi, (wire, item, data)) in enumerate(packets):
        if data.Stage != 'sliced':
            raise ValueError('请接单层智能切片 Path，双层编辑必须在连续路径之前。')
        tol = float(data.Metadata.get('Tolerance', 0.001))
        if not math.isfinite(tol) or tol <= 0:
            raise ValueError('Path 公差无效。')
        prefix = ()
        empty_paths.extend((prefix + tuple(p) for p in data.Metadata.get('EmptyPaths', ())))
        for source_chunk in data.Chunks:
            chunk = dict(source_chunk)
            chunk.update(TreePath=prefix + tuple(chunk['TreePath']), Segments=[], Curves=[])
            chunks.append(chunk)
            if not any((r.get('Role') == 'wall' for r in source_chunk.get('Segments', ()))):
                chunk.update(source_chunk, TreePath=prefix + tuple(source_chunk['TreePath']))
                continue
            for record in source_chunk['Segments']:
                slot = len(chunk['Segments'])
                chunk['Segments'].append((record,))
                if record.get('Role') != 'wall':
                    continue
                if record.get('WallCount', 1) != 1 or record.get('LayerIndex') is None:
                    raise ValueError('输入含已编辑墙或非层段。')
                (outer, plane) = (record['Curve'], record['Plane'])
                _simple(outer, tol)
                if not plane.IsValid or not outer.IsInPlane(plane, tol):
                    raise ValueError('轮廓未位于其记录的层平面内。')
                prepare_sample_field(record)
                width = float(record.get('Width', source_chunk.get('Width', 0)))
                if not math.isfinite(width) or width <= tol * 2:
                    raise ValueError('Path.Width 无效或小于两倍公差。')
                entry = dict(source=record, chunk=chunk, slot=slot, tolerance=tol, width=width, geo=(pi, record['GeoId']), points=[])
                entries.append(entry)
                groups.setdefault((pi, record['GeoId'], record['LayerIndex']), []).append(entry)
    if not entries:
        raise ValueError('Path 中没有可编辑轮廓。')
    if len({tuple(c['TreePath']) for c in chunks}) != len(chunks):
        raise ValueError('输入含重复分块树路径。')
    for layer in groups.values():
        for (i, entry) in enumerate(layer):
            for other in layer[:i]:
                relation = rg.Curve.PlanarClosedCurveRelationship(entry['source']['Curve'], other['source']['Curve'], entry['source']['Plane'], entry['tolerance'])
                if relation != rg.RegionContainment.Disjoint:
                    raise ValueError('同层含嵌套孔环或相交轮廓，不能按无孔实体判定内外。')
    for point in points:
        distances = [(_closest(e['source']['Curve'], point)[1].DistanceTo(point), n) for (n, e) in enumerate(entries)]
        distances.sort()
        owner = entries[distances[0][1]]['geo']
        tol = entries[distances[0][1]]['tolerance']
        if any((entries[n]['geo'] != owner and abs(d - distances[0][0]) <= tol for (d, n) in distances)):
            raise ValueError('连接点到多个来源几何等距，无法自动归属。')
        for (key, layer) in groups.items():
            if key[:2] != owner:
                continue
            ranked = sorted(((_closest(e['source']['Curve'], point)[1].DistanceTo(point), n) for (n, e) in enumerate(layer)))
            if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) <= layer[ranked[0][1]]['tolerance']:
                raise ValueError('连接点在某层到多个轮廓等距，请移动选点。')
            layer[ranked[0][1]]['points'].append(point)
    unconnected = 0
    for entry in entries:
        (source, tol, width) = (entry['source'], entry['tolerance'], entry['width'])
        (offset, gap) = (width * offset_factor, width * gap_factor)
        try:
            inner = _inner_curve(source['Curve'], source['Plane'], offset, tol)
            (loops, selected) = _wall_loops(source['Curve'], inner, source['Plane'], entry['points'], gap, tol)
            unconnected += not bool(selected)
            produced = []
            for (index, curve) in enumerate(loops):
                record = _make_record(source, curve, index, width, offset, gap, selected, tol)
                produced.append(record)
            entry['chunk']['Segments'][entry['slot']] = tuple(produced)
        except Exception as exc:
            raise ValueError('{}：{}'.format(source['SegmentId'], exc))
    for chunk in chunks:
        if not any((isinstance(group, tuple) for group in chunk.get('Segments', ()))):
            continue
        records = tuple((r for group in chunk['Segments'] for r in group))
        _refresh_role_chunk(chunk, records)
    meta = dict(packets[0][2].Metadata)
    meta.update(Inputs=tuple((dict(data.Metadata) for (_, _, data) in packets)), WallCount=2, OffsetFactor=offset_factor, GapFactor=gap_factor, EmptyPaths=tuple(empty_paths), Sampling='segment_endpoints', HeightTransfer='nearest_source_parameter', Tolerance=max((float(d.Metadata.get('Tolerance', 0.001)) for (_, _, d) in packets)))
    notices = []
    if unconnected:
        notices.append('{} 条轮廓未分配连接点，保留独立内外两圈。'.format(unconnected))
    return (_path_output('sliced', chunks, meta), notices)


def _run_component():
    input_path = globals().get("Path")
    import Grasshopper as gh
    from Grasshopper.Kernel.Types import GH_ObjectWrapper
    component = ghenv.Component
    globals()["Path"] = None
    globals()["Preview"] = DataTree[rg.Curve]()
    try:
        if not _ensure_ports(component):
            return
        for param in list(component.Params.Input)+list(component.Params.Output):
            if getattr(param, "VariableName", param.NickName) in ("ConnectPoints", "Path") and hasattr(param, "Hidden"):
                param.Hidden = True
        component.Name, component.NickName = "双层墙 Path", "DoubleWallPath"
        def compute():
            return build_double_wall(input_path, globals().get("ConnectPoints"),
                                     globals().get("OffsetFactor"), globals().get("GapFactor"))
        cache, error = _cache_result(globals().get("_double_wall_cache"), bool(globals().get("Run")), compute)
        globals()["_double_wall_cache"] = cache
        if cache is not None:
            data = cache["Data"]
            globals()["Path"] = GH_ObjectWrapper(data)
            globals()["Preview"] = _preview_tree(data)
            component.Message = "Double Wall\nCached | {} curves".format(data.CurveCount)
            for notice in cache["Notices"]:
                component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Warning, _newpath_zh_message(notice))
        else:
            component.Message = "Double Wall\nClick Run"
        if error:
            suffix = "；当前输出为上次成功结果。" if cache is not None else "；当前无有效输出。"
            component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(error+suffix))
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))


if "ghenv" in globals():
    _run_component()


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = '双层墙路径'
    ghenv.Component.NickName = '双层墙路径'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'Path'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '向闭合墙线内侧偏移，并在指定连接点生成双层墙路径。'
    ghenv.Component.Tooltip = '向闭合墙线内侧偏移，并在指定连接点生成双层墙路径。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '闭合单层 sliced Path；仅编辑wall，brim/infill透传', 'ConnectPoints': '向所属几何各层最近轮廓投影的连接点', 'OffsetFactor': '内偏移中心距/Width，默认1，至少1', 'GapFactor': '连接线中心距/Width，默认1，至少1', 'Run': '按钮重算=False'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '双层 sliced Path；仅编辑wall，brim/infill透传', 'Preview': '双层路径曲线副本'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
