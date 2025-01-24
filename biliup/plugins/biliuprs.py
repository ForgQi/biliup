import multiprocessing as mp
import time
from typing import List

import stream_gears

from ..engine import Plugin
from ..engine.upload import UploadBase, logger


@Plugin.upload(platform="biliup-rs")
class BiliWeb(UploadBase):
    def __init__(
            self, principal, data, submit_api=None, copyright=2, postprocessor=None, dtime=None,
            dynamic='', lines='AUTO', threads=3, tid=122, tags=None, cover_path=None, description='',
            dolby=0, hires=0, no_reprint=0, open_elec=0, credits=None,
            user_cookie='cookies.json', copyright_source=None, extra_fields = ""
    ):
        super().__init__(principal, data, persistence_path='bili.cookie', postprocessor=postprocessor)
        if tags is None:
            tags = []
        else:
            tags = [str(tag).format(streamer=self.data['name']) for tag in tags]
        self.lines = lines
        self.submit_api = submit_api
        self.threads = threads
        self.tid = tid
        self.tags = tags
        if cover_path:
            self.cover_path = cover_path
        elif "live_cover_path" in self.data:
            self.cover_path = self.data["live_cover_path"]
        else:
            self.cover_path = None
        self.desc = description
        self.credits = credits if credits else []
        self.dynamic = dynamic
        self.copyright = copyright
        self.dtime = dtime
        self.dolby = dolby
        self.hires = hires
        self.no_reprint = no_reprint
        self.open_elec = open_elec
        self.user_cookie = user_cookie
        self.copyright_source = copyright_source
        self.extra_fields = extra_fields
        
    def upload(self, file_list: List[UploadBase.FileInfo]) -> List[UploadBase.FileInfo]:
        if self.credits:
            desc_v2 = self.creditsToDesc_v2()
        else:
            desc_v2 = [{
                "raw_text": self.desc,
                "biz_id": "",
                "type": 1
            }]

        ex_parent_conn, ex_child_conn = mp.Pipe()
        upload_args = {
            "ex_conn": ex_child_conn,
            "lines": self.lines,
            "video_path": [file.video for file in file_list],
            "cookie_file": self.user_cookie,
            "title": self.data["format_title"][:80],
            "tid": self.tid,
            "tag": ','.join(self.tags),
            "copyright": self.copyright,
            "source": self.copyright_source if self.copyright_source else self.data["url"],
            "desc": self.desc,
            "dynamic": self.dynamic,
            "cover": self.cover_path if self.cover_path is not None else "",
            "dolby": self.dolby,
            "lossless_music": self.hires,
            "no_reprint": self.no_reprint,
            "open_elec": self.open_elec,
            "limit": self.threads,
            "desc_v2": desc_v2,
            "extra_fields": self.extra_fields,
            "dtime": int(time.time() + self.dtime) if self.dtime else None,
        }
        upload_process = mp.get_context('spawn').Process(target=stream_gears_upload, daemon=True, kwargs=upload_args)
        upload_process.start()
        upload_process.join()
        if ex_parent_conn.poll():
            raise RuntimeError(ex_parent_conn.recv())

        logger.info(f"上传成功: {self.principal}")
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


def stream_gears_upload(ex_conn, lines, *args, **kwargs):
    try:
        if lines == 'bda':
            kwargs['line'] = stream_gears.UploadLine.Bda
        elif lines == 'bda2':
            kwargs['line'] = stream_gears.UploadLine.Bda2
        elif lines == 'ws':
            kwargs['line'] = stream_gears.UploadLine.Ws
        elif lines == 'qn':
            kwargs['line'] = stream_gears.UploadLine.Qn
        elif lines == 'tx':
            kwargs['line'] = stream_gears.UploadLine.Tx
        elif lines == 'txa':
            kwargs['line'] = stream_gears.UploadLine.Txa
        elif lines == 'bldsa':
            kwargs['line'] = stream_gears.UploadLine.Bldsa

        stream_gears.upload(*args, **kwargs)
    except Exception as e:
        ex_conn.send(e)
