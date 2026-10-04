"""开放连续路径

【功能】
将同组逐层开放曲线交替反向连接为往返打印路径。

【输入端】
Path（通用对象；数据树；必填）：逐层开放曲线的 sliced Path。
Run（布尔值；单项；可选）：按钮触发；关闭时保留缓存。

【输出端】
Path（通用对象；单项）：连续 Path，携带曲线、逐点属性、来源与MotionKind动作。
Preview（曲线；数据树）：Path 内连续曲线副本。

【详细用法与约束】
用途：wall薄片逐层往返打印；整体skirt移缝到wall起点最近处，并以挤出连接融合到首个wall块。
skirt各圈完整保留，外到内连接；普通brim/infill独立保留。wall不跨分支连接。
输入：Path / Tree / object / 必填，sliced阶段完整PATH；Run / Item / bool / 可选=False。
输出：Path / Item / object，continuous完整数据包；Preview / Tree，真实连续曲线。
按真实LayerIndex升序，每层一条开放曲线；第一条正向，第二条反向，交替循环。
曲线原方向须由上游保持一致；不按最近端点自动翻转，不移缝、不裁短。
拒绝闭合曲线、缺层、同层多曲线、跨来源或跨功能组连接。
逐层端头用直线挤出连接；完全重合端点不另建连接。公差继承输入包。
反向曲线重新计算采样参数并从原采样场取得高度和生长向量；不反转生长方向。
保留真实曲线，不用稀疏端点折线替代；失效圆弧记录移除，下游可重新识别。
每组第一层重新从正向开始；上游分组会限定本电池连续连接的范围。
Run=False保留缓存，失败保留上次成功结果并报错；重开需重算，禁止Internalise。
"""

# Localized presentation only; keep required inputs, type hints and solver behavior.
def _newpath_zh_message(message):
    text = str(message)
    if any('\u4e00' <= char <= '\u9fff' for char in text):
        return text
    return '运行提示，请检查相关输入。原始信息：' + text


# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '将同组逐层开放曲线交替反向连接为往返打印路径。'
    ghenv.Component.Tooltip = '将同组逐层开放曲线交替反向连接为往返打印路径。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '逐层开放曲线的 sliced Path', 'Run': '按钮触发；关闭时保留缓存'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '连续 Path，携带曲线、逐点属性、来源与MotionKind动作', 'Preview': 'Path 内连续曲线副本'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description



PORT_ALIASES = {'Paths': 'Path'}

import math

import bisect

import Rhino.Geometry as rg

from Grasshopper import DataTree

COMPONENT_MARKER = 'OpenContinuousPath:r3'

COMPONENT_MESSAGE = 'Open Path\nAlternating layers'

INPUT_SPECS = [
    ("Path", "Path", '逐层开放曲线的 sliced Path', "tree", "object", False),
    ("Run", "Run", '按钮触发；关闭时保留缓存', "item", "bool", True),
]

OUTPUT_SPECS = [
    ("Path", "Path", '连续 Path，携带曲线、逐点属性、来源与MotionKind动作', "item"),
    ("Preview", "Preview", 'Path 内连续曲线副本', "tree"),
]

PATH_PROTOCOL = "codex.ghpython.Path"

PATH_SCHEMA = 1


def _open_wall_and_aux_runs(inputs):
    # Preserve original order. An auxiliary between walls is a run boundary.
    for source in inputs:
        runs, current = [], []
        for record, curve in zip(source['Segments'],source['Curves']):
            if record['Role'] != 'wall':
                if current:
                    runs.append(current)
                    current = []
                runs.append([(record,curve)])
            else:
                current.append((record,curve))
        if current or not runs:
            runs.append(current)
        for i,run in enumerate(runs):
            chunk = dict(source)
            chunk.update(Segments=tuple(r for r,c in run),Curves=tuple(c for r,c in run))
            if len(runs)>1:
                chunk['ContinuousGroupId'] = source['ContinuousGroupId']+':run:'+str(i)
            yield chunk


