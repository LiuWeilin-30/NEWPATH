"""生成skirt

【功能】
按模型俯视凸包生成整体包围裙边，并保留原模型路径。

【输入端】
Path（通用对象；数据树；必填）：完整sliced Path。
FirstOffset（数值；单项；可选）：首圈中心线距离；默认Width；模型单位。
GeneralOffset（数值；单项；可选）：圈间中心线距离；默认Width；模型单位。
OffsetCount（数值；单项；可选）：整数圈数0～1000；默认3；0关闭。

【输出端】
Path（通用对象；单项）：辅助环在前、原模型在后的完整PATH。
Preview（曲线；数据树）：完整路径曲线副本。

【详细用法与约束】
输入Path / Tree / object：完整sliced Path；保留全部模型路径。
FirstOffset / Item / number：首圈距离，默认Width。
GeneralOffset / Item / number：普通圈距，默认Width。
OffsetCount / Item / number：整数0～1000，默认3，0关闭。
输出Path / Item / object及Preview / Tree；失败清空输出。
所有wall曲线的Top投影共同取凸包，水平最低真实首层生成外到内的闭合包围环。
接受单/多物体、开放/闭合曲线，凹口跨过；直线退化投影采用方形端部包围。
凸角为尖角，距离相对离散凸包；曲线按模型公差近似，不代表实体净距验收。
宽度与首层高度继承落地wall；共同落地轮廓宽高必须一致，拒绝斜首层和UV高度。
Role=brim，AuxStrategy=global_top_convex_hull；单来源继承模型身份，多来源保留共享辅助身份。
SkirtSources追溯全部来源；模型原连续组不拆层；开放连续路径只衔接wall。
不支持将路径数据内部化保存。
"""

# Localized presentation only; keep required inputs, type hints and solver behavior.
def _newpath_zh_message(message):
    text = str(message)
    if any('\u4e00' <= char <= '\u9fff' for char in text):
        return text
    return '运行提示，请检查相关输入。原始信息：' + text


# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '按模型俯视凸包生成整体包围裙边，并保留原模型路径。'
    ghenv.Component.Tooltip = '按模型俯视凸包生成整体包围裙边，并保留原模型路径。'
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
COMPONENT_MARKER = 'SkirtPath:r2'
COMPONENT_MESSAGE = '生成skirt\nTop整体包围'
PATH_PROTOCOL = 'codex.ghpython.Path'
PATH_SCHEMA = 1
INPUT_SPECS = [('Path', 'Path', '完整sliced Path', 'tree', 'object', False), ('FirstOffset', 'FirstOffset', '首圈中心线距离；默认Width；模型单位', 'item', 'number', True), ('GeneralOffset', 'GeneralOffset', '圈间中心线距离；默认Width；模型单位', 'item', 'number', True), ('OffsetCount', 'OffsetCount', '整数圈数0～1000；默认3；0关闭', 'item', 'number', True)]
OUTPUT_SPECS = [
    ('Path','Path','辅助环在前、原模型在后的完整PATH','item'),
    ('Preview','Preview','完整路径曲线副本','tree'),
]



def _convex_hull_xy(points):
    values = sorted(set((float(p.X), float(p.Y)) for p in points))
    if not values or any(not math.isfinite(x) for p in values for x in p):
        raise ValueError('Top投影为空或含无效坐标。')
    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
    if len(values) <= 2:
        return values
    lower, upper = [], []
    for target, sequence in ((lower, values), (upper, reversed(values))):
        for p in sequence:
            while len(target) >= 2 and cross(target[-2], target[-1], p) <= 0:
                target.pop()
            target.append(p)
    return lower[:-1] + upper[:-1]


def _offset_hull(hull, distance, z):
    # CCW convex support lines, intersected analytically; square ends for a line.
    if len(hull) == 1:
        x, y = hull[0]
        result = [(x-distance,y-distance),(x+distance,y-distance),
                  (x+distance,y+distance),(x-distance,y+distance)]
    elif len(hull) == 2:
        a, b = hull
        dx, dy = b[0]-a[0], b[1]-a[1]
        length = math.hypot(dx, dy)
        ux, uy = dx/length, dy/length
        result = [(a[0]-distance*ux+distance*uy,a[1]-distance*uy-distance*ux),
                  (b[0]+distance*ux+distance*uy,b[1]+distance*uy-distance*ux),
                  (b[0]+distance*ux-distance*uy,b[1]+distance*uy+distance*ux),
                  (a[0]-distance*ux-distance*uy,a[1]-distance*uy+distance*ux)]
    else:
        result = []
        for i, p in enumerate(hull):
            a, b = hull[i-1], hull[(i+1)%len(hull)]
            ux, uy = p[0]-a[0], p[1]-a[1]
            vx, vy = b[0]-p[0], b[1]-p[1]
            lu, lv = math.hypot(ux,uy), math.hypot(vx,vy)
            n1, n2 = (uy/lu,-ux/lu), (vy/lv,-vx/lv)
            den = n1[0]*n2[1]-n1[1]*n2[0]
            if abs(den) < 1e-15:
                raise ValueError('凸包转角退化；未缩小输入距离。')
            result.append((p[0]+distance*(n2[1]-n1[1])/den,
                           p[1]+distance*(n1[0]-n2[0])/den))
    points = [rg.Point3d(x,y,z) for x,y in result]
    points.append(rg.Point3d(points[0]))
    curve = rg.PolylineCurve(points)
    if not curve.IsValid or not curve.IsClosed:
        raise ValueError('包围skirt生成失败。')
    return curve


