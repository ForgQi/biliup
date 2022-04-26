import logging
import os
import shutil
import subprocess
from pathlib import Path
from functools import reduce
from operator import add

logger = logging.getLogger('biliup')


class UploadBase:
    def __init__(self, principal, data, persistence_path=None, postprocessor=None):
        self.principal = principal
        self.persistence_path = persistence_path
        self.data = data
        self.post_processor = postprocessor

    # @property
    @staticmethod
    def file_list(index):
        file_list = []
        for file_name in os.listdir('.'):
            if index in file_name and os.path.isfile(file_name):
                file_list.append(file_name)
        file_list = sorted(file_list)
        return file_list

    @staticmethod
    def remove_filelist(file_list):
        for r in file_list:
            os.remove(r)
            logger.info('删除-' + r)

    def filter_file(self, index):
        file_list = UploadBase.file_list(index)
        if len(file_list) == 0:
            return False
        for r in file_list:
            file_size = os.path.getsize(r) / 1024 / 1024
            threshold = self.data.get('threshold') if self.data.get('threshold') else 20
            if file_size <= threshold:
                os.remove(r)
                logger.info('过滤删除-' + r)
        file_list = UploadBase.file_list(index)
        if len(file_list) == 0:
            logger.info('视频过滤后无文件可传')
            return False
        for f in file_list:
            if f.endswith('.part'):
                os.rename(f, os.path.splitext(f)[0])
                logger.info('%s存在已更名' % f)
        return True

    def upload(self, file_list):
        raise NotImplementedError()

    def start(self):
        if self.filter_file(self.principal):
            logger.info('准备上传' + self.data["format_title"])
            needed2process = self.upload(UploadBase.file_list(self.principal))
            if needed2process:
                self.postprocessor(needed2process)

    def postprocessor(self, data):
        # data = file_list
        if self.post_processor is None:
            return self.remove_filelist(data)
        for post_processor in self.post_processor:
            if post_processor == 'rm':
                self.remove_filelist(data)
                continue
            if post_processor.get('mv'):
                for file in data:
                    path = Path(file)
                    dest = Path(post_processor['mv'])
                    if not dest.is_dir():
                        dest.mkdir(parents=True, exist_ok=True)
                    #path.rename(dest / path.name)
                    shutil.move(path, dest / path.name)
                    logger.info(f"move to {(dest / path.name).absolute()}")
            if post_processor.get('run'):
                process = subprocess.run(
                    post_processor['run'], shell=True, input=reduce(add, map(lambda x: str(Path(x).absolute()) + '\n', data)),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
                logger.info(process.stdout.rstrip())
