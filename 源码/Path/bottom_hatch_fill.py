"""蛇形填充

【功能】
按指定层范围和扫描间距，为切片路径添加蛇形底部填充。

【输入端】
Path（通用对象；数据树；必填）：完整sliced PATH。
Distance（数值；单项；可选）：内缩量和扫描间距；默认0.4；模型单位。
Angle（数值；单项；可选）：扫描角度；默认45度。
Domain（通用对象；单项；可选）：单层号0、Panel文本0 to 2或Domain；真实层号0起算含两端；留空全局最低Z。

【输出端】
Path（通用对象；单项）：保留原路径并加入填充的完整PATH。
Preview（曲线；数据树）：完整路径曲线副本。

【详细用法与约束】
Path / Tree / object / 必填：完整 sliced PATH。
Distance / Item / number / 可选=0.4：内缩量与扫描间距，模型单位，必须为正数。
Angle / Item / number / 可选=45：扫描角度，度。
输出 Path / Item / object：保留所有原路径并加入独立 infill 块；Preview / Tree：曲线副本。
Domain / Item / object / 可选：单层号0、Panel文本0 to 2或Domain，0起算含两端；留空全局最低Z。
指定范围处理所有输入块的对应真实LayerIndex；保留逐块扫描与蛇形直连。
原四个几何函数与扫描主流程保留；指定范围时按块、按真实层号分别调用；无Run，自动求解。
仅处理wall角色，brim/infill完整透传；选中的边界须闭合水平、同块来源/层号/宽高含义一致。
新填充继承来源与名义宽高；逐点高度/生长方向取最近来源采样场插值，未重算支承。
原蛇形跨岛/孔洞连接未校验且不自动变为空走；连续组件保留开放填充端点。
成功时画布显示填充条数，不把通用算法限制作为运行警告；无结果仍提示。
参数/协议失败清空输出并报错；无几何结果保留原路径。拒绝重复应用本填充。

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
    ghenv.Component.Description = '按指定层范围和扫描间距，为切片路径添加蛇形底部填充。'
    ghenv.Component.Tooltip = '按指定层范围和扫描间距，为切片路径添加蛇形底部填充。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '完整sliced PATH', 'Distance': '内缩量和扫描间距；默认0.4；模型单位', 'Angle': '扫描角度；默认45度', 'Domain': '单层号0、Panel文本0 to 2或Domain；真实层号0起算含两端；留空全局最低Z'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '保留原路径并加入填充的完整PATH', 'Preview': '完整路径曲线副本'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description



PORT_ALIASES = {'Paths': 'Path', 'domain': 'Domain'}
import math
import bisect
import Rhino
import Rhino.Geometry as rg
import Grasshopper as gh
from Grasshopper import DataTree
COMPONENT_MARKER = 'BottomHatchFill:r12'
COMPONENT_MESSAGE = 'Snake Fill\nPath + Preview'
PATH_PROTOCOL = 'codex.ghpython.Path'
PATH_SCHEMA = 1
INPUT_SPECS = [
    ('Path','Path','完整sliced PATH','tree','object',False),
    ('Distance','Distance','内缩量和扫描间距；默认0.4；模型单位','item','number',True),
    ('Angle','Angle','扫描角度；默认45度','item','number',True),
    ('Domain','Domain','单层号0、Panel文本0 to 2或Domain；真实层号0起算含两端；留空全局最低Z','item','object',True),
]
OUTPUT_SPECS = [('Path','Path','保留原路径并加入填充的完整PATH','item'),
                ('Preview','Preview','完整路径曲线副本','tree')]


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

def _set_records(chunk, records):
    chunk.update(Segments=tuple(records), Curves=tuple(s["Curve"] for s in records))
    for key in ("Points", "Heights", "GrowthVectors"):
        chunk[key] = tuple(v for record in records for v in record.get(key, ()))

def preprocess_curve(crv: rg.Curve, tol: float) -> rg.Curve:
    '\n    Simplify the curve and ensure it is closed and valid in the XY plane.\n    '
    if not crv: return None
    
    # Project onto XY to prevent small Z noise from causing Offset failures.
    flat_crv = crv.DuplicateCurve()
    flat_crv.Translate(rg.Vector3d(0,0, -flat_crv.PointAtStart.Z))
    
    # Ensure closure.
    if not flat_crv.IsClosed:
        flat_crv.MakeClosed(tol)
        
    # Simplify.
    simple_crv = flat_crv.Simplify(rg.CurveSimplifyOptions.All, tol, 0.1)
    return simple_crv if simple_crv else flat_crv

def get_inset_curves(curve: rg.Curve, distance: float, tol: float):
    '\n    Apply orientation-based inward offsets with support for multiple islands.\n    '
    if not curve: return []
    
    plane = rg.Plane.WorldXY
    
    # 1. Determine orientation (CCW is positive; Offset behavior depends on the version, usually positive outward for CCW).
    # Unified convention: CCW -> negative inward; CW -> positive inward.
    orientation = curve.ClosedCurveOrientation(plane)
    
    final_dist = -distance 
    if orientation == rg.CurveOrientation.Clockwise:
        final_dist = distance 
        
    # 2. Apply the offset.
    offsets = curve.Offset(plane, final_dist, tol, rg.CurveOffsetCornerStyle.Sharp)
    
    valid_results = []
    if offsets:
        for c in offsets:
            if not c: continue
            
            # Check closure.
            if not c.IsClosed: c.MakeClosed(tol)
            
            # 3. Validate and retain all valid islands.
            amp = rg.AreaMassProperties.Compute(c)
            if amp and amp.Area > (tol * tol):
                valid_results.append(c)
                
    return valid_results

def generate_hatch_lines(boundary_curves, spacing, rot_angle_deg, tol):
    '\n    Use CurveLine Intersection instead of CurveCurve to improve efficiency.\n    '
    if not boundary_curves: return []
    
    # Compute the combined BoundingBox of all boundaries.
    union_box = rg.BoundingBox.Empty
    for c in boundary_curves:
        union_box.Union(c.GetBoundingBox(True))
        
    if not union_box.IsValid: return []

    # Prepare scan vectors.
    rot_rad = math.radians(rot_angle_deg)
    
    dir_x = rg.Vector3d.XAxis
    dir_y = rg.Vector3d.YAxis
    
    dir_x.Rotate(rot_rad, rg.Vector3d.ZAxis) # Scan-line direction.
    dir_y.Rotate(rot_rad, rg.Vector3d.ZAxis) # Step direction.
    
    # Compute coverage bounds.
    center = union_box.Center
    diag = union_box.Diagonal.Length
    half_diag = diag * 1.5 
    
    # Starting point.
    start_p = center - (dir_y * half_diag)
    
    # Compute the step count.
    steps = int((diag * 3) / spacing)
    
    hatch_segments = []
    base_line_len = diag * 3
    
    # Process all boundaries as a batch.
    for i in range(steps):
        curr_center = start_p + (dir_y * (i * spacing))
        
        # Construct the line segment.
        p_a = curr_center - (dir_x * (base_line_len * 0.5))
        p_b = curr_center + (dir_x * (base_line_len * 0.5))
        scan_line = rg.Line(p_a, p_b)
        
        # Collect intersection parameters t.
        intersection_params = []
        
        for b_crv in boundary_curves:
            events = rg.Intersect.Intersection.CurveLine(b_crv, scan_line, tol, tol)
            if events:
                for event in events:
                    intersection_params.append(event.ParameterB)
        
        intersection_params.sort()
        
        # Pair intersections to generate segments.
        if len(intersection_params) >= 2:
            for k in range(0, len(intersection_params) - 1, 2):
                t1 = intersection_params[k]
                t2 = intersection_params[k+1]
                
                if abs(t2 - t1) > 0.0001:
                    seg_pt1 = scan_line.PointAt(t1)
                    seg_pt2 = scan_line.PointAt(t2)
                    hatch_segments.append(rg.LineCurve(seg_pt1, seg_pt2))
                    
    return hatch_segments

def connect_segments_snake(segments, tol):
    if not segments: return []
    
    path_groups = []
    current_path_pts = []
    
    for i in range(len(segments)):
        seg = segments[i].DuplicateCurve()
        
        # Reverse alternate rows for the serpentine path.
        if i % 2 == 1:
            seg.Reverse()
            
        start_pt = seg.PointAtStart
        end_pt = seg.PointAtEnd
        
        current_path_pts.append(start_pt)
        current_path_pts.append(end_pt)
            
    if len(current_path_pts) > 1:
        return [rg.Polyline(current_path_pts).ToNurbsCurve()]
    return []

def create_bottom_fill(Curves, Distance, Angle, tol):
    d_val = float(Distance) if Distance is not None else 0.4
    a_val = float(Angle) if Angle is not None else 45.0
    result_tree = gh.DataTree[object]()
    global_min_z = float('inf')
    for i in range(Curves.BranchCount):
        branch = Curves.Branch(i)
        for crv in branch:
            if crv:
                z = crv.GetBoundingBox(False).Min.Z
                if z < global_min_z:
                    global_min_z = z
    if global_min_z == float('inf'):
        return result_tree
    z_threshold = tol
    for i in range(Curves.BranchCount):
        branch = Curves.Branch(i)
        path = Curves.Path(i)
        if not branch:
            continue
        layer_boundaries = []
        original_z = global_min_z
        for crv in branch:
            if not crv:
                continue
            bbox = crv.GetBoundingBox(False)
            if abs(bbox.Min.Z - global_min_z) < z_threshold:
                original_z = crv.PointAtStart.Z
                proc_crv = preprocess_curve(crv, tol)
                if proc_crv:
                    insets = get_inset_curves(proc_crv, d_val, tol)
                    layer_boundaries.extend(insets)
        if not layer_boundaries:
            continue
        raw_segs = generate_hatch_lines(layer_boundaries, d_val, a_val, tol)
        if raw_segs:
            connected = connect_segments_snake(raw_segs, tol)
            final_curves = []
            trans_vec = rg.Vector3d(0, 0, original_z)
            for c in connected:
                c.Translate(trans_vec)
                final_curves.append(c)
            result_tree.AddRange(final_curves, path)
    return result_tree

def _domain_bounds(value):
    value = _range_value(value)
    return None if value is None else _layer_range(value)


def _number(value, default, name, positive=False):
    value = default if value is None else float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(name + ' 必须为有限' + ('正数。' if positive else '数。'))
    return value


def _selected_sources(records, minimum, tolerance):
    return [r for r in records if r.get('Role') in ('wall',)
            and abs(r['Curve'].GetBoundingBox(False).Min.Z - minimum) < tolerance]


def _validate_sources(records, tolerance):
    first = records[0]
    required = ('GeoId', 'LayerIndex', 'SourcePath', 'SegmentId', 'Plane', 'LayerNormal',
                'LayerHeight', 'Width', 'HeightMetric', 'IsFirstLayer')
    for r in records:
        if any(k not in r for k in required):
            raise ValueError('选中轮廓缺少来源、层号、宽高或高度定义，请重算上游。')
        if any(r[k] != first[k] for k in ('GeoId','LayerIndex','LayerHeight','Width','HeightMetric','IsFirstLayer')):
            raise ValueError('同一打印块的选中轮廓来源/层号/工艺不同，无法为合并填充唯一赋值；请先分组。')
        if not isinstance(r['LayerIndex'], int) or isinstance(r['LayerIndex'], bool) or r['LayerIndex'] < 0:
            raise ValueError('真实层号无效。')
        if not isinstance(r['IsFirstLayer'], bool):
            raise ValueError('首层标记无效。')
        _number(r['Width'], None, 'Width', True)
        _number(r['LayerHeight'], None, 'LayerHeight', True)
        c = r['Curve']
        box = c.GetBoundingBox(False)
        if not c.IsClosed or not c.IsValid or box.Max.Z-box.Min.Z > tolerance:
            raise ValueError('原扫描算法仅支持有效、闭合、水平轮廓；不自动投影斜层。')
        if abs(c.PointAtStart.Z-first['Curve'].PointAtStart.Z) > tolerance:
            raise ValueError('选中边界不在同一水平高度。')
        prepare_sample_field(r)


def _new_fill_record(sources, curve, group_id, segment_id, tolerance):
    points, parameters = curve_endpoints(curve, tolerance)
    if len(points) < 2:
        raise ValueError('填充路径缺少有效端点。')
    fields = [(s, prepare_sample_field(s)) for s in sources]
    values = []
    for point in points:
        candidates = []
        for source, field in fields:
            c = source['SampleCurve']
            ok, t = c.ClosestPoint(point)
            if not ok:
                raise ValueError('填充点无法对应上游高度采样场。')
            candidates.append((c.PointAt(t).DistanceTo(point), field))
        field = min(candidates, key=lambda pair: pair[0])[1]
        h, v = read_sample_field(field, point)
        _number(h, None, 'Heights', True)
        values.append((h, v))
    source = sources[0]
    record = {k: source[k] for k in ('GeoId','LayerIndex','SourcePath','Plane','LayerNormal',
                                   'LayerHeight','Width','HeightMetric','IsFirstLayer')}
    if 'SourceObjectKey' in source:
        record['SourceObjectKey'] = source['SourceObjectKey']
    if 'GeoIndex' in source:
        record['GeoIndex'] = source['GeoIndex']
    record.update(Curve=curve, SegmentId=segment_id, SourceSegmentId=source['SegmentId'],
                  SourceSegmentIds=tuple(s['SegmentId'] for s in sources),
                  SourceRecords=tuple(dict(s) for s in sources),
                  SourceCurve=source['Curve'], Role='infill', PathGroupId=group_id,
                  PathGroupRole='infill', FillStrategy='bottom_hatch',
                  Points=tuple(points), SampleCurve=curve, SampleParameters=tuple(parameters),
                  Heights=tuple(h for h,v in values), GrowthVectors=tuple(v for h,v in values),
                  Sampling='segment_endpoints', HeightTransfer='nearest_source_parameter',
                  ConnectionPolicy='original_snake_unclassified')
    return record


def build_bottom_fill(input_tree, distance=None, angle=None, doc_tolerance=None, domain=None):
    bounds = _domain_bounds(domain)
    distance = _number(distance, 0.4, 'Distance', True)
    angle = _number(angle, 45.0, 'Angle')
    packets = [(wire, i, _unwrap_path(v)) for (wire, items) in _tree_items(input_tree) for (i, v) in enumerate(items)]
    packets = _path_packets(packets)
    if not packets:
        raise ValueError('请连接完整 sliced PATH。')
    _has_wall(packets)
    if any((p.Stage != 'sliced' for (_, _, p) in packets)):
        raise ValueError('蛇形填充须放在连续路径之前。')
    units = {str(p.Metadata.get('Units')) for (_, _, p) in packets}
    if len(units) != 1 or next(iter(units)) in ('None', ''):
        raise ValueError('PATH 单位缺失或不一致。')
    if any((p.Metadata.get('BottomHatchFill') or any((s.get('FillStrategy') == 'bottom_hatch' for c in p.Chunks for s in c['Segments'])) for (_, _, p) in packets)):
        raise ValueError('输入已含蛇形填充，请从填充前的 PATH 重算，避免重复添加。')
    if doc_tolerance is None:
        doc = Rhino.RhinoDoc.ActiveDoc
        doc_tolerance = doc.ModelAbsoluteTolerance if doc else packets[0][2].Metadata.get('Tolerance')
    tolerance = _number(doc_tolerance, None, 'Tolerance', True)
    original_tree = DataTree[rg.Curve]()
    (slots, chunks, empty, occupied) = ([], [], [], set())
    minimum = float('inf')
    for (pi, (wire, item, data)) in enumerate(packets):
        prefix = ()
        empty.extend((prefix + tuple(p) for p in data.Metadata.get('EmptyPaths', ())))
        for (ci, source) in enumerate(data.Chunks):
            key = prefix + tuple(source['TreePath'])
            if key in occupied:
                raise ValueError('输入包含重复打印块路径。')
            occupied.add(key)
            chunk = dict(source, TreePath=key)
            chunks.append(chunk)
            records = tuple(source['Segments'])
            if len(records) != len(source['Curves']):
                raise ValueError('sliced 曲线与段记录不一致。')
            tree_key = (len(slots),)
            original_tree.EnsurePath(_gh_path(tree_key))
            for record in records:
                if record.get('Role') not in ('wall',):
                    continue
                curve = record['Curve']
                if curve is None or not curve.IsValid:
                    raise ValueError('PATH 包含无效轮廓。')
                minimum = min(minimum, curve.GetBoundingBox(False).Min.Z)
                original_tree.Add(curve.DuplicateCurve(), _gh_path(tree_key))
            slots.append((pi, ci, source, key, records))
    selected = {}
    if bounds is None:
        for (index, (_, _, _, _, records)) in enumerate(slots):
            sources = _selected_sources(records, minimum, tolerance)
            if sources:
                _validate_sources(sources, tolerance)
                selected[index] = sources
        result = create_bottom_fill(original_tree, distance, angle, tolerance)
    else:
        jobs = []
        for (pi, ci, source, key, records) in slots:
            layers = {}
            for record in records:
                if record.get('Role') not in ('wall',):
                    continue
                layer = record.get('LayerIndex')
                if not isinstance(layer, int) or isinstance(layer, bool) or layer < 0:
                    raise ValueError('真实层号无效。')
                if bounds[0] <= layer <= bounds[1]:
                    layers.setdefault(layer, []).append(record)
            for layer in sorted(layers):
                sources = layers[layer]
                _validate_sources(sources, tolerance)
                selected[len(jobs)] = sources
                jobs.append((pi, ci, source, key, sources))
        slots = jobs
        result = DataTree[rg.Curve]()
        for (index, (_, _, _, _, sources)) in enumerate(slots):
            path = _gh_path((index,))
            layer_tree = DataTree[rg.Curve]()
            for record in sources:
                layer_tree.Add(record['Curve'].DuplicateCurve(), path)
            filled = create_bottom_fill(layer_tree, distance, angle, tolerance)
            if filled.PathExists(path):
                for curve in filled.Branch(path):
                    result.Add(curve, path)
    namespace = max((p[0] for p in occupied.union(empty) if p), default=-1) + 1
    used_ids = {s['SegmentId'] for c in chunks for s in c['Segments']}
    count = 0
    for (index, (pi, ci, source, key, records)) in enumerate(slots):
        path = _gh_path((index,))
        curves = list(result.Branch(path)) if result.PathExists(path) else []
        for (k, curve) in enumerate(curves):
            if index not in selected:
                raise ValueError('原填充结果没有对应来源层。')
            group_id = 'bottom_hatch:{}:{}:{}'.format(pi, ci, k)
            if bounds is not None:
                group_id += ':layer:{}'.format(selected[index][0]['LayerIndex'])
            sid = group_id
            while sid in used_ids:
                sid += ':new'
            used_ids.add(sid)
            record = _new_fill_record(selected[index], curve, group_id, sid, tolerance)
            record['ChunkId'] = len(chunks)
            new_chunk = dict(ObjectIndex=source['ObjectIndex'], TreePath=(namespace, index, k), ChunkId=len(chunks), GeoId=record['GeoId'], LayerHeight=record['LayerHeight'], Width=record['Width'], SourceTreePath=key, PathGroupId=group_id, PathGroupRole='infill', Sampling='segment_endpoints', FillStrategy='bottom_hatch')
            _set_records(new_chunk, [record])
            chunks.append(new_chunk)
            count += 1
    metadata = dict(packets[0][2].Metadata) if len(packets) == 1 else {}
    metadata.update(Inputs=tuple((dict(p.Metadata) for (_, _, p) in packets)), Units=next(iter(units)), Tolerance=tolerance, EmptyPaths=tuple(empty), BottomHatchFill=True, BottomHatchCount=count, BottomHatchDistance=distance, BottomHatchAngle=angle, BottomHatchDomain=bounds, BottomHatchSelection='layer_domain' if bounds is not None else 'global_min_z', HeightTransfer='nearest_source_parameter')
    metadata.update(GroupingComplete=all(d.Metadata.get('GroupingComplete') is True for _, _, d in packets), AuxiliaryOrderDirty=True)
    notices = []
    if not count:
        notices.append('原算法未生成填充，所有输入路径保留。')
    return (_path_output('sliced', chunks, metadata), notices)


def _run_component():
    input_path = globals().get("Path")
    from Grasshopper.Kernel.Types import GH_ObjectWrapper
    component = ghenv.Component
    globals()['Path'] = None
    globals()['Preview'] = DataTree[rg.Curve]()
    try:
        if not _ensure_ports(component):
            return
        component.Name, component.NickName = '蛇形填充', 'SnakeFill'
        component.Message = COMPONENT_MESSAGE
        value = input_path
        if value is None or (hasattr(value,'BranchCount') and value.BranchCount==0):
            component.Message = 'Snake Fill\n等待 Path'
            return
        data, notices = build_bottom_fill(value, globals().get('Distance'), globals().get('Angle'),
                                          domain=globals().get('Domain'))
        preview = _preview_tree(data)
        globals()['Path'] = GH_ObjectWrapper(data)
        globals()['Preview'] = preview
        component.Message = '蛇形填充\n已生成 {} 条填充'.format(data.Metadata['BottomHatchCount'])
        for notice in notices:
            component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Warning, _newpath_zh_message(notice))
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))


if 'ghenv' in globals():
    _run_component()


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = '蛇形填充'
    ghenv.Component.NickName = '蛇形填充'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'Path'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '按指定层范围和扫描间距，为切片路径添加蛇形底部填充。'
    ghenv.Component.Tooltip = '按指定层范围和扫描间距，为切片路径添加蛇形底部填充。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '完整sliced PATH', 'Distance': '内缩量和扫描间距；默认0.4；模型单位', 'Angle': '扫描角度；默认45度', 'Domain': '单层号0、Panel文本0 to 2或Domain；真实层号0起算含两端；留空全局最低Z'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '保留原路径并加入填充的完整PATH', 'Preview': '完整路径曲线副本'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
