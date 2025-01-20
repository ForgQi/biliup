import time
import json
import re
import asyncio

from biliup.common.util import client
from biliup.config import config
from . import match1, logger
from biliup.Danmaku import DanmakuClient
from ..engine.decorators import Plugin
from ..engine.download import DownloadBase


OFFICIAL_API = "https://api.live.bilibili.com"
STREAM_NAME_REGEXP = r"/live-bvc/\d+/(live_[^/\.]+)"

@Plugin.download(regexp=r'(?:https?://)?(b23\.tv|live\.bilibili\.com)')
class Bililive(DownloadBase):
    def __init__(self, fname, url, suffix='flv'):
        super().__init__(fname, url, suffix)
        self.live_start_time = 0
        self.bilibili_danmaku = config.get('bilibili_danmaku', False)
        self.bilibili_danmaku_detail = config.get('bilibili_danmaku_detail', False)
        self.__real_room_id = None
        self.__login_mid = 0
        # self.fake_headers['referer'] = url
        if config.get('user', {}).get('bili_cookie'):
            self.fake_headers['cookie'] = config.get('user', {}).get('bili_cookie')
        if config.get('user', {}).get('bili_cookie_file'):
            cookie_file_name = config.get('user', {}).get('bili_cookie_file')
            try:
                with open(cookie_file_name, encoding='utf-8') as stream:
                    cookies = json.load(stream)["cookie_info"]["cookies"]
                    cookies_str = ''
                    for i in cookies:
                        cookies_str += f"{i['name']}={i['value']};"
                    self.fake_headers['cookie'] = cookies_str
            except Exception:
                logger.exception("load_cookies error")

    async def acheck_stream(self, is_check=False):

        if "b23.tv" in self.url:
            try:
                resp = await client.get(self.url, follow_redirects=False)
                if resp.status_code not in {301, 302}:
                    raise Exception("不支持的链接")
                url = str(resp.next_request.url)
                if "live.bilibili" not in url:
                    raise Exception("不支持的链接")
                self.url = url
            except Exception as e:
                logger.error(f"{self.plugin_msg}: {e}")
                return False

        room_id = match1(self.url, r'bilibili.com/(\d+)')
        quality_number = int(config.get('bili_qn', 10000))

        # 获取直播状态与房间标题
        info_by_room_url = f"{OFFICIAL_API}/xlive/web-room/v1/index/getInfoByRoom?room_id={room_id}"
        try:
            room_info = await client.get(info_by_room_url, headers=self.fake_headers)
            room_info = room_info.json()
        except Exception as e:
            logger.error(f"{self.plugin_msg}: {e}")
            return False
        if room_info['code'] != 0:
            logger.error(f"{self.plugin_msg}: {room_info}")
            return False
        else:
            room_info = room_info['data']
        if room_info['room_info']['live_status'] != 1:
            logger.debug(f"{self.plugin_msg}: 未开播")
            self.raw_stream_url = None
            return False

        self.live_cover_url = room_info['room_info']['cover']
        self.room_title = room_info['room_info']['title']
        self.__real_room_id = room_info['room_info']['room_id']
        live_start_time = room_info['room_info']['live_start_time']
        special_type = room_info['room_info']['special_type'] # 0: 公开直播, 1: 大航海专属
        if live_start_time > self.live_start_time:
            self.live_start_time = live_start_time
            is_new_live = True
        else:
            is_new_live = False

        if is_check:
            return True
        else:
            self.__login_mid = await self.check_login_status()

        # 复用原画 m3u8 流
        if  self.raw_stream_url is not None \
            and ".m3u8" in self.raw_stream_url \
            and quality_number >= 10000 \
            and not is_new_live:
            url = await self.acheck_url_healthy(self.raw_stream_url)
            if url is not None:
                logger.debug(f"{self.plugin_msg}: 复用 {url}")
                return True
            else:
                self.raw_stream_url = None

        protocol = config.get('bili_protocol', 'stream')
        perf_cdn = config.get('bili_perfCDN')
        perf_cdn = config.get('bili_cdn', perf_cdn.split(',') if isinstance(perf_cdn, str) else [])
        self.__hls_timeout = config.get('bili_hls_transcode_timeout', 60)

        normalize_url = lambda a: a if a.startswith(('http://', 'https://')) else 'http://' + a
        self.__api_list = [
            normalize_url(config.get('bili_liveapi', OFFICIAL_API)).rstrip('/'),
            normalize_url(config.get('bili_fallback_api', OFFICIAL_API)).rstrip('/'),
        ]

        stream_urls = await self.aget_stream(quality_number, protocol, special_type)
        if not stream_urls:
            if protocol == 'hls_fmp4':
                if int(time.time()) - live_start_time <= self.__hls_timeout:
                    logger.warning(f"{self.plugin_msg}: 暂未提供 hls_fmp4 流，等待下一次检测")
                    return False
                else:
                    # 回退首个可用格式
                    stream_urls = await self.aget_stream(quality_number, 'stream', special_type)
            else:
                logger.error(f"{self.plugin_msg}: 获取{protocol}流失败")
                return False

        target_quality_stream = stream_urls.get(
            quality_number, next(iter(stream_urls.values()))
        )
        stream_url = {}
        if perf_cdn is not None:
            for cdn in perf_cdn:
                stream_info = target_quality_stream.get(cdn)
                if stream_info is not None:
                    current_cdn = cdn
                    stream_url = stream_info['url']
                    break
        if not stream_url:
            current_cdn, stream_info = next(iter(target_quality_stream.items()))
            stream_url = stream_info['url']
            logger.debug(f"{self.plugin_msg}: 使用 {current_cdn} 流")

        self.raw_stream_url = normalize_cn204(
            f"{stream_url['host']}{stream_url['base_url']}{stream_url['extra']}"
        )

        # 替换 cn-gotcha01 节点
        cn01_sids = config.get('bili_replace_cn01', [])
        if cn01_sids and "cn-gotcha01" in current_cdn:
            if isinstance(cn01_sids, str): cn01_sids = cn01_sids.split(',')
            for sid in cn01_sids:
                new_url = f"https://{sid}.bilivideo.com{stream_url['base_url']}{stream_url['extra']}"
                new_url = await self.acheck_url_healthy(new_url)
                if new_url is not None:
                    self.raw_stream_url = new_url
                    break
                else:
                    logger.debug(f"{self.plugin_msg}: {sid} is not available")

        # 强制原画
        if quality_number <= 10000 and config.get('bili_force_source'):
            # 不处理 qn 20000
            if stream_info['suffix'] not in {'origin', 'uhd', 'maxhevc'}:
                __base_url = stream_url['base_url'].replace(f"_{stream_info['suffix']}", "")
                __sk = match1(stream_url['extra'], r'sk=([^&]+)')
                __extra = stream_url['extra'].replace(__sk, __sk[:32])
                __url = normalize_cn204(f"{stream_url['host']}{__base_url}{__extra}")
                if (await self.acheck_url_healthy(__url)) is not None:
                    self.raw_stream_url = __url
                    logger.info(f"{self.plugin_msg}: force_source 处理 {current_cdn} 成功 {stream_info['stream_name']}")
                else:
                    logger.debug(
                        f"{self.plugin_msg}: force_source 处理 {current_cdn} 失败 {stream_info['stream_name']}"
                    )


        if config.get('bili_cdn_fallback'):
            __url = await self.acheck_url_healthy(self.raw_stream_url)
            if __url is None:
                for cdn, stream_info in target_quality_stream.items():
                    stream_url = stream_info['url']
                    __fallback_url = normalize_cn204(
                        f"{stream_url['host']}{stream_url['base_url']}{stream_url['extra']}"
                    )
                    try:
                        __url = await self.acheck_url_healthy(__fallback_url)
                        if __url is not None:
                            self.raw_stream_url = __url
                            logger.info(f"{self.plugin_msg}: cdn_fallback 回退到 {cdn} - {__fallback_url}")
                            break
                    except Exception as e:
                        logger.error(f"{self.plugin_msg}: cdn_fallback {e} - {__fallback_url}")
                        continue
                else:
                    logger.error(f"{self.plugin_msg}: 所有 cdn 均不可用")
                    self.raw_stream_url = None
                    return False
            else:
                self.raw_stream_url = __url

        return True

    def danmaku_init(self):
        if self.bilibili_danmaku:
            self.danmaku = DanmakuClient(
                self.url, self.gen_download_filename(), {
                    'room_id': self.__real_room_id,
                    'cookie': self.fake_headers.get("cookie") if self.__login_mid else "",
                    'detail': self.bilibili_danmaku_detail,
                    'uid': self.__login_mid if self.bilibili_danmaku_detail else 0
                }
            )


    async def get_play_info(self, api: str, qn: int = 10000) -> dict:
        full_url = f"{api}/xlive/web-room/v2/index/getRoomPlayInfo"
        params = {
            'room_id': self.__real_room_id,
            'protocol': '0,1',  # 流协议，0: http_stream(flv), 1: http_hls
            'format': '0,1,2',  # 编码格式，0: flv, 1: ts, 2: fmp4
            'codec': '0',  # 编码器，0: avc, 1: hevc, 2: av1
            'qn': qn,
            'platform': 'html5',  # 平台名称，web, html5, android, ios
            # 'ptype': '8', # P2P配置，-1: disable, 8: WebRTC, 8192: MisakaTunnel
            'dolby': '5', # 杜比格式，5: 杜比音频
            # 'panorama': '1', # 全景(不支持 html5)
            # 'http': '1', # 优先 http 协议
        }
        try:
            api_res = await client.get(
                full_url, params=params, headers=self.fake_headers
            )
            api_res = json.loads(api_res.text)
            if api_res['code'] != 0:
                logger.error(f"{self.plugin_msg}: {api} 返回内容错误: {api_res}")
                return {}
            return api_res['data']
        except json.JSONDecodeError:
            logger.error(f"{self.plugin_msg}: {api} 返回内容错误: {api_res.text}")
        except Exception as e:
            logger.error(f"{self.plugin_msg}: {api} 获取 play_info 失败 -> {e}", exc_info=True)
        return {}

    async def get_master_m3u8(self, api: str) -> dict:
        full_url = f"{api}/live-bvc/master.m3u8"
        params = {
            "cid": self.__real_room_id,
            "mid": self.__login_mid,
            "pt": "html5", # platform
            # "p2p_type": "-1",
        }
        try:
            m3u8_res = await client.get(
                full_url, params=params, headers=self.fake_headers
            )
            if m3u8_res.status_code == 200 and m3u8_res.text.startswith("#EXTM3U"):
                return parse_master_m3u8(m3u8_res.text)
        except Exception as e:
            logger.error(f"{self.plugin_msg}: {api} 获取 m3u8 失败 -> {e}", exc_info=True)
        return {}

    async def aget_stream(self, qn: int = 10000, protocol: str = 'stream', special_type: int = 0) -> dict:
        """
        :param qn: 目标画质
        :param protocol: 流协议
        :param special_type: 特殊直播类型
        :return: 流信息
        """
        stream_urls = {}
        for api in self.__api_list:
            play_info = await self.get_play_info(api, qn)
            if not play_info or check_areablock(play_info):
                # logger.error(f"{self.plugin_msg}: {api} 返回内容错误: {play_info}")
                continue
            streams = play_info['playurl_info']['playurl']['stream']
            if protocol == 'hls_fmp4':
                if config.get('bili_anonymous_origin'):
                    if special_type in play_info['all_special_types'] and not self.__login_mid:
                        logger.warn(f"{self.plugin_msg}: 特殊直播{special_type}")
                    else:
                        stream_urls = await self.get_master_m3u8(api)
                        if stream_urls:
                            break
                # 处理 API 信息
                stream = streams[1] if len(streams) > 1 else streams[0]
                for format in stream['format']:
                    if format['format_name'] == 'fmp4':
                        stream_urls = parse_stream_url(format['codec'][0])
                        # fmp4 可能没有原画
                        if qn == 10000 and qn in stream_urls.keys():
                            break
                        else:
                            stream_urls = {}
            else:
                stream_urls = parse_stream_url(streams[0]['format'][0]['codec'][0])
            if stream_urls:
                break
        # 空字典照常返回，重试交给上层方法处理
        return stream_urls

    async def check_login_status(self) -> int:
        """
        检查B站登录状态
        :return: 当前登录用户 mid
        """
        try:
            _res = await client.get('https://api.bilibili.com/x/web-interface/nav', headers=self.fake_headers)
            user_data = json.loads(_res.text).get('data', {})
            if user_data.get('isLogin'):
                logger.info(f"用户名：{user_data['uname']}, mid：{user_data['mid']}, isLogin：{user_data['isLogin']}")
                return user_data['mid']
            else:
                logger.warning(f"{self.plugin_msg}: 未登录，或将只能录制到最低画质。")
        except Exception as e:
            logger.error(f"{self.plugin_msg}: 登录态校验失败 {e}")
        return 0