def build_skirt_path(input_tree, first_offset=None, general_offset=None, offset_count=None, doc_tolerance=None):
    count = _number(offset_count, 3, 'OffsetCount')
    if count != int(count) or not 0 <= count <= 1000:
        raise ValueError('OffsetCount必须为0到1000的整数。')
    count = int(count)
    for value, name in ((first_offset,'FirstOffset'),(general_offset,'GeneralOffset')):
        if _value(value) is not None:
            _number(value, None, name, True)
    packets = _path_packets([(wire,item,_unwrap_path(value))
        for wire,values in _tree_items(input_tree) for item,value in enumerate(values)])
    if not packets or any(p.Stage != 'sliced' for _,_,p in packets):
        raise ValueError('请连接sliced阶段的完整Path。')
    _has_wall(packets)
    units = {str(p.Metadata.get('Units')) for _,_,p in packets}
    if len(units) != 1 or next(iter(units)) in ('None',''):
        raise ValueError('输入Path单位缺失或不一致。')
    if doc_tolerance is None:
        doc = Rhino.RhinoDoc.ActiveDoc
        doc_tolerance = doc.ModelAbsoluteTolerance if doc else max(
            _number(p.Metadata.get('Tolerance'),None,'Tolerance',True) for _,_,p in packets)
    tolerance = _number(doc_tolerance,None,'Tolerance',True)
    originals = [dict(c) for _,_,p in packets for c in p.Chunks]
    candidates = []
    for c in originals:
        for r in c.get('Segments',()):
            if count and r.get('AuxStrategy') == 'global_top_convex_hull':
                raise ValueError('输入已含包围skirt，请从生成前的Path重算。')
            if r['Role'] == 'wall':
                curve = r.get('Curve')
                if curve is None or not curve.IsValid or curve.GetLength() <= 0:
                    raise ValueError('wall包含无效或零长度曲线。')
                candidates.append((r,c,curve.GetBoundingBox(True).Min.Z))
    meta = dict(packets[0][2].Metadata) if len(packets) == 1 else {}
    meta.update(Inputs=tuple(dict(p.Metadata) for _,_,p in packets), Units=next(iter(units)),
                Tolerance=tolerance, EmptyPaths=tuple(e for _,_,p in packets for e in p.Metadata.get('EmptyPaths',())),
                PreservePathGroups=True, AuxiliaryOrderDirty=False,
                GroupingComplete=all(p.Metadata.get('GroupingComplete') is True for _,_,p in packets))
    if not count or not candidates:
        return _path_output('sliced',originals,meta), ([] if candidates else ['没有wall，保留输入Path。'])
    z = min(entry[2] for entry in candidates)
    first_layers = [entry for entry in candidates if abs(entry[2]-z) <= tolerance]
    source, source_chunk, _ = first_layers[0]
    source = dict(source)
    for name in ('Width','LayerHeight'):
        source[name] = _number(source.get(name,source_chunk.get(name)),None,name,True)
    for r,c,_ in first_layers:
        if r.get('IsFirstLayer') is not True or r.get('HeightMetric') != 'current_layer_normal':
            raise ValueError('skirt需要水平真实首层及current_layer_normal高度。')
        box = r['Curve'].GetBoundingBox(True)
        if abs(box.Max.Z-z) > tolerance:
            raise ValueError('skirt落地层必须水平。')
        prepare_sample_field(r)
        for name in ('Width','LayerHeight'):
            value = _number(r.get(name,c.get(name)),None,name,True)
            if abs(value-source[name]) > tolerance:
                raise ValueError('共同skirt的落地轮廓必须具有相同'+name+'。')
        if any(abs(h-source['LayerHeight']) > tolerance for h in r['Heights']):
            raise ValueError('首层逐点高度不一致。')
        if any(abs(v.X)>1e-6 or abs(v.Y)>1e-6 or abs(v.Z-1)>1e-6 for v in r['GrowthVectors']):
            raise ValueError('首层须为世界Z向上。')
    first, step = _ring_parameters(source,first_offset,general_offset,tolerance)
    samples = []
    for r,c,_ in candidates:
        poly = r['Curve'].ToPolyline(tolerance,0.1,0.0,0.0)
        if poly is None:
            raise ValueError('Top投影轮廓离散失败。')
        pts,_ = curve_endpoints(poly,tolerance)
        samples.extend(pts)
        if len(samples) > 200000:
            raise ValueError('Top投影采样超过200000点。')
    hull = _convex_hull_xy(samples)
    gid = 'skirt:global-top'
    source_keys = []
    for record, source_chunk, _ in candidates:
        key = record.get('SourceObjectKey', source_chunk.get('SourceObjectKey',
            (tuple(record['SourcePath']), record['GeoId'])))
        if key not in source_keys:
            source_keys.append(key)
    # A skirt around one source belongs to that model, while retaining its
    # independent functional group. A shared envelope keeps auxiliary identity.
    single_source = len(source_keys) == 1
    skirt_geo = source['GeoId'] if single_source else gid
    skirt_path = source['SourcePath'] if single_source else ()
    skirt_key = source_keys[0] if single_source else ('skirt-envelope',)
    provenance = tuple(dict(GeoId=r.get('GeoId'), SourcePath=r.get('SourcePath'),
        SourceObjectKey=r.get('SourceObjectKey',c.get('SourceObjectKey')), SegmentId=r['SegmentId'])
        for r,c,_ in candidates)
    records = []
    for index in range(count-1,-1,-1):
        distance = first+index*step
        curve = _offset_hull(hull,distance,z)
        points,parameters = curve_endpoints(curve,tolerance)
        record = dict(Curve=curve,SampleCurve=curve,Points=tuple(points),SampleParameters=tuple(parameters),
            Heights=tuple(source['LayerHeight'] for p in points),
            GrowthVectors=tuple(rg.Vector3d.ZAxis for p in points),
            Role='brim',PathGroupRole='brim',PathGroupId=gid,SegmentId=gid+':ring:'+str(index),
            GeoId=skirt_geo,SourcePath=skirt_path,SourceObjectKey=skirt_key,SkirtSources=provenance,
            ChunkId=0,LayerIndex=source['LayerIndex'],IsFirstLayer=True,
            Width=source['Width'],LayerHeight=source['LayerHeight'],HeightMetric='current_layer_normal',
            Plane=rg.Plane(rg.Point3d(0,0,z),rg.Vector3d.ZAxis),LayerNormal=rg.Vector3d.ZAxis,
            Sampling='segment_endpoints',AuxStrategy='global_top_convex_hull',
            AuxRingIndex=index,AuxOffset=distance,ApproximationTolerance=tolerance)
        records.append(record)
    chunk = dict(ObjectIndex=-1,PrintBlockIndex=0,TreePath=(0,0,0),ChunkId=0,GeoId=skirt_geo,
        SourceObjectKey=skirt_key,ContinuousGroupId=gid,PathGroupId=gid,PathGroupRole='brim',
        Width=source['Width'],LayerHeight=source['LayerHeight'],AuxStrategy='global_top_convex_hull')
    _set_records(chunk,records)
    meta.update(SkirtRingCount=count,SkirtEnvelope='all_wall_top_projection_convex_hull',
                SkirtApproximationTolerance=tolerance)
    result = _path_output('sliced',[chunk]+originals,meta)
    _validate_path_contract(result)
    return result, ['Top凸包按模型公差离散；距离相对离散凸包，曲线近似误差为模型公差。凸角为尖角，直线端部为方形包围。']

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























