import asyncio
import socket
import json
import os
import pathlib
import concurrent.futures
import threading

import aiohttp_cors
import requests
import stream_gears
from aiohttp import web
from aiohttp.client import ClientSession
from sqlalchemy import select, update
from sqlalchemy.exc import NoResultFound, MultipleResultsFound
from urllib.parse import urlparse, unquote

import biliup.common.reload
from biliup.config import config
from biliup.plugins.bili_webup import BiliBili, Data
from .aiohttp_basicauth_middleware import basic_auth_middleware
from biliup.database.db import SessionLocal
from biliup.database.models import UploadStreamers, LiveStreamers, Configuration, StreamerInfo
from ..app import logger

try:
    from importlib.resources import files
except ImportError:
    # Try backported to PY<37 `importlib_resources`.
    from importlib_resources import files

BiliBili = BiliBili(Data())

routes = web.RouteTableDef()


async def get_basic_config(request):
    res = {
        "line": config.data['lines'],
        "limit": config.data['threads'],
    }
    if config.data.get("toml"):
        res['toml'] = True
    else:
        res['user'] = {
            "SESSDATA": config.data['user']['cookies']['SESSDATA'],
            "bili_jct": config.data['user']['cookies']['bili_jct'],
            "DedeUserID__ckMd5": config.data['user']['cookies']['DedeUserID__ckMd5'],
            "DedeUserID": config.data['user']['cookies']['DedeUserID'],
            "access_token": config.data['user']['access_token'],
        }

    return web.json_response(res)


async def url_status(request):
    from biliup.app import context
    return web.json_response(context['KernelFunc'].get_url_status())


async def set_basic_config(request):
    post_data = await request.json()
    config.data['lines'] = post_data['line']
    if config.data['lines'] == 'cos':
        config.data['lines'] = 'cos-internal'
    config.data['threads'] = post_data['limit']
    if not config.data.get("toml"):
        cookies = {
            "SESSDATA": str(post_data['user']['SESSDATA']),
            "bili_jct": str(post_data['user']['bili_jct']),
            "DedeUserID__ckMd5": str(post_data['user']['DedeUserID__ckMd5']),
            "DedeUserID": str(post_data['user']['DedeUserID']),
        }
        config.data['user']['cookies'] = cookies
        config.data['user']['access_token'] = str(post_data['user']['access_token'])
    return web.json_response({"status": 200})


async def get_streamer_config(request):
    return web.json_response(config.data['streamers'])


# async def set_streamer_config(request):
#     post_data = await request.json()
#     # config.data['streamers'] = post_data['streamers']
#     for i, j in post_data['streamers'].items():
#         if i not in config.data['streamers']:
#             config.data['streamers'][i] = {}
#         for key, Value in j.items():
#             config.data['streamers'][i][key] = Value
#     for i in config.data['streamers']:
#         if i not in post_data['streamers']:
#             del config.data['streamers'][i]
#     return web.json_response({"status": 200}, status=200)


async def save_config(request):
    config.save()
    biliup.common.reload.global_reloader.triggered = True
    import logging
    logger = logging.getLogger('biliup')
    logger.info("配置保存，将在进程空闲时重启")
    return web.json_response({"status": 200}, status=200)


async def root_handler(request):
    return web.HTTPFound('/index.html')


async def cookie_login(request):
    if config.data.get("toml"):
        print("trying to login by cookie")
        try:
            stream_gears.login_by_cookies()
        except Exception as e:
            return web.HTTPBadRequest(text="login failed" + str(e))
    else:
        cookie = config.data['user']['cookies']
        try:
            BiliBili.login_by_cookies(cookie)
        except Exception as e:
            print(e)
            return web.HTTPBadRequest(text="login failed")
    return web.json_response({"status": 200})


# async def sms_login(request):
#     pass


# async def sms_send(request):
#     # post_data = await request.json()

#     pass


