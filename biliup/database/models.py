import logging
from datetime import datetime
from pathlib import Path
from typing import List

from sqlalchemy import create_engine, ForeignKey, JSON, TEXT, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

logger = logging.getLogger('biliup')


def get_path(*other):
    """获取数据文件绝对路径"""
    dir_path = Path.cwd().joinpath("data")
    # 若目录不存在则创建
    if not dir_path.is_dir():
        dir_path.mkdir(parents=True)
    return str(dir_path.joinpath(*other))


db_url = f"sqlite:///{get_path('data.sqlite3')}"  # 数据库 URL, 使用默认 DBAPI

engine = create_engine(
    db_url,
    echo=True,  # 显示执行的 SQL 记录, 仅调试用, 发布前请注释
)

convention = {
    "ix": 'ix_%(column_0_label)s',
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s"
}


class BaseModel(DeclarativeBase):
    pass


BaseModel.metadata = MetaData(naming_convention=convention)  # 定义命名惯例，用来生成自动迁移脚本


class StreamerInfo(BaseModel):
    """下载信息"""
    __tablename__ = "streamerinfo"

    id: Mapped[int] = mapped_column(primary_key=True)  # 自增主键
    name: Mapped[str] = mapped_column(nullable=False)  # streamer 名称
    url: Mapped[str] = mapped_column(nullable=False)  # 录制的 url
    title: Mapped[str] = mapped_column(nullable=False)  # 直播标题
    date: Mapped[datetime] = mapped_column(nullable=False)  # 开播时间
    live_cover_path: Mapped[str] = mapped_column(nullable=False)  # 封面存储路径
    filelist: Mapped[List["FileList"]] = relationship(back_populates="streamerinfo")


class FileList(BaseModel):
    """存储文件名列表, 通过外键和 StreamerInfo 表关联"""
    __tablename__ = "filelist"

    id: Mapped[int] = mapped_column(primary_key=True)  # 自增主键
    file: Mapped[str] = mapped_column(nullable=False)  # 文件名
    # 外键, 对应 StreamerInfo 中的下载信息, 且启用级联删除
    streamer_info_id = mapped_column(ForeignKey("streamerinfo.id", ondelete="CASCADE"), nullable=False)
    streamerinfo: Mapped[StreamerInfo] = relationship(back_populates="filelist")


class Configuration(BaseModel):
    """暂时将配置文件整体存入，后续可拆表拆字段"""
    __tablename__ = "configuration"

    id: Mapped[int] = mapped_column(primary_key=True)  # 自增主键
    key: Mapped[str] = mapped_column(nullable=False)  # 全局配置键
    value = mapped_column(TEXT(), nullable=False)  # 全局配置值


class UploadStreamers(BaseModel):
    """投稿模板"""
    __tablename__ = "uploadstreamers"

    id: Mapped[int] = mapped_column(primary_key=True)  # 自增主键
    template_name: Mapped[str] = mapped_column(nullable=False)  # 模板名称
    title: Mapped[str] = mapped_column(
        nullable=True)  # 自定义标题的时间格式, {title}代表当场直播间标题 {streamer}代表在本config里面设置的主播名称 {url}代表设置的该主播的第一条直播间链接
    tid: Mapped[int] = mapped_column(nullable=True)  # 投稿分区码,171为电子竞技分区
    copyright: Mapped[int] = mapped_column(nullable=True)  # 1为自制
    cover_path: Mapped[str] = mapped_column(nullable=True)  # 封面路径
    # 支持strftime, {title}, {streamer}, {url}占位符。
    description = mapped_column(TEXT(), nullable=True)  # 视频简介
    dynamic: Mapped[str] = mapped_column(nullable=True)  # 粉丝动态
    dtime: Mapped[int] = mapped_column(nullable=True)  # 设置延时发布时间，距离提交大于2小时，格式为时间戳
    dolby: Mapped[int] = mapped_column(nullable=True)  # 是否开启杜比音效, 1为开启
    hires: Mapped[int] = mapped_column(nullable=True)  # 是否开启Hi-Res, 1为开启
    open_elec: Mapped[int] = mapped_column(nullable=True)  # 是否开启充电面板, 1为开启
    no_reprint: Mapped[int] = mapped_column(nullable=True)  # 自制声明, 1为未经允许禁止转载
    uploader: Mapped[str] = mapped_column(nullable=True)  # 覆盖全局默认上传插件，Noop为不上传，但会执行后处理
    user_cookie: Mapped[str] = mapped_column(nullable=True)  # 使用指定的账号上传
    tags = mapped_column(JSON(), nullable=False)  # JSONField()  # 标签
    credits = mapped_column(JSON(), nullable=True)  # JSONField(null=True)  # 简介@模板
    up_selection_reply: Mapped[int] = mapped_column(nullable=True)  # 精选评论
    up_close_reply: Mapped[int] = mapped_column(nullable=True)  # 关闭评论
    up_close_danmu: Mapped[int] = mapped_column(nullable=True)  # 精选评论
    livestreamers: Mapped[List["LiveStreamers"]] = relationship(back_populates="uploadstreamers")


class LiveStreamers(BaseModel):
    """每个直播间的配置"""
    __tablename__ = "livestreamers"

    id: Mapped[int] = mapped_column(primary_key=True)  # 自增主键
    url: Mapped[str] = mapped_column(nullable=False, unique=True)  # 直播间地址
    remark: Mapped[str] = mapped_column(nullable=False)  # 对应配置文件中 {streamer} 变量
    filename_prefix: Mapped[str] = mapped_column(nullable=True)  # filename_prefix 支持模板
    # 外键, 对应 UploadStreamers, 且启用级联删除
    upload_streamers_id = mapped_column(ForeignKey("uploadstreamers.id", ondelete="CASCADE"), nullable=True)
    uploadstreamers: Mapped[UploadStreamers] = relationship(back_populates="livestreamers")
    format: Mapped[str] = mapped_column(nullable=True)  # 视频格式
    preprocessor = mapped_column(JSON(), nullable=True)  # 开始下载直播时触发
    downloaded_processor = mapped_column(JSON(), nullable=True)  # 准备上传直播时触发
    postprocessor = mapped_column(JSON(), nullable=True)  # 上传完成后触发
    opt_args = mapped_column(JSON(), nullable=True)  # ffmpeg参数
