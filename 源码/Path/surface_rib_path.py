"""双道结构肋

【功能】
按定位点、层范围和悬垂角，在墙体内侧生成双道往返结构肋。

【输入端】
Path（通用对象；数据树；必填）：闭合 sliced Path；仅编辑wall。
Points（点；列表；必填）：加肋定位点；多分支请Flatten。
RibLength（数值；单项；必填）：从内侧墙道起算的完整延伸长度。
Domain（通用对象；单项；必填）：零基层范围，包含两端；Panel 0 to 10或Domain。
Angle（数值；单项；可选）：相对层法线的悬垂角（度），0<Angle<=45，默认45。

【输出端】
Path（通用对象；单项）：加入结构肋的 sliced Path；仅编辑wall，brim/infill透传。
Preview（曲线；数据树）：结构肋路径曲线副本。

【详细用法与约束】
用途：用点定位墙线，沿层平面内的内法线插入双道往返肋。
Path / Tree / object / 必填：闭合 sliced Path，接在连续路径之前。
Points / List / point / 必填：加肋位置；每点归属最近来源，各层投影到墙线。
RibLength / Item / number / 必填：从内侧接入墙道起算的完整延伸长度。
Domain / Item / object / 必填：真实零基层号，包含两端，支持Panel的0 to 10及原生Domain。
Angle / Item / number / 可选：相对层法线的悬垂角，单位度，默认45，范围0<Angle<=45。
按范围起始层距模型底/顶的层号距离选择方向，等距按底部：底部肋下端全长向上收回，顶部肋上端全长向下收回。
零长度端保留原墙线；从零端按实际层间法向距离*tan(Angle)增长，达到全长后保持。
范围高度不足以达到完整RibLength时报错，不缩短长度；缺失范围端层、非平行层报错。
双道中心距沿用Width。沿用原裁口、交叉检查和最近参数H/方向继承，未重算真实支承高度。
仅编辑wall；brim/infill及范围外记录透传。输出Path / Item、Preview / Tree，仅曲线预览。
自动求解，失败清空输出。

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
    ghenv.Component.Description = '按定位点、层范围和悬垂角，在墙体内侧生成双道往返结构肋。'
    ghenv.Component.Tooltip = '按定位点、层范围和悬垂角，在墙体内侧生成双道往返结构肋。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '闭合 sliced Path；仅编辑wall', 'Points': '加肋定位点；多分支请Flatten', 'RibLength': '从内侧墙道起算的完整延伸长度', 'Domain': '零基层范围，包含两端；Panel 0 to 10或Domain', 'Angle': '相对层法线的悬垂角（度），0<Angle<=45，默认45'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '加入结构肋的 sliced Path；仅编辑wall，brim/infill透传', 'Preview': '结构肋路径曲线副本'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description



PORT_ALIASES = {'Paths': 'Path'}
import math
import bisect
import Rhino.Geometry as rg
from Grasshopper import DataTree

COMPONENT_MARKER = 'SurfaceRib:r16'
COMPONENT_MESSAGE = 'Point Rib\nInward + Taper'
PATH_PROTOCOL = "codex.ghpython.Path"
PATH_SCHEMA = 1
INPUT_SPECS = [('Path', 'Path', '闭合 sliced Path；仅编辑wall', 'tree', 'object', False), ('Points', 'Points', '加肋定位点；多分支请Flatten', 'list', 'point', False), ('RibLength', 'RibLength', '从内侧墙道起算的完整延伸长度', 'item', 'number', False), ('Domain', 'Domain', '零基层范围，包含两端；Panel 0 to 10或Domain', 'item', 'object', False), ('Angle', 'Angle', '相对层法线的悬垂角（度），0<Angle<=45，默认45', 'item', 'number', True)]
OUTPUT_SPECS = [('Path', 'Path', '加入结构肋的 sliced Path；仅编辑wall，brim/infill透传', 'item'), ('Preview', 'Preview', '结构肋路径曲线副本', 'tree')]

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


def _hits(a, b, tol):
    events = rg.Intersect.Intersection.CurveCurve(a, b, tol, tol)
    if events is None:
        raise ValueError("曲线求交失败。")
    points = []
    for event in events:
        if event.IsOverlap:
            raise ValueError("肋与路径重叠，无法确定唯一接入位置。")
        if event.IsPoint and not any(p.DistanceTo(event.PointA) <= tol for p in points):
            points.append(event.PointA)
    return points


def _append(parts, tol):
    result = rg.PolyCurve()
    for part in parts:
        if part is None or part.GetLength() <= tol:
            raise ValueError("结构肋路径裁切退化。")
        if result.SegmentCount and result.PointAtEnd.DistanceTo(part.PointAtStart) > tol:
            raise ValueError("结构肋路径端点不连续。")
        if not result.Append(part):
            raise ValueError("结构肋拼接失败。")
    return result


def _rooted_side(center, main, plane, distance, width, tol):
    offsets = center.Offset(plane, distance, tol, rg.CurveOffsetCornerStyle.Sharp)
    if not offsets or len(offsets) != 1:
        raise ValueError("肋偏移失败或分裂，不缩小打印宽度。")
    side = offsets[0].DuplicateCurve()
    if side.IsClosed or not side.IsValid:
        raise ValueError("肋偏移结果无效。")
    if side.PointAtEnd.DistanceTo(center.PointAtStart) < side.PointAtStart.DistanceTo(center.PointAtStart):
        side.Reverse()
    tangent = rg.Vector3d(side.TangentAtStart)
    if not tangent.Unitize():
        raise ValueError("肋根切向退化。")
    extended = _append([rg.LineCurve(side.PointAtStart-tangent*(4*width), side.PointAtStart), side], tol)
    hits = _hits(extended, main, tol)
    if not hits:
        raise ValueError("肋侧线未接回主路径，检查肋根角度、Width和邻近路径。")
    candidates = []
    for hit in hits:
        if hit.DistanceTo(center.PointAtStart) <= 4*width:
            t, p = _closest(extended, hit)
            candidates.append((t, p))
    if not candidates:
        raise ValueError("肋侧线接回点偏离肋根，检查肋根角度、Width和邻近路径。")
    candidates.sort(key=lambda item: item[0], reverse=True)
    t, p = candidates[0]
    trimmed = extended.Trim(t, extended.Domain.Max)
    if trimmed is None or trimmed.GetLength() <= tol:
        raise ValueError("肋长度不足。")
    return trimmed, _closest(main, p)[0]


def _rib_cut(main, center, plane, width, tol):
    # center already points from the contact end toward the free rib end.
    root_t, root = _closest(main, center.PointAtStart)
    if root.DistanceTo(center.PointAtStart) > tol:
        raise ValueError("肋根未接触主路径。")
    a, ta = _rooted_side(center, main, plane, width/2, width, tol)
    b, tb = _rooted_side(center, main, plane, -width/2, width, tol)
    length = main.GetLength()
    sa, sb, sr = (_station(main, t) for t in (ta, tb, root_t))
    span = (sb-sa) % length
    if (sr-sa) % length > span:
        a, b, ta, tb, sa, sb = b, a, tb, ta, sb, sa
        span = (sb-sa) % length
    if span <= tol or span > 8*width or span >= length-tol:
        raise ValueError("肋根断口过大或退化。")
    tip = rg.LineCurve(a.PointAtEnd, b.PointAtEnd)
    if not b.Reverse():
        raise ValueError("肋回程反向失败。")
    detour = _append([a, tip, b], tol)
    loop = _append([detour, _trim_forward(main, tb, ta, tol)], tol)
    _simple(loop, tol)
    return dict(start=ta, end=tb, station=sa, span=span, curve=detour)


def _insert_ribs(main, centers, plane, width, tol):
    if not centers:
        return main.DuplicateCurve()
    cuts = sorted((_rib_cut(main, c, plane, width, tol) for c in centers), key=lambda c: c['station'])
    length = main.GetLength()
    parts = []
    for i, cut in enumerate(cuts):
        following = cuts[(i+1) % len(cuts)]
        separation = (following['station']-cut['station']) % length if len(cuts)>1 else length
        if separation <= cut['span']+tol:
            raise ValueError("多个肋根断口重叠。")
        parts.extend([cut['curve'], _trim_forward(main, cut['end'], following['start'], tol)])
    result = _append(parts, tol)
    _simple(result, tol)
    return result


def _rib_record(source, curve, count, tol):
    if not count:
        return dict(source, Curve=curve)
    field = prepare_sample_field(source)
    points, parameters = curve_endpoints(curve, tol)
    samples = [read_sample_field(field, p) for p in points]
    result = dict(source)
    result.update(Curve=curve, SourceCurve=source['Curve'], SourceSegmentId=source['SegmentId'],
                  SegmentId=source['SegmentId']+':rib', RibCount=count, RibPassCount=2,
                  RibCenterDistance=float(source['Width']), RibHeightTransfer='nearest_source_parameter',
                  SampleCurve=curve, SampleParameters=tuple(parameters), Points=tuple(points),
                  Heights=tuple(h for h,v in samples), GrowthVectors=tuple(v for h,v in samples))
    return result


def _inward_centers(section, entries, plane, tol, rib_length=None):
    'Split at actual intersections; for double walls, SourceCurve defines the geometric interior and Curve defines the wall band.'
    parameters = [section.Domain.Min, section.Domain.Max]
    for entry in entries:
        source = entry['source']
        boundaries = [source['Curve']]
        if source.get('WallCount') == 2:
            boundaries.append(source['SourceCurve'])
        for boundary in boundaries:
            parameters.extend(_closest(section, p)[0] for p in _hits(section, boundary, tol))
    parameters.sort()
    unique = []
    for t in parameters:
        if not unique or section.PointAt(t).DistanceTo(section.PointAt(unique[-1])) > tol:
            unique.append(t)
    result = []
    for start, end in zip(unique, unique[1:]):
        center = section.Trim(start, end)
        if center is None or center.GetLength() <= tol:
            continue
        midpoint = center.PointAt((center.Domain.Min+center.Domain.Max)/2)
        inside, wall_band = False, False
        for entry in entries:
            source = entry['source']
            main = source['Curve']
            boundary = source['SourceCurve'] if source.get('WallCount') == 2 else main
            inside = inside or boundary.Contains(midpoint, plane, tol) == rg.PointContainment.Inside
            if source.get('WallCount') == 2:
                wall_band = wall_band or main.Contains(midpoint, plane, tol) == rg.PointContainment.Inside
        if not inside or wall_band:
            continue
        owners = []
        for entry in entries:
            for end_index, point in enumerate((center.PointAtStart, center.PointAtEnd)):
                if _closest(entry['source']['Curve'], point)[1].DistanceTo(point) <= tol:
                    owners.append((entry, end_index))
        if not owners:
            continue
        if len(owners) == 2 and {end_index for _, end_index in owners} == {0, 1} and rib_length is not None:
            owners = [next(item for item in owners if item[1] == 0)]
        if len(owners) != 1:
            raise ValueError('内法线接入墙道不唯一，无法确定肋根。')
        owner, end_index = owners[0]
        if end_index:
            center.Reverse()
        if rib_length is not None:
            available = center.GetLength()
            far_contact = any(_closest(e['source']['Curve'], center.PointAtEnd)[1].DistanceTo(center.PointAtEnd) <= tol for e in entries)
            if rib_length > available+tol or (far_contact and rib_length >= available-tol):
                raise ValueError('RibLength超过可用内部肋长或到达另一侧墙，请减小指定肋长。')
            if rib_length < available:
                ok, tip_t = center.LengthParameter(rib_length)
                if not ok:
                    raise ValueError('无法定位指定肋长的端点。')
                center = center.Trim(center.Domain.Min, tip_t)
        result.append((owner, center))
    return result


def _range_value(value):
    for _ in range(8):
        other = getattr(value, "Value", value)
        if other is value:
            return value
        value = other
    raise ValueError("层范围包装嵌套过深。")

def _layer_range(value, allow_negative=False, allow_reverse=False):
    import math
    import re
    value = _range_value(value)
    if isinstance(value, str):
        number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
        match = re.fullmatch(r"\s*("+number+r")(?:\s*to\s*("+number+r"))?\s*", value, re.I)
        if match is None:
            raise ValueError("请接单层号（如0）、Panel文本0 to 2或Domain。")
        a, b = match.groups()
        values = (a, a if b is None else b)
    elif hasattr(value, "T0") and hasattr(value, "T1"):
        values = (value.T0, value.T1)
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        values = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        values = (value, value)
    else:
        raise ValueError("请接单层号（如0）、Panel文本0 to 2或Domain。")
    if any(isinstance(v, bool) for v in values):
        raise ValueError("层号不能使用布尔值。")
    try:
        a, b = (float(v) for v in values)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("层号必须是有限整数。")
    if not all(math.isfinite(v) and v.is_integer() for v in (a, b)):
        raise ValueError("层号必须是有限整数，不能静默取整。")
    if not allow_negative and min(a, b) < 0:
        raise ValueError("此组件层号必须非负。")
    if not allow_reverse and a > b:
        raise ValueError("此组件范围起点不能大于终点。")
    return int(a), int(b)

def _domain_bounds(value):
    value = _range_value(value)
    return None if value is None else _layer_range(value)

def _rib_points(values):
    if values is None:
        raise ValueError('请连接Points加肋定位点。')
    values = list(values) if isinstance(values, (list, tuple)) else [values]
    result = []
    for value in values:
        value = _range_value(value)
        if not isinstance(value, rg.Point3d) or not all(math.isfinite(v) for v in (value.X, value.Y, value.Z)):
            raise ValueError('Points必须为有效Point3d。')
        result.append(value)
    if not result:
        raise ValueError('Points列表为空。')
    return result


def _rib_boundary(entry):
    source = entry['source']
    return source['SourceCurve'] if source.get('WallCount') == 2 else source['Curve']


def _point_center(point, entries, plane, tol, length):
    normal = rg.Vector3d(plane.Normal)
    normal.Unitize()
    projected = point-normal*((point-plane.Origin)*normal)
    candidates = []
    for entry in entries:
        boundary = _rib_boundary(entry)
        t, root = _closest(boundary, projected)
        candidates.append((root.DistanceTo(projected), entry, boundary, t, root))
    candidates.sort(key=lambda v: v[0])
    _, entry, boundary, t, root = candidates[0]
    for distance, other, curve, parameter, location in candidates[1:]:
        if abs(distance-candidates[0][0]) <= tol and location.DistanceTo(root) > tol:
            raise ValueError('定位点到多个墙线等距，请移动Points。')
    tangent = boundary.TangentAt(t)
    inward = rg.Vector3d(normal.Y*tangent.Z-normal.Z*tangent.Y,
                          normal.Z*tangent.X-normal.X*tangent.Z,
                          normal.X*tangent.Y-normal.Y*tangent.X)
    if not inward.Unitize():
        raise ValueError('定位点墙线切向退化。')
    probe = max(10*tol, entry['width']*.01)
    signs = [sign for sign in (1, -1) if boundary.Contains(root+inward*(sign*probe), plane, tol) == rg.PointContainment.Inside]
    if len(signs) != 1:
        raise ValueError('定位点位于尖角或无法确定内法线，请移动Points。')
    inward *= signs[0]
    reach = boundary.GetLength()+length+4*entry['width']
    section = rg.LineCurve(root-inward*(4*tol), root+inward*reach)
    # First free interior interval: never jump across a wall or an internal hole.
    boundaries = [e['source']['Curve'] for e in entries]+[boundary]
    hits = sorted(((_closest(section, p)[0], p) for curve in boundaries for p in _hits(section, curve, tol)), key=lambda item: item[0])
    for (_, a), (_, b) in zip(hits, hits[1:]):
        if a.DistanceTo(b) <= tol:
            continue
        mid = a+(b-a)*.5
        if boundary.Contains(mid, plane, tol) != rg.PointContainment.Inside:
            continue
        if any(e['source'].get('WallCount') == 2 and e['source']['Curve'].Contains(mid, plane, tol) == rg.PointContainment.Inside for e in entries):
            continue
        centers = _inward_centers(rg.LineCurve(a, b), entries, plane, tol, length)
        if len(centers) != 1:
            raise ValueError('无法确定唯一内侧墙道接入点。')
        return centers[0]
    raise ValueError('内法线未找到有效内部空间。')


def _point_plans(groups, points, bounds, length, angle):
    start, end = bounds
    plans = {}
    for index, point in enumerate(points):
        distances = []
        for key, entries in groups.items():
            for e in entries:
                distances.append((_closest(_rib_boundary(e), point)[1].DistanceTo(point), key[:2], e['tol']))
        distances.sort(key=lambda v: v[0])
        owner, tol = distances[0][1:]
        if any(key != owner and abs(d-distances[0][0]) <= tol for d,key,_ in distances):
            raise ValueError('Points[{}]到多个来源等距。'.format(index))
        layers = {key[2]: entries for key, entries in groups.items() if key[:2] == owner}
        if start not in layers or end not in layers:
            raise ValueError('Domain端层在定位点所属模型中不存在。')
        bottom = start-min(layers) <= max(layers)-start
        selected = sorted(i for i in layers if start <= i <= end)
        reference = layers[start][0]['source']['Plane']
        heights = {start: 0.0}
        for previous, current in zip(selected, selected[1:]):
            plane = layers[current][0]['source']['Plane']
            before = layers[previous][0]['source']['Plane']
            if abs(abs(plane.Normal*reference.Normal)-1) > 1e-8:
                raise ValueError('悬垂角控制暂不支持非平行层。')
            step = abs((plane.Origin-before.Origin)*reference.Normal)
            if step <= tol:
                raise ValueError('层间法向距离退化。')
            heights[current] = heights[previous]+step
        slope = math.tan(math.radians(angle))
        if heights[end]*slope < length-tol:
            raise ValueError('Domain高度不足以按Angle收回完整RibLength，请扩大范围或减小长度。')
        for layer in selected:
            distance = heights[end]-heights[layer] if bottom else heights[layer]
            extension = min(length, distance*slope)
            if extension > tol:
                plans.setdefault(owner+(layer,), []).append((index, point, extension))
    return plans


def build_surface_rib(input_tree, points, rib_length=None, domain=None, angle=None):
    packets = [(wire, i, _unwrap_path(v)) for (wire, items) in _tree_items(input_tree) for (i, v) in enumerate(items)]
    packets = _path_packets(packets)
    if packets and (not _has_wall(packets)):
        return _path_passthrough(packets)
    if isinstance(rib_length, bool) or rib_length is None:
        raise ValueError('RibLength必须是有限正数。')
    rib_length = float(rib_length)
    angle = 45.0 if angle is None else angle
    if isinstance(angle, bool) or not math.isfinite(float(angle)) or (not 0 < float(angle) <= 45):
        raise ValueError('Angle必须大于0且不超过45度。')
    angle = float(angle)
    if not math.isfinite(rib_length) or rib_length <= 0:
        raise ValueError('RibLength必须是有限正数。')
    bounds = _domain_bounds(domain)
    if bounds is None:
        raise ValueError('请连接Domain层范围。')
    points = _rib_points(points)
    point_counts = [0] * len(points)
    if not packets:
        raise ValueError('请连接 Path 数据包。')
    if len({str(d.Metadata.get('Units')) for (_, _, d) in packets}) != 1:
        raise ValueError('输入 Path 单位不同。')
    (chunks, empty, groups) = ([], [], {})
    for (pi, (wire, item, data)) in enumerate(packets):
        if data.Stage != 'sliced':
            raise ValueError('请接尚未加入本结构肋的 sliced Path，放在连续路径之前。')
        tol = float(data.Metadata.get('Tolerance', 0.001))
        if not math.isfinite(tol) or tol <= 0:
            raise ValueError('Path 公差无效。')
        prefix = ()
        empty.extend((prefix + tuple(p) for p in data.Metadata.get('EmptyPaths', ())))
        for original in data.Chunks:
            chunk = dict(original, TreePath=prefix + tuple(original['TreePath']), Segments=[], Curves=[])
            chunks.append(chunk)
            for source in original['Segments']:
                if source.get('Role') != 'wall':
                    chunk['Segments'].append(source)
                    chunk['Curves'].append(source['Curve'])
                    continue
                if source.get('RibCount'):
                    raise ValueError('wall已包含结构肋，请重新计算上游。')
                (main, plane) = (source['Curve'], source['Plane'])
                _simple(main, tol)
                if not plane.IsValid or not main.IsInPlane(plane, tol) or source.get('LayerIndex') is None:
                    raise ValueError('输入必须是层平面内的闭合层段。')
                prepare_sample_field(source)
                width = float(source.get('Width', original.get('Width', 0)))
                if not math.isfinite(width) or width <= 2 * tol:
                    raise ValueError('Path.Width 无效。')
                slot = len(chunk['Segments'])
                chunk['Segments'].append(source)
                chunk['Curves'].append(main)
                entry = dict(source=dict(source, Width=width), chunk=chunk, slot=slot, tol=tol, width=width, centers=[])
                groups.setdefault((pi, source['GeoId'], source['LayerIndex']), []).append(entry)
    if not groups:
        raise ValueError('Path 没有可编辑层段。')
    plans = _point_plans(groups, points, bounds, rib_length, angle)
    affected_layers = []
    count = 0
    for (key, entries) in groups.items():
        first = entries[0]
        (plane, tol) = (first['source']['Plane'], first['tol'])
        if any((not e['source']['Curve'].IsInPlane(plane, tol) for e in entries)):
            raise ValueError('同来源同层的记录平面不一致。')
        for (point_index, point, extension) in plans.get(key, ()):
            try:
                (owner, center) = _point_center(point, entries, plane, tol, extension)
            except Exception as exc:
                raise ValueError('Points[{}] / {} / 第{}层：{}'.format(point_index, key[1], key[2], exc))
            owner['centers'].append(center)
            count += 1
            point_counts[point_index] += 1
        if any((e['centers'] for e in entries)):
            affected_layers.append(key)
        results = []
        for e in entries:
            source = e['source']
            try:
                curve = _insert_ribs(source['Curve'], e['centers'], source['Plane'], e['width'], e['tol']) if e['centers'] else source['Curve']
                record = _rib_record(source, curve, len(e['centers']), e['tol']) if e['centers'] else e['chunk']['Segments'][e['slot']]
            except Exception as exc:
                raise ValueError('{}：{}'.format(source['SegmentId'], exc))
            results.append((curve, bool(e['centers'])))
            e['chunk']['Segments'][e['slot']] = record
            e['chunk']['Curves'][e['slot']] = curve
        for (i, (curve, edited)) in enumerate(results):
            for (other, other_edited) in results[:i]:
                if (edited or other_edited) and _hits(curve, other, tol):
                    raise ValueError('新增肋与同层其他轮廓相交。')
    if not groups:
        raise ValueError('Path 没有可编辑层段。')
    if len({c['TreePath'] for c in chunks}) != len(chunks):
        raise ValueError('重复分块树路径。')
    for chunk in chunks:
        chunk.update(Curves=tuple(chunk['Curves']), Segments=tuple(chunk['Segments']))
        if any((r.get('Role') == 'wall' for r in chunk['Segments'])):
            _refresh_role_chunk(chunk, chunk['Segments'])
    meta = dict(packets[0][2].Metadata)
    meta.update(PointRibs=True, RibCount=count, RibPassCount=2, EmptyPaths=tuple(empty), RibPointCount=len(points), RibCountsByPoint=tuple(point_counts), RibLayers=tuple(affected_layers), RibLayerCount=len(affected_layers), RibLayerSelection='domain', RibDomain=bounds, RibAngle=angle, RibDirection='inward', RibLength=rib_length, RibHeightTransfer='nearest_source_parameter', Inputs=tuple((dict(d.Metadata) for (_, _, d) in packets)), Tolerance=max((float(d.Metadata.get('Tolerance', 0.001)) for (_, _, d) in packets)))
    return (_path_output('sliced', chunks, meta), [])


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
            if getattr(param, "VariableName", param.NickName) in ("Points", "Path") and hasattr(param, "Hidden"):
                param.Hidden = True
        component.Name, component.NickName = "双道结构肋", "SurfaceRib"
        paths_input = input_path
        points_input = globals().get("Points")
        if paths_input is None or (hasattr(paths_input, "BranchCount") and paths_input.BranchCount == 0):
            component.Message = "Point Rib\n等待 Path"
            return
        if points_input is None or (isinstance(points_input, (list, tuple)) and len(points_input) == 0):
            component.Message = "Point Rib\n等待 Points"
            return
        data, notices = build_surface_rib(paths_input, points_input, globals().get('RibLength'), globals().get('Domain'), globals().get('Angle'))
        globals()["Path"] = GH_ObjectWrapper(data)
        globals()["Preview"] = _preview_tree(data)
        component.Message = "Point Rib\n{} layers | {} ribs".format(
            data.Metadata.get('RibLayerCount', 0), data.Metadata.get('RibCount', 0)
        )
        for notice in notices:
            component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Warning, _newpath_zh_message(notice))
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))


if "ghenv" in globals():
    _run_component()


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = '双道结构肋'
    ghenv.Component.NickName = '双道结构肋'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'Path'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '按定位点、层范围和悬垂角，在墙体内侧生成双道往返结构肋。'
    ghenv.Component.Tooltip = '按定位点、层范围和悬垂角，在墙体内侧生成双道往返结构肋。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '闭合 sliced Path；仅编辑wall', 'Points': '加肋定位点；多分支请Flatten', 'RibLength': '从内侧墙道起算的完整延伸长度', 'Domain': '零基层范围，包含两端；Panel 0 to 10或Domain', 'Angle': '相对层法线的悬垂角（度），0<Angle<=45，默认45'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '加入结构肋的 sliced Path；仅编辑wall，brim/infill透传', 'Preview': '结构肋路径曲线副本'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