def check_similar_remark(json_data):
    '''
    :return: True if similar remark exists
    '''
    for fname in config['streamers'].keys():
        if (
            json_data['remark'] in fname or
            fname in json_data['remark']
        ):
            return True
    return False

@routes.get('/v1/get_qrcode')
async def qrcode_get(request):
    try:
        r = eval(stream_gears.get_qrcode())
    except Exception as e:
        return web.HTTPBadRequest(text="get qrcode failed")
    return web.json_response(r)


pool = concurrent.futures.ProcessPoolExecutor()


@routes.post('/v1/login_by_qrcode')
async def qrcode_login(request):
    post_data = await request.json()
    try:
        loop = asyncio.get_event_loop()
        # loop
        task = loop.run_in_executor(pool, stream_gears.login_by_qrcode, (json.dumps(post_data, )))
        res = await asyncio.wait_for(task, 180)
        data = json.loads(res)
        filename = f'data/{data["token_info"]["mid"]}.json'
        with open(filename, 'w', encoding='utf-8') as file:
            file.write(res)
        return web.json_response({
            'filename': filename
        })
    except Exception as e:
        logger.exception('login_by_qrcode')
        return web.HTTPBadRequest(text="login failed" + str(e))


async def pre_archive(request):
    if config.data.get("toml"):
        config.load_cookies()
    cookies = config.data['user']['cookies']
    return web.json_response(BiliBili.tid_archive(cookies))


async def tag_check(request):
    if BiliBili.check_tag(request.rel_url.query['tag']):
        return web.json_response({"status": 200})
    else:
        return web.HTTPBadRequest(text="标签违禁")


@routes.get('/v1/videos')
async def streamers(request):
    media_extensions = ['.mp4', '.flv', '.3gp', '.webm', '.mkv', '.ts']
    _blacklist = ['next-env.d.ts']
    # 获取文件列表
    file_list = []
    i = 1
    for file_name in os.listdir('.'):
        if file_name in _blacklist:
            continue
        name, ext = os.path.splitext(file_name)
        if ext in media_extensions:
            file_list.append({'key': i, 'name': file_name, 'updateTime': os.path.getmtime(file_name),
                              'size': os.path.getsize(file_name)})
            i += 1
    return web.json_response(file_list)


@routes.get('/v1/streamer-info')
async def streamers(request):
    res = []
    with SessionLocal() as db:
        result = db.scalars(select(StreamerInfo))
        for s_info in result:
            streamer_info = s_info.as_dict()
            streamer_info['files'] = []
            for file in s_info.filelist:
                tmp = file.as_dict()
                del tmp['streamer_info_id']
                streamer_info['files'].append(tmp)
            streamer_info['date'] = int(streamer_info['date'].timestamp())
            res.append(streamer_info)
    return web.json_response(res)


@routes.get('/v1/streamers')
async def streamers(request):
    from biliup.app import context
    from biliup.common.util import check_timerange
    res = []
    with SessionLocal() as db:
        result = db.scalars(select(LiveStreamers))
        for ls in result:
            temp = ls.as_dict()
            url = temp['url']
            status = 'Idle'
            if context['PluginInfo'].url_status.get(url) == 1:
                status = 'Working'
            if not check_timerange(temp['remark']):
                status = 'OutOfSchedule'
            if context['url_upload_count'].get(url, 0) > 0:
                status = 'Inspecting'
            temp['status'] = status
            if temp.get("upload_streamers_id"):  # 返回 upload_id 而不是 upload_streamers
                temp["upload_id"] = temp["upload_streamers_id"]
            temp.pop("upload_streamers_id")
            res.append(temp)
    return web.json_response(res)