def _open_auxiliary_run(source):
    original = source['Segments'][0]
    if original.get('IsConnection'):
        raise ValueError('输入包含已规划连接，请连接原始sliced Path。')
    curve = source['Curves'][0].DuplicateCurve()
    if curve.GetLength() <= 0:
        raise ValueError('辅助路径必须非零长度。')
    prepare_sample_field(original)
    record = dict(original,Curve=curve,MotionKind='extrude',
                  PointRange=(0,len(original['Points'])))
    chunk = dict(source)
    for field in ('ArcSegments','SampleCurve','SampleParameters','PointRange'):
        chunk.pop(field,None)
    chunk.update(Curves=(curve,),Segments=(record,),Points=tuple(record['Points']),
        Heights=tuple(record['Heights']),GrowthVectors=tuple(record['GrowthVectors']),
        PathMotionPolicy='extrusion_only',ContinuousSourceTreePath=tuple(source['TreePath']),
        ContinuousRunIndex=0)
    return chunk


def _fuse_skirt_wall(chunks):
    """Attach each packet's global skirt to its first printed wall, without reordering walls."""
    skirts, targets = {}, {}
    for i, chunk in enumerate(chunks):
        packet = (tuple(chunk.get('InputPath', ())), chunk.get('InputItem', 0))
        records = tuple(chunk.get('Segments', ()))
        if records and all(r.get('AuxStrategy') == 'global_top_convex_hull'
                           and r.get('Role') == 'brim' for r in records):
            skirts.setdefault(packet, []).append((i, chunk))
        elif any(r.get('Role') == 'wall' and not r.get('IsConnection') for r in records):
            targets.setdefault(packet, i)
    removed, replacements = set(), {}
    for packet, entries in skirts.items():
        if packet not in targets:
            raise ValueError('包围skirt缺少同一输入Path内可融合的wall。')
        target_index = targets[packet]
        target = chunks[target_index]
        wall = dict(next(r for r in target['Segments'] if not r.get('IsConnection')))
        if 'Width' not in wall:
            wall['Width'] = target.get('Width')
        anchor = wall['Curve'].PointAtStart
        object_key = _path_object_key(target)
        rings = []
        for i, chunk in entries:
            for original in chunk['Segments']:
                curve = original['Curve'].DuplicateCurve()
                if not curve.IsClosed:
                    raise ValueError('融合skirt必须为闭合圈。')
                ok, t = curve.ClosestPoint(anchor)
                if not ok or not curve.ChangeClosedCurveSeam(t):
                    raise ValueError('skirt最近点移缝失败。')
                # The skirt producer emits exact polylines. Never approximate an edited curve here.
                ok, polyline = curve.TryGetPolyline()
                if not ok:
                    raise ValueError('融合skirt需要生成skirt输出的折线圈，请重算上游。')
                pts, params = curve_endpoints(curve)
                field = prepare_sample_field(original)
                samples = [read_sample_field(field, p) for p in pts]
                record = dict(original)
                for key in ('ArcSegments', 'PointRange', 'SeamHeight', 'SeamDistance'):
                    record.pop(key, None)
                record.update(Curve=curve, SampleCurve=curve, SampleParameters=tuple(params),
                    Points=tuple(pts), Heights=tuple(h for h,v in samples),
                    GrowthVectors=tuple(v for h,v in samples),
                    SourceSegmentId=original['SegmentId'],
                    SegmentId=wall['SegmentId']+':skirt:'+str(len(rings)),
                    OriginalSourceObjectKey=original.get('SourceObjectKey', _path_object_key(chunk)),
                    SourceObjectKey=object_key, OriginalChunkId=original.get('ChunkId'),
                    ChunkId=target.get('ChunkId', wall['ChunkId']),
                    SeamPoint=curve.PointAtStart, SkirtWallStart=anchor,
                    SeamRule='closest_to_wall_start', MotionKind='extrude')
                rings.append(record)
            removed.add(i)
        rings.sort(key=lambda r: -r['AuxRingIndex'])
        points, heights, vectors, records = [], [], [], []
        previous = None
        for record in rings:
            start = len(points)
            if previous is not None and points[-1].DistanceTo(record['Points'][0]) > 0:
                records.append(_skirt_connection(previous, record, points[-1], record['Points'][0],
                                                  start, 'skirt_ring_to_ring'))
            record['PointRange'] = (start, start+len(record['Points']))
            records.append(record)
            points.extend(record['Points'])
            heights.extend(record['Heights'])
            vectors.extend(record['GrowthVectors'])
            previous = record
        start = len(points)
        if points[-1].DistanceTo(anchor) > 0:
            records.append(_skirt_connection(previous, wall, points[-1], anchor, start, 'skirt_to_wall'))
        prefix_points = list(points)
        if prefix_points[-1].DistanceTo(anchor) > 0:
            prefix_points.append(anchor)
        # A closed PolyCurve cannot accept another segment. Consolidate only the exact
        # polygonal skirt + bridges into an open prefix; keep the wall's true geometry.
        clean = [prefix_points[0]]
        for point in prefix_points[1:]:
            if clean[-1].DistanceTo(point) > 0:
                clean.append(point)
        prefix = rg.PolylineCurve(clean)
        if prefix.IsClosed:
            raise ValueError('skirt首接缝与wall起点重合，无法建立开放前缀；请增加skirt起始距离。')
        combined = _assemble_run_curve([dict(Curve=prefix), dict(Curve=target['Curves'][0])])
        for original in target['Segments']:
            record = dict(original)
            a, b = record['PointRange']
            record['PointRange'] = (a+start, b+start)
            records.append(record)
        points.extend(target['Points'])
        heights.extend(target['Heights'])
        vectors.extend(target['GrowthVectors'])
        chunk = dict(target)
        for key in ('Role','PathGroupRole','PathGroupId','GroupingRole','ArcSegments',
                    'SampleCurve','SampleParameters','PointRange'):
            chunk.pop(key, None)
        widths = {r.get('Width',target.get('Width')) for r in records}
        chunk.update(GeoId=wall['GeoId'], Curves=(combined,), Segments=tuple(records), Points=tuple(points),
            Heights=tuple(heights), GrowthVectors=tuple(vectors),
            Width=next(iter(widths)) if len(widths)==1 else None,
            SkirtFused=True, SkirtWallStart=anchor, SkirtRingCount=len(rings),
            SkirtSourceTreePaths=tuple(tuple(c['TreePath']) for _,c in entries),
            SeamRule='skirt:closest_to_wall_start;wall:open:0')
        replacements[target_index] = chunk
    return [replacements.get(i,c) for i,c in enumerate(chunks) if i not in removed]


