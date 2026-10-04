"""G-code上传

【功能】
将 G-code 保存到本地并上传至指定打印设备。

【输入端】
GCode（通用对象；数据树；必填）：按执行顺序排列的 G-code 文本树。
Folder（通用对象；单项；可选）：保存目录；Save 时必填。
Name（通用对象；单项；可选）：文件名前缀，默认 Print。
Density（数值；单项；可选）：材料密度 g/cm³，默认 1.04。
Save（布尔值；单项；可选）：按钮上升沿保存一次；序号按目录现有文件递增。
Host（通用对象；单项；可选）：Moonraker 地址，默认 http://192.168.1.114。
Key（通用对象；单项；可选）：可选 API 密钥；局域网已授权时留空。
Upload（布尔值；单项；可选）：按钮上升沿上传一次；不启动打印。

【输出端】
Report（通用对象；单项）：时间、长度、重量、物体数及保存、上传结果。

【详细用法与约束】
G-code 上传：独立电池，保留 G-code 分析导出的分析与本地保存功能。
输入：GCode(Tree/object)；Folder(Item/object，本地保存目录)；Name(Item/object，默认 Print)；
Density(Item/number，g/cm³，默认1.04)；Save(Item/bool，上升沿本地保存)；
Host(Item/object，默认 http://192.168.1.114)；Key(Item/object，可选 API 密钥)；
Upload(Item/bool，上升沿上传一次)。输出：Report(Item/object)。
上传至 Moonraker gcodes 根目录，固定 print=false；不会启动打印。
本地文件保持 Name_序号_预计时间；远端追加随机标识，避免文件名冲突。
重量及时间算法与导出电池一致，估时不包含加热、归零和加速度。
网络请求有超时；失败后必须释放按钮再重试，不会自动重发。
"""

# Localized presentation only; keep required inputs, type hints and solver behavior.
def _newpath_zh_message(message):
    text = str(message)
    if any('\u4e00' <= char <= '\u9fff' for char in text):
        return text
    return '运行提示，请检查相关输入。原始信息：' + text


# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '将 G-code 保存到本地并上传至指定打印设备。'
    ghenv.Component.Tooltip = '将 G-code 保存到本地并上传至指定打印设备。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'GCode': '按执行顺序排列的 G-code 文本树', 'Folder': '保存目录；Save 时必填', 'Name': '文件名前缀，默认 Print', 'Density': '材料密度 g/cm³，默认 1.04', 'Save': '按钮上升沿保存一次；序号按目录现有文件递增', 'Host': 'Moonraker 地址，默认 http://192.168.1.114', 'Key': '可选 API 密钥；局域网已授权时留空', 'Upload': '按钮上升沿上传一次；不启动打印'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Report': '时间、长度、重量、物体数及保存、上传结果'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description


import math
import os
import re
COMPONENT_MARKER = 'gcode_upload:1'
COMPONENT_MESSAGE = 'G-code 分析与上传\n本地保存 · 仅上传文件'
INPUT_SPECS = [('GCode', 'GCode', '按执行顺序排列的 G-code 文本树', 'tree', 'object', False), ('Folder', 'Folder', '保存目录；Save 时必填', 'item', 'object', True), ('Name', 'Name', '文件名前缀，默认 Print', 'item', 'object', True), ('Density', 'Density', '材料密度 g/cm³，默认 1.04', 'item', 'number', True), ('Save', 'Save', '按钮上升沿保存一次；序号按目录现有文件递增', 'item', 'bool', True), ('Host', 'Host', 'Moonraker 地址，默认 http://192.168.1.114', 'item', 'object', True), ('Key', 'Key', '可选 API 密钥；局域网已授权时留空', 'item', 'object', True), ('Upload', 'Upload', '按钮上升沿上传一次；不启动打印', 'item', 'bool', True)]
OUTPUT_SPECS = [('Report', 'Report', '时间、长度、重量、物体数及保存、上传结果', 'item', 'object', False)]
WORD = re.compile('([A-Z])\\s*([-+]?(?:\\d+(?:\\.\\d*)?|\\.\\d+))', re.I)
AREA = re.compile('^;\\s*FilamentArea\\s*:\\s*([-+]?(?:\\d+(?:\\.\\d*)?|\\.\\d+))\\s*mm2\\s*$', re.I)