@routes.post('/v1/streamers')
async def add_lives(request):
    from biliup.app import context
    json_data = await request.json()
    if check_similar_remark(json_data):
        return web.HTTPBadRequest(text=f"{json_data['remark']} 与现存备注存在部分重复，禁止添加")
    uid = json_data.get('upload_id')
    with SessionLocal() as db:
        if uid:
            us = db.get(UploadStreamers, uid)
            to_save = LiveStreamers(**LiveStreamers.filter_parameters(json_data), upload_streamers_id=us.id)
        else:
            to_save = LiveStreamers(**LiveStreamers.filter_parameters(json_data))
        try:
            db.add(to_save)
            db.commit()
        # db.flush(to_save)
        except Exception as e:
            logger.exception("Error handling request")
            return web.HTTPBadRequest(text=str(e))
        config.load_from_db(db)
        context['PluginInfo'].add(json_data['remark'], json_data['url'])
        return web.json_response(to_save.as_dict())


@routes.put('/v1/streamers')
async def lives(request):
    from biliup.app import context
    json_data = await request.json()
    # old = LiveStreamers.get_by_id(json_data['id'])
    # if check_similar_remark(json_data):
    #     return web.HTTPBadRequest(text=f"{json_data['remark']} 与现存备注存在部分重复，禁止修改")
    with SessionLocal() as db:
        old = db.get(LiveStreamers, json_data['id'])
        old_url = old.url
        # 如果备注修改，才需要检查是否与现存备注存在部分重复
        if json_data['remark'] != old.remark:
            if check_similar_remark(json_data):
                return web.HTTPBadRequest(text=f"{json_data['remark']} 与现存备注存在部分重复，禁止修改")
        uid = json_data.get('upload_id')
        # semi-ui 不能直接为 ArrayField 设置空默认值
        # 当前端更新后，应移除这里的数据修改
        new_data = {key: json_data.get(key, None) for key in old.as_dict() if key != 'upload_streamers_id'}
        try:
            if uid:
                # us = UploadStreamers.get_by_id(json_data['upload_id'])
                us = db.get(UploadStreamers, json_data['upload_id'])
                # LiveStreamers.update(**json_data, upload_streamers=us).where(LiveStreamers.id == old.id).execute()
                # db.update_live_streamer(**{**json_data, "upload_streamers_id": us.id})
                db.execute(update(LiveStreamers), [{**new_data, "upload_streamers_id": us.id}])
                db.commit()
            else:
                # LiveStreamers.update(**json_data).where(LiveStreamers.id == old.id).execute()
                db.execute(update(LiveStreamers), [new_data])
                db.commit()
        except Exception as e:
            return web.HTTPBadRequest(text=str(e))
        config.load_from_db(db)
        context['PluginInfo'].delete(old_url)
        context['PluginInfo'].add(json_data['remark'], json_data['url'])
        # return web.json_response(LiveStreamers.get_dict(id=json_data['id']))
        return web.json_response(db.get(LiveStreamers, json_data['id']).as_dict())


@routes.delete('/v1/streamers/{id}')
async def streamers(request):
    from biliup.app import context
    # org = LiveStreamers.get_by_id(request.match_info['id'])
    with SessionLocal() as db:
        org = db.get(LiveStreamers, request.match_info['id'])
    # LiveStreamers.delete_by_id(request.match_info['id'])
        db.delete(org)
        db.commit()
        context['PluginInfo'].delete(org.url)
    return web.HTTPOk()


@routes.get('/v1/upload/streamers')
async def get_streamers(request):
    with SessionLocal() as db:
        res = db.scalars(select(UploadStreamers))
        return web.json_response([resp.as_dict() for resp in res])


@routes.get('/v1/upload/streamers/{id}')
async def streamers_id(request):
    _id = request.match_info['id']
    with SessionLocal() as db:
        res = db.get(UploadStreamers, _id).as_dict()
        return web.json_response(res)


@routes.delete('/v1/upload/streamers/{id}')
async def streamers(request):
    with SessionLocal() as db:
        us = db.get(UploadStreamers, request.match_info['id'])
        db.delete(us)
        db.commit()
    # UploadStreamers.delete_by_id(request.match_info['id'])
    return web.HTTPOk()


