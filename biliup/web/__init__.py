import json
import os
import pathlib

import aiohttp_cors
import requests
import stream_gears
from aiohttp import web
from sqlalchemy import select, update
from sqlalchemy.exc import NoResultFound, MultipleResultsFound

import biliup.common.reload
from biliup.config import config
from biliup.plugins.bili_webup import BiliBili, Data
from .aiohttp_basicauth_middleware import basic_auth_middleware
from biliup.database.db import Session
from biliup.database.models import UploadStreamers, LiveStreamers, Configuration, StreamerInfo

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


async def set_streamer_config(request):
    post_data = await request.json()
    # config.data['streamers'] = post_data['streamers']
    for i, j in post_data['streamers'].items():
        if i not in config.data['streamers']:
            config.data['streamers'][i] = {}
        for key, Value in j.items():
            config.data['streamers'][i][key] = Value
    for i in config.data['streamers']:
        if i not in post_data['streamers']:
            del config.data['streamers'][i]

    return web.json_response({"status": 200}, status=200)


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


async def sms_login(request):
    pass


async def sms_send(request):
    # post_data = await request.json()

    pass


async def qrcode_get(request):
    if config.data.get("toml"):
        try:
            r = eval(stream_gears.get_qrcode())
        except Exception as e:
            return web.HTTPBadRequest(text="get qrcode failed")
    else:
        r = BiliBili.get_qrcode()
    return web.json_response(r)


async def qrcode_login(request):
    post_data = await request.json()
    if config.data.get("toml"):
        try:
            if stream_gears.login_by_qrcode(json.dumps(post_data)):
                return web.json_response({"status": 200})
        except Exception as e:
            return web.HTTPBadRequest(text="login failed" + str(e))
    else:
        try:
            r = await BiliBili.login_by_qrcode(post_data)
        except:
            return web.HTTPBadRequest(text="timeout for qrcode validate")
        for cookie in r['data']['cookie_info']['cookies']:
            config.data['user']['cookies'][cookie['name']] = cookie['value']
        config.data['user']['access_token'] = r['data']['token_info']['access_token']
        return web.json_response(r)


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
    # 获取文件列表
    file_list = []
    i = 1
    for file_name in os.listdir('.'):
        name, ext = os.path.splitext(file_name)
        if ext in media_extensions:
            file_list.append({'key': i, 'name': file_name, 'updateTime': os.path.getmtime(file_name),
                              'size': os.path.getsize(file_name)})
            i += 1
    return web.json_response(file_list)


@routes.get('/v1/streamer-info')
async def streamers(request):
    res = []
    result = Session.scalars(select(StreamerInfo))
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
    res = []
    result = Session.scalars(select(LiveStreamers))
    for ls in result:
        temp = ls.as_dict()
        url = temp['url']
        status = 'Idle'
        if context['PluginInfo'].url_status.get(url) == 1:
            status = 'Working'
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
    uid = json_data.get('upload_id')
    if uid:
        us = Session.get(UploadStreamers, uid)
        to_save = LiveStreamers(**LiveStreamers.filter_parameters(json_data), upload_streamers_id=us.id)
    else:
        to_save = LiveStreamers(**LiveStreamers.filter_parameters(json_data))
    try:
        Session.add(to_save)
        Session.commit()
    except Exception as e:
        return web.HTTPBadRequest(text=str(e))
    config.load_from_db()
    context['PluginInfo'].add(json_data['remark'], json_data['url'])
    return web.json_response(to_save.as_dict())


@routes.put('/v1/streamers')
async def lives(request):
    from biliup.app import context
    json_data = await request.json()
    # old = LiveStreamers.get_by_id(json_data['id'])
    old = Session.get(LiveStreamers, json_data['id'])
    old_url = old.url
    uid = json_data.get('upload_id')
    try:
        if uid:
            # us = UploadStreamers.get_by_id(json_data['upload_id'])
            us = Session.get(UploadStreamers, json_data['upload_id'])
            # LiveStreamers.update(**json_data, upload_streamers=us).where(LiveStreamers.id == old.id).execute()
            # db.update_live_streamer(**{**json_data, "upload_streamers_id": us.id})
            Session.execute(update(LiveStreamers), [{**json_data, "upload_streamers_id": us.id}])
            Session.commit()
        else:
            # LiveStreamers.update(**json_data).where(LiveStreamers.id == old.id).execute()
            Session.execute(update(LiveStreamers), [json_data])
            Session.commit()
    except Exception as e:
        return web.HTTPBadRequest(text=str(e))
    config.load_from_db()
    context['PluginInfo'].delete(old_url)
    context['PluginInfo'].add(json_data['remark'], json_data['url'])
    # return web.json_response(LiveStreamers.get_dict(id=json_data['id']))
    return web.json_response(Session.get(LiveStreamers, json_data['id']).as_dict())


