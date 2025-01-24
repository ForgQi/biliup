'use client'
import React from 'react'
import styles from '../../styles/dashboard.module.scss'
import { Form, Select, Space } from '@douyinfe/semi-ui'
import { IconUpload, IconDownload } from '@douyinfe/semi-icons'

const Global: React.FC = () => {
  return (
    <>
      {/* 全局下载 */}
      <div className={styles.frameDownload}>
        <div className={styles.frameInside}>
          <div className={styles.group}>
            <div className={styles.buttonOnlyIconSecond} />
            <div
              className={styles.lineStory}
              style={{
                color: 'var(--semi-color-bg-0)',
                display: 'flex',
              }}
            >
              <IconDownload size="small" />
            </div>
          </div>
          <p className={styles.meegoSharedWebWorkIt}>全局下载设置</p>
        </div>
        <Form.Select
          label="下载插件（downloader）"
          field="downloader"
          placeholder="stream-gears（默认）"
          maxTagCount={3}
          // initValue="stream-gears"
          extraText={
            <div style={{ fontSize: '14px' }}>
              选择全局默认的下载插件, 可选:
              <br />
              1. streamlink（仅限 hls 流，不支持的流将回退到 ffmpeg。需安装 FFmpeg）
              <br />
              2. ffmpeg（需安装 FFmpeg）
              <br />
              3. stream-gears（默认。防 FLV 流花屏）
              <br />
              4. sync-downloader（流式边录边传。文件不保存硬盘，不受 pool2/threads 控制，请确保上传带宽充足。需安装 FFmpeg）
            </div>
          }
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        >
          <Select.Option value="streamlink">streamlink（hls多线程下载）</Select.Option>
          <Select.Option value="ffmpeg">ffmpeg</Select.Option>
          <Select.Option value="stream-gears">stream-gears（默认）</Select.Option>
          <Select.Option value="sync-downloader">sync-downloader（边录边传）</Select.Option>
        </Form.Select>
        <Form.InputNumber
          label="视频分段大小（file_size）"
          extraText={
            <div style={{ fontSize: '14px' }}>
              录像单文件大小限制，超过此大小触发文件分割。下载回放时无法使用。
              <br />
              单位：Byte，示例：4294967296（4GB）
            </div>
          }
          field="file_size"
          placeholder=""
          suffix={'Byte'}
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
        />
        <Form.Input
          field="segment_time"
          extraText={
            <div style={{ fontSize: '14px' }}>
              录像单文件时间限制，超过此时长触发文件分割。
              <br />
              格式：&apos;00:00:00&apos;（时:分:秒）
            </div>
          }
          label="视频分段时长（segment_time）"
          placeholder="01:00:00"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
          rules={[
            {
              pattern: /^[^：]*$/,
              message: '请使用英文冒号',
            },
            {
              pattern: /^[0-9:]*$/,
              message: '只接受数字和英文冒号',
            },
            {
              pattern: /^[0-9]{2,4}:[0-5][0-9]:[0-5][0-9]$/,
              message: '分或秒不符合规范',
            },
          ]}
          stopValidateWithError={true}
        />
        <Form.Input
          field="filename_prefix"
          extraText={
            <div style={{ fontSize: '14px' }}>
              全局文件名模板。可被单个主播文件名模板覆盖。可用变量如下
              <br />
              {'\u007B'}streamer{'\u007D'}: 录播备注（必须保留）
              <span style={{ margin: '0 20px' }}></span>
              {'\u007B'}title{'\u007D'}: 直播标题
              <br />
              %Y-%m-%d %H_%M_%S: 开始录制时的 年-月-日 时_分_秒
            </div>
          }
          label="文件名模板（filename_prefix）"
          placeholder="{streamer}%Y-%m-%dT%H_%M_%S"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        />
        <Form.Switch
          field="segment_processor_parallel"
          extraText={<div style={{ fontSize: '14px' }}>开启后无法保证分段后处理先后执行顺序</div>}
          label="视频分段后处理并行（segment_processor_parallel)"
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
        />
        <Form.InputNumber
          field="filtering_threshold"
          extraText={
            <div style={{ fontSize: '14px' }}>
              小于此大小的视频文件将会被过滤删除。
              <br />
              单位：MB
            </div>
          }
          label="碎片过滤（filtering_threshold）"
          suffix={'MB'}
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        />

        <Form.InputNumber
          field="delay"
          label="下播延迟检测（delay)"
          extraText={
            <div style={{ fontSize: '14px' }}>
              当检测到主播下播后，延迟一定时间再次检测确认，避免特殊情况提早启动上传导致分稿件。
              <br />
              单位：秒
              <br />
              默认延迟时间为 0 秒
            </div>
          }
          placeholder="0"
          suffix="s"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        />
        <Form.InputNumber
          field="event_loop_interval"
          extraText={
            <div style={{ fontSize: '14px' }}>
              单个主播检测间隔时间，单位：秒。比如虎牙有10个主播，每个主播会间隔10秒检测
              <br />
              单位：秒
            </div>
          }
          label="直播事件检测间隔（event_loop_interval）"
          suffix="s"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        />
        <Form.InputNumber
          field="pool1_size"
          extraText="负责下载事件的线程池大小，用于限制最大同时录制数。"
          label="下载线程池大小（pool1_size）"
          placeholder={5}
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        />
      </div>

      <Space />

      {/* 全局上传 */}
      <div className={styles.frameUpload}>
        <div className={styles.frameInside}>
          <div className={styles.group}>
            <div className={styles.buttonOnlyIconSecond} />
            <div
              className={styles.lineStory}
              style={{
                color: 'var(--semi-color-bg-0)',
                display: 'flex',
              }}
            >
              <IconUpload size="small" />
            </div>
          </div>
          <p className={styles.meegoSharedWebWorkIt}>全局上传设置</p>
        </div>

        <Form.Select
          field="submit_api"
          label="提交接口（submit_api）"
          extraText="B站投稿提交接口，默认为自动选择。"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        >
          <Form.Select.Option value="web">网页端（web）</Form.Select.Option>
          <Form.Select.Option value="client">客户端（client）</Form.Select.Option>
        </Form.Select>
        <Form.Select
          field="uploader"
          label="上传插件（uploader）"
          extraText="全局默认上传插件选择。"
          placeholder="biliup-rs"
          noLabel={true}
          style={{ width: '100%', display: 'none' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
          initValue="Noop"
        >
          <Form.Select.Option value="bili_web">bili_web</Form.Select.Option>
          <Form.Select.Option value="biliup-rs">biliup-rs</Form.Select.Option>
          <Form.Select.Option value="Noop">Noop（即不上传，但会执行后处理）</Form.Select.Option>
        </Form.Select>
        <Form.Select
          field="lines"
          label="上传线路（lines）"
          extraText="b站上传线路选择，默认为自动模式，可手动切换为bda, bda2, ws, qn, bldsa, tx, txa"
          placeholder="AUTO（自动，默认）"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        >
          <Form.Select.Option value="AUTO">AUTO（自动，默认）</Form.Select.Option>
          <Form.Select.Option value="alia">alia（海外-阿里云）</Form.Select.Option>
          {/* <Form.Select.Option value="bda">bda</Form.Select.Option> */}
          <Form.Select.Option value="bda2">bda2（大陆-百度云）</Form.Select.Option>
          <Form.Select.Option value="bldsa">bldsa（大陆-B站自建）</Form.Select.Option>
          <Form.Select.Option value="qn">qn（全球-七牛）</Form.Select.Option>
          <Form.Select.Option value="tx">tx（大陆-腾讯云）</Form.Select.Option>
          <Form.Select.Option value="txa">txa（海外-腾讯云）</Form.Select.Option>
        </Form.Select>
        <Form.InputNumber
          field="threads"
          placeholder={3}
          extraText="单文件并发上传数,未达到带宽上限时,增大此值可提高上传速度(不要设置过大,部分线路限制为8,如速度不佳优先调整上传线路)"
          label="上传并发（threads）"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
          showClear={true}
        />

        <Form.InputNumber
          field="pool2_size"
          extraText={
            <div style={{ fontSize: '14px' }}>负责上传事件的线程池大小。根据实际带宽设置。</div>
          }
          placeholder={3}
          label="上传线程池大小（pool2_size）"
          style={{ width: '100%' }}
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
        />
        <Form.Switch
          field="use_live_cover"
          extraText={
            <div style={{ fontSize: '14px' }}>
              使用直播间封面作为投稿封面。此封面优先级低于单个主播指定的自定义封面，保存于cover文件夹下，上传后自动删除。
              <br />
              目前支持平台：哔哩哔哩，Twitch，YouTube。
            </div>
          }
          label="使用直播间封面作为投稿封面（use_live_cover)"
          fieldStyle={{
            alignSelf: 'stretch',
            padding: 0,
          }}
        />
      </div>
    </>
  )
}

export default Global