@routes.post('/v1/upload/streamers')
async def streamers_post(request):
    json_data = await request.json()
    with SessionLocal() as db:
        if "id" in json_data.keys():  # 前端未区分更新和新建, 暂时从后端区分
            db.execute(update(UploadStreamers), [json_data])
            id = json_data["id"]
        else:
            to_save = UploadStreamers(**UploadStreamers.filter_parameters(json_data))
            db.add(to_save)
            db.commit()
            id = to_save.id
        db.commit()
        config.load_from_db(db)
        # res = to_save.as_dict()
        # return web.json_response(res)
        return web.json_response(db.get(UploadStreamers, id).as_dict())


@routes.put('/v1/upload/streamers')
async def streamers_put(request):
    json_data = await request.json()
    with SessionLocal() as db:
    # UploadStreamers.update(**json_data)
        db.execute(update(UploadStreamers), [json_data])
        db.commit()
        config.load_from_db(db)
        # return web.json_response(UploadStreamers.get_dict(id=json_data['id']))
        return web.json_response(db.get(UploadStreamers, json_data['id']).as_dict())


@routes.get('/v1/users')
async def users(request):
    # records = Configuration.select().where(Configuration.key == 'bilibili-cookies')
    res = []
    with SessionLocal() as db:
        records = db.scalars(
            select(Configuration).where(Configuration.key == 'bilibili-cookies'))

        for record in records:
            res.append({
                'id': record.id,
                'name': record.value,
                'value': record.value,
                'platform': record.key,
            })
    return web.json_response(res)


@routes.post('/v1/users')
async def users(request):
    json_data = await request.json()
    to_save = Configuration(key=json_data['platform'], value=json_data['value'])
    with SessionLocal() as db:
        db.add(to_save)
        # to_save.save()
        # db.flush(to_save)
        resp = {
            'id': to_save.id,
            'name': to_save.value,
            'value': to_save.value,
            'platform': to_save.key,
        }
        db.commit()
        return web.json_response([resp])


@routes.delete('/v1/users/{id}')
async def users(request):
    # Configuration.delete_by_id(request.match_info['id'])
    with SessionLocal() as db:
        configuration = db.get(Configuration, request.match_info['id'])
        db.delete(configuration)
        db.commit()
    return web.HTTPOk()


@routes.get('/v1/configuration')
async def users(request):
    try:
        # record = Configuration.get(Configuration.key == 'config')
        with SessionLocal() as db:
            record = db.execute(
                select(Configuration).where(Configuration.key == 'config')
            ).scalar_one()
            return web.json_response(json.loads(record.value))
    except NoResultFound:
        return web.json_response({})
    except MultipleResultsFound as e:
        return web.json_response({"status": 500, 'error': f"有多个空间配置同时存在: {e}"}, status=500)



@routes.put('/v1/configuration')
async def users(request):
    json_data = await request.json()
    with SessionLocal() as db:
        try:
            # record = Configuration.get(Configuration.key == 'config')
            record = db.execute(
                select(Configuration).where(Configuration.key == 'config')
            ).scalar_one()  # 判断是否只有一行空间配置
            record.value = json.dumps(json_data)
            # db.flush(record)
            resp = record.as_dict()
            # to_save = Configuration(key='config', value=json.dumps(json_data), id=record.id)
        except NoResultFound:  # 如果数据库中没有空间配置行，新建
            to_save = Configuration(key='config', value=json.dumps(json_data))
            # to_save.save()
            db.add(to_save)
            db.commit()
            # db.flush(to_save)
            resp = to_save.as_dict()
        except MultipleResultsFound as e:  # 如果有多行，报错
            return web.json_response({"status": 500, 'error': f"有多个空间配置同时存在: {e}"}, status=500)
        db.commit()
        config.load_from_db(db)
    return web.json_response(resp)