# Copy from room-player.js
def check_areablock(data):
    '''
    :return: True if area block
    '''
    if not data['playurl_info']['playurl']:
        logger.error('Sorry, bilibili is currently not available in your country according to copyright restrictions.')
        logger.error('非常抱歉，根据版权方要求，您所在的地区无法观看本直播')
        return True
    return False

def normalize_cn204(url: str) -> str:
    if config.get('bili_normalize_cn204') and "cn-gotcha204" in url:
        return re.sub(r"(?<=cn-gotcha204)-[1-4]", "", url, 1)
    return url


def parse_stream_url(*args) -> dict:
    ov2cn = config.get('bili_ov2cn', False)
    suffix_regexp = r'suffix=([^&]+)'
    if isinstance(args[0], str):
        url = args[0]
        host = "https://" + match1(url, r'https?://([^/]+)')
        stream_url = {
            'host': host if not ov2cn else host.replace("ov-", "cn-"),
            'base_url': url.split("?")[0].split(host)[1] + "?",
            'extra': url.split("?")[1]
        }
        return {
            'url': stream_url,
            'stream_name': match1(url, STREAM_NAME_REGEXP),
            'suffix': match1(url, suffix_regexp)
        }
    elif isinstance(args[0], dict):
        streams = {}
        current_qn = args[0]['current_qn']
        streams.setdefault(current_qn, {})
        base_url = args[0]['base_url']
        for info in args[0]['url_info']:
            cdn_name = match1(info['extra'], r'cdn=([^&]+)')
            stream_url = {
                'host': info['host'] if not ov2cn else info['host'].replace("ov-", "cn-"),
                'base_url': base_url,
                'extra': info['extra']
            }
            streams[current_qn].setdefault(cdn_name, {
                'url': stream_url,
                'stream_name': match1(base_url, STREAM_NAME_REGEXP),
                'suffix': match1(info['extra'], suffix_regexp)
            })
        return streams


def parse_master_m3u8(m3u8_content: str) -> dict:
    """
    Returns:
        {
            "qn值": {
                "cdn名称": {
                    "url": parsed_stream_url,
                    "stream_name": "流名称",
                    "suffix": "二压后缀"
                }
            }
        }
    """
    lines = m3u8_content.strip().splitlines()
    current_qn = None
    result = {}

    if not lines[0].startswith('#EXTM3U'):
        raise ValueError('Invalid m3u8 file')

    for line in lines:
        if line.startswith('#EXT-X-STREAM-INF:'):
            codec = match1(line, r'CODECS="([^"]+)"')
            current_qn = match1(line, r'BILI-QN=(\d+)')

            if codec and current_qn:
                if 'avc' in codec.lower():
                    result.setdefault(current_qn, {})
                else:
                    current_qn = None

        elif line.startswith('http') and current_qn is not None:
            cdn_name = match1(line, r'cdn=([^&]+)')
            if cdn_name:
                result[current_qn].setdefault(cdn_name, parse_stream_url(line))

    return dict(sorted(result.items(), key=lambda x: int(x[0]), reverse=True))