def _skirt_connection(previous, target, a, b, start, reason):
    width = target.get('Width')
    if width is None or not math.isfinite(width) or width <= 0:
        raise ValueError('skirt挤出连接需要目标段有效Width。')
    return dict(Curve=rg.LineCurve(a,b), IsConnection=True, MotionKind='extrude',
        MotionReason=reason, Role=target['Role'],
        SegmentId=target['SegmentId']+':skirt-connection',
        FromSegmentId=previous['SegmentId'], ToSegmentId=target['SegmentId'],
        FromLayerIndex=previous['LayerIndex'], ToLayerIndex=target['LayerIndex'],
        GeoId=target['GeoId'], SourcePath=target['SourcePath'],
        SourceObjectKey=target.get('SourceObjectKey'), ChunkId=target['ChunkId'],
        PathGroupId=target.get('PathGroupId'), LayerIndex=None,
        LayerHeight=target['LayerHeight'], HeightMetric=target['HeightMetric'],
        IsFirstLayer=target['IsFirstLayer'], Width=width, PointRange=(start-1,start+1))

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

def _prepare_inputs(input_tree):
    if input_tree is None or not hasattr(input_tree, "BranchCount") or input_tree.BranchCount == 0:
        raise ValueError("未检测到输入 Path，请连接 Smart Slicer 的 Path 输出。")
    packets = []
    for wire_path, items in _tree_items(input_tree):
        for item_index, item in enumerate(items):
            data = _unwrap_path(item)
            if data.Stage != "sliced":
                raise ValueError("Continuous Path 需要 sliced 阶段的 Path，不能重复处理已连续化结果。")
            packets.append((wire_path, item_index, data))
    packets = _path_packets(packets)
    if not packets:
        raise ValueError("输入树中没有 Path 数据项。")
    _has_wall(packets)  # Validate all explicit purposes; any role may be continuous.
    curves = DataTree[rg.Curve]()
    chunks, heights, metadata = [], {}, []
    multiple = len(packets) > 1
    empty_paths = []
    units = {str(p.Metadata.get("Units", "None")) for _, _, p in packets}
    if len(units) > 1:
        raise ValueError("输入 Path 的模型单位不同，不能直接合并；请在上游统一单位。")
    for wire_path, item_index, data in packets:
        prefix = ()
        metadata.append(dict(data.Metadata))
        for empty in data.Metadata.get("EmptyPaths", ()):
            empty_paths.append(prefix+tuple(empty))
        for source in data.Chunks:
            chunk = dict(source)
            path_key = prefix+tuple(source["TreePath"])
            if path_key in heights:
                raise ValueError("Path 内出现重复分块树路径。")
            base_height = float(source["LayerHeight"])
            if not math.isfinite(base_height) or base_height <= 0:
                raise ValueError("Path 内名义层高无效。")
            records = tuple(source["Segments"])
            source_curves = tuple(source["Curves"])
            if len(records) != len(source_curves):
                raise ValueError("Path 曲线与段信息数量不一致。")
            for record, curve in zip(records, source_curves):
                if not isinstance(curve, rg.Curve) or not curve.IsValid:
                    raise ValueError("Path 含无效曲线。")
                if abs(float(record["LayerHeight"])-base_height) > 1e-12*max(1, base_height):
                    raise ValueError("同一分块内名义层高不一致；请在上游拆分分块。")
            chunk["TreePath"] = path_key
            chunk["InputPath"] = wire_path
            chunk["InputItem"] = item_index
            chunks.append(chunk)
            heights[path_key] = base_height
            path = _gh_path(path_key)
            curves.EnsurePath(path)
            for curve in source_curves:
                curves.Add(curve, path)
    return curves, chunks, heights, metadata, tuple(empty_paths)