@routes.post('/v1/uploads')
async def m_upload(request):
    from ..uploader import biliup_uploader
    json_data = await request.json()
    json_data['params']['uploader'] = 'stream_gears'
    json_data['params']['name'] = json_data['params']['template_name']
    # json_data['params']['extra_fields'] = "{\"is_only_self\": 1}"
    threading.Thread(target=biliup_uploader, args=(json_data['files'], json_data['params'])).start()
    return web.json_response({'status': 'ok'})


@routes.post('/v1/dump')
async def dump_config(request):
    json_data = await request.json()
    with SessionLocal() as db:
        config.load_from_db(db)
    file = config.dump(json_data['path'])
    return web.json_response({'path': file})


@routes.get('/v1/status')
async def app_status(request):
    from biliup.app import context
    from biliup.config import Config
    from biliup.app import PluginInfo
    from biliup import __version__
    res = {'version': __version__, }
    for key, value in context.items():  # 遍历删除不能被 json 序列化的键值对
        if isinstance(value, Config):
            continue
        if isinstance(value, PluginInfo):
            continue
        res[key] = value
    return web.json_response(res)


@routes.get('/bili/archive/pre')
async def pre_archive(request):
    # path = 'cookies.json'
    # conf = Configuration.get_or_none(Configuration.key == 'bilibili-cookies')
    with SessionLocal() as db:
        confs = db.scalars(
            select(Configuration).where(Configuration.key == 'bilibili-cookies'))
    # if conf is not None:
    #     path = conf.value
        for conf in confs:
            path = conf.value
            try:
                config.load_cookies(path)
                cookies = config.data['user']['cookies']
                res = BiliBili.tid_archive(cookies)
                if res['code'] != 0:
                    continue
                return web.json_response(res)
            except:
                logger.exception('pre_archive')
                continue
    return web.json_response({"status": 500, 'error': "无可用 cookie 文件"}, status=500)


@routes.get('/bili/space/myinfo')
async def myinfo(request):
    file = request.query['user']
    try:
        config.load_cookies(file)
    except FileNotFoundError:
        return web.json_response({"status": 500, 'error': f"{file} 文件不存在"}, status=500)
    cookies = config.data['user']['cookies']
    return web.json_response(BiliBili.myinfo(cookies))


@routes.get('/bili/proxy')
async def proxy(request):
    url = unquote(request.query['url'])
    parsed_url = urlparse(url)

    if not parsed_url.hostname or not parsed_url.hostname.endswith('.hdslb.com'):
        return web.HTTPForbidden(reason="Access to the requested domain is forbidden")

    async with ClientSession() as session:
        try:
            async with session.get(url) as response:
                content = await response.read()
                return web.Response(body=content, status=response.status)
        except Exception as e:
            return web.HTTPBadRequest(reason=str(e))


def find_all_folders(directory):
    result = []
    for foldername, subfolders, filenames in os.walk(directory):
        for subfolder in subfolders:
            result.append(os.path.relpath(os.path.join(foldername, subfolder), directory))
    return result


