import asyncio
import base64
import concurrent.futures
import hashlib
import json
import logging
import math
import os
import re
import sys
import threading
import time
import queue
import urllib.parse
from dataclasses import asdict, dataclass, field, InitVar
from json import JSONDecodeError
from os.path import splitext, basename
from typing import Callable, Dict, Union, Any, List
from urllib import parse
from urllib.parse import quote

import aiohttp
from concurrent.futures.thread import ThreadPoolExecutor
import concurrent
import requests.utils
import rsa
import xml.etree.ElementTree as ET
from requests.adapters import HTTPAdapter, Retry

from biliup.config import config
from ..engine import Plugin
from ..engine.upload import UploadBase, logger


logger = logging.getLogger('biliup.engine.bili_web_sync')


@Plugin.upload(platform="bili_web_sync")
class BiliWebAsync(UploadBase):
    def __init__(
            self, principal, data, submit_api=None, copyright=2, postprocessor=None, dtime=None,
            dynamic='', lines='AUTO', threads=3, tid=122, tags=None, cover_path=None, description='',
            dolby=0, hires=0, no_reprint=0, open_elec=0, credits=None,
            user_cookie='cookies.json', copyright_source=None, extra_fields="", video_queue=None
    ):
        super().__init__(principal, data, persistence_path='bili.cookie', postprocessor=postprocessor)
        if credits is None:
            credits = []
        if tags is None:
            tags = []
        self.lines = lines
        self.submit_api = submit_api
        self.threads = threads
        self.tid = tid
        self.tags = tags
        self.dtime = dtime
        if cover_path:
            self.cover_path = cover_path
        elif "live_cover_path" in self.data:
            self.cover_path = self.data["live_cover_path"]
        else:
            self.cover_path = None
        self.desc = description
        self.credits = credits
        self.dynamic = dynamic
        self.copyright = copyright
        self.dolby = dolby
        self.hires = hires
        self.no_reprint = no_reprint
        self.open_elec = open_elec
        self.copyright_source = copyright_source
        self.extra_fields = extra_fields

        self.user_cookie = user_cookie
        self.video_queue: queue.SimpleQueue = video_queue

    def upload(self, total_size: int, stop_event: threading.Event, output_prefix: str, file_name_callback: Callable[[str], None] = None) -> List[UploadBase.FileInfo]:
        # print("开始同步上传")
        logger.info("开始同步上传")
        file_index = 1
        videos = Data()
        bili = BiliBili(videos)
        bili.login(self.persistence_path, self.user_cookie)

        videos.title = self.data["format_title"][:80]  # 稿件标题限制80字
        if self.credits:
            videos.desc_v2 = self.creditsToDesc_v2()
        else:
            videos.desc_v2 = [{
                "raw_text": self.desc,
                "biz_id": "",
                "type": 1
            }]
        videos.desc = self.desc
        videos.copyright = self.copyright
        if self.copyright == 2:
            videos.source = self.data["url"]  # 添加转载地址说明
        # 设置视频分区,默认为174 生活，其他分区
        videos.tid = self.tid
        videos.set_tag(self.tags)
        if self.dtime:
            videos.delay_time(int(time.time()) + self.dtime)
        if self.cover_path:
            videos.cover = bili.cover_up(self.cover_path).replace('http:', '')

        # 其他参数设置
        videos.extra_fields = self.extra_fields
        videos.dolby = self.dolby
        videos.hires = self.hires
        videos.no_reprint = self.no_reprint
        videos.open_elec = self.open_elec

        thread_list = []
        while True:
            # 调试使用 分p 强制停止
            # if file_index > 10:
            #     logger.info(f"[consumer debug] 停止下载回调")
            #     stop_event.set()
            #     break

            file_name = f"{output_prefix}_{file_index}.mkv"

            # if file_name_callback:
            # file_name_callback(file_name)
            data_size = 0
            video_upload_queue = queue.SimpleQueue()

            t = threading.Thread(target=bili.upload_stream, args=(video_upload_queue,
                                 file_name, total_size, self.lines, videos, stop_event, file_name_callback), daemon=True, name=f"upload_{file_index}")
            thread_list.append(t)
            t.start()

            while True:
                try:
                    data = self.video_queue.get(timeout=10)
                except queue.Empty:
                    break

                if data is None:
                    video_upload_queue.put(None)
                    break

                video_upload_queue.put(data)
                # print(video_upload_queue.empty())
                data_size += len(data)
            # print(f"[consumer] 读取 {file_name} {data_size} 字节")
            logger.info(f"[consumer] 读取 {file_name} {data_size} 字节")
            file_index += 1
            # print("[consumer] bili.video.videos", bili.video.videos)
            logger.info(f"[consumer] bili.video.videos {bili.video.videos}")
            if data_size < 100:
                # print(f"[consumer] 停止下载回调")
                # n = video_upload_queue.get()
                logger.info(f"[consumer] 停止下载回调")
                stop_event.set()
                break

        logger.info("等待上传线程结束")
        for t in thread_list:
            t.join()

        # ret = bili.submit(self.submit_api)  # 提交视频
        # logger.info(f"上传成功: {ret}")
        file_list = []
        # if config.get('sync_save_dir', None):
        #     file_list = [os for file_name in os.listdir("sync_downloaded")]
        # print("上传完成", file_list)
        return file_list

    def creditsToDesc_v2(self):
        desc_v2 = []
        desc_v2_tmp = self.desc
        for credit in self.credits:
            try:
                num = desc_v2_tmp.index("@credit")
                desc_v2.append({
                    "raw_text": " " + desc_v2_tmp[:num],
                    "biz_id": "",
                    "type": 1
                })
                desc_v2.append({
                    "raw_text": credit["username"],
                    "biz_id": str(credit["uid"]),
                    "type": 2
                })
                self.desc = self.desc.replace(
                    "@credit", "@" + credit["username"] + "  ", 1)
                desc_v2_tmp = desc_v2_tmp[num + 7:]
            except IndexError:
                logger.error('简介中的@credit占位符少于credits的数量,替换失败')
        desc_v2.append({
            "raw_text": " " + desc_v2_tmp,
            "biz_id": "",
            "type": 1
        })
        desc_v2[0]["raw_text"] = desc_v2[0]["raw_text"][1:]  # 开头空格会导致识别简介过长
        return desc_v2


