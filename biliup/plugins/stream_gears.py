import time

import stream_gears
from typing import List

from ..engine import Plugin
from ..engine.upload import UploadBase, logger


@Plugin.upload(platform="stream_gears")
class BiliWeb(UploadBase):
    def __init__(
            self, principal, data, submit_api=None, copyright=2, postprocessor=None, dtime=None,
            dynamic='', lines='AUTO', threads=3, tid=122, tags=None, cover_path=None, description='',
            dolby=0, hires=0, no_reprint=0, open_elec=0, credits=None,
            user_cookie='cookies.json', copyright_source=None, extra_fields = ""
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
        self.dtime = dtime
        self.dolby = dolby
        self.hires = hires
        self.no_reprint = no_reprint
        self.open_elec = open_elec
        self.user_cookie = user_cookie
        self.copyright_source = copyright_source

        self.extra_fields = extra_fields
        

    def upload(self, file_list) -> List[UploadBase.FileInfo]:
        line = None
        if self.lines == 'kodo':
            line = stream_gears.UploadLine.Kodo
        elif self.lines == 'bda2':
            line = stream_gears.UploadLine.Bda2
        elif self.lines == 'ws':
            line = stream_gears.UploadLine.Ws
        elif self.lines == 'qn':
            line = stream_gears.UploadLine.Qn
        elif self.lines == 'cos':
            line = stream_gears.UploadLine.Cos
        elif self.lines == 'cos-internal':
            line = stream_gears.UploadLine.CosInternal
        tag = ','.join(self.tags)
        if self.credits:
            desc_v2 = self.creditsToDesc_v2()
        else:
            desc_v2 = [{
                "raw_text": self.desc,
                "biz_id": "",
                "type": 1
            }]

        source = self.copyright_source if self.copyright_source else "https://github.com/biliup/biliup"
        cover = self.cover_path if self.cover_path is not None else ""
        dtime = None
        if self.dtime:
            dtime = int(time.time() + self.dtime)
        stream_gears.upload(
            file_list,
            self.user_cookie,
            self.data["format_title"][:80],
            self.tid,
            tag,
            self.copyright,
            source,
            self.desc,
            self.dynamic,
            cover,
            self.dolby,
            self.hires,
            self.no_reprint,
            self.open_elec,
            self.threads,
            desc_v2,
            dtime,
            line,
            self.extra_fields
        )
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