def flatten_gcode(tree):
    if tree is None:
        raise ValueError('缺少 GCode')
    if hasattr(tree, 'BranchCount') and hasattr(tree, 'Branch'):
        return [str(line) for i in range(tree.BranchCount) for line in tree.Branch(i)]
    if isinstance(tree, (list, tuple)):
        return [str(line) for branch in tree for line in (branch if isinstance(branch, (list, tuple)) else [branch])]
    raise ValueError('GCode 应为文本树')

def analyze(lines, density=1.04):
    density = float(density)
    if not math.isfinite(density) or density <= 0:
        raise ValueError('Density 必须为有限正数')
    pos = dict(X=0.0, Y=0.0, Z=0.0)
    (epos, feed) = (0.0, None)
    xyz_relative = e_relative = False
    area = None
    seconds = length = extrusion = 0.0
    objects = None
    has_motion = False
    for raw in lines:
        raw = str(raw).strip()
        match = AREA.fullmatch(raw)
        if match:
            value = float(match.group(1))
            if not math.isfinite(value) or value <= 0 or (area is not None and area != value):
                raise ValueError('FilamentArea 元数据无效或冲突')
            area = value
        if raw.upper().startswith('M117'):
            match = re.search('\\[(\\d+)\\s+Objects\\]', raw, re.I)
            if match:
                objects = int(match.group(1))
        code = raw.split(';', 1)[0].strip().upper()
        if not code:
            continue
        words = WORD.findall(code)
        if not words:
            continue
        command = words[0][0] + str(int(float(words[0][1]))) if words[0][0] in 'GM' else ''
        values = {letter: float(value) for (letter, value) in words[1:]}
        if any((not math.isfinite(v) for v in values.values())):
            raise ValueError('G-code 包含非有限数值')
        if command == 'G90':
            xyz_relative = False
        elif command == 'G91':
            xyz_relative = True
        elif command == 'M82':
            e_relative = False
        elif command == 'M83':
            e_relative = True
        elif command == 'G92':
            for axis in pos:
                if axis in values:
                    pos[axis] = values[axis]
            if 'E' in values:
                epos = values['E']
        elif command == 'G4':
            seconds += values.get('P', 0.0) / 1000.0 + values.get('S', 0.0)
        elif command in ('G0', 'G1', 'G2', 'G3'):
            if 'F' in values:
                feed = values['F']
            target = {axis: (pos[axis] + values[axis] if xyz_relative else values[axis]) if axis in values else pos[axis] for axis in pos}
            has_xyz = any((axis in values for axis in pos))
            distance = math.dist(tuple(pos.values()), tuple(target.values())) if has_xyz else 0.0
            if command in ('G2', 'G3') and has_xyz:
                (cx, cy) = (pos['X'] + values.get('I', 0.0), pos['Y'] + values.get('J', 0.0))
                radius = math.hypot(pos['X'] - cx, pos['Y'] - cy)
                if radius <= 0:
                    raise ValueError('圆弧 I/J 半径无效')
                a0 = math.atan2(pos['Y'] - cy, pos['X'] - cx)
                a1 = math.atan2(target['Y'] - cy, target['X'] - cx)
                angle = (a1 - a0) % (2 * math.pi) if command == 'G3' else (a0 - a1) % (2 * math.pi)
                if angle < 1e-10:
                    angle = 2 * math.pi
                distance = math.hypot(radius * angle, target['Z'] - pos['Z'])
            delta_e = 0.0
            if 'E' in values:
                delta_e = values['E'] if e_relative else values['E'] - epos
                epos = epos + values['E'] if e_relative else values['E']
            if distance > 0 and (feed is None or feed <= 0):
                raise ValueError('运动指令缺少有效 F，无法估算时间')
            if has_xyz:
                has_motion = True
                if feed is not None and feed > 0:
                    seconds += distance * 60.0 / feed
                if delta_e > 0:
                    length += distance
                    extrusion += delta_e
            elif 'E' in values and feed is not None and (feed > 0):
                seconds += abs(delta_e) * 60.0 / feed
            pos = target
    if not has_motion:
        raise ValueError('GCode 没有可分析的运动指令')
    return dict(seconds=seconds, length=length, weight=extrusion * area * density / 1000.0 if area else None, area=area, objects=objects)