class BiliBili:
    def __init__(self, video: 'Data'):
        self.app_key = None
        self.appsec = None
        # if self.app_key is None or self.appsec is None:
        self.app_key = 'ae57252b0c09105d'
        self.appsec = 'c75875c596a69eb55bd119e74b07cfe3'
        self.__session = requests.Session()
        self.video = video
        self.__session.mount('https://', HTTPAdapter(max_retries=Retry(total=5)))
        self.__session.headers.update({
            'user-agent': "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/63.0.3239.108",
            'referer': "https://www.bilibili.com/",
            'connection': 'keep-alive'
        })
        self.cookies = None
        self.access_token = None
        self.refresh_token = None
        self.account = None
        self.__bili_jct = None
        self._auto_os = None
        self.persistence_path = 'engine/bili.cookie'

        self.save_dir = config.get('sync_save_dir', None)
        self.save_path = ''
        if self.save_dir and not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)

    def check_tag(self, tag):
        r = self.__session.get("https://member.bilibili.com/x/vupre/web/topic/tag/check?tag=" + tag).json()
        if r["code"] == 0:
            return True
        else:
            return False

    def get_qrcode(self):
        params = {
            "appkey": "4409e2ce8ffd12b8",
            "local_id": "0",
            "ts": int(time.time()),
        }
        params["sign"] = hashlib.md5(
            f"{urllib.parse.urlencode(params)}59b43e04ad6965f34319062b478f83dd".encode()).hexdigest()
        response = self.__session.post("http://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code", data=params,
                                       timeout=5)
        r = response.json()
        if r and r["code"] == 0:
            return r

    async def login_by_qrcode(self, value):
        params = {
            "appkey": "4409e2ce8ffd12b8",
            "auth_code": value["data"]["auth_code"],
            "local_id": "0",
            "ts": int(time.time()),
        }
        params["sign"] = hashlib.md5(
            f"{urllib.parse.urlencode(params)}59b43e04ad6965f34319062b478f83dd".encode()).hexdigest()
        for i in range(0, 120):
            await asyncio.sleep(1)
            response = self.__session.post("http://passport.bilibili.com/x/passport-tv-login/qrcode/poll", data=params,
                                           timeout=5)
            r = response.json()
            if r and r["code"] == 0:
                return r
        raise "Qrcode timeout"

    def tid_archive(self, cookies):
        requests.utils.add_dict_to_cookiejar(self.__session.cookies, cookies)
        response = self.__session.get("https://member.bilibili.com/x/vupre/web/archive/pre")
        return response.json()

    def myinfo(self, cookies):
        requests.utils.add_dict_to_cookiejar(self.__session.cookies, cookies)
        response = self.__session.get('http://api.bilibili.com/x/space/myinfo')
        return response.json()

    def login(self, persistence_path, user_cookie):
        self.persistence_path = user_cookie
        if os.path.isfile(user_cookie):
            print('使用持久化内容上传')
            self.load()
        if self.cookies:
            try:
                self.login_by_cookies(self.cookies)
            except:
                logger.exception('login error')
                self.login_by_password(**self.account)
        else:
            self.login_by_password(**self.account)
        self.store()

    def load(self):
        try:
            with open(self.persistence_path) as f:
                self.cookies = json.load(f)

                self.access_token = self.cookies['token_info']['access_token']
        except (JSONDecodeError, KeyError):
            logger.exception('加载cookie出错')

    def store(self):
        with open(self.persistence_path, "w") as f:
            json.dump({**self.cookies,
                       'access_token': self.access_token,
                       'refresh_token': self.refresh_token
                       }, f)

    def send_sms(self, phone_number, country_code):
        params = {
            "actionKey": "appkey",
            "appkey": "783bbb7264451d82",
            "build": 6510400,
            "channel": "bili",
            "cid": country_code,
            "device": "phone",
            "mobi_app": "android",
            "platform": "android",
            "tel": phone_number,
            "ts": int(time.time()),
        }
        sign = hashlib.md5(f"{urllib.parse.urlencode(params)}2653583c8873dea268ab9386918b1d65".encode()).hexdigest()
        payload = f"{urllib.parse.urlencode(params)}&sign={sign}"
        response = self.__session.post("https://passport.bilibili.com/x/passport-login/sms/send", data=payload,
                                       timeout=5)
        return response.json()

    def login_by_sms(self, code, params):
        params["code"] = code
        params["sign"] = hashlib.md5(
            f"{urllib.parse.urlencode(params)}59b43e04ad6965f34319062b478f83dd".encode()).hexdigest()
        response = self.__session.post("https://passport.bilibili.com/x/passport-login/login/sms", data=params,
                                       timeout=5)
        r = response.json()
        if r and r["code"] == 0:
            return r

    def login_by_password(self, username, password):
        print('使用账号上传')
        key_hash, pub_key = self.get_key()
        encrypt_password = base64.b64encode(rsa.encrypt(f'{key_hash}{password}'.encode(), pub_key))
        payload = {
            "actionKey": 'appkey',
            "appkey": self.app_key,
            "build": 6270200,
            "captcha": '',
            "challenge": '',
            "channel": 'bili',
            "device": 'phone',
            "mobi_app": 'android',
            "password": encrypt_password,
            "permission": 'ALL',
            "platform": 'android',
            "seccode": "",
            "subid": 1,
            "ts": int(time.time()),
            "username": username,
            "validate": "",
        }
        response = self.__session.post("https://passport.bilibili.com/x/passport-login/oauth2/login", timeout=5,
                                       data={**payload, 'sign': self.sign(parse.urlencode(payload))})
        r = response.json()
        if r['code'] != 0 or r.get('data') is None or r['data'].get('cookie_info') is None:
            raise RuntimeError(r)
        try:
            for cookie in r['data']['cookie_info']['cookies']:
                self.__session.cookies.set(cookie['name'], cookie['value'])
                if 'bili_jct' == cookie['name']:
                    self.__bili_jct = cookie['value']
            self.cookies = self.__session.cookies.get_dict()
            self.access_token = r['data']['token_info']['access_token']
            self.refresh_token = r['data']['token_info']['refresh_token']
        except:
            raise RuntimeError(r)
        return r

    def login_by_cookies(self, cookie):
        print('使用cookies上传')
        cookies_dict = {c['name']: c['value'] for c in cookie['cookie_info']['cookies']}

        requests.utils.add_dict_to_cookiejar(self.__session.cookies, cookies_dict)
        if 'bili_jct' in cookie:
            self.__bili_jct = cookie["bili_jct"]
        data = self.__session.get("https://api.bilibili.com/x/web-interface/nav", timeout=5).json()
        if data["code"] != 0:
            raise Exception(data)

    def sign(self, param):
        return hashlib.md5(f"{param}{self.appsec}".encode()).hexdigest()

    def get_key(self):
        url = "https://passport.bilibili.com/x/passport-login/web/key"
        payload = {
            'appkey': f'{self.app_key}',
            'sign': self.sign(f"appkey={self.app_key}"),
        }
        response = self.__session.get(url, data=payload, timeout=5)
        r = response.json()
        if r and r["code"] == 0:
            return r['data']['hash'], rsa.PublicKey.load_pkcs1_openssl_pem(r['data']['key'].encode())

    def probe(self):
        ret = self.__session.get('https://member.bilibili.com/preupload?r=probe', timeout=5).json()
        logger.info(f"线路:{ret['lines']}")
        data, auto_os = None, None
        min_cost = 0
        if ret['probe'].get('get'):
            method = 'get'
        else:
            method = 'post'
            data = bytes(int(1024 * 0.1 * 1024))
        for line in ret['lines']:
            start = time.perf_counter()
            test = self.__session.request(method, f"https:{line['probe_url']}", data=data, timeout=30)
            cost = time.perf_counter() - start
            print(line['query'], cost)
            if test.status_code != 200:
                return
            if not min_cost or min_cost > cost:
                auto_os = line
                min_cost = cost
        auto_os['cost'] = min_cost
        return auto_os

    def upload_file(self, filepath: str, lines='AUTO', tasks=3):
        """上传本地视频文件,返回视频信息dict
        b站目前支持4种上传线路upos, kodo, gcs, bos
        gcs: {"os":"gcs","query":"bucket=bvcupcdngcsus&probe_version=20221109",
        "probe_url":"//storage.googleapis.com/bvcupcdngcsus/OK"},
        bos: {"os":"bos","query":"bucket=bvcupcdnboshb&probe_version=20221109",
        "probe_url":"??"}
        """
        preferred_upos_cdn = None
        if not self._auto_os:
            if lines == 'bda':
                self._auto_os = {"os": "upos", "query": "upcdn=bda&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdnbda.bilivideo.com/OK"}
                preferred_upos_cdn = 'bda'
            elif lines in {'bda2', 'cs-bda2'}:
                self._auto_os = {"os": "upos", "query": "upcdn=bda2&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdnbda2.bilivideo.com/OK"}
                preferred_upos_cdn = 'bda2'
            elif lines == 'ws':
                self._auto_os = {"os": "upos", "query": "upcdn=ws&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdnws.bilivideo.com/OK"}
                preferred_upos_cdn = 'ws'
            elif lines in {'qn', 'cs-qn'}:
                self._auto_os = {"os": "upos", "query": "upcdn=qn&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdnqn.bilivideo.com/OK"}
                preferred_upos_cdn = 'qn'
            elif lines == 'bldsa':
                self._auto_os = {"os": "upos", "query": "upcdn=bldsa&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdnbldsa.bilivideo.com/OK"}
                preferred_upos_cdn = 'bldsa'
            elif lines == 'tx':
                self._auto_os = {"os": "upos", "query": "upcdn=tx&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdntx.bilivideo.com/OK"}
                preferred_upos_cdn = 'tx'
            elif lines == 'txa':
                self._auto_os = {"os": "upos", "query": "upcdn=txa&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdntxa.bilivideo.com/OK"}
                preferred_upos_cdn = 'txa'
            else:
                self._auto_os = self.probe()
            logger.info(f"线路选择 => {self._auto_os['os']}: {self._auto_os['query']}. time: {self._auto_os.get('cost')}")
        if self._auto_os['os'] == 'upos':
            upload = self.upos
        # elif self._auto_os['os'] == 'cos':
        #     upload = self.cos
        # elif self._auto_os['os'] == 'cos-internal':
        #     upload = lambda *args, **kwargs: self.cos(*args, **kwargs, internal=True)
        # elif self._auto_os['os'] == 'kodo':
        #     upload = self.kodo
        else:
            logger.error(f"NoSearch:{self._auto_os['os']}")
            raise NotImplementedError(self._auto_os['os'])
        logger.info(f"os: {self._auto_os['os']}")
        total_size = os.path.getsize(filepath)
        with open(filepath, 'rb') as f:
            query = {
                'r': self._auto_os['os'] if self._auto_os['os'] != 'cos-internal' else 'cos',
                'profile': 'ugcupos/bup' if 'upos' == self._auto_os['os'] else "ugcupos/bupfetch",
                'ssl': 0,
                'version': '2.8.12',
                'build': 2081200,
                'name': f.name,
                'size': total_size,
            }
            resp = self.__session.get(
                f"https://member.bilibili.com/preupload?{self._auto_os['query']}", params=query,
                timeout=5)
            ret = resp.json()
            logger.debug(f"preupload: {ret}")
            if preferred_upos_cdn:
                original_endpoint: str = ret['endpoint']
                if re.match(r'//upos-(sz|cs)-upcdn(bda2|ws|qn)\.bilivideo\.com', original_endpoint):
                    if re.match(r'bda2|qn|ws', preferred_upos_cdn):
                        logger.debug(f"Preferred UpOS CDN: {preferred_upos_cdn}")
                        new_endpoint = re.sub(r'upcdn(bda2|qn|ws)', f'upcdn{preferred_upos_cdn}', original_endpoint)
                        logger.debug(f"{original_endpoint} => {new_endpoint}")
                        ret['endpoint'] = new_endpoint
                    else:
                        logger.error(f"Unrecognized preferred_upos_cdn: {preferred_upos_cdn}")
                else:
                    logger.warning(
                        f"Assigned UpOS endpoint {original_endpoint} was never seen before, something else might have changed, so will not modify it")
            return asyncio.run(upload(f, total_size, ret, tasks=tasks))

    def upload_stream(self, stream_queue: queue.SimpleQueue, file_name, total_size, lines='AUTO', videos: 'Data' = None, stop_event: threading.Event = None, file_name_callback: Callable[[str], None] = None):

        logger.info(f"{file_name} 开始上传")
        if self.save_dir:
            self.save_path = os.path.join(self.save_dir, file_name)
        preferred_upos_cdn = None
        if not self._auto_os:
            if lines == 'bda':
                self._auto_os = {"os": "upos", "query": "upcdn=bda&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdnbda.bilivideo.com/OK"}
                preferred_upos_cdn = 'bda'
            elif lines in {'bda2', 'cs-bda2'}:
                self._auto_os = {"os": "upos", "query": "upcdn=bda2&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdnbda2.bilivideo.com/OK"}
                preferred_upos_cdn = 'bda2'
            elif lines == 'ws':
                self._auto_os = {"os": "upos", "query": "upcdn=ws&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdnws.bilivideo.com/OK"}
                preferred_upos_cdn = 'ws'
            elif lines in {'qn', 'cs-qn'}:
                self._auto_os = {"os": "upos", "query": "upcdn=qn&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdnqn.bilivideo.com/OK"}
                preferred_upos_cdn = 'qn'
            elif lines == 'bldsa':
                self._auto_os = {"os": "upos", "query": "upcdn=bldsa&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdnbldsa.bilivideo.com/OK"}
                preferred_upos_cdn = 'bldsa'
            elif lines == 'tx':
                self._auto_os = {"os": "upos", "query": "upcdn=tx&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdntx.bilivideo.com/OK"}
                preferred_upos_cdn = 'tx'
            elif lines == 'txa':
                self._auto_os = {"os": "upos", "query": "upcdn=txa&probe_version=20221109",
                                 "probe_url": "//upos-cs-upcdntxa.bilivideo.com/OK"}
                preferred_upos_cdn = 'txa'
            else:
                self._auto_os = self.probe()
            logger.info(f"线路选择 => {self._auto_os['os']}: {self._auto_os['query']}. time: {self._auto_os.get('cost')}")
        if self._auto_os['os'] == 'upos':
            upload = self.upos_stream
        else:
            logger.error(f"NoSearch:{self._auto_os['os']}")
            raise NotImplementedError(self._auto_os['os'])
        logger.info(f"os: {self._auto_os['os']}")
        query = {
            'r': self._auto_os['os'] if self._auto_os['os'] != 'cos-internal' else 'cos',
            'profile': 'ugcupos/bup' if 'upos' == self._auto_os['os'] else "ugcupos/bupfetch",
            'ssl': 0,
            'version': '2.8.12',
            'build': 2081200,
            'name': file_name,
            'size': total_size,
        }
        resp = self.__session.get(
            f"https://member.bilibili.com/preupload?{self._auto_os['query']}", params=query,
            timeout=5)
        ret = resp.json()
        logger.debug(f"preupload: {ret}")
        if preferred_upos_cdn:
            original_endpoint: str = ret['endpoint']
            if re.match(r'//upos-(sz|cs)-upcdn(bda2|ws|qn)\.bilivideo\.com', original_endpoint):
                if re.match(r'bda2|qn|ws', preferred_upos_cdn):
                    logger.debug(f"Preferred UpOS CDN: {preferred_upos_cdn}")
                    new_endpoint = re.sub(r'upcdn(bda2|qn|ws)', f'upcdn{preferred_upos_cdn}', original_endpoint)
                    logger.debug(f"{original_endpoint} => {new_endpoint}")
                    ret['endpoint'] = new_endpoint
                else:
                    logger.error(f"Unrecognized preferred_upos_cdn: {preferred_upos_cdn}")
            else:
                logger.warning(
                    f"Assigned UpOS endpoint {original_endpoint} was never seen before, something else might have changed, so will not modify it")
        video_part = asyncio.run(upload(stream_queue, file_name, total_size, ret))
        if not video_part:
            stop_event.set()
            # print("异常流 直接退出")
            return
        video_part['title'] = video_part['title'][:80]
        videos.append(video_part)  # 添加已经上传的视频

        edit = False if videos.aid is None else True
        ret = self.submit(None, edit=edit)
        # logger.info(f"上传成功: {ret}")
        if edit:
            logger.info(f"编辑添加成功: {ret}")
        else:
            logger.info(f"上传成功: {ret}")
        aid = ret['data']['aid']
        videos.aid = aid

        if file_name_callback:
            file_name_callback(self.save_path)

    async def cos(self, file, total_size, ret, chunk_size=10485760, tasks=3, internal=False):
        filename = file.name
        url = ret["url"]
        if internal:
            url = url.replace("cos.accelerate", "cos-internal.ap-shanghai")
        biz_id = ret["biz_id"]
        post_headers = {
            "Authorization": ret["post_auth"],
        }
        put_headers = {
            "Authorization": ret["put_auth"],
        }

        initiate_multipart_upload_result = ET.fromstring(self.__session.post(f'{url}?uploads&output=json', timeout=5,
                                                                             headers=post_headers).content)
        upload_id = initiate_multipart_upload_result.find('UploadId').text
        # 开始上传
        parts = []  # 分块信息
        chunks = math.ceil(total_size / chunk_size)  # 获取分块数量

        async def upload_chunk(session, chunks_data, params):
            async with session.put(url, params=params, raise_for_status=True,
                                   data=chunks_data, headers=put_headers) as r:
                end = time.perf_counter() - start
                parts.append({"Part": {"PartNumber": params['chunk'] + 1, "ETag": r.headers['Etag']}})
                sys.stdout.write(f"\r{params['end'] / 1000 / 1000 / end:.2f}MB/s "
                                 f"=> {params['partNumber'] / chunks:.1%}")

        start = time.perf_counter()
        await self._upload({
            'uploadId': upload_id,
            'chunks': chunks,
            'total': total_size
        }, file, chunk_size, upload_chunk, tasks=tasks)
        cost = time.perf_counter() - start
        fetch_headers = {
            "X-Upos-Fetch-Source": ret["fetch_headers"]["X-Upos-Fetch-Source"],
            "X-Upos-Auth": ret["fetch_headers"]["X-Upos-Auth"],
            "Fetch-Header-Authorization": ret["fetch_headers"]["Fetch-Header-Authorization"]
        }
        parts = sorted(parts, key=lambda x: x['Part']['PartNumber'])
        complete_multipart_upload = ET.Element('CompleteMultipartUpload')
        for part in parts:
            part_et = ET.SubElement(complete_multipart_upload, 'Part')
            part_number = ET.SubElement(part_et, 'PartNumber')
            part_number.text = str(part['Part']['PartNumber'])
            e_tag = ET.SubElement(part_et, 'ETag')
            e_tag.text = part['Part']['ETag']
        xml = ET.tostring(complete_multipart_upload)
        ii = 0
        while ii <= 3:
            try:
                res = self.__session.post(url, params={'uploadId': upload_id}, data=xml, headers=post_headers,
                                          timeout=15)
                if res.status_code == 200:
                    break
                raise IOError(res.text)
            except IOError:
                ii += 1
                logger.info("请求合并分片出现问题，尝试重连，次数：" + str(ii))
                time.sleep(15)
        ii = 0
        while ii <= 3:
            try:
                res = self.__session.post("https:" + ret["fetch_url"], headers=fetch_headers, timeout=15).json()
                if res.get('OK') == 1:
                    logger.info(f'{filename} uploaded >> {total_size / 1000 / 1000 / cost:.2f}MB/s. {res}')
                    return {"title": splitext(filename)[0], "filename": ret["bili_filename"], "desc": ""}
                raise IOError(res)
            except IOError:
                ii += 1
                logger.info("上传出现问题，尝试重连，次数：" + str(ii))
                time.sleep(15)

    async def kodo(self, file, total_size, ret, chunk_size=4194304, tasks=3):
        filename = file.name
        bili_filename = ret['bili_filename']
        key = ret['key']
        endpoint = f"https:{ret['endpoint']}"
        token = ret['uptoken']
        fetch_url = ret['fetch_url']
        fetch_headers = ret['fetch_headers']
        url = f'{endpoint}/mkblk'
        headers = {
            'Authorization': f"UpToken {token}",
        }
        # 开始上传
        parts = []  # 分块信息
        chunks = math.ceil(total_size / chunk_size)  # 获取分块数量

        async def upload_chunk(session, chunks_data, params):
            async with session.post(f'{url}/{len(chunks_data)}',
                                    data=chunks_data, headers=headers) as response:
                end = time.perf_counter() - start
                ctx = await response.json()
                parts.append({"index": params['chunk'], "ctx": ctx['ctx']})
                sys.stdout.write(f"\r{params['end'] / 1000 / 1000 / end:.2f}MB/s "
                                 f"=> {params['partNumber'] / chunks:.1%}")

        start = time.perf_counter()
        await self._upload({}, file, chunk_size, upload_chunk, tasks=tasks)
        cost = time.perf_counter() - start

        logger.info(f'{filename} uploaded >> {total_size / 1000 / 1000 / cost:.2f}MB/s')
        parts.sort(key=lambda x: x['index'])
        self.__session.post(f"{endpoint}/mkfile/{total_size}/key/{base64.urlsafe_b64encode(key.encode()).decode()}",
                            data=','.join(map(lambda x: x['ctx'], parts)), headers=headers, timeout=10)
        r = self.__session.post(f"https:{fetch_url}", headers=fetch_headers, timeout=5).json()
        if r["OK"] != 1:
            raise Exception(r)
        return {"title": splitext(filename)[0], "filename": bili_filename, "desc": ""}

    async def upos(self, file, total_size, ret, tasks=3):
        filename = file.name
        chunk_size = ret['chunk_size']
        auth = ret["auth"]
        endpoint = ret["endpoint"]
        biz_id = ret["biz_id"]
        upos_uri = ret["upos_uri"]
        url = f"https:{endpoint}/{upos_uri.replace('upos://', '')}"  # 视频上传路径
        headers = {
            "X-Upos-Auth": auth
        }
        # 向上传地址申请上传，得到上传id等信息
        upload_id = self.__session.post(f'{url}?uploads&output=json', timeout=15,
                                        headers=headers).json()["upload_id"]
        # 开始上传
        parts = []  # 分块信息
        chunks = math.ceil(total_size / chunk_size)  # 获取分块数量

        async def upload_chunk(session, chunks_data, params):
            async with session.put(url, params=params, raise_for_status=True,
                                   data=chunks_data, headers=headers):
                end = time.perf_counter() - start
                parts.append({"partNumber": params['chunk'] + 1, "eTag": "etag"})
                sys.stdout.write(f"\r{params['end'] / 1000 / 1000 / end:.2f}MB/s "
                                 f"=> {params['partNumber'] / chunks:.1%}")

        start = time.perf_counter()
        await self._upload({
            'uploadId': upload_id,
            'chunks': chunks,
            'total': total_size
        }, file, chunk_size, upload_chunk, tasks=tasks)
        cost = time.perf_counter() - start
        p = {
            'name': filename,
            'uploadId': upload_id,
            'biz_id': biz_id,
            'output': 'json',
            'profile': 'ugcupos/bup'
        }
        attempt = 0
        while attempt <= 5:  # 一旦放弃就会丢失前面所有的进度，多试几次吧
            try:
                r = self.__session.post(url, params=p, json={"parts": parts}, headers=headers, timeout=15).json()
                if r.get('OK') == 1:
                    logger.info(f'{filename} uploaded >> {total_size / 1000 / 1000 / cost:.2f}MB/s. {r}')
                    return {"title": splitext(filename)[0], "filename": splitext(basename(upos_uri))[0], "desc": ""}
                raise IOError(r)
            except IOError:
                attempt += 1
                logger.info(f"请求合并分片时出现问题，尝试重连，次数：" + str(attempt))
                time.sleep(15)

    async def upos_stream(self, stream_queue, file_name, total_size, ret):
        # print("--------------, ", file_name)
        chunk_size = ret['chunk_size']
        auth = ret["auth"]
        endpoint = ret["endpoint"]
        biz_id = ret["biz_id"]
        upos_uri = ret["upos_uri"]
        url = f"https:{endpoint}/{upos_uri.replace('upos://', '')}"  # 视频上传路径
        headers = {
            "X-Upos-Auth": auth
        }
        # 向上传地址申请上传，得到上传id等信息
        upload_id = self.__session.post(f'{url}?uploads&output=json', timeout=15,
                                        headers=headers).json()["upload_id"]
        # 开始上传
        parts = []  # 分块信息
        chunks = math.ceil(total_size / chunk_size)  # 获取分块数量

        start = time.perf_counter()

        # print("-----------")
        # print(upload_id, chunks, chunk_size, total_size)
        logger.info(
            f"{file_name} - upload_id: {upload_id}, chunks: {chunks}, chunk_size: {chunk_size}, total_size: {total_size}")
        n = 0
        st = time.perf_counter()
        max_workers = 3
        semaphore = threading.Semaphore(max_workers)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for index, chunk in enumerate(self.queue_reader_generator(stream_queue, chunk_size, total_size)):
                if not chunk:
                    break
                const_time = time.perf_counter() - st
                speed = len(chunk) * 8 / 1024 / 1024 / const_time
                logger.info(f"{file_name} - chunks-({index+1}/{chunks}) - down - speed: {speed:.2f}Mbps")
                n += len(chunk)
                params = {
                    'uploadId': upload_id,
                    'chunks': chunks,
                    'total': total_size,
                    'chunk': index,
                    'size': chunk_size,
                    'partNumber': index + 1,
                    'start': index * chunk_size,
                    'end': index * chunk_size + chunk_size
                }
                params_clone = params.copy()
                semaphore.acquire()
                future = executor.submit(self.upload_chunk_thread,
                                         url, chunk, params_clone, headers, file_name)
                future.add_done_callback(lambda x: semaphore.release())
                futures.append(future)
                st = time.perf_counter()

                for f in list(futures):
                    if f.done():
                        futures.remove(f)

                # 等待所有分片上传完成，并按顺序收集结果
            for future in concurrent.futures.as_completed(futures):
                pass

            results = [{
                "partNumber": i + 1,
                "eTag": "etag"
            } for i in range(chunks)]
            parts.extend(results)

        if len(parts) == 0:
            return None
        logger.info(f"{file_name} - total_size: {total_size}, n: {n}")
        cost = time.perf_counter() - start
        p = {
            'name': file_name,
            'uploadId': upload_id,
            'biz_id': biz_id,
            'output': 'json',
            'profile': 'ugcupos/bup'
        }
        attempt = 0
        while attempt <= 5:  # 一旦放弃就会丢失前面所有的进度，多试几次吧
            try:
                r = self.__session.post(url, params=p, json={"parts": parts}, headers=headers, timeout=15).json()
                if r.get('OK') == 1:
                    logger.info(f'{file_name} uploaded >> {total_size / 1000 / 1000 / cost:.2f}MB/s. {r}')
                    return {"title": splitext(file_name)[0], "filename": splitext(basename(upos_uri))[0], "desc": ""}
                raise IOError(r)
            except IOError:
                attempt += 1
                logger.info(f"请求合并分片时出现问题，尝试重连，次数：" + str(attempt))
                time.sleep(15)
        pass

    def upload_chunk_thread(self, url, chunk, params_clone, headers, file_name, max_retries=3, backoff_factor=1):
        st = time.perf_counter()
        retries = 0
        while retries < max_retries:
            try:
                r = requests.put(url=url, params=params_clone, data=chunk, headers=headers)

                # 如果上传成功，退出重试循环
                if r.status_code == 200:
                    const_time = time.perf_counter() - st
                    speed = len(chunk) * 8 / 1024 / 1024 / const_time
                    logger.info(
                        f"{file_name} - chunks-{params_clone['chunk'] +1 } - up status: {r.status_code} - speed: {speed:.2f}Mbps"
                    )
                    return {
                        "partNumber": params_clone['chunk'] + 1,
                        "eTag": "etag"
                    }

                # 如果上传失败，但未达到最大重试次数，等待一段时间后重试
                else:
                    retries += 1
                    logger.warning(
                        f"{file_name} - chunks-{params_clone['chunk']} - up failed: {r.status_code}. Retrying {retries}/{max_retries}")

                    # 计算退避时间，逐步增加重试间隔
                    backoff_time = backoff_factor ** retries
                    time.sleep(backoff_time)

            except Exception as e:
                retries += 1
                logger.error(f"upload_chunk_thread err {str(e)}. Retrying {retries}/{max_retries}")

                # 计算退避时间，逐步增加重试间隔
                backoff_time = backoff_factor ** retries
                time.sleep(backoff_time)

        # 如果重试了所有次数仍然失败，记录错误
        logger.error(f"{file_name} - chunks-{params_clone['chunk']} - Upload failed after {max_retries} attempts.")
        return None

    async def upload_chunk(self, session: aiohttp.ClientSession, url: str, chunk: bytes, params: Dict, headers: Dict, file_name: str) -> Dict:
        """
        上传单个分片的协程函数
        """

        try:
            st = time.perf_counter()
            # print(4321)
            await asyncio.sleep(0)
            # asyncio.set_event_loop(asyncio.new_event_loop())
            # async with aiohttp.ClientSession() as session:
            async with session.put(url, params=params, raise_for_status=True, data=chunk, headers=headers) as r:
                # r = await session.put(url, params=params, data=chunk, headers=headers)
                # print(1234, r.status)

                const_time = time.perf_counter() - st
                speed = len(chunk) * 8 / 1024 / 1024 / const_time
                logger.info(
                    f"{file_name} - chunks-{params['chunk']} - up status: {r.status} - speed: {speed:.2f}Mbps")
                # et =
                return {
                    "partNumber": params['chunk'] + 1,
                    "eTag": "etag"
                }

        except Exception as e:
            import traceback
            print(traceback.print_exc())
            logger.info(f"{file_name} - chunks-{params['chunk']} upload failed: {str(e)}")
            raise e

    async def _upload_chunk_with_semaphore(self, semaphore: asyncio.Semaphore,
                                           session: aiohttp.ClientSession,
                                           url: str,
                                           chunk: bytes,
                                           params: Dict,
                                           headers: Dict,
                                           file_name: str,) -> Dict:
        """
        使用信号量控制的分片上传
        """
        print(123321)
        # return
        async with semaphore:
            return await self.upload_chunk(session, url, chunk, params, headers, file_name)

    async def async_queue_reader(slef, simple_queue, max_bytes, total_size):
        """
        将 queue.SimpleQueue 封装为异步生成器。
        每次从队列中获取数据，直到队列为空且收到结束信号（None）。
        如果读取的数据总字节数达到 max_bytes，则结束本次生成。

        :param simple_queue: queue.SimpleQueue 实例
        :param max_bytes: 每次最大读取的字节数
        """
        total_bytes = 0
        while True:
            # 从队列中获取数据
            data = simple_queue.get()
            # 如果接收到 None，则表示结束
            if data is None:
                # 填充 0x00 直到达到 total_size
                while total_bytes < total_size:
                    chunk_size = min(total_size - total_bytes, max_bytes)
                    total_bytes += chunk_size
                    yield b"\x00" * chunk_size
                break

            total_bytes += len(data)
            yield data

            # 如果达到最大字节数限制，结束本次生成
            if total_bytes >= max_bytes:
                break

    def queue_reader(slef, simple_queue, max_bytes, total_size):
        """
        将 queue.SimpleQueue 封装为异步生成器。
        每次从队列中获取数据，直到队列为空且收到结束信号（None）。
        如果读取的数据总字节数达到 max_bytes，则结束本次生成。

        :param simple_queue: queue.SimpleQueue 实例
        :param max_bytes: 每次最大读取的字节数
        """

        total_bytes = 0
        result = []
        while True:
            # 从队列中获取数据
            # print(simple_queue.empty())
            data = simple_queue.get()
            # 如果接收到 None，则表示结束
            if data is None:
                # 填充 0x00 直到达到 total_size
                while total_bytes < total_size:
                    logger.debug(f"视频数据已经获取完成,需要补 {total_size-total_bytes} 个 0x00")
                    chunk_size = total_size - total_bytes
                    total_bytes += chunk_size
                    # yield b"\x00" * chunk_size
                    result.append(b"\x00" * chunk_size)
                break

            # print(len(data))
            total_bytes += len(data)
            result.append(data)
            # 如果达到最大字节数限制，结束本次生成
            if total_bytes == total_size:
                break
        return b"".join(result)

    async def queue_reader_generator_async(self,
                                           simple_queue: queue.SimpleQueue,
                                           chunk_size: int,
                                           max_size: int):
        """
        异步版本的队列读取生成器
        """
        for index, chunk in enumerate(self.queue_reader_generator(simple_queue, chunk_size, max_size)):
            yield index, chunk

    def queue_reader_generator(self, simple_queue: queue.SimpleQueue, chunk_size: int, max_size: int):
        """
        从 simple_queue 中读取数据并按 chunk_size 大小分块产出 (yield)
        当队列中获取到 None 或者数据总量达到 max_size 后，就用 0x00 补齐到 chunk_size

        :param simple_queue: queue.SimpleQueue 实例，数据流会以多个分包 (bytes) 入队，最后以 None 表示结束
        :param chunk_size: 消费者每次想要获取的数据块大小
        :param max_size: 需要最终补齐的总大小（单位：字节），必须是 chunk_size 的整数倍
        :return: 生成器，按 chunk_size 大小分批次产出数据
        """
        if max_size % chunk_size != 0:
            raise ValueError("max_size must be a multiple of chunk_size")

        total_chunks = max_size // chunk_size
        chunks_yielded = 0
        current_buffer = bytearray()
        save_file = None
        if self.save_dir:
            save_file = open(self.save_path, "wb")

        while chunks_yielded < total_chunks:
            try:
                data = simple_queue.get(timeout=10)
            except queue.Empty:
                break

            if data is None:
                # 数据流结束，用0x00填充剩余的块
                remaining_chunks = total_chunks - chunks_yielded
                if remaining_chunks == total_chunks:
                    # print("空包跳过")
                    break
                if len(current_buffer) > 0:
                    # 处理当前缓冲区中的最后一块数据
                    padding_size = chunk_size - len(current_buffer)
                    if padding_size > 0:
                        current_buffer += b'\x00' * padding_size
                        logger.info(f"最后一个包差了 {padding_size} 个字节")
                    yield bytes(current_buffer)
                    chunks_yielded += 1
                    remaining_chunks -= 1
                break
                logger.info(f"还差 {remaining_chunks} 个完整包")

                # 输出剩余的全0块
                for _ in range(remaining_chunks):
                    yield b'\x00' * chunk_size
                    chunks_yielded += 1
                break

            save_file and save_file.write(data)

            # 将新数据添加到缓冲区
            current_buffer.extend(data)

            # 输出完整的块
            while len(current_buffer) >= chunk_size and chunks_yielded < total_chunks:
                yield bytes(current_buffer[:chunk_size])
                current_buffer = current_buffer[chunk_size:]
                chunks_yielded += 1

        # print("本段分p完成")
        save_file and save_file.close()
        yield None

    @staticmethod
    async def _upload(params, file, chunk_size, afunc, tasks=3):
        params['chunk'] = -1

        async def upload_chunk():
            while True:
                chunks_data = file.read(chunk_size)
                if not chunks_data:
                    return
                params['chunk'] += 1
                params['size'] = len(chunks_data)
                params['partNumber'] = params['chunk'] + 1
                params['start'] = params['chunk'] * chunk_size
                params['end'] = params['start'] + params['size']
                clone = params.copy()
                for i in range(10):
                    try:
                        await afunc(session, chunks_data, clone)
                        break
                    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                        logger.error(f"retry chunk{clone['chunk']} >> {i + 1}. {e}")

        async with aiohttp.ClientSession() as session:
            await asyncio.gather(*[upload_chunk() for _ in range(tasks)])

    def submit(self, submit_api=None, edit=False):
        if not self.video.title:
            self.video.title = self.video.videos[0]["title"]
        self.__session.get('https://member.bilibili.com/x/geetest/pre/add', timeout=5)

        if submit_api is None:
            total_info = self.__session.get('http://api.bilibili.com/x/space/myinfo', timeout=15).json()
            if total_info.get('data') is None:
                logger.error(total_info)
            total_info = total_info.get('data')
            if total_info['level'] > 3 and total_info['follower'] > 1000:
                user_weight = 2
            else:
                user_weight = 1
            logger.info(f'用户权重: {user_weight}')
            submit_api = 'web' if user_weight == 2 else 'client'
        ret = None
        if submit_api == 'web':
            ret = self.submit_web(edit=edit)
            if ret["code"] == 21138:
                logger.info(f'改用客户端接口提交{ret}')
                submit_api = 'client'
        if submit_api == 'client':
            ret = self.submit_client(edit=edit)
        if not ret:
            raise Exception(f'不存在的选项：{submit_api}')
        if ret["code"] == 0:
            return ret
        else:
            raise Exception(ret)

    def submit_web(self, edit=False):
        logger.info('使用网页端api提交')
        api = 'https://member.bilibili.com/x/vu/web/add?csrf=' + self.__bili_jct
        if edit:
            api = 'https://member.bilibili.com/x/vu/web/edit?csrf=' + self.__bili_jct
        return self.__session.post(api, timeout=5,
                                   json=asdict(self.video)).json()

    def submit_client(self, edit=False):
        logger.info('使用客户端api端提交')
        if not self.access_token:
            if self.account is None:
                raise RuntimeError("Access token is required, but account and access_token does not exist!")
            self.login_by_password(**self.account)
            self.store()
        api = 'http://member.bilibili.com/x/vu/client/add?access_key=' + self.access_token
        if edit:
            api = 'http://member.bilibili.com/x/vu/client/edit?access_key=' + self.access_token
        print(asdict(self.video))
        while True:
            ret = self.__session.post(api, timeout=5, json=asdict(self.video)).json()
            if ret['code'] == -101:
                logger.info(f'刷新token{ret}')
                self.login_by_password(**config['user']['account'])
                self.store()
                continue
            return ret

    def cover_up(self, img: str):
        """
        :param img: img path or stream
        :return: img URL
        """
        from PIL import Image
        from io import BytesIO

        with Image.open(img) as im:
            # 宽和高,需要16：10
            xsize, ysize = im.size
            if xsize / ysize > 1.6:
                delta = xsize - ysize * 1.6
                region = im.crop((delta / 2, 0, xsize - delta / 2, ysize))
            else:
                delta = ysize - xsize * 10 / 16
                region = im.crop((0, delta / 2, xsize, ysize - delta / 2))
            buffered = BytesIO()
            region.save(buffered, format=im.format)
        r = self.__session.post(
            url='https://member.bilibili.com/x/vu/web/cover/up',
            data={
                'cover': b'data:image/jpeg;base64,' + (base64.b64encode(buffered.getvalue())),
                'csrf': self.__bili_jct
            }, timeout=30
        )
        buffered.close()
        res = r.json()
        if res.get('data') is None:
            raise Exception(res)
        return res['data']['url']

    def get_tags(self, upvideo, typeid="", desc="", cover="", groupid=1, vfea=""):
        """
        上传视频后获得推荐标签
        :param vfea:
        :param groupid:
        :param typeid:
        :param desc:
        :param cover:
        :param upvideo:
        :return: 返回官方推荐的tag
        """
        url = f'https://member.bilibili.com/x/web/archive/tags?' \
              f'typeid={typeid}&title={quote(upvideo["title"])}&filename=filename&desc={desc}&cover={cover}' \
              f'&groupid={groupid}&vfea={vfea}'
        return self.__session.get(url=url, timeout=5).json()

    def __enter__(self):
        return self

    def __exit__(self, e_t, e_v, t_b):
        self.close()

    def close(self):
        """Closes all adapters and as such the session"""
        self.__session.close()


