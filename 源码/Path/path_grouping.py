"""路径分组

【功能】
按指定高度重新划分打印块，并可按块显示不同颜色。

【输入端】
Path（通用对象；数据树；必填）：已有sliced PATH。
ChunkHeight（数值；单项；可选）：分块高度，默认10。
Palette（数值；单项；可选）：整数0–10选择色板：0参考渐变，1–10等饱和等明度；留空关闭彩色预览。

【输出端】
Path（通用对象；单项）：重新分组的完整PATH。
Preview（曲线；数据树）：按打印分块显示曲线副本。
Colour（颜色；列表）：扁平颜色列表；每个非空打印块一个颜色，顺序对应Preview非空分支。

【详细用法与约束】
只将已有 sliced PATH 分组，不切片、不移动/重建曲线、不重新计算宽高。
Path / Tree / object / 必填；ChunkHeight / Item / number / 可选，默认10模型单位。
Palette / Item / number / 可选：0–10选择色板；留空关闭彩色预览并输出空Colour。
输出 Path / Item / object，Preview / Tree纯曲线，Colour / List纯颜色；每个非空打印块一个颜色。
每个打印块一种颜色，按块顺序在色带上等距取色；单块取中点，单色库统一着色。
Preview仍为原生曲线副本，下游业务连接Path。
层高自动读取每段LayerHeight；Width、Heights、GrowthVectors及未知段字段原样继承。
同一包内按GeoId、Role、PathGroupId隔离；跨旧Chunk恢复层序，层内维持输入遇见顺序。
原空层压缩、15倍层高搜索、12倍层高合并、底部增强、封顶与排序完整保留。
原代码唯一控制流适配是无曲线时从函数返回，替代原外层几何循环的continue。
ChunkHeight小于半层高可能使原循环停滞，入口拒绝，不自动调整参数。
仅接受sliced阶段；不将connection或已连续路径重新拆分。所有输出仍为sliced。
未知块字段保留于GroupingSourceChunks，来源完整记录；跨组不添加连接。

Role=brim按已有落地轮廓PathGroupId整组保留；组内圈序不重排，不按竖向分束拆圈。
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
    ghenv.Component.Description = '按指定高度重新划分打印块，并可按块显示不同颜色。'
    ghenv.Component.Tooltip = '按指定高度重新划分打印块，并可按块显示不同颜色。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '已有sliced PATH', 'ChunkHeight': '分块高度，默认10', 'Palette': '整数0–10选择色板：0参考渐变，1–10等饱和等明度；留空关闭彩色预览'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '重新分组的完整PATH', 'Preview': '按打印分块显示曲线副本', 'Colour': '扁平颜色列表；每个非空打印块一个颜色，顺序对应Preview非空分支'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description

import Grasshopper
import Rhino
import System


PORT_ALIASES = {'Paths': 'Path'}
import math
import Rhino.Geometry as rg
import Grasshopper as gh
from Grasshopper import DataTree
COMPONENT_MARKER = 'PathGrouping:r12'
COMPONENT_MESSAGE = "Path Grouping\nColour Preview"
PATH_PROTOCOL = "codex.ghpython.Path"
PATH_SCHEMA = 1
INPUT_SPECS = [
    ("Path", "Path", '已有sliced PATH', "tree", "object", False),
    ("ChunkHeight", "ChunkHeight", '分块高度，默认10', "item", "number", True),
    ("Palette", "Palette", '整数0–10选择色板：0参考渐变，1–10等饱和等明度；留空关闭彩色预览', "item", "number", True),
]
OUTPUT_SPECS = [
    ("Path", "Path", '重新分组的完整PATH', "item"),
    ("Preview", "Preview", '按打印分块显示曲线副本', "tree"),
    ("Colour", "Colour", '扁平颜色列表；每个非空打印块一个颜色，顺序对应Preview非空分支', "list"),
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

def _set_records(chunk, records):
    chunk.update(Segments=tuple(records), Curves=tuple(s["Curve"] for s in records))
    for key in ("Points", "Heights", "GrowthVectors"):
        chunk[key] = tuple(v for record in records for v in record.get(key, ()))
def sequence_original(temp_results, layer_height, chunk_height, last_nozzle_pos):
    d_merge = layer_height * 12.0
    merge_layer_limit = int((chunk_height * 2.0) / layer_height)
    step_limit = max(1, int(chunk_height / layer_height))
    all_final_chunks = []
    raw_layers = [res for res in temp_results if res and len(res) > 0]
    if not raw_layers: return [], last_nozzle_pos

    first_split, last_split = len(raw_layers), -1
    for i in range(len(raw_layers)):
        if len(raw_layers[i]) > 1:
            if i < first_split: first_split = i
            if i > last_split: last_split = i
    
    effective_last_split = last_split if last_split != -1 else len(raw_layers) - 1
    cap_start_idx = last_split + 1 if last_split != -1 else len(raw_layers)
    cap_curves = []
    if cap_start_idx < len(raw_layers):
        for i in range(cap_start_idx, len(raw_layers)): cap_curves.extend(raw_layers[i])

    strands = [] 
    for layer_idx in range(effective_last_split + 1):
        curves = raw_layers[layer_idx]
        used_indices = [False] * len(curves)
        
        # Try connecting existing strands.
        for s in strands:
            if s["last_layer"] == layer_idx - 1:
                best_idx, min_d = -1, layer_height * 15 # Search radius
                for i, crv in enumerate(curves):
                    if not used_indices[i]:
                        d = crv.GetBoundingBox(True).Center.DistanceTo(s["last_cp"])
                        if d < min_d: min_d, best_idx = d, i
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
        if not remaining: break
        
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
            if h_take <= 0: break

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

def build_grouped_path(input_tree, chunk_height=None):
    chunk_height = 10.0 if chunk_height is None else float(chunk_height)
    if not math.isfinite(chunk_height) or chunk_height <= 0:
        raise ValueError('ChunkHeight 必须为有限正数。')
    packets = [(wire, i, _unwrap_path(v)) for (wire, items) in _tree_items(input_tree) for (i, v) in enumerate(items)]
    packets = _path_packets(packets)
    if not packets:
        raise ValueError('请连接完整 PATH。')
    if any((p.Stage != 'sliced' for (_, _, p) in packets)):
        raise ValueError('路径分组必须放在连续路径之前。')
    if len({str(p.Metadata.get('Units')) for (_, _, p) in packets}) != 1:
        raise ValueError('输入 PATH 单位不一致。')
    (chunks, empty_paths, notices) = ([], [], [])
    nozzle = rg.Point3d(0, 0, 0)
    for (pi, (wire, item, data)) in enumerate(packets):
        groups = {}
        bottom_limits = {}
        for chunk in data.Chunks:
            for r in chunk.get('Segments', ()):
                if r.get('Role') in ('brim', 'infill'):
                    identity = r.get('SourceObjectKey', _path_object_key(chunk))
                    bottom_limits[identity] = max(bottom_limits.get(identity, -1), r['LayerIndex'])
        block_ids = {}
        for (ci, chunk) in enumerate(data.Chunks):
            records = tuple(chunk['Segments'])
            if len(records) != len(chunk['Curves']):
                raise ValueError('输入曲线与段记录数量不一致。')
            if not records:
                empty_paths.append((pi, 1, ci))
            for (si, record) in enumerate(records):
                li = record.get('LayerIndex')
                if isinstance(li, bool) or not isinstance(li, int) or li < 0:
                    raise ValueError('缺少有效真实LayerIndex，不按Z猜测层号。')
                role = record.get('Role')
                if role not in ('wall', 'brim', 'infill') or record.get('IsConnection'):
                    raise ValueError('缺少角色或包含连接段，不能执行层分组。')
                height = float(record['LayerHeight'])
                if not math.isfinite(height) or height <= 0:
                    raise ValueError('PATH名义层高无效。')
                if int(chunk_height * 2.0 / height) < 1:
                    raise ValueError('ChunkHeight不得小于半层高；原分组算法在此范围可能停滞。')
                curve = record['Curve']
                if curve is None or not curve.IsValid:
                    raise ValueError('PATH包含无效曲线。')
                identity = record.get('SourceObjectKey', _path_object_key(chunk))
                bottom_layer = li if li <= bottom_limits.get(identity, -1) else None
                key = (identity, role, record.get('PathGroupId', chunk.get('PathGroupId')), bottom_layer)
                groups.setdefault(key, []).append((li, ci, si, record, chunk))
        role_order = {'brim': 0, 'infill': 1, 'wall': 2}
        object_order = {_path_object_key(ch): ch['ObjectIndex'] for ch in data.Chunks}
        ordered_groups = sorted(groups.items(), key=lambda pair: (object_order[pair[0][0]], pair[0][3] is None, pair[0][3] if pair[0][3] is not None else 0, role_order[pair[0][1]]))
        for gi, (key, entries) in enumerate(ordered_groups):
            heights = {float(e[3]['LayerHeight']) for e in entries}
            if len(heights) != 1:
                raise ValueError('同一来源/角色组内名义层高不同；请在上游明确分组。')
            height = next(iter(heights))
            (layers, lookup) = ({}, {})
            for (li, ci, si, record, chunk) in entries:
                token = _CurveOccurrence(record['Curve'])
                layers.setdefault(li, []).append(token)
                lookup[id(token)] = (ci, si, record, chunk)
            layer_ids = sorted(layers)
            if any((b - a > 1 for (a, b) in zip(layer_ids, layer_ids[1:]))):
                notices.append('来源 {} 有空层；沿用原算法压缩空层分组，记录真实层号保留。'.format(key[0]))
            if key[1] in ('brim', 'infill') or key[3] is not None:
                if key[1] == 'brim' and (not key[2] or len(layer_ids) != 1):
                    raise ValueError('裙边必须有独立落地轮廓PathGroupId，且组内属于同一真实层。')
                ordered = [[token for layer in layer_ids for token in layers[layer]]]
                nozzle = ordered[0][-1].PointAtEnd
            else:
                (ordered, nozzle) = sequence_original([layers[i] for i in layer_ids], height, chunk_height, nozzle)
            for oi, curves in enumerate(ordered):
                per_object = block_ids.setdefault(key[0], {})
                block_key = ('bottom', key[3]) if key[3] is not None else ('wall', gi, oi)
                block = per_object.setdefault(block_key, len(per_object))
                continuous_group = 'packet:{}:group:{}:run:{}'.format(pi, gi, oi)
                (records, sources) = ([], {})
                for curve in curves:
                    (ci, si, original, parent) = lookup[id(curve)]
                    record = dict(original)
                    record.update(GroupingSourceChunkId=original.get('ChunkId'), GroupingSourceTreePath=tuple(parent['TreePath']), GroupingInput=(wire, item, ci, si), ChunkId=oi, PrintGroupId='{}:{}:{}'.format(pi, gi, oi))
                    records.append(record)
                    sources[ci] = dict(parent)
                chunk = dict(next(iter(sources.values())))
                chunk.update(TreePath=(pi, 0, gi, oi), ChunkId=oi, LayerHeight=height, GeoId=records[0]['GeoId'], ObjectIndex=object_order[key[0]], PrintBlockIndex=block, ContinuousGroupId=continuous_group, OrderingSourceGroupId=continuous_group, PrintGroupId='{}:{}:{}'.format(pi, gi, oi), GroupingSourceChunks=tuple(sources.values()), GroupingRole=key[1])
                widths = {r.get('Width') for r in records}
                chunk['Width'] = next(iter(widths)) if len(widths) == 1 else None
                _set_records(chunk, records)
                chunks.append(chunk)
        empty_paths.extend(((pi, 2) + tuple(p) for p in data.Metadata.get('EmptyPaths', ())))
    meta = dict(packets[0][2].Metadata)
    meta.update(Inputs=tuple((dict(p.Metadata) for (_, _, p) in packets)), EmptyPaths=tuple(empty_paths), GroupingAlgorithm='original_multi_mode_sequencing', ChunkHeight=chunk_height, GroupingComplete=True, PathGroups=tuple((dict(PrintGroupId=c['PrintGroupId'], TreePath=c['TreePath'], Role=c['GroupingRole'], PathGroupId=c.get('PathGroupId')) for c in chunks)))
    return (_path_output('sliced', chunks, meta), notices)


class _CurveOccurrence:
    def __init__(self, curve):
        self.curve = curve

    def GetBoundingBox(self, accurate):
        return self.curve.GetBoundingBox(accurate)

    @property
    def PointAtStart(self):
        return self.curve.PointAtStart

    @property
    def PointAtEnd(self):
        return self.curve.PointAtEnd


def _palette_colours(values):
    from System.Drawing import Color
    value = values
    for _ in range(8):
        if not hasattr(value, "Value"):
            break
        value = value.Value
    if value is None:
        return []
    try:
        number = float(value)
        valid = not isinstance(value, bool) and math.isfinite(number) and number.is_integer() and 0 <= number <= 10
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        raise ValueError("Palette请输入0–10的整数选择色板；留空关闭彩色预览。")
    if int(number) == 0:
        # User reference: orange-red, gold, pale yellow, light blue, deep blue.
        return [Color.FromArgb(*rgb) for rgb in
                ((235,65,0),(245,157,12),(249,244,105),(161,190,218),(100,136,188),(48,72,119))]
    # Hue sequences adapted from CARTOColors analytical palettes; not original RGBs.
    # All stops AND interpolated colours share HSL S=0.62, L=0.60.
    hues = (
        (275,240,205,175,145),  # 1 Purple-blue-cyan-green / BluGrn
        (195,220,245,270,295),  # 2 Cyan-blue-purple / Purp
        (5,25,45,65),          # 3 Coral-gold / OrYel
        (165,135,105,75,45),   # 4 Teal-gold / TealGrn
        (265,290,315,340,365), # 5 Magenta-coral / Sunset
        (55,30,5,-20,-45),    # 6 Gold-orange-rose-purple / SunsetDark
        (185,210,235,260),    # 7 Lake-blue-indigo / Teal
        (345,315,285,255),    # 8 Rose-iris / PuRd
        (20,55,90,125,160),   # 9 Orange-yellow-grass-green / Earth
        (150,185,220,255,290),# 10 Mint-blue-purple / Tropic
    )
    return [_hue_colour(h) for h in hues[int(number)-1]]


def _hue_colour(hue):
    import colorsys
    from System.Drawing import Color
    rgb = colorsys.hls_to_rgb((hue % 360) / 360.0, 0.60, 0.62)
    return Color.FromArgb(*(int(round(v * 255)) for v in rgb))


def _sample_palette(palette, index, count, hue_only=False):
    from System.Drawing import Color
    position = (0.5 if count <= 1 else float(index) / (count - 1)) * (len(palette) - 1)
    left = int(position)
    right = min(left + 1, len(palette) - 1)
    fraction = position - left
    if hue_only:
        import colorsys
        def hue(c):
            return colorsys.rgb_to_hls(c.R / 255.0, c.G / 255.0, c.B / 255.0)[0] * 360
        first, last = hue(palette[left]), hue(palette[right])
        delta = (last - first + 180) % 360 - 180
        return _hue_colour(first + delta * fraction)
    channels = [int(round(getattr(palette[left], c) * (1.0 - fraction)
                          + getattr(palette[right], c) * fraction)) for c in "ARGB"]
    return Color.FromArgb(*channels)


def _group_colours(data, preview, palette_values=None):
    palette = _palette_colours(palette_values)
    if not palette:
        return []
    colours = []
    for index in range(preview.BranchCount):
        if len(preview.Branch(preview.Path(index))):
            colours.append(index)
    return [_sample_palette(palette, index, len(colours), len(palette) <= 5) for index in range(len(colours))]


def _sdk_solve(instance, input_path, chunk_height, palette, environment):
    # Each solve owns its namespace and preview; no persistent display conduits.
    globals().update(Path=input_path, ChunkHeight=chunk_height, Palette=palette, ghenv=environment)
    _run_component()
    preview, colours = globals()["Preview"], globals()["Colour"]
    instance._group_preview = []
    box = rg.BoundingBox.Empty
    colour_index = 0
    for i in range(preview.BranchCount):
        curves = preview.Branch(i)
        if not len(curves) or not colours:
            continue
        colour = colours[colour_index]
        colour_index += 1
        for curve in curves:
            instance._group_preview.append((curve, colour))
            box.Union(curve.GetBoundingBox(True))
    instance._group_box = box
    # The SDK override owns display; avoid the generic output's monochrome overlay.
    for param in environment.Component.Params.Output:
        if param.Name == "Preview":
            param.Hidden = True
    # SDK output marshalling requires a CLR collection, not a wrapped Python list.
    from System import Array
    from System.Drawing import Color
    return globals()["Path"], preview, Array[Color](colours)


def _sdk_draw(instance, args):
    for curve, colour in getattr(instance, "_group_preview", ()):
        args.Display.DrawCurve(curve, colour, 2)


def _run_component():
    input_path = globals().get("Path")
    from Grasshopper.Kernel.Types import GH_ObjectWrapper
    component = ghenv.Component
    globals()["Path"] = None
    globals()["Preview"] = DataTree[rg.Curve]()
    globals()["Colour"] = []
    try:
        if not _ensure_ports(component):
            return
        component.Name, component.NickName = "路径分组", "PathGrouping"
        component.Message = COMPONENT_MESSAGE
        data, notices = build_grouped_path(input_path, globals().get("ChunkHeight"))
        preview = _preview_tree(data)
        colours = _group_colours(data, preview, globals().get("Palette"))
        globals()["Path"] = GH_ObjectWrapper(data)
        globals()["Preview"] = preview
        globals()["Colour"] = colours
        for notice in notices:
            component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Warning, _newpath_zh_message(notice))
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))


class Script_Instance(Grasshopper.Kernel.GH_ScriptInstance):
    def BeforeRunScript(self):
        self._group_preview = []
        self._group_box = rg.BoundingBox.Empty

    def RunScript(self, Path, ChunkHeight, Palette):
        Path, Preview, Colour = _sdk_solve(self, Path, ChunkHeight, Palette, ghenv)
        ghenv.Component.Name = ghenv.Component.NickName = '路径分组'
        return Path, Preview, Colour

    def get_ClippingBox(self):
        return getattr(self, '_group_box', rg.BoundingBox.Empty)

    def DrawViewportWires(self, args):
        _sdk_draw(self, args)

    def DrawViewportMeshes(self, args):
        pass


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = '路径分组'
    ghenv.Component.NickName = '路径分组'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'Path'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '按指定高度重新划分打印块，并可按块显示不同颜色。'
    ghenv.Component.Tooltip = '按指定高度重新划分打印块，并可按块显示不同颜色。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '已有sliced PATH', 'ChunkHeight': '分块高度，默认10', 'Palette': '整数0–10选择色板：0参考渐变，1–10等饱和等明度；留空关闭彩色预览'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '重新分组的完整PATH', 'Preview': '按打印分块显示曲线副本', 'Colour': '扁平颜色列表；每个非空打印块一个颜色，顺序对应Preview非空分支'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
