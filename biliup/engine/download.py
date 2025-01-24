import asyncio
import inspect
import logging
import os
import queue
import re
import subprocess
import threading
import time
import shutil
from abc import ABC, abstractmethod
from typing import AsyncGenerator, List, Callable, Optional
from urllib.parse import urlparse
from datetime import datetime, timedelta

import requests
from requests.utils import DEFAULT_ACCEPT_ENCODING
from httpx import HTTPStatusError

from biliup.common.util import client, loop, check_timerange
from biliup.database.db import add_stream_info, SessionLocal, get_stream_info, update_cover_path, update_room_title, update_file_list
from biliup.plugins import random_user_agent
import stream_gears
from PIL import Image

from biliup.config import config
from biliup.Danmaku import IDanmakuClient
from biliup.plugins.bili_webup_sync import BiliWebAsync
from biliup.uploader import fmt_title_and_desc

from .sync_downloader import SyncDownloader

logger = logging.getLogger('biliup')


class DownloadBase(ABC):
    def __init__(self, fname, url, suffix=None, opt_args=None):
        self.room_title = None
        if opt_args is None:
            opt_args = []
        self.fname = fname
        self.url = url
        # 录制后保存文件格式而非源流格式 对应原配置文件format 仅ffmpeg及streamlink生效
        if not suffix:
            logger.error(f'检测到suffix不存在，请补充后缀')
        else:
            self.suffix = suffix.lower()
        self.live_cover_path = None
        self.database_row_id = 0
        self.downloader = config.get('downloader', 'stream-gears')
        # ffmpeg.exe -i  http://vfile1.grtn.cn/2018/1542/0254/3368/154202543368.ssm/154202543368.m3u8
        # -c copy -bsf:a aac_adtstoasc -movflags +faststart output.mp4
        self.raw_stream_url = None

        # 主播单独传参会覆盖全局设置。例如新增了一个全局的filename_prefix参数，在下面添加self.filename_prefix = config.get('filename_prefix'),
        # 即可通过self.filename_prefix在下载或者上传时候传递主播单独的设置参数用于调用（如果该主播有设置单独参数，将会优先使用单独参数；如无，则会优先你用全局参数。）
        self.filename_prefix = config.get('filename_prefix')
        self.use_live_cover = config.get('use_live_cover', False)
        self.opt_args = opt_args
        self.live_cover_url = None
        self.fake_headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'accept-encoding': DEFAULT_ACCEPT_ENCODING,
            'accept-language': 'zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3',
            'user-agent': random_user_agent(),
        }
        self.segment_time = config.get('segment_time', '01:00:00')
        self.time_range = config.get('time_range')
        self.file_size = config.get('file_size')

        # 是否是下载模式 跳过下播检测
        self.is_download = False

        # 分段后处理
        self.segment_processor = config.get('segment_processor')
        self.segment_processor_thread = []
        # 分段后处理并行
        self.segment_processor_parallel = config.get('segment_processor_parallel', False)

        # 弹幕客户端
        self.danmaku: Optional[IDanmakuClient] = None

        self.plugin_msg = f"[{self.__class__.__name__}]{self.fname} - {url}"

    @abstractmethod
    async def acheck_stream(self, is_check=False):
        # is_check 是否是检测模式 检测模式可以忽略只有下载时需要的耗时操作
        raise NotImplementedError()

    def pre_check(self):
        if check_timerange(self.fname):
            return True

    def download(self):
        logger.info(f"{self.plugin_msg}: Start downloading {self.raw_stream_url}")
        # 调试使用边录边上传功能
        # self.downloader = 'sync-downloader'
        if self.is_download:
            if not shutil.which("ffmpeg"):
                logger.error("未安装 FFMpeg 或不存在于 PATH 内")
                logger.debug("Current user's PATH is:" + os.getenv("PATH"))
                return False
            else:
                return self.ffmpeg_segment_download()

        parsed_url_path = urlparse(self.raw_stream_url).path
        if self.downloader != 'stream-gears':
            if not shutil.which("ffmpeg"):
                logger.error("未安装 FFMpeg 或不存在于 PATH 内，本次下载使用 stream-gears")
                logger.debug("Current user's PATH is:" + os.getenv("PATH"))

            # 同步下载上传器
            if self.downloader == 'sync-downloader':
                logger.info(f"{self.plugin_msg}: 使用同步下载器")
                stream_info = config.get('streamers', {}).get(self.fname, {})
                stream_info.update({'name': self.fname})
                if not self.file_size:
                    self.file_size = 2 * 1024 * 1024 * 1024
                else:
                    min_size = 10 * 1024 * 1024
                    self.file_size = ((self.file_size + min_size - 1) // min_size) * min_size  # 向上取整
                sync_download(self.raw_stream_url, self.fake_headers,
                              max_file_size=int(self.file_size / 1024 / 1024),
                              output_prefix=self.gen_download_filename(True),
                              stream_info=stream_info,
                              file_name_callback=lambda file_name: self.__download_segment_callback(file_name))
                return True
            # streamlink无法处理flv,所以回退到ffmpeg
            if self.downloader == 'streamlink' and '.flv' not in parsed_url_path:
                return self.ffmpeg_download(use_streamlink=True)
            return self.ffmpeg_download()

        if '.flv' in parsed_url_path:
            # 假定flv流
            self.suffix = 'flv'
        else:
            # 其他流stream_gears会按hls保存为ts
            self.suffix = 'ts'
        stream_gears_download(self.raw_stream_url, self.fake_headers, self.gen_download_filename(),
                              self.segment_time,
                              self.file_size,
                              lambda file_name: self.__download_segment_callback(file_name))
        return True

    def ffmpeg_segment_download(self):
        # TODO 无日志
        # , '-report'
        # ffmpeg 输入参数
        input_args = [
            '-loglevel', 'quiet', '-y'
        ]
        # ffmpeg 输出参数
        output_args = [
            '-bsf:a', 'aac_adtstoasc'
        ]
        input_args += ['-headers', ''.join('%s: %s\r\n' % x for x in self.fake_headers.items()),
                       '-rw_timeout', '20000000']
        if '.m3u8' in urlparse(self.raw_stream_url).path:
            input_args += ['-max_reload', '1000']

        input_args += ['-i', self.raw_stream_url]

        output_args += ['-f', 'segment']
        # output_args += ['-segment_format', self.suffix]
        output_args += ['-segment_list', 'pipe:1']
        output_args += ['-segment_list_type', 'flat']
        output_args += ['-reset_timestamps', '1']
        # output_args += ['-strftime', '1']
        if self.segment_time:
            output_args += ['-segment_time', self.segment_time]
        else:
            # 避免适配两套
            output_args += ['-segment_time', '9999:00:00']

        output_args += ['-c', 'copy']
        output_args += self.opt_args
        file_name = self.gen_download_filename(is_fmt=True)
        args = ['ffmpeg', *input_args, *output_args, f'{file_name}_%d.{self.suffix}']
        with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as proc:
            for line in iter(proc.stdout.readline, b''):  # b'\n'-separated lines
                try:
                    ffmpeg_file_name = line.rstrip().decode(errors='ignore')
                    time.sleep(1)
                    # 文件重命名
                    self.download_file_rename(ffmpeg_file_name, f'{file_name}.{self.suffix}')
                    self.__download_segment_callback(f'{file_name}.{self.suffix}')
                    file_name = self.gen_download_filename(is_fmt=True)
                except:
                    logger.error(f'分段事件失败：{self.__class__.__name__} - {self.fname}', exc_info=True)

        return proc.returncode == 0

    def ffmpeg_download(self, use_streamlink=False):
        # streamlink进程
        streamlink_proc = None
        # updatedFileList = False
        try:
            # 文件名不含后戳
            fmt_file_name = self.gen_download_filename(is_fmt=True)
            # ffmpeg 输入参数
            input_args = []
            # ffmpeg 输出参数
            output_args = [
                '-c',
                'copy',
            ]
            if use_streamlink:
                streamlink_cmd = [
                    'streamlink',
                    '--stream-segment-threads', '3',
                    '--hls-playlist-reload-attempts', '1',
                    '--http-header',
                    ';'.join([f'{key}={value}' for key, value in self.fake_headers.items()]),
                    self.raw_stream_url,
                    'best',
                    '-O'
                ]
                streamlink_proc = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE)
                input_uri = 'pipe:0'
            else:
                input_args += ['-headers', ''.join('%s: %s\r\n' % x for x in self.fake_headers.items()),
                               '-rw_timeout', '20000000']
                if '.m3u8' in urlparse(self.raw_stream_url).path:
                    input_args += ['-max_reload', '1000']
                input_uri = self.raw_stream_url

            input_args += ['-i', input_uri]

            duration = get_duration(self.segment_time, self.time_range)
            if duration:
                output_args += ['-to', duration]
            if self.file_size:
                output_args += ['-fs', str(self.file_size)]

            output_args += self.opt_args

            if self.suffix == 'mp4':
                output_args += ['-bsf:a', 'aac_adtstoasc', '-f', 'mp4']
            elif self.suffix == 'ts':
                output_args += ['-f', 'mpegts']
            elif self.suffix == 'mkv':
                output_args += ['-f', 'matroska']
            else:
                output_args += ['-f', self.suffix]

            args = ['ffmpeg', '-y', *input_args, *output_args,
                    f'{fmt_file_name}.{self.suffix}.part']
            with subprocess.Popen(args, stdin=subprocess.DEVNULL if not streamlink_proc else streamlink_proc.stdout,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as proc:
                # with SessionLocal() as db:
                #     update_file_list(db, self.database_row_id, fmt_file_name)
                #     updatedFileList = True
                for line in iter(proc.stdout.readline, b''):  # b'\n'-separated lines
                    decode_line = line.rstrip().decode(errors='ignore')
                    print(decode_line)
                    logger.debug(decode_line)

            if proc.returncode == 0:
                # 文件重命名
                self.download_file_rename(f'{fmt_file_name}.{self.suffix}.part', f'{fmt_file_name}.{self.suffix}')
                # 触发分段事件
                self.__download_segment_callback(f'{fmt_file_name}.{self.suffix}')
                return True
            else:
                return False
        # except:
        #     if updatedFileList:
        #         with SessionLocal() as db:
        #             delete_file_list(db, self.database_row_id, None)
        finally:
            try:
                if streamlink_proc:
                    streamlink_proc.terminate()
                    streamlink_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                streamlink_proc.kill()
            except:
                logger.exception(f'terminate {self.fname} failed')

    def __download_segment_callback(self, file_name: str):
        """
        分段后触发返回含后戳的文件名
        """
        exclude_ext_file_name = os.path.splitext(file_name)[0]
        danmaku_file_name = os.path.splitext(file_name)[0] + '.xml'
        if self.danmaku:
            self.danmaku.save(danmaku_file_name)

        def x():
            # 将文件名和直播标题存储到数据库
            with SessionLocal() as db:
                update_file_list(db, self.database_row_id, file_name)
            if self.segment_processor:
                try:
                    if not self.segment_processor_parallel and prev_thread:
                        prev_thread.join()
                    from biliup.common.tools import processor
                    data = os.path.abspath(file_name)
                    if os.path.exists(danmaku_file_name):
                        data += f'\n{os.path.abspath(danmaku_file_name)}'
                    processor(self.segment_processor, data)
                except:
                    logger.warning(f'{self.plugin_msg}: 执行后处理失败', exc_info=True)

        thread = threading.Thread(target=x, daemon=True, name=f"segment_processor_{exclude_ext_file_name}")
        prev_thread = self.segment_processor_thread[-1] if self.segment_processor_thread else None
        self.segment_processor_thread.append(thread)
        thread.start()

    def download_success_callback(self):
        pass

    def run(self):
        try:
            if not asyncio.run_coroutine_threadsafe(self.acheck_stream(), loop).result() or not self.pre_check():
                return False
            with SessionLocal() as db:
                update_room_title(db, self.database_row_id, self.room_title)
            self.danmaku_init()
            if self.danmaku:
                self.danmaku.start()
            retval = self.download()
            return retval
        finally:
            if self.danmaku:
                self.danmaku.stop()
                self.danmaku = None

    def start(self):
        logger.info(f"{self.plugin_msg}: 开始下载")
        # 开始时间
        start_time = time.localtime()
        # 结束时间
        end_time = None

        with SessionLocal() as db:
            self.database_row_id = add_stream_info(db, self.fname, self.url, start_time)  # 返回数据库中此行记录的 id
        ret = True
        while ret:
            # 下载结果
            try:
                ret = self.run()
            except Exception:
                logger.warning(f"{self.plugin_msg}: 下载失败", exc_info=True)
            finally:
                self.close()

            # 下载模式跳过下播延迟检测
            if self.is_download:
                break

            # 最后一次下载完成时间
            end_time = time.localtime()

        self.download_cover(
            time.strftime(self.gen_download_filename().encode("unicode-escape").decode(), end_time if end_time else time.localtime()
                          ).encode().decode("unicode-escape"))
        # 更新数据库中封面存储路径
        with SessionLocal() as db:
            update_cover_path(db, self.database_row_id, self.live_cover_path)

        for thread in self.segment_processor_thread:
            if thread.is_alive():
                logger.info(f'{self.plugin_msg}: 等待分段后处理完成 - {thread.name}')
                thread.join()
        if (self.is_download and ret) or not self.is_download:
            self.download_success_callback()
        # self.segment_processor_thread
        logger.info(f'{self.plugin_msg}: 退出下载')
        stream_info = {
            'name': self.fname,
            'url': self.url,
            'title': self.room_title,
            'date': start_time,
            'end_time': end_time if end_time else time.localtime(),
            'live_cover_path': self.live_cover_path,
            'is_download': self.is_download,
        }
        return stream_info

    def download_cover(self, fmtname):
        # 获取封面
        if self.use_live_cover and self.live_cover_url is not None:
            try:
                save_dir = f'cover/{self.__class__.__name__}/{self.fname}/'
                if not os.path.exists(save_dir):
                    os.makedirs(save_dir)

                url_path = urlparse(self.live_cover_url).path
                suffix = None
                if '.jpg' in url_path:
                    suffix = 'jpg'
                elif '.png' in url_path:
                    suffix = 'png'
                elif '.webp' in url_path:
                    suffix = 'webp'

                if suffix:
                    live_cover_path = f'{save_dir}{fmtname}.{suffix}'
                    if os.path.exists(live_cover_path):
                        self.live_cover_path = live_cover_path
                    else:
                        response = requests.get(self.live_cover_url, headers=self.fake_headers, timeout=30)
                        with open(live_cover_path, 'wb') as f:
                            f.write(response.content)

                    if suffix == 'webp':
                        with Image.open(live_cover_path) as img:
                            img = img.convert('RGB')
                            img.save(f'{save_dir}{fmtname}.jpg', format='JPEG')
                        os.remove(live_cover_path)
                        live_cover_path = f'{save_dir}{fmtname}.jpg'

                    self.live_cover_path = live_cover_path
                    logger.info(
                        f'{self.plugin_msg}: 封面下载成功，路径：{os.path.abspath(self.live_cover_path)}')
                else:
                    logger.warning(
                        f'{self.plugin_msg}: 封面为不支持的格式：{self.live_cover_url}')
            except:
                logger.exception(f'{self.plugin_msg}: 封面下载失败')

    async def acheck_url_healthy(self, url):
        async def __client_get(url, stream: bool = False):
            client.headers.update(self.fake_headers)
            if stream:
                async with client.stream("GET", url, timeout=60, follow_redirects=False) as response:
                    pass
            else:
                response = await client.get(url)
            if response.status_code not in (301, 302):
                response.raise_for_status()
            return response

        try:
            if '.m3u8' in url:
                r = await __client_get(url)
                import m3u8
                m3u8_obj = m3u8.loads(r.text)
                if m3u8_obj.is_variant:
                    url = m3u8_obj.playlists[0].uri
                    logger.info(f'{self.plugin_msg}: stream url: {url}')
                    r = await __client_get(url)
            else:  # 处理 Flv
                r = await __client_get(url, stream=True)
                if r.headers.get('Location'):
                    url = r.headers['Location']
                    logger.info(f'{self.plugin_msg}: stream url: {url}')
                    r = await __client_get(url, stream=True)
            if r.status_code == 200:
                return url
        except HTTPStatusError as e:
            logger.debug(f'{self.plugin_msg}: url {url}: status_code-{e.response.status_code}')
        except:
            logger.debug(f'{self.plugin_msg}: url {url}: ', exc_info=True)
        return None

    def gen_download_filename(self, is_fmt=False):
        if self.filename_prefix:  # 判断是否存在自定义录播命名设置
            filename = (self.filename_prefix.format(streamer=self.fname, title=self.room_title).encode(
                'unicode-escape').decode()).encode().decode("unicode-escape")
        else:
            filename = f'{self.fname}%Y-%m-%dT%H_%M_%S'
        filename = get_valid_filename(filename)
        if is_fmt:
            file_time = time.time()
            while True:
                fmt_file_name = time.strftime(filename.encode("unicode-escape").decode(),
                                              time.localtime(file_time)).encode().decode("unicode-escape")
                if os.path.exists(f"{fmt_file_name}.{self.suffix}"):
                    file_time += 1
                else:
                    return fmt_file_name
        else:
            return filename

    @staticmethod
    def download_file_rename(old_file_name, file_name):
        try:
            os.rename(old_file_name, file_name)
            logger.info(f'更名 {old_file_name} 为 {file_name}')
        except:
            logger.error(f'更名 {old_file_name} 为 {file_name} 失败', exc_info=True)

    def danmaku_init(self):
        pass

    def close(self):
        pass


def stream_gears_download(url, headers, file_name, segment_time=None, file_size=None,
                          file_name_callback: Callable[[str], None] = None):
    class Segment:
        pass

    segment = Segment()
    if segment_time:
        seg_time = segment_time.split(':')
        # print(int(seg_time[0]) * 60 * 60 + int(seg_time[1]) * 60 + int(seg_time[2]))
        segment.time = int(seg_time[0]) * 60 * 60 + int(seg_time[1]) * 60 + int(seg_time[2])
    if file_size:
        segment.size = file_size
    if file_size is None and segment_time is None:
        segment.size = 8 * 1024 * 1024 * 1024
    # FIXME: 下载时如出现403，这里不会回到上层方法获取新链接
    if file_name_callback:
        stream_gears.download_with_callback(
            url,
            headers,
            file_name,
            segment,
            file_name_callback
        )
    else:
        stream_gears.download(
            url,
            headers,
            file_name,
            segment,
        )


def sync_download(stream_url, headers, segment_duration=60, max_file_size=100, output_prefix="segment", stream_info=None, file_name_callback: Callable[[str], None] = None):
    logger.info(f"启动同步下载器 max_file_size {max_file_size}MB")
    video_queue = queue.SimpleQueue()
    # video_queue.put(b'\x00')

    def upload(video_queue, stream_info, stop_event: threading.Event):
        with SessionLocal() as db:
            data = get_stream_info(db, f"{stream_info['name']}")
        data, _ = fmt_title_and_desc({**data, "name": stream_info['name']})
        stream_info.update(data)
        # 获取 BiliWebAsync.__init__ 的参数名
        init_params = inspect.signature(BiliWebAsync.__init__).parameters
        # 过滤 info 中的无关键
        filtered_info = {key: value for key, value in stream_info.items() if key in init_params}

        # 映射 'uploader' 到 'principal'
        filtered_info['principal'] = ""
        filtered_info["data"] = stream_info
        uploader = BiliWebAsync(**filtered_info, video_queue=video_queue)
        uploader.upload(total_size=max_file_size * 1024 * 1024,
                        stop_event=stop_event, output_prefix=output_prefix,
                        file_name_callback=file_name_callback)
        # print("上传器结束")
        logger.info(f"{stream_info['name']} 上传器结束")
        # video_queue = queue.SimpleQueue()

    downloader = SyncDownloader(stream_url, headers, segment_duration, max_file_size, output_prefix, video_queue)

    # 启动上传器
    upload_thread = threading.Thread(target=upload, args=(video_queue, stream_info, downloader.stop_event), daemon=True)
    upload_thread.start()

    downloader.run()
    logger.info(f"{stream_info['name']} 下载器结束")


def get_valid_filename(name):
    """
    Return the given string converted to a string that can be used for a clean
    filename. Remove leading and trailing spaces; convert other spaces to
    underscores; and remove anything that is not an alphanumeric, dash,
    underscore, or dot.
    # >>> get_valid_filename("john's portrait in 2004.jpg")
    >>> get_valid_filename("{self.fname}%Y-%m-%dT%H_%M_%S")
    '{self.fname}%Y-%m-%dT%H_%M_%S'
    """
    # s = str(name).strip().replace(" ", "_") #因为有些人会在主播名中间加入空格，为了避免和录播完毕自动改名冲突，所以注释掉
    s = re.sub(r"(?u)[^-\w.%{}\[\]【】「」（）・°\s]", "", str(name))
    if s in {"", ".", ".."}:
        raise RuntimeError("Could not derive file name from '%s'" % name)
    return s


def get_duration(segment_time_str, time_range):
    """
    计算当前时间到给定结束时间的时差
    如果计算的时差大于segment_time，则返回segment_time。
    """
    if not time_range or '-' not in time_range:
        return segment_time_str
    end_time_str = time_range.split('-')[1]

    now = datetime.now()
    end_time_today_str = now.strftime("%Y-%m-%d") + " " + end_time_str
    end_time_today = datetime.strptime(end_time_today_str, "%Y-%m-%d %H:%M:%S")
    # 判断结束时间是否是第二天的时间
    if now > end_time_today:
        end_time_today += timedelta(days=1)

    time_diff = end_time_today - now
    if segment_time_str:
        segment_time_parts = list(map(int, segment_time_str.split(":")))
        segment_time = timedelta(hours=segment_time_parts[0],
                                 minutes=segment_time_parts[1], seconds=segment_time_parts[2])

        if time_diff > segment_time:
            return segment_time_str

    # 增加10s，防止time_diff过小多次执行下载
    if time_diff.total_seconds() <= 60:
        time_diff = time_diff + timedelta(seconds=10)

    hours, remainder = divmod(time_diff.total_seconds(), 3600)
    minutes, seconds = divmod(remainder, 60)

    to_parameter = f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"

    return to_parameter


class BatchCheck(ABC):
    @staticmethod
    @abstractmethod
    async def abatch_check(check_urls: List[str]) -> AsyncGenerator[str, None]:
        """
        批量检测直播或下载状态
        返回的是url_list
        """