@dataclass
class Data:
    """
    cover: 封面图片，可由recovers方法得到视频的帧截图
    """
    copyright: int = 2
    source: str = ''
    tid: int = 21
    cover: str = ''
    title: str = ''
    desc_format_id: int = 0
    desc: str = ''
    desc_v2: list = field(default_factory=list)
    dynamic: str = ''
    subtitle: dict = field(init=False)
    tag: Union[list, str] = ''
    videos: list = field(default_factory=list)
    dtime: Any = None
    open_subtitle: InitVar[bool] = False
    dolby: int = 0
    hires: int = 0
    no_reprint = 0
    open_elec = 0
    extra_fields = ""

    aid: int = None
    # interactive: int = 0
    # no_reprint: int 1
    # open_elec: int 1

    def __post_init__(self, open_subtitle):
        self.subtitle = {"open": int(open_subtitle), "lan": ""}
        if self.dtime and self.dtime - int(time.time()) <= 14400:
            self.dtime = None
        if isinstance(self.tag, list):
            self.tag = ','.join(self.tag)

    def delay_time(self, dtime: int):
        """设置延时发布时间，距离提交大于2小时，格式为10位时间戳"""
        if dtime - int(time.time()) > 7200:
            self.dtime = dtime

    def set_tag(self, tag: list):
        """设置标签，tag为数组"""
        self.tag = ','.join(tag)

    def append(self, video):
        self.videos.append(video)
