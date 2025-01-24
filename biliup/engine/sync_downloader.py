#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import os
import queue
import threading
import subprocess
import time
from urllib.parse import urlparse


logger = logging.getLogger('biliup.engine.sync_downloader')


def pad_file_to_size(filename, min_file_size):
    """
    若文件大小不足 min_file_size，则在文件末尾填充 0x00 至目标大小。
    """
    if not os.path.exists(filename):
        return

    current_size = os.path.getsize(filename)
    if current_size < min_file_size:
        need_pad = min_file_size - current_size
        print(f"[pad_file_to_size] 补齐文件 {filename}："
              f"填充 {need_pad} 字节 0x00 使其达到 {min_file_size} 字节")
        with open(filename, "ab") as f:
            f.write(b"\x00" * need_pad)


class SyncDownloader:
    """
    同步下载-切片类
    说明：
      1. 在 run() 方法中，单线程循环执行录制逻辑；
      2. 每次启动一段 ffmpeg 录制，并读取 streamlink stdout 作为输入；
      3. 当 ffmpeg 录制结束后，杀掉当前的 streamlink 进程并补齐文件大小；
      4. 若中途发现 streamlink 无数据可读（EOF），则说明没有更多内容可下载，结束整个程序。
    """

    def __init__(self,
                 stream_url="http://localhost:8888/stream0/index.m3u8",
                 headers={"a": "1"},
                 segment_duration=10,
                 max_file_size=100,
                 output_prefix="segment_",
                 video_queue=None):
        """
        :param stream_url:   拉流地址

        :param segment_duration: 每段录制时长（秒）（暂时不用）
        :param max_file_size:     文件最小大小，不足时进行 0x00 填充 单位 MB
        :param read_block_size:   从 streamlink stdout 读取数据时的单次块大小
        :param output_prefix:     输出文件名前缀
        """
        self.stream_url = stream_url
        self.quality = "best"
        self.headers = headers
        self.segment_duration = segment_duration
        self.read_block_size = 4096
        self.max_file_size = max_file_size
        self.output_prefix = output_prefix

        self.video_queue: queue.SimpleQueue = video_queue
        self.stop_event = threading.Event()

    def run_ffmpeg_with_url(self, ffmpeg_cmd, output_filename):
        with subprocess.Popen(ffmpeg_cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE) as ffmpeg_proc:
            # print("[run] 启动 ffmpeg...")
            logging.info("[run] 启动 ffmpeg...")
            if output_filename == "-":
                data = ffmpeg_proc.stdout.read(1024)  # 读取第一个 data
                if not data:  # 如果第一个 data 为空
                    logging.info("[run] ffmpeg 没有输出数据，返回 False")
                    return False
                self.video_queue.put(data)  # 将第一个数据放入队列
                while True:
                    data = ffmpeg_proc.stdout.read(1024)
                    if not data:
                        logging.info("[run] ffmpeg stdout 已到达 EOF。结束本段写入。")
                        break
                    self.video_queue.put(data)
            ffmpeg_proc.wait()
        return True  # 如果正常执行，返回 True

    def run_streamlink_with_ffmpeg(self, streamlink_cmd, ffmpeg_cmd, output_filename):
        with subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as streamlink_proc:
            logging.info("[run] 启动 streamlink...")
            with subprocess.Popen(ffmpeg_cmd, stdin=streamlink_proc.stdout, stdout=subprocess.PIPE) as ffmpeg_proc:
                logging.info("[run] 启动 ffmpeg...")
                if output_filename == "-":
                    logging.info("[run] 读取 ffmpeg stdout...")
                    # 读取第一个数据
                    data = ffmpeg_proc.stdout.read(1024)
                    if not data:  # 如果第一个数据为空
                        logging.info("[run] ffmpeg 没有输出数据，返回 False")
                        streamlink_proc.kill()  # 终止 streamlink 进程
                        ffmpeg_proc.kill()  # 终止 ffmpeg 进程
                        return False

                    self.video_queue.put(data)  # 将第一个数据放入队列
                    # 继续读取剩余数据
                    while True:
                        data = ffmpeg_proc.stdout.read(1024)
                        if not data:
                            logging.info("[run] ffmpeg stdout 已到达 EOF。结束本段写入。")
                            break
                        self.video_queue.put(data)

                ffmpeg_proc.wait()
                logging.info("[run] ffmpeg 已到达输出大小并退出。结束本段写入。")

            # streamlink_proc.kill()  # 确保终止 streamlink 进程
            # streamlink_proc.wait()

        return True  # 如果一切正常，返回 True

    def build_ffmpeg_cmd(self, input_source, output_filename, headers, segment_duration):
        cmd = [
            "ffmpeg",
            "-loglevel", "quiet",
            # "-"  # 覆盖输出文件
        ]
        if headers:
            cmd += ["-headers", ''.join(f'{key}: {value}\r\n' for key, value in headers.items())]
        for i in ["-i", input_source,  # 输入源
                  # "-t", str(segment_duration),
                  "-fs", f"{self.max_file_size}M",
                  "-c:v", "copy",
                  "-c:a", "copy",
                  "-movflags", "+frag_keyframe+empty_moov",
                  "-f", "matroska"]:
            cmd.append(i)
        cmd.append("-")

        return cmd

    def run(self):
        """
        主逻辑：循环进行分段录制。
        - 每段录制：
          1) 启动 streamlink；若 EOF 则退出。
          2) 启动 ffmpeg (带 -fs 参数限制输出大小)；
          3) 从 streamlink stdout 读数据，写给 ffmpeg stdin；
          4) ffmpeg 到时长后退出，然后杀掉 streamlink；
          5) 进入下一段，如此往复。
        - 若中途发现 streamlink 无数据（EOF）则跳出循环。
        """

        file_index = 1
        retry_count = 0
        while True:
            if self.stop_event.is_set():
                break
            if retry_count >= 5:
                logger.info("这个直播流已经失效，停止下载器")
                return

            output_filename = f"{self.output_prefix}{file_index:03d}.mkv"
            # print(f"\n[run] ========== 准备录制第 {file_index} 段：{output_filename} ==========")
            # logging.info(f"\n[run] == 当前下载流地址：{self.stream_url} ==")
            logging.info(f"\n[run] == 准备录制第 {file_index} 段：{output_filename} ==")
            output_filename = "-"
            is_hls = '.m3u8' in urlparse(self.stream_url).path
            if not is_hls:
                # print("[run] 输入源不是 HLS 地址，将直接使用 ffmpeg 进行录制。", self.stream_url)
                logging.info("[run] 输入源不是 HLS 地址，将直接使用 ffmpeg 进行录制。")
                ffmpeg_cmd = self.build_ffmpeg_cmd(self.stream_url, output_filename,
                                                   self.headers, self.segment_duration)
                if not self.run_ffmpeg_with_url(ffmpeg_cmd, output_filename):
                    retry_count += 1
                    time.sleep(1)
                    continue
            else:
                # print("[run] 输入源是 HLS 地址，将使用 streamlink + ffmpeg 进行录制。")
                logging.info("[run] 输入源是 HLS 地址，将使用 streamlink + ffmpeg 进行录制。")
                streamlink_cmd = [
                    "streamlink",
                    "--stream-segment-threads", "3",
                    "--hls-playlist-reload-attempts", "1",
                    "--http-header", ';'.join([f'{key}={value}' for key, value in self.headers.items()]),
                    self.stream_url,
                    self.quality,
                    "-O"  # 输出到 stdout
                ]
                # output_filename = "-"
                ffmpeg_cmd = self.build_ffmpeg_cmd("pipe:0", output_filename, None, self.segment_duration)
                if not self.run_streamlink_with_ffmpeg(streamlink_cmd, ffmpeg_cmd, output_filename):
                    retry_count += 1
                    time.sleep(1)
                    continue

            # 6. 进入下一段
            # if file_index != 1:
            self.video_queue.put(None)  # 通知消费者线程本段录制结束
            file_index += 1


