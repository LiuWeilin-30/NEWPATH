"""螺旋填充

【功能】
在首个模型组的指定层添加螺旋填充，并保留全部原路径。

【输入端】
Path（通用对象；数据树；必填）：完整sliced PATH。
Distance（数值；单项；可选）：偏移步距/ 默认1。
Domain（通用对象；单项；可选）：层范围：0起算包含两端，0到2为第一至第三层；单层号0、Panel文本0 to 2或Domain；留空最低层。

【输出端】
Path（通用对象；单项）：全部墙和填充 PATH。
Preview（曲线；数据树）：按角色分组的完整曲线预览。

【详细用法与约束】
用途：保留全部输入路径，在第一组指定层添加原版拓扑 U 形填充。
Path / Tree / object / 必填：sliced PATH，放在连续路径之前。
Distance / Item / number / 可选：原算法偏移步距；留空或0沿用1，负数保留原处理。
Domain / Item / object / 可选：单层号0、Panel 文本 0 to 2 或 GH Domain，0起算、包含两端，0到2即第一至第三层；留空选最低Z层。
第一组为首个PATH跳过前置纯brim/infill块后的模型组（空组不跳过）；其他分组完整保留。
输出 Path / Item / object：全部墙与填充；Preview / Tree：独立曲线副本。
原偏移/分裂/裁缝/连接函数逐字保留；WorldXY、0.001、500圈及末步0.55均不变。
原slice角色标为wall，已有其他角色保留；新增infill独立成块，禁止隐式跨岛连线。
每段/块含PathGroupId、PathGroupRole、SourceTreePath；原ChunkId/来源保留。
新增填充继承Width及逐点高度/法向插值。没有填充结果时仍返回原墙并提示。

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
    ghenv.Component.Description = '在首个模型组的指定层添加螺旋填充，并保留全部原路径。'
    ghenv.Component.Tooltip = '在首个模型组的指定层添加螺旋填充，并保留全部原路径。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '完整sliced PATH', 'Distance': '偏移步距/ 默认1', 'Domain': '层范围：0起算包含两端，0到2为第一至第三层；单层号0、Panel文本0 to 2或Domain；留空最低层'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '全部墙和填充 PATH', 'Preview': '按角色分组的完整曲线预览'}.get(_ui_port.Name)
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
from Grasshopper.Kernel.Data import GH_Path

COMPONENT_MARKER = 'SpiralInfill:r11'
COMPONENT_MESSAGE = "Spiral Infill\nWall + Infill"
PATH_PROTOCOL = "codex.ghpython.Path"
PATH_SCHEMA = 1
INPUT_SPECS = [
    ("Path", "Path", '完整sliced PATH', "tree", "object", False),
    ("Distance", "Distance", '偏移步距/ 默认1', "item", "number", True),
    ("Domain", "Domain", '层范围：0起算包含两端，0到2为第一至第三层；单层号0、Panel文本0 to 2或Domain；留空最低层', "item", "object", True),
]
OUTPUT_SPECS = [
    ("Path", "Path", '全部墙和填充 PATH', "item"),
    ("Preview", "Preview", '按角色分组的完整曲线预览', "tree"),
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

def get_area(c):
    amp = rg.AreaMassProperties.Compute(c)
    return amp.Area if amp else 0

def get_centroid(c):
    try:
        return rg.AreaMassProperties.Compute(c).Centroid
    except:
        return c.GetBoundingBox(True).Center

def generate_layers(input_curve, distance):
    if not input_curve: return [], 0.0, rg.Plane.WorldXY
    
    # Convert to NURBS and try to obtain a plane.
    c0 = input_curve.ToNurbsCurve()
    # Basic planarity repair.
    bbox = c0.GetBoundingBox(True)
    z_height = bbox.Min.Z
    plane = rg.Plane(rg.Point3d(0,0,z_height), rg.Vector3d.ZAxis)

    dist = float(distance)
    tol = 0.001
    
    # --- Orientation correction ---
    # Use one Offset operation to determine inward versus outward direction.
    try:
        temp = c0.Offset(plane, dist, tol, rg.CurveOffsetCornerStyle.Sharp)
    except: return [], dist, plane
    
    curr_area = get_area(c0)
    # If Offset increases the area, dist points outward; negate it to point inward.
    next_area = sum([get_area(x) for x in temp]) if temp else 0
    if next_area > curr_area: dist = -dist 
    
    layers = [] 
    current_loops = [c0]
    
    # Threshold: 1.5 times the step distance; retain small islands while filtering tiny noise.
    min_len = abs(dist) * 1.5
    
    # Limit the ring count to prevent infinite loops.
    for _ in range(500): 
        next_loops = []
        for lc in current_loops:
            try:
                res = lc.Offset(plane, dist, tol, rg.CurveOffsetCornerStyle.Sharp)
                if res:
                    for r in res:
                        if r.GetLength() > min_len:
                            next_loops.append(r)
            except: continue
        
        if not next_loops:
            # Final infill step: fill the central hole.
            fill_layer = []
            fill_dist = dist * 0.55
            for last_c in current_loops:
                try:
                    f_res = last_c.Offset(plane, fill_dist, tol, rg.CurveOffsetCornerStyle.Sharp)
                    if f_res:
                        for f in f_res:
                            if f.GetLength() > min_len:
                                fill_layer.append(f)
                except: pass
            if fill_layer: layers.append(fill_layer)
            break
            
        layers.append(next_loops)
        current_loops = next_loops
        
    return layers, dist, plane

def organize_strict_tree(layers, step_dist):
    if not layers: return []
    
    completed_groups = []
    
    # Initialize all first-layer curves as independent root branches.
    active_branches = []
    for c in layers[0]:
        active_branches.append({
            'curves': [c],
            'last_curve': c
        })
        
    # Trace downward layer by layer.
    for i in range(1, len(layers)):
        current_layer = layers[i]
        
        matches = {idx: [] for idx in range(len(active_branches))}
        orphans = []
        
        for child in current_layer:
            best_branch_idx = -1
            min_geo_dist = float('inf')
            
            # Check sample points.
            sample_pts = [child.PointAtStart, child.PointAt(child.Domain.Mid)]
            
            for b_idx, branch in enumerate(active_branches):
                parent = branch['last_curve']
                this_dist = float('inf')
                for pt in sample_pts:
                    rc, t = parent.ClosestPoint(pt)
                    d = parent.PointAt(t).DistanceTo(pt)
                    if d < this_dist: this_dist = d
                
                if this_dist < min_geo_dist:
                    min_geo_dist = this_dist
                    best_branch_idx = b_idx
            
            # Topological distance threshold.
            if best_branch_idx != -1 and min_geo_dist < abs(step_dist) * 1.8:
                matches[best_branch_idx].append(child)
            else:
                orphans.append(child)
                
        next_gen_branches = []
        
        for b_idx, children in matches.items():
            parent_data = active_branches[b_idx]
            current_history = parent_data['curves']
            
            if len(children) == 0:
                completed_groups.append(current_history)
            elif len(children) == 1:
                current_history.append(children[0])
                next_gen_branches.append({
                    'curves': current_history,
                    'last_curve': children[0]
                })
            else:
                # At a split, archive the parent and start child branches.
                completed_groups.append(current_history)
                for child in children:
                    next_gen_branches.append({
                        'curves': [child],
                        'last_curve': child
                    })
        
        for orphan in orphans:
            next_gen_branches.append({
                'curves': [orphan],
                'last_curve': orphan
            })
            
        active_branches = next_gen_branches
        
    for branch in active_branches:
        completed_groups.append(branch['curves'])
        
    return completed_groups

def process_uturn(group_curves, boundary_curve, trim_width):
    if not group_curves: return None
    
    # Sort: Outer -> Inner.
    work_curves = sorted(group_curves, key=lambda c: get_area(c), reverse=True)
    if not work_curves: return None
    
    # Determine the cutting axis.
    center = get_centroid(work_curves[-1]) 
    bc = boundary_curve.ToNurbsCurve()
    rc, t = bc.ClosestPoint(center)
    outer_pt = bc.PointAt(t)
    
    vec = outer_pt - center
    if vec.Length < 0.001: vec = rg.Vector3d.XAxis
    vec.Unitize()
    ray = rg.LineCurve(center, outer_pt + vec * 100000)
    
    segments = []
    prev_cut_pt = None 
    
    for i, c in enumerate(work_curves):
        # Use a slightly larger intersection tolerance here to ensure detection.
        ccx = rg.Intersect.Intersection.CurveCurve(c, ray, 0.001, 0.001)
        t_param = 0.0
        
        if ccx.Count > 0:
            if ccx.Count == 1 or i == 0: 
                t_param = ccx[0].ParameterA
            else:
                # Choose the intersection nearest the previous cutting point to preserve seam continuity.
                if prev_cut_pt:
                    best_t = ccx[0].ParameterA
                    min_d = float('inf')
                    for event in ccx:
                        pt = c.PointAt(event.ParameterA)
                        d = pt.DistanceTo(prev_cut_pt)
                        if d < min_d:
                            min_d = d
                            best_t = event.ParameterA
                    t_param = best_t
                else:
                    t_param = ccx[0].ParameterA
        else:
            rc_p, t_p = c.ClosestPoint(center)
            t_param = t_p if rc_p else 0.0
            
        prev_cut_pt = c.PointAt(t_param)
        
        c.ChangeClosedCurveSeam(t_param)
        L = c.GetLength()
        
        cut_w = trim_width
        if cut_w > L * 0.35: cut_w = L * 0.35
            
        start_cut = cut_w / 2.0
        end_cut = cut_w / 2.0
        if i == 0: start_cut = 0.0
        
        rc1, t0 = c.LengthParameter(start_cut)
        rc2, t1 = c.LengthParameter(L - end_cut)
        
        if rc1 and rc2:
            if t0 >= t1: t0, t1 = 0.0, L * 0.999
            segments.append(c.Trim(t0, t1))
        else:
            segments.append(c)

    # Stitch.
    list_a = [s for k,s in enumerate(segments) if s and k%2==0]
    list_b = [s for k,s in enumerate(segments) if s and k%2!=0]
    
    if not list_a and not list_b: return work_curves[0]
    
    def stitch(segs):
        if not segs: return []
        p = []
        for k in range(len(segs)-1):
            p.append(segs[k])
            p.append(rg.LineCurve(segs[k].PointAtEnd, segs[k+1].PointAtStart))
        p.append(segs[-1])
        return p
        
    path_a = stitch(list_a)
    path_b = stitch(list_b)
    
    final = []
    final.extend(path_a)
    
    if path_b:
        if path_a:
            final.append(rg.LineCurve(path_a[-1].PointAtEnd, path_b[-1].PointAtEnd))
        rev_b = []
        for item in reversed(path_b):
            d = item.DuplicateCurve()
            d.Reverse()
            rev_b.append(d)
        final.extend(rev_b)
        
    joined = rg.Curve.JoinCurves(final)
    return joined[0] if joined else None

def _domain_bounds(value):
    value = _range_value(value)
    return None if value is None else _layer_range(value)


def _infill_record(source, curve, index, group_id, tolerance):
    points, parameters = curve_endpoints(curve, tolerance)
    field = prepare_sample_field(source)
    samples = [read_sample_field(field, p) for p in points]
    record = dict(source)
    for key in ("PointRange", "SeamHeight", "SeamDistance", "SeamPoint"):
        record.pop(key, None)
    record.update(Curve=curve, SourceCurve=source["Curve"], SourceSegmentId=source["SegmentId"],
                  SegmentId=source["SegmentId"]+":infill:"+str(index), Role="infill",
                  PathGroupId=group_id, PathGroupRole="infill", InfillIslandIndex=index,
                  Sampling="segment_endpoints", SampleCurve=curve, SampleParameters=tuple(parameters),
                  Points=tuple(points), Heights=tuple(h for h, _ in samples),
                  GrowthVectors=tuple(v for _, v in samples), HeightTransfer="nearest_source_parameter")
    return record


def _set_records(chunk, records):
    chunk.update(Segments=tuple(records), Curves=tuple(s["Curve"] for s in records))
    for key in ("Points", "Heights", "GrowthVectors"):
        chunk[key] = tuple(v for record in records for v in record.get(key, ()))


def build_spiral_infill(input_tree, distance=None, domain=None, doc_tolerance=None):
    bounds = _domain_bounds(domain)
    distance = float(distance) if distance else 1.0
    if not math.isfinite(distance):
        raise ValueError('Distance 必须是有限数。')
    packets = [(wire, i, _unwrap_path(v)) for (wire, items) in _tree_items(input_tree) for (i, v) in enumerate(items)]
    packets = _path_packets(packets)
    if not packets:
        raise ValueError('请连接完整 PATH。')
    _has_wall(packets)
    if any((data.Stage != 'sliced' for (_, _, data) in packets)):
        raise ValueError('填充须放在连续路径之前，请连接 sliced PATH。')
    if len({str(data.Metadata.get('Units')) for (_, _, data) in packets}) != 1:
        raise ValueError('输入PATH单位不一致。')
    target = None
    for (ci, chunk) in enumerate(packets[0][2].Chunks):
        records = chunk.get('Segments', ())
        if records and all((r.get('Role') in ('brim', 'infill') for r in records)):
            continue
        target = (0, ci)
        break
    targets = set() if target is None else {target}
    if target is not None:
        first = packets[0][2].Chunks[target[1]]
        origin = first.get('OrderingSourceGroupId')
        if origin is not None and first.get('Segments'):
            targets.update((0, i) for i, c in enumerate(packets[0][2].Chunks)
                           if c.get('OrderingSourceGroupId') == origin
                           and _path_object_key(c) == _path_object_key(first))
    candidates = [r for pi, ci in sorted(targets) for r in packets[pi][2].Chunks[ci]['Segments']]
    candidates = [s for s in candidates if s.get('LayerIndex') is not None and s.get('Role') == 'wall']
    minimum_z = min((s['Curve'].GetBoundingBox(False).Min.Z for s in candidates), default=float('inf'))
    if doc_tolerance is None:
        doc = Rhino.RhinoDoc.ActiveDoc
        doc_tolerance = doc.ModelAbsoluteTolerance if doc else packets[0][2].Metadata.get('Tolerance', 0.001)
    doc_tolerance = float(doc_tolerance)
    if not math.isfinite(doc_tolerance) or doc_tolerance <= 0:
        raise ValueError('模型公差无效。')
    (chunks, empty_paths, group_map, notices) = ([], [], [], [])
    selected_count = generated_count = 0
    for (pi, (wire, item, data)) in enumerate(packets):
        prefix = ()
        empty_paths.extend((prefix + tuple(p) for p in data.Metadata.get('EmptyPaths', ())))
        original_paths = [tuple(c['TreePath']) for c in data.Chunks] + [tuple(p) for p in data.Metadata.get('EmptyPaths', ())]
        namespace = max((p[0] for p in original_paths if p), default=-1) + 1
        for (ci, source_chunk) in enumerate(data.Chunks):
            base_path = prefix + tuple(source_chunk['TreePath'])
            records = []
            additions = []
            wall_id = 'packet:{}:chunk:{}:wall'.format(pi, ci)
            for (si, source) in enumerate(source_chunk['Segments']):
                record = dict(source)
                role = source.get('Role')
                if role not in ('wall', 'brim', 'infill'):
                    raise ValueError('缺少有效Role，请重新计算上游。')
                records.append(record)
                if (pi, ci) not in targets or role != 'wall' or source.get('LayerIndex') is None:
                    continue
                curve = source['Curve']
                selected = bounds[0] <= source['LayerIndex'] <= bounds[1] if bounds is not None else abs(curve.GetBoundingBox(False).Min.Z - minimum_z) < doc_tolerance
                if not selected:
                    continue
                selected_count += 1
                boundary = curve.DuplicateCurve()
                (layers, real_dist, plane) = generate_layers(boundary, distance)
                groups = organize_strict_tree(layers, abs(real_dist)) if layers else []
                count_before = generated_count
                for (gi, group) in enumerate(groups):
                    result = process_uturn(group, boundary, abs(real_dist) * 2.0)
                    if result is None:
                        continue
                    gid = 'packet:{}:chunk:{}:segment:{}:infill:{}'.format(pi, ci, si, gi)
                    infill = _infill_record(record, result, gi, gid, doc_tolerance)
                    new_chunk = dict(source_chunk)
                    new_chunk.update(ContinuousGroupId=gid, OrderingSourceGroupId=gid)
                    new_chunk.update(TreePath=prefix + (namespace, ci, si, gi), SourceTreePath=tuple(source_chunk['TreePath']), PathGroupId=gid, PathGroupRole='infill', Role='infill')
                    _set_records(new_chunk, [infill])
                    additions.append(new_chunk)
                    generated_count += 1
                if count_before == generated_count:
                    notices.append('{} 未生成填充，原路径已保留（沿用原算法结果）。'.format(source['SegmentId']))
            chunk = dict(source_chunk)
            chunk.update(TreePath=base_path)
            chunks.append(chunk)
            chunks.extend(additions)
    paths = [tuple(c['TreePath']) for c in chunks]
    if len(set(paths)) != len(paths):
        raise ValueError('输入分组路径重复，无法唯一标记输出。')
    for c in chunks:
        group_map.append(dict(GroupId=c.get('PathGroupId'), Roles=tuple(dict.fromkeys((r['Role'] for r in c['Segments']))), TreePath=c['TreePath']))
    meta = dict(packets[0][2].Metadata)
    meta.update(Inputs=tuple((dict(d.Metadata) for (_, _, d) in packets)), EmptyPaths=tuple(empty_paths), PathGroups=tuple(group_map), InfillDomain=bounds, InfillDistance=distance, InfillTarget=target, InfillSelection='first_model_chunk', InfillCount=generated_count, Tolerance=max((float(d.Metadata.get('Tolerance', 0.001)) for (_, _, d) in packets)))
    meta.update(GroupingComplete=all(d.Metadata.get('GroupingComplete') is True for _, _, d in packets), AuxiliaryOrderDirty=True)
    if selected_count == 0:
        notices.append('首包没有模型组，全部原路径已保留。' if target is None else '第一模型组没有选中wall层，全部原路径已保留。')
    return (_path_output('sliced', chunks, meta), notices)


def _run_component():
    input_path = globals().get("Path")
    from Grasshopper.Kernel.Types import GH_ObjectWrapper
    component = ghenv.Component
    globals()["Path"] = None
    globals()["Preview"] = DataTree[rg.Curve]()
    try:
        if not _ensure_ports(component):
            return
        component.Name, component.NickName = "螺旋填充", "SpiralInfill"
        component.Message = COMPONENT_MESSAGE
        data, notices = build_spiral_infill(input_path, globals().get("Distance"), globals().get("Domain"))
        globals()["Path"] = GH_ObjectWrapper(data)
        globals()["Preview"] = _preview_tree(data)
        for notice in notices:
            component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Warning, _newpath_zh_message(notice))
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))


if "ghenv" in globals():
    _run_component()


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = '螺旋填充'
    ghenv.Component.NickName = '螺旋填充'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'Path'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '在首个模型组的指定层添加螺旋填充，并保留全部原路径。'
    ghenv.Component.Tooltip = '在首个模型组的指定层添加螺旋填充，并保留全部原路径。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '完整sliced PATH', 'Distance': '偏移步距/ 默认1', 'Domain': '层范围：0起算包含两端，0到2为第一至第三层；单层号0、Panel文本0 to 2或Domain；留空最低层'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '全部墙和填充 PATH', 'Preview': '按角色分组的完整曲线预览'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