def _assemble_run_curve(records):
    """Keep exact ordered geometry; sparse attribute samples are not a polyline."""
    parts = [record['Curve'] for record in records]
    if len(parts) == 1:
        return parts[0].DuplicateCurve()
    curve = rg.PolyCurve()
    for part in parts:
        if not curve.AppendSegment(part.DuplicateCurve()):
            raise ValueError('连续曲线组合失败，未使用端点折线替代。')
    if not curve.IsValid:
        raise ValueError('连续曲线无效，未使用端点折线替代。')
    return curve

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
            if getattr(param,"VariableName",param.NickName) in ("GuidePoints","Path") and hasattr(param,"Hidden"):
                param.Hidden=True
        component.Name, component.NickName = "开放连续路径", "开放连续路径"
        def compute():
            return build_open_continuous_path(input_path)
        cache, error = _cache_result(globals().get("_open_continuous_path_cache"), bool(globals().get("Run")), compute)
        globals()["_open_continuous_path_cache"] = cache
        if cache is not None:
            data = cache["Data"]
            globals()["Path"] = GH_ObjectWrapper(data)
            globals()["Preview"] = _preview_tree(data)
            for notice in cache["Notices"]:
                component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Warning, _newpath_zh_message(notice))
            component.Message = "Open Continuous Path\nCached | {} curves".format(data.CurveCount)
        else:
            component.Message = "Open Continuous Path\nClick Run"
        if error:
            suffix = "；当前输出为上次成功结果。" if cache is not None else "；当前无有效输出。"
            component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(error+suffix))
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))

