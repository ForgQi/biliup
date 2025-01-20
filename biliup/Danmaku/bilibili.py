import json
import logging
import zlib
from struct import pack, unpack

import aiohttp
import brotli

from biliup.plugins import match1
from biliup.plugins import random_user_agent

logger = logging.getLogger('biliup')


class Bilibili:
    heartbeat = b'\x00\x00\x00\x1f\x00\x10\x00\x01\x00\x00\x00\x02\x00\x00\x00\x01\x5b\x6f\x62\x6a\x65\x63\x74\x20' \
                b'\x4f\x62\x6a\x65\x63\x74\x5d '
    heartbeatInterval = 30
    headers = {
        'accept': '*/*',
        'accept-encoding': 'gzip, deflate',
        'accept-language': 'zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3',
        'user-agent': random_user_agent(),
        'origin': 'https://live.bilibili.com',
        'referer': 'https://live.bilibili.com'
    }

    @staticmethod
    async def get_ws_info(url, content):

        uid = content['uid']
        # 传入内容中，如果 uid 不为 0，则 cookie 必然存在，且必然为详细模式
        if uid > 0:
            Bilibili.headers['cookie'] = content['cookie']

        # 获取弹幕认证信息
        danmu_wss_url = 'wss://broadcastlv.chat.bilibili.com/sub'
        room_id = content.get('room_id')
        async with aiohttp.ClientSession(headers=Bilibili.headers) as session:
            if not room_id:
                async with session.get("https://api.live.bilibili.com/room/v1/Room/room_init?id=" + match1(url, r'/(\d+)'),
                                   timeout=5) as resp:
                    room_json = await resp.json()
                    room_id = room_json['data']['room_id']
            async with session.get(f"https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?type=0&id={room_id}",
                                   timeout=5) as resp:
                danmu_info = await resp.json()
                danmu_token = danmu_info['data']['token']
                try:
                    # 允许可能获取不到返回的host
                    danmu_host = danmu_info['data']['host_list'][0]
                    danmu_wss_url = f"wss://{danmu_host['host']}:{danmu_host['wss_port']}/sub"
                except:
                    pass

            w_data = {
                'uid': uid,
                'roomid': room_id,
                'protover': 3,
                'platform': 'web',
                'type': 2,
                'key': danmu_token,
            }

            data = json.dumps(w_data).encode('utf-8')
            # logger.info(f"danmaku auth info {data}")
            reg_datas = [(pack('>i', len(data) + 16) + b'\x00\x10\x00\x01' + pack('>i', 7) + pack('>i', 1) + data)]
        return danmu_wss_url, reg_datas

    @staticmethod
    def decode_msg(data):
        msgs = []

        def decode_packet(packet_data):
            dm_list = []
            while True:
                try:
                    packet_len, header_len, ver, op, seq = unpack('!IHHII', packet_data[0:16])
                except Exception:
                    break
                if len(packet_data) < packet_len:
                    break

                if ver == 2:
                    dm_list.extend(decode_packet(zlib.decompress(packet_data[16:packet_len])))
                elif ver == 3:
                    dm_list.extend(decode_packet(brotli.decompress(packet_data[16:packet_len])))
                elif ver == 0 or ver == 1:
                    dm_list.append({
                        'type': op,
                        'body': packet_data[16:packet_len]
                    })
                else:
                    break

                if len(packet_data) == packet_len:
                    break
                else:
                    packet_data = packet_data[packet_len:]
            return dm_list

        dm_list = decode_packet(data)
        for dm in dm_list:
            try:
                msg = {}
                if dm.get('type') == 5:
                    j = json.loads(dm.get('body'))
                    msg['msg_type'] = {
                        'SEND_GIFT': 'gift',
                        'DANMU_MSG': 'danmaku',
                        'WELCOME': 'enter',
                        'NOTICE_MSG': 'broadcast',
                        'SUPER_CHAT_MESSAGE': 'super_chat',
                        'LIVE_INTERACTIVE_GAME': 'interactive_danmaku',  # 新增互动弹幕，经测试与弹幕内容一致
                        'GUARD_BUY': 'guard_buy'
                    }.get(j.get('cmd'), 'other')
                    # 2021-06-03 bilibili 字段更新, 形如 DANMU_MSG:4:0:2:2:2:0
                    if msg.get('msg_type', 'UNKNOWN').startswith('DANMU_MSG'):
                        msg['msg_type'] = 'danmaku'

                    if msg['msg_type'] == 'danmaku':
                        msg['name'] = (j.get('info', ['', '', ['', '']])[2][1] or
                                       j.get('data', {}).get('uname', ''))
                        msg['uid'] = j.get('info', ['', '', ['', '']])[2][0]
                        msg['content'] = j.get('info', ['', ''])[1]
                        msg["color"] = f"{j.get('info', '16777215')[0][3]}"

                        # 区分是表情包还是普通弹幕
                        msg_extra = json.loads(j.get('info', [['','','','','','','','','','','','','','','',{}]])[0][15].get("extra", "{}"))
                        if msg_extra.get("emoticon_unique", "") != "":
                            msg['content'] = f"表情【{msg_extra['emoticon_unique']}】"

                    elif msg['msg_type'] == 'super_chat':
                        msg['name'] = j.get('data', {}).get('user_info', {}).get('uname', "")
                        msg['uid'] = j.get('data', {}).get('uid', '')
                        msg['content'] = j.get('data', {}).get('message', '')
                        msg['price'] = int(j.get('data', {}).get('price', 0)) * 1000
                        msg['num'] = 1
                        msg['gift_name'] = "醒目留言"

                    elif msg['msg_type'] == "guard_buy":
                        msg['name'] = j.get('data', {}).get('username', '')
                        msg['uid'] = j.get('data', {}).get('uid', '')
                        msg['gift_name'] = j.get('data', {}).get('gift_name', '')
                        msg['price'] = j.get('data', {}).get('price', '')
                        msg['num'] = j.get('data', {}).get('num', '')
                        msg['content'] = f"{msg['name']}上了{msg['num']}个月{msg['gift_name']}"

                    elif msg['msg_type'] == 'gift':
                        msg['name'] = j.get('data', {}).get('uname', '')
                        msg['uid'] = j.get('data', {}).get('uid', '')
                        msg['gift_name'] = j.get('data', {}).get('giftName', '')
                        msg['price'] = j.get('data', {}).get('price', '')
                        msg['num'] = j.get('data', {}).get('num', '')
                        msg['content'] = f"{msg['name']}投喂了{msg['num']}个{msg['gift_name']}"

                    elif msg['msg_type'] == 'interactive_danmaku':
                        msg['name'] = j.get('data', {}).get('uname', '')
                        msg['content'] = j.get('data', {}).get('msg', '')
                        msg["color"] = '16777215'

                    elif msg['msg_type'] == 'broadcast':
                        msg['type'] = j.get('msg_type', 0)
                        msg['roomid'] = j.get('real_roomid', 0)
                        msg['content'] = j.get('msg_common', '')
                        msg['raw'] = j
                    else:
                        msg['content'] = j
                else:
                    msg = {'name': '', 'content': dm.get('body'), 'msg_type': 'other'}
                msgs.append(msg)
            except Exception as Error:
                logger.warning(f"{Bilibili.__name__}: 弹幕接收异常 - {Error}")
        return msgs