def main():
    slicer = SyncDownloader(
        stream_url="http://127.0.0.1:8888/live/index.m3u8",
        segment_duration=10,
        read_block_size=4096,
        output_prefix="segment_"
    )

    # ====【可选】消费者线程示例，演示如何拿到 video_queue 的数据====
    def consumer():
        file_index = 1
        while True:
            data_count = 0
            with open(f"output_{file_index}.mkv", "wb") as f:
                while True:
                    data = slicer.video_queue.get()  # 阻塞式获取
                    if data is None:
                        break
                    f.write(data)
                    data_count += len(data)
                    # print(f"[consumer] 写入文件 output_{file_index}.mkv，大小：{data_count} 字")
            print(f"[consumer] 写入文件 output_{file_index}.mkv，大小：{data_count} 字")

            if data_count < 100:
                print(f"[consumer] 无效文件，删除 output_{file_index}.mkv")
                os.remove(f"output_{file_index}.mkv")
                slicer.stop_event.set()
                break

            pad_file_to_size(f"output_{file_index}.mkv", 100 * 1024 * 1024)  # 补齐文件大小
            file_index += 1
            # if slicer.stop_event.is_set():
            # break
            # 在这里可以对 data 做进一步处理，比如再推到别的地方

    # 启动消费者线程
    t = threading.Thread(target=consumer, daemon=True)
    t.start()

    # 启动下载录制主逻辑
    slicer.run()

    # 停止消费者（如录制完毕后可执行）
    # slicer.video_queue.put(None)

    # t.join()


if __name__ == "__main__":
    main()