@routes.delete('/v1/streamers/{id}')
async def streamers(request):
    from biliup.app import context
    # org = LiveStreamers.get_by_id(request.match_info['id'])
    org = Session.get(LiveStreamers, request.match_info['id'])
    # LiveStreamers.delete_by_id(request.match_info['id'])
    Session.delete(org)
    Session.commit()
    context['PluginInfo'].delete(org.url)
    return web.HTTPOk()


@routes.get('/v1/upload/streamers')
async def get_streamers(request):
    res = Session.scalars(select(UploadStreamers))
    return web.json_response([resp.as_dict() for resp in res])


@routes.get('/v1/upload/streamers/{id}')
async def streamers_id(request):
    id = request.match_info['id']
    res = Session.get(UploadStreamers, id).as_dict()
    return web.json_response(res)


@routes.delete('/v1/upload/streamers/{id}')
async def streamers(request):
    us = Session.get(UploadStreamers, request.match_info['id'])
    Session.delete(us)
    Session.commit()
    # UploadStreamers.delete_by_id(request.match_info['id'])
    return web.HTTPOk()


@routes.post('/v1/upload/streamers')
async def streamers_post(request):
    json_data = await request.json()
    if "id" in json_data.keys():  # 前端未区分更新和新建, 暂时从后端区分
        Session.execute(update(UploadStreamers), [json_data])
        id = json_data["id"]
    else:
        to_save = UploadStreamers(**UploadStreamers.filter_parameters(json_data))
        Session.add(to_save)
        Session.flush()
        id = to_save.id
    Session.commit()
    config.load_from_db()
    # res = to_save.as_dict()
    # return web.json_response(res)
    return web.json_response(Session.get(UploadStreamers, id).as_dict())


@routes.put('/v1/upload/streamers')
async def streamers_put(request):
    json_data = await request.json()
    # UploadStreamers.update(**json_data)
    Session.execute(update(UploadStreamers), [json_data])
    Session.commit()
    config.load_from_db()
    # return web.json_response(UploadStreamers.get_dict(id=json_data['id']))
    return web.json_response(Session.get(UploadStreamers, json_data['id']).as_dict())


@routes.get('/v1/users')
async def users(request):
    # records = Configuration.select().where(Configuration.key == 'bilibili-cookies')
    records = Session.scalars(
        select(Configuration).where(Configuration.key == 'bilibili-cookies'))
    res = []
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
    Session.add(to_save)
    # to_save.save()
    resp = {
        'id': to_save.id,
        'name': to_save.value,
        'value': to_save.value,
        'platform': to_save.key,
    }
    Session.commit()
    return web.json_response([resp])


@routes.delete('/v1/users/{id}')
async def users(request):
    # Configuration.delete_by_id(request.match_info['id'])
    configuration = Session.get(Configuration, request.match_info['id'])
    Session.delete(configuration)
    Session.commit()
    return web.HTTPOk()


@routes.get('/v1/configuration')
async def users(request):
    try:
        # record = Configuration.get(Configuration.key == 'config')
        record = Session.execute(
            select(Configuration).where(Configuration.key == 'config')
        ).scalar_one()
    except NoResultFound:
        return web.json_response({})
    except MultipleResultsFound as e:
        return web.json_response({"status": 500, 'error': f"有多个空间配置同时存在: {e}"}, status=500)
    return web.json_response(json.loads(record.value))