def duration_label(seconds):
    total = int(round(seconds))
    (hours, remainder) = divmod(total, 3600)
    (minutes, second) = divmod(remainder, 60)
    return '{:02d}h{:02d}m{:02d}s'.format(hours, minutes, second)

def save_gcode(lines, folder, name, seconds):
    folder = str(folder or '').strip()
    if not folder:
        raise ValueError('Folder 不能为空')
    folder = os.path.abspath(os.path.expanduser(folder))
    name = str(name or 'Print').strip()
    if not name or name in ('.', '..') or re.search('[<>:"/\\\\|?*\\x00-\\x1f]', name) or name.endswith(('.', ' ')):
        raise ValueError('Name 含无效文件名字符')
    if not os.path.isdir(folder):
        os.makedirs(folder)
    duration = duration_label(seconds)
    current_pattern = re.compile('^' + re.escape(name) + '_(\\d+)_\\d+h\\d{2}m\\d{2}s\\.gcode$', re.I)
    former_pattern = re.compile('^' + re.escape(name) + '_\\d+h\\d{2}m\\d{2}s_(\\d+)\\.gcode$', re.I)
    used = [int(match.group(1)) for item in os.listdir(folder) for match in [current_pattern.fullmatch(item) or former_pattern.fullmatch(item)] if match]
    index = max(used, default=0) + 1
    while True:
        path = os.path.join(folder, '{}_{:03d}_{}.gcode'.format(name, index, duration))
        try:
            with open(path, 'x', encoding='utf-8', newline='\n') as stream:
                stream.write('\n'.join(lines) + '\n')
            return path
        except FileExistsError:
            index += 1

def _port_name(param):
    return str(getattr(param, 'VariableName', param.NickName))

def _ensure_ports(component):
    return True
import json
import uuid
import urllib.error
import urllib.parse
import urllib.request

def normalize_host(host):
    host = str(host or 'http://192.168.1.114').strip().rstrip('/')
    if '://' not in host:
        host = 'http://' + host
    parsed = urllib.parse.urlsplit(host)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Host 应为 http/https 设备地址，不包含用户名、密码或查询参数')
    return host

