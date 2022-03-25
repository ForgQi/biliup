import logging
import os
import subprocess
import sys
import time
from biliup import config
from ..plugins import fake_headers
from biliup import common

import requests

logger = logging.getLogger('biliup')


class DownloadBase:
    def __init__(self, fname, url, suffix=None, opt_args=None):
        self.room_title = None
        self.room_cover = None
        if opt_args is None:
            opt_args = []
        self.fname = fname
        self.url = url
        self.suffix = suffix
        self.title = None
        # ffmpeg.exe -i  http://vfile1.grtn.cn/2018/1542/0254/3368/154202543368.ssm/154202543368.m3u8
        # -c copy -bsf:a aac_adtstoasc -movflags +faststart output.mp4
        self.raw_stream_url = None
        self.opt_args = opt_args

        self.default_output_args = [
            '-bsf:a', 'aac_adtstoasc',
        ]
        if config.get('segment_time'):
            self.default_output_args += \
                ['-segment_time', f"{config.get('segment_time') if config.get('segment_time') else '00:50:00'}"]
        else:
            self.default_output_args += \
                ['-fs', f"{config.get('file_size') if config.get('file_size') else '2621440000'}"]
        self.default_input_args = ['-headers', ''.join('%s: %s\r\n' % x for x in fake_headers.items()),
                                   '-reconnect_streamed', '1', '-reconnect_delay_max', '20', '-rw_timeout', '20000000']

    def check_stream(self):
        logger.debug(self.fname)
        raise NotImplementedError()

    def download(self, filename):
        # 下载封面图默认放入目录下
        if self.room_cover:
            '''
            cover_folder：封面图存放目录
            cover_filename：封面图文件名，不包含路径。为方便后续处理，设定为房间标题
            room_cover_path：封面图文件完整路径
            '''
            cover_folder = "./cover"
            try:
                # logger.info(f'尝试创建封面图存放目录{cover_folder}')
                os.makedirs(cover_folder, exist_ok=True)
                cover_filename = self.room_title + self.room_cover[self.room_cover.rindex('.'):]
                self.room_cover_path = os.path.join(cover_folder, cover_filename)
                # logger.info(f'尝试下载封面图{cover_filename}')
                cover_res = requests.get(self.room_cover)
                if cover_res.status_code == 200:
                    with open(self.room_cover_path, 'wb') as f:
                        f.write(cover_res.content)
            except Exception as e:
                logger.warning('自动下载封面失败：%s，不使用封面' % e)
                self.room_cover_path = None
                # logger.warning(room_cover)

        args = ['ffmpeg', '-y', *self.default_input_args,
                '-i', self.raw_stream_url, *self.default_output_args, *self.opt_args,
                '-c', 'copy', '-f', self.suffix]
        if config.get('segment_time'):
            args += ['-f', 'segment', f'{filename} part-%03d.{self.suffix}']
        else:
            args += [f'{filename}.part']

        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            with proc.stdout as stdout:
                for line in iter(stdout.readline, b''):  # b'\n'-separated lines
                    decode_line = line.decode(errors='ignore')
                    print(decode_line, end='', file=sys.stderr)
                    logger.debug(decode_line.rstrip())
            retval = proc.wait()
        except KeyboardInterrupt:
            if sys.platform != 'win32':
                proc.communicate(b'q')
            raise
        return retval

    def run(self):
        if not self.check_stream():
            return False
        file_name = f'{self.file_name}.{self.suffix}'
        retval = self.download(file_name)
        logger.info(f'{retval}part: {file_name}')
        self.rename(file_name)
        return retval

    def start(self):
        i = 0
        logger.info('开始下载%s：%s' % (self.__class__.__name__, self.fname))
        date = common.time.now()
        while i < 30:
            try:
                ret = self.run()
            except:
                logger.exception("Uncaught exception:")
                continue
            finally:
                self.close()
            if ret is False:
                break
            elif ret == 1:
                time.sleep(45)
            i += 1
        logger.info(f'退出下载{i}: {self.fname}')
        return {
            'name': self.fname,
            'url': self.url,
            'title': self.room_title,
            'date': date,
            'room_cover_path': self.room_cover_path,
        }

    @staticmethod
    def rename(file_name):
        try:
            os.rename(file_name + '.part', file_name)
            logger.debug('更名{0}为{1}'.format(file_name + '.part', file_name))
        except FileNotFoundError:
            logger.info('FileNotFoundError:' + file_name)
        except FileExistsError:
            os.rename(file_name + '.part', file_name)
            logger.info('FileExistsError:更名{0}为{1}'.format(file_name + '.part', file_name))

    @property
    def file_name(self):
        return f'{self.fname}{time.strftime("%Y-%m-%dT%H_%M_%S", time.localtime())}'

    def close(self):
        pass