@routes.put('/v1/configuration')
async def users(request):
    json_data = await request.json()
    try:
        # record = Configuration.get(Configuration.key == 'config')
        record = Session.execute(
            select(Configuration).where(Configuration.key == 'config')
        ).scalar_one()
        record.value = json.dumps(json_data)
        Session.commit()
        # to_save = Configuration(key='config', value=json.dumps(json_data), id=record.id)
    except NoResultFound:
        to_save = Configuration(key='config', value=json.dumps(json_data))
        # to_save.save()
        Session.add(to_save)
        return web.json_response(to_save.as_dict())
    except MultipleResultsFound as e:
        return web.json_response({"status": 500, 'error': f"有多个空间配置同时存在: {e}"}, status=500)
    config.load_from_db()
    return web.json_response(record.as_dict())


@routes.post('/v1/dump')
async def dump_config(request):
    json_data = await request.json()
    config.load_from_db()
    file = config.dump(json_data['path'])
    return web.json_response({'path': file})


@routes.get('/bili/archive/pre')
async def pre_archive(request):
    path = 'cookies.json'
    # conf = Configuration.get_or_none(Configuration.key == 'bilibili-cookies')
    conf = Session.scalars(
        select(Configuration).where(Configuration.key == 'bilibili-cookies')).first()
    if conf is not None:
        path = conf.value
    config.load_cookies(path)
    cookies = config.data['user']['cookies']
    return web.json_response(BiliBili.tid_archive(cookies))


@routes.get('/bili/space/myinfo')
async def myinfo(request):
    file = request.query['user']
    try:
        config.load_cookies(file)
    except FileNotFoundError:
        return web.json_response({"status": 500, 'error': f"找不到 {file} ！！！"}, status=500)
    cookies = config.data['user']['cookies']
    return web.json_response(BiliBili.myinfo(cookies))


@routes.get('/bili/proxy')
async def proxy(request):
    return web.Response(body=requests.get(request.query['url']).content)


def find_all_folders(directory):
    result = []
    for foldername, subfolders, filenames in os.walk(directory):
        for subfolder in subfolders:
            result.append(os.path.relpath(os.path.join(foldername, subfolder), directory))
    return result


@web.middleware
async def get_session(request, handler):
    """ 中间件，用来在请求结束时关闭对应线程会话 """
    resp = await handler(request)
    Session.remove()
    return resp


async def service(args):
    try:
        from importlib.resources import files
    except ImportError:
        # Try backported to PY<37 `importlib_resources`.
        from importlib_resources import files

    app = web.Application(middlewares=[get_session])
    app.add_routes([
        web.get('/api/check_tag', tag_check),
        web.get('/url-status', url_status),
        web.get('/api/basic', get_basic_config),
        web.post('/api/setbasic', set_basic_config),
        web.get('/api/getconfig', get_streamer_config),
        web.post('/api/setconfig', set_streamer_config),
        web.get('/api/login_by_cookie', cookie_login),
        web.get('/api/login_by_sms', sms_login),
        web.post('/api/send_sms', sms_send),
        web.get('/api/save', save_config),
        web.get('/api/get_qrcode', qrcode_get),
        web.post('/api/login_by_qrcode', qrcode_login),
        web.get('/api/archive_pre', pre_archive),
        web.get('/', root_handler)
    ])
    routes.static('/static', '.', show_index=True)
    app.add_routes(routes)
    if args.static_dir:
        app.add_routes([web.static('/', args.static_dir, show_index=False)])
    else:
        # res = [web.static('/', files('biliup.web').joinpath('public'))]
        res = []
        for fdir in pathlib.Path(files('biliup.web').joinpath('public')).glob('*.html'):
            fname = fdir.relative_to(files('biliup.web').joinpath('public'))

            def _copy(fname):
                async def static_view(request):
                    return web.FileResponse(files('biliup.web').joinpath('public/' + str(fname)))

                return static_view

            res.append(web.get('/' + str(fname.with_suffix('')), _copy(fname)))
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

    runner = web.AppRunner(app)
    setup_middlewares(app)
    await runner.setup()
    site = web.TCPSite(runner, host=args.host, port=args.port)
    return runner, site


async def handle_404(request):
    return web.HTTPFound('404')

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
    error_middleware = create_error_middleware({
        404: handle_404,
        500: handle_500,
    })
    app.middlewares.append(error_middleware)