def upload_gcode(lines, host, name, seconds, key=None, timeout=30):
    host = normalize_host(host)
    name = str(name or 'Print').strip()
    if not name or name in ('.', '..') or re.search('[<>:"/\\\\|?*\\x00-\\x1f]', name) or name.endswith(('.', ' ')):
        raise ValueError('Name 含无效文件名字符')
    filename = '{}_{}_{}.gcode'.format(name, duration_label(seconds), uuid.uuid4().hex)
    boundary = 'GCodeUpload' + uuid.uuid4().hex
    chunks = []
    for (field, value) in (('root', 'gcodes'), ('print', 'false')):
        chunks.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="' + field + '"\r\n\r\n' + value + '\r\n').encode('utf-8'))
    chunks.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="' + filename + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode('utf-8'))
    chunks.append(('\n'.join(lines) + '\n').encode('utf-8'))
    chunks.append(('\r\n--' + boundary + '--\r\n').encode('ascii'))
    request = urllib.request.Request(host + '/server/files/upload', data=b''.join(chunks), method='POST')
    request.add_header('Content-Type', 'multipart/form-data; boundary=' + boundary)
    if key is not None and str(key).strip():
        request.add_header('X-Api-Key', str(key).strip())

    class NoRedirect(urllib.request.HTTPRedirectHandler):

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            result = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise ValueError('上传 HTTP {}；检查地址、授权和设备存储空间'.format(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError('网络失败或超时；请先在设备文件列表确认是否已收到文件，再重新按上传') from None
    if not isinstance(result, dict):
        raise ValueError('上传返回格式异常，请核对设备文件列表')
    result = result.get('result', result)
    if not isinstance(result, dict) or result.get('item', {}).get('root') != 'gcodes' or (not result.get('item', {}).get('path')):
        raise ValueError('设备未返回 G-code 保存结果，请核对设备文件列表')
    return result['item']['path']

def run_component():
    import Grasshopper as gh
    component = ghenv.Component
    (component.Name, component.NickName) = ('G-code 上传', 'GCodeUpload')
    component.Message = COMPONENT_MESSAGE
    try:
        if not _ensure_ports(component):
            return '等待端口配置'
        key = str(getattr(component, 'InstanceGuid', id(component)))
        states = globals().setdefault('_UPLOAD_ACTION_STATES', {})
        state = states.setdefault(key, {'Save': False, 'Upload': False})
        rising = {}
        for action in ('Save', 'Upload'):
            pressed = bool(globals().get(action))
            rising[action] = pressed and (not state[action])
            state[action] = pressed
        lines = flatten_gcode(globals().get('GCode'))
        density = globals().get('Density')
        info = analyze(lines, 1.04 if density is None else density)
        weight = '{:.2f} g'.format(info['weight']) if info['weight'] is not None else '未知（G-code 无 FilamentArea）'
        report = '打印长度: {:.2f} mm\n重量: {}\n预计时间: {}\n物体数: {}'.format(info['length'], weight, duration_label(info['seconds']), info['objects'] if info['objects'] is not None else '未知')
        for action in ('Save', 'Upload'):
            if not rising[action]:
                continue
            try:
                if action == 'Save':
                    path = save_gcode(lines, globals().get('Folder'), globals().get('Name'), info['seconds'])
                    report += '\n已保存: ' + path
                else:
                    path = upload_gcode(lines, globals().get('Host'), globals().get('Name'), info['seconds'], globals().get('Key'))
                    report += '\n已上传: ' + path + '\n设备: ' + normalize_host(globals().get('Host')) + '\n上传模式: 仅保存文件，不启动打印'
            except Exception as exc:
                label = '本地保存' if action == 'Save' else '上传'
                component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(label + '失败: ' + str(exc)))
                report += '\n' + label + '失败: ' + str(exc)
        return report
    except Exception as exc:
        component.AddRuntimeMessage(gh.Kernel.GH_RuntimeMessageLevel.Error, _newpath_zh_message(str(exc)))
        return '分析失败: ' + str(exc)
if 'ghenv' in globals():
    Report = run_component()


# NEWPATH publishing identity; embedded source, no external loader.
if 'ghenv' in globals():
    ghenv.Component.Name = 'G-code上传'
    ghenv.Component.NickName = 'G-code上传'
    ghenv.Component.Category = 'NEWPATH'
    ghenv.Component.SubCategory = 'GCode'

# Chinese UI text; identifiers and component category stay in English.
if 'ghenv' in globals():
    ghenv.Component.Description = '将 G-code 保存到本地并上传至指定打印设备。'
    ghenv.Component.Tooltip = '将 G-code 保存到本地并上传至指定打印设备。'
    for _ui_port in ghenv.Component.Params.Input:
        _ui_description = {'GCode': '按执行顺序排列的 G-code 文本树', 'Folder': '保存目录；Save 时必填', 'Name': '文件名前缀，默认 Print', 'Density': '材料密度 g/cm³，默认 1.04', 'Save': '按钮上升沿保存一次；序号按目录现有文件递增', 'Host': 'Moonraker 地址，默认 http://192.168.1.114', 'Key': '可选 API 密钥；局域网已授权时留空', 'Upload': '按钮上升沿上传一次；不启动打印'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
    for _ui_port in ghenv.Component.Params.Output:
        _ui_description = {'Report': '时间、长度、重量、物体数及保存、上传结果'}.get(_ui_port.Name)
        if _ui_description is not None:
            _ui_port.Description = _ui_description
            _ui_port.ToolTip = _ui_description