def _ring_parameters(record, first, step, tolerance):
    width = record['Width']
    first = _number(first, width, 'FirstOffset', True)
    step = _number(step, width, 'GeneralOffset', True)
    if min(first, step) <= 2 * tolerance:
        raise ValueError('偏移距离须大于两倍模型公差，避免重复/退化路径。')
    return first, step














def _run_component():
    input_path = globals().get('Path')
    from Grasshopper.Kernel.Types import GH_ObjectWrapper
    component = ghenv.Component
    globals()['Path'] = None
    globals()['Preview'] = DataTree[rg.Curve]()
    try:
        if not _ensure_ports(component):
            return
        component.Name, component.NickName = '生成skirt', '生成skirt'
        component.Message = COMPONENT_MESSAGE
        if input_path is None or (hasattr(input_path, 'BranchCount') and input_path.BranchCount == 0):
            component.Message = '生成skirt\n等待 Path'
            return
        result, notices = build_skirt_path(input_path, globals().get('FirstOffset'),
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
    ghenv.Component.Name = '生成skirt'
    ghenv.Component.NickName = '生成skirt'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'Path'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '按模型俯视凸包生成整体包围裙边，并保留原模型路径。'
    ghenv.Component.Tooltip = '按模型俯视凸包生成整体包围裙边，并保留原模型路径。'
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