async def service(args):
    app = web.Application()
    app.add_routes([
        web.get('/api/check_tag', tag_check),
        web.get('/url-status', url_status),
        web.get('/api/basic', get_basic_config),
        web.post('/api/setbasic', set_basic_config),
        web.get('/api/getconfig', get_streamer_config),
        # web.post('/api/setconfig', set_streamer_config),
        web.get('/api/login_by_cookie', cookie_login),
        # web.get('/api/login_by_sms', sms_login),
        # web.post('/api/send_sms', sms_send),
        web.get('/api/save', save_config),
        # web.get('/api/get_qrcode', qrcode_get),
        # web.post('/api/login_by_qrcode', qrcode_login),
        web.get('/api/archive_pre', pre_archive),
        web.get('/', root_handler)
    ])
    routes.static('/static', '.', show_index=False)
    app.add_routes(routes)
    if args.static_dir:
        app.add_routes([web.static('/', args.static_dir, show_index=False)])
    else:
        # res = [web.static('/', files('biliup.web').joinpath('public'))]
        res = []
        for fdir in pathlib.Path(files('biliup.web').joinpath('public')).glob('**/*.html'):
            fname = fdir.relative_to(files('biliup.web').joinpath('public'))

            def _copy(fname):
                async def static_view(request):
                    return web.FileResponse(files('biliup.web').joinpath('public').joinpath(fname))

                return static_view

            res.append(web.get('/' + str(fname.with_suffix('')).replace('\\', '/'), _copy(fname)))
            # res.append(web.static('/'+fdir.replace('\\', '/'), files('biliup.web').joinpath('public/'+fdir)))
        res.append(web.static('/', files('biliup.web').joinpath('public')))
        app.add_routes(res)
    if args.password:
        app.middlewares.append(basic_auth_middleware(('/',), {'biliup': args.password}, ))

    # web.run_app(app, host=host, port=port)
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            allow_methods="*",
            expose_headers="*",
            allow_headers="*"
        )
    })

    for route in list(app.router.routes()):
        cors.add(route)

    if args.no_access_log:
        runner = web.AppRunner(app, access_log=None)
    else:
        runner = web.AppRunner(app)
    setup_middlewares(app)
    await runner.setup()
    site = web.TCPSite(runner, host=args.host, port=args.port)
    await site.start()
    log_startup(args.host, args.port)
    return runner


async def handle_404(request):
    return web.FileResponse(files('biliup.web').joinpath('public').joinpath('404.html'))


async def handle_500(request):
    return web.json_response({"status": 500, 'error': "Error handling request"}, status=500)


def create_error_middleware(overrides):
    @web.middleware
    async def error_middleware(request, handler):
        try:
            return await handler(request)
        except web.HTTPException as ex:
            override = overrides.get(ex.status)
            if override:
                return await override(request)

            raise
        except Exception:
            request.protocol.logger.exception("Error handling request")
            return await overrides[500](request)

    return error_middleware


def setup_middlewares(app):
    @web.middleware
    async def file_type_check_middleware(request, handler):
        allowed_extensions = {'.mp4', '.flv', '.3gp', '.webm', '.mkv', '.ts', '.xml', '.log'}

        if request.path.startswith('/static/'):
            filename = request.match_info.get('filename')
            if filename:
                extension = '.' + filename.split('.')[-1]
                if extension not in allowed_extensions:
                    return web.HTTPForbidden(reason="File type not allowed")
        return await handler(request)

    error_middleware = create_error_middleware({
        404: handle_404,
        500: handle_500,
    })
    app.middlewares.append(error_middleware)
    app.middlewares.append(file_type_check_middleware)


def log_startup(host, port) -> None:
    """Show information about the address when starting the server."""
    messages = ['WebUI 已启动，请浏览器访问']
    host = host if host else "0.0.0.0"
    scheme = "http"
    display_hostname = host

    if host in {"0.0.0.0", "::"}:
        messages.append(f" * Running on all addresses ({host})")
        if host == "0.0.0.0":
            localhost = "127.0.0.1"
            display_hostname = get_interface_ip(socket.AF_INET)
        else:
            localhost = "[::1]"
            display_hostname = get_interface_ip(socket.AF_INET6)

        messages.append(f" * Running on {scheme}://{localhost}:{port}")

    if ":" in display_hostname:
        display_hostname = f"[{display_hostname}]"

    messages.append(f" * Running on {scheme}://{display_hostname}:{port}")

    print("\n".join(messages))


def get_interface_ip(family: socket.AddressFamily) -> str:
    """Get the IP address of an external interface. Used when binding to
    0.0.0.0 or ::1 to show a more useful URL.

    :meta private:
    """
    # arbitrary private address
    host = "fd31:f903:5ab5:1::1" if family == socket.AF_INET6 else "10.253.155.219"

    with socket.socket(family, socket.SOCK_DGRAM) as s:
        try:
            s.connect((host, 58162))
        except OSError:
            return "::1" if family == socket.AF_INET6 else "127.0.0.1"

        return s.getsockname()[0]  # type: ignore