def build_open_continuous_path(input_tree):
    _, inputs, _, metadata, empty_paths = _prepare_inputs(input_tree)
    chunks = []
    for source in _open_wall_and_aux_runs(inputs):
        paired = list(zip(source['Segments'], source['Curves']))
        if paired and paired[0][0]['Role'] != 'wall':
            chunks.append(_open_auxiliary_run(source))
            continue
        for record, curve in paired:
            if type(record.get('LayerIndex')) is not int or record['LayerIndex'] < 0:
                raise ValueError('开放连续路径需要真实非负LayerIndex。')
            if curve.IsClosed or curve.GetLength() <= 0:
                raise ValueError('开放连续路径仅接受非零长度开放曲线。')
            if record.get('IsConnection'):
                raise ValueError('输入包含已规划连接，请连接原始sliced Path。')
        paired.sort(key=lambda pair: pair[0]['LayerIndex'])
        points, heights, vectors, segments = [], [], [], []
        previous = None
        for index, (original, original_curve) in enumerate(paired):
            if previous is not None:
                if original['LayerIndex'] != previous['LayerIndex'] + 1:
                    raise ValueError('每层须恰好一条开放曲线且层号连续；请在上游拆分独立组。')
                for field in ('GeoId', 'SourcePath', 'SourceObjectKey', 'Role', 'PathGroupId'):
                    if original.get(field) != previous.get(field):
                        raise ValueError('开放连续路径不能跨来源或功能组连接：' + field)
            curve = original_curve.DuplicateCurve()
            if index % 2 and not curve.Reverse():
                raise ValueError('开放曲线反向失败。')
            pts, params = curve_endpoints(curve)
            sampler = prepare_sample_field(original)
            samples = [read_sample_field(sampler, point) for point in pts]
            hs = tuple(sample[0] for sample in samples)
            vs = tuple(sample[1] for sample in samples)
            start = len(points)
            record = dict(original)
            record.pop('ArcSegments', None)
            record.update(Curve=curve, SourceCurve=original_curve,
                          SourceSegmentId=original['SegmentId'],
                          SegmentId=original['SegmentId'] + ':open-continuous',
                          Points=tuple(pts), Heights=hs, GrowthVectors=vs,
                          SampleCurve=curve, SampleParameters=tuple(params),
                          PointRange=(start, start + len(pts)),
                          MotionKind='extrude', SeamDistance=0.0,
                          SeamPoint=pts[-1], SeamHeight=hs[-1],
                          TraversalReversed=bool(index % 2))
            if previous is not None and points[-1].DistanceTo(pts[0]) > 0.0:
                connection = dict(
                    Curve=rg.LineCurve(points[-1], pts[0]), IsConnection=True,
                    Role=record['Role'], MotionKind='extrude',
                    MotionReason='adjacent_single_open_layers',
                    SegmentId=record['SegmentId'] + ':connection',
                    GeoId=record['GeoId'], SourcePath=record['SourcePath'],
                    ChunkId=record['ChunkId'], LayerIndex=None,
                    LayerHeight=record['LayerHeight'], PointRange=(start - 1, start + 1),
                    FromSegmentId=previous['SegmentId'], ToSegmentId=record['SegmentId'],
                    FromLayerIndex=previous['LayerIndex'], ToLayerIndex=record['LayerIndex'])
                for field in ('Width', 'IsFirstLayer', 'HeightMetric', 'PathGroupId', 'SourceObjectKey'):
                    if field in record:
                        connection[field] = record[field]
                if 'Width' not in connection and source.get('Width') is not None:
                    connection['Width'] = source['Width']
                segments.append(connection)
            segments.append(record)
            points.extend(pts)
            heights.extend(hs)
            vectors.extend(vs)
            previous = record
        chunk = dict(source)
        for field in ('ArcSegments', 'SampleCurve', 'SampleParameters', 'PointRange'):
            chunk.pop(field, None)
        chunk.update(Curves=(_assemble_run_curve(segments),) if segments else (),
                     Segments=tuple(segments), Points=tuple(points), Heights=tuple(heights),
                     GrowthVectors=tuple(vectors), Sampling='segment_endpoints',
                     SeamRule='open:0', PathMotionPolicy='extrusion_only',
                     ContinuousSourceTreePath=tuple(source['TreePath']), ContinuousRunIndex=0)
        chunks.append(chunk)
    chunks = _fuse_skirt_wall(chunks)
    inherited = dict(metadata[0]) if len(metadata) == 1 else {}
    inherited.update(Inputs=tuple(metadata), Units=metadata[0].get('Units', 'None'),
                     EmptyPaths=empty_paths, Sampling='segment_endpoints', SeamRule='open:0',
                     PathMotionPolicy='extrusion_only', OpenTraversal='alternating_original_direction')
    data = _path_output('continuous', chunks, inherited)
    return data, []


if "ghenv" in globals():
    _run_component()


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = '开放连续路径'
    ghenv.Component.NickName = '开放连续路径'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'Path'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '将同组逐层开放曲线交替反向连接为往返打印路径。'
    ghenv.Component.Tooltip = '将同组逐层开放曲线交替反向连接为往返打印路径。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'Path': '逐层开放曲线的 sliced Path', 'Run': '按钮触发；关闭时保留缓存'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Path': '连续 Path，携带曲线、逐点属性、来源与MotionKind动作', 'Preview': 'Path 内连续曲线副本'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
