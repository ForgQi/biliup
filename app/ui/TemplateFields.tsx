import React, { useState, useMemo, useEffect, useCallback } from 'react'
import { FormFCChild } from '@douyinfe/semi-ui/lib/es/form'
import {
  IconChevronDown,
  IconChevronUp,
  IconMinusCircle,
  IconPlusCircle,
} from '@douyinfe/semi-icons'
import {
  Avatar,
  Button,
  Collapsible,
  Form,
  InputGroup,
  Space,
  Typography,
  ArrayField,
  Notification,
  ScrollList,
  ScrollItem,
  // TextArea,
  useFormState,
} from '@douyinfe/semi-ui'
import useSWR from 'swr'
import { BiliType, fetcher, StudioEntity } from '../lib/api-streamer'
import { useBiliUsers, useTypeTree } from '../lib/use-streamers'

const TemplateFields: React.FC<FormFCChild<StudioEntity & { isDtime: boolean }>> = ({
  formState,
  formApi,
  values,
}) => {
  const {
    Section,
    Input,
    DatePicker,
    TimePicker,
    Select,
    Switch,
    InputNumber,
    Checkbox,
    CheckboxGroup,
    RadioGroup,
    Radio,
    Cascader,
    TagInput,
    TextArea,
  } = Form
  const { Text } = Typography
  const { typeTree, isError, isLoading } = useTypeTree()
  const treeData = typeTree?.map((type: BiliType) => {
    return {
      ...type,
      children: type.children.map(cType => {
        return {
          label: (
            <>
              {cType.name}{' '}
              <Text type="quaternary" size="small">
                {cType.desc}
              </Text>
            </>
          ),
          value: cType.id,
        }
      }),
    }
  })
  const collapsed = (
    <>
      <CheckboxGroup
        field="sound"
        options={[
          { label: '杜比音效', value: 'dolby' },
          { label: 'Hi-Res无损音质', value: 'hires' },
        ]}
        direction="horizontal"
        label="音效设置"
      />
      <CheckboxGroup
        field="interaction"
        options={[
          { label: '关闭弹幕', value: 'up_close_danmu' },
          { label: '关闭评论', value: 'up_close_reply' },
          { label: '开启精选评论', value: 'up_selection_reply' },
        ]}
        direction="horizontal"
        label="互动设置"
      />
      <Input field="dynamic" label="粉丝动态" style={{ width: 464 }} />
      <Form.Select
        field="uploader"
        label="上传插件"
        initValue={values.uploader ?? 'biliup-rs'}
        style={{ width: 250 }}
        showClear
      >
        <Form.Select.Option value="bili_web">bili_web</Form.Select.Option>
        <Form.Select.Option value="biliup-rs">biliup-rs</Form.Select.Option>
        <Form.Select.Option value="Noop">Noop</Form.Select.Option>
      </Form.Select>
      <Switch field="no_reprint" label="自制声明" />
      <Form.Switch field="open_elec" label="开启充电面板" />
      <ArrayField field="credits">
        {({ add, arrayFields }) => (
          <Form.Section text="简介@替换">
            <div className="semi-form-field-extra" style={{ fontSize: '14px' }}>
              如需在简介中@别人，请使用此项。示例：
              <br />
              简介：{'\u007B'}streamer{'\u007D'}主播直播间地址：{'\u007B'}url{'\u007D'} 【@credit】
              <br />
              其中的&quot;@credits&quot;会依次替换为下面输入的@
            </div>
            <Button icon={<IconPlusCircle />} onClick={add} theme="light">
              添加行
            </Button>
            {arrayFields.map(({ field, key, remove }, i) => (
              <div key={key} style={{ width: 1000, display: 'flex' }}>
                <InputGroup>
                  <Input field={`${field}.username`} label="需要@的用户名" placeholder="username" />
                  <Input field={`${field}.uid`} label="需要@的uid" placeholder="uid" />
                </InputGroup>
                <Button
                  type="danger"
                  theme="borderless"
                  icon={<IconMinusCircle />}
                  onClick={remove}
                  style={{ margin: 12 }}
                />
              </div>
            ))}
          </Form.Section>
        )}
      </ArrayField>
    </>
  )
  const [isOpen, setOpen] = useState(false)
  const { biliUsers } = useBiliUsers()
  const list = biliUsers?.map(item => {
    return {
      value: item.value,
      label: (
        <>
          <Avatar size="extra-small" src={item.face} />
          <span style={{ marginLeft: 8 }}>{item.name}</span>
        </>
      ),
    }
  })
  const toggle = () => {
    setOpen(!isOpen)
    formApi.scrollToField('isDtime')
  }
  const scrollStyle = {
    border: 'unset',
    boxShadow: 'unset',
    width: 300,
    height: 300,
  }
  const allowedHoursList = new Array(24 * 15).fill(0).map((_, i) => {
    return {
      value: i,
      text: `${i}小时`,
      disabled: i < 4 || i >= 24 * 15,
    }
  })
  const allowedMinutesList = new Array(60 / 5).fill(0).map((_, i) => {
    return {
      value: i * 5,
      text: `${i * 5}分钟`,
    }
  })
  const [selectedHours, setSelectedHours] = useState(4)
  const [selectedMinutes, setSelectedMinutes] = useState(0)

  useEffect(() => {
    const dtime = formApi.getValue('dtime')
    if (dtime) {
      const hours = Math.floor(dtime / 3600)
      const minutes = Math.floor((dtime % 3600) / 60)
      if (hours >= 4 && hours < 24 * 15) {
        setSelectedHours(hours)
        setSelectedMinutes(Math.floor(minutes / 5) * 5)
      }
    }
  }, [formApi]);

  // useEffect(() => {
  //   const extraFieldsList = formApi.getValue('extra_fields_list') as Array<{key: string; value: string}>;
  //   if (!extraFieldsList?.length) return;

  //   const extraFieldsObj = extraFieldsList.reduce((tempObj: Record<string, string | number>, item) => {
  //     if (item?.key && item?.value !== undefined) {
  //       tempObj[item.key] = !isNaN(Number(item.value)) ? Number(item.value) : item.value;
  //     }
  //     return tempObj;
  //   }, {});
  //   formApi.setValue('extra_fields', JSON.stringify(extraFieldsObj));
  // }, []);

  // useEffect(() => {
  //   const extraFields = formApi.getValue('extra_fields');
  //   if (extraFields) {
  //     try {
  //       const parsedFields = JSON.parse(extraFields);
  //       const fieldsList = Object.entries(parsedFields).map(([key, value]) => ({
  //         key,
  //         value: String(value)
  //       }));
  //       formApi.setValue('extra_fields_list', fieldsList);
  //     } catch (e) {
  //       console.error('解析 extra_fields 失败:', e);
  //     }
  //   }
  // }, []);

  return (
    <>
      <Section text={'基本信息'}>
        <Input
          rules={[{ required: true }]}
          field="template_name"
          label="模板名称"
          style={{ width: 464 }}
        />
        <Form.Select
          rules={[{ required: true }]}
          field="user_cookie"
          label={{ text: '投稿账号' }}
          style={{ width: 176 }}
          optionList={list}
        />
      </Section>
      <Section text={'基本设置'}>
        <Input
          field="title"
          label="视频标题"
          style={{ width: 464 }}
          placeholder="稿件标题"
          extraText={
            <div style={{ fontSize: 14 }}>
              {'\u007B'}streamer{'\u007D'}: 录播备注
              <br />
              {'\u007B'}title{'\u007D'}: 直播标题
              <br />
              %Y-%m-%d %H_%M_%S: 开始录制时的 年-月-日 时_分_秒
            </div>
          }
        />
        <RadioGroup
          field="copyright"
          label="类型"
          direction="vertical"
          initValue={formApi.getValue('copyright') ?? 2}
          extraText={<div style={{ fontSize: 14 }}>如不填写转载来源默认为直播间地址</div>}
        >
          <Radio value={2} style={{ alignItems: 'center', flexShrink: 0 }}>
            <span style={{ flexShrink: 0 }}>转载</span>
            <Input
              field="copyright_source"
              onClick={() => formApi.setValue('copyright', 2)}
              placeholder="转载视频请注明来源（例：转自http://www.xx.com/yy）注明来源会更快地通过审核哦"
              noLabel
              fieldStyle={{ padding: 0, marginLeft: 24, width: 560 }}
            />
          </Radio>
          <div onClick={() => formApi.setValue('copyright_source', '')}>
            <Radio value={1}>自制</Radio>
          </div>
        </RadioGroup>
        <Cascader
          field="tid"
          label="分区"
          style={{ width: 272 }}
          treeData={treeData}
          placeholder="投稿分区"
          dropdownStyle={{ maxWidth: 670 }}
          rules={[{ required: true }]}
        />
        <TagInput
          max={12}
          maxLength={20}
          field="tags"
          label="标签"
          allowDuplicates={false}
          addOnBlur={true}
          separator=","
          placeholder="可用英文逗号分隔以批量输入标签，失焦/Enter 以保存"
          onChange={v => console.log(v)}
          style={{ width: 560 }}
          rules={[{ required: true, message: 'Tag不能为空' }]}
          onExceed={v => {
            Notification.warning({
              title: '标签输入错误',
              position: 'top',
              content: '标签数量不能超过12个',
              duration: 3,
            })
          }}
          onInputExceed={v => {
            Notification.warning({
              title: '标签输入错误',
              position: 'top',
              content: '单个标签字数不能超过20',
              duration: 3,
            })
          }}
        />
        <Input
          field="cover_path"
          label="视频封面"
          style={{ width: 464 }}
          placeholder="/cover/up.jpg"
        />
        <TextArea
          style={{ maxWidth: 560 }}
          field="description"
          label="简介"
          placeholder="填写更全面的相关信息，让更多的人能找到你的视频吧"
          autosize
          maxCount={2000}
          showClear
        />
        <TextArea
          style={{ maxWidth: 560 }}
          field="extra_fields"
          label="额外字段"
          placeholder="Json格式，示例：{key: 'value'}"
          autosize
          maxCount={2000}
          showClear
          rules={[
            {
              validator: (rule, value) => {
                if (!value) return true;
                try {
                  JSON.parse(value);
                  return true;
                } catch (e) {
                  return false;
                }
              },
              message: '请输入正确的Json格式文本',
            }
          ]}
        />
        {/* <ArrayField
          field='extra_fields_list'
          initValue={(() => {
            const extraFields = formApi.getValue('extra_fields');
            if (!extraFields) return [];
            try {
              const parsedFields = JSON.parse(extraFields);
              return Object.entries(parsedFields).map(([key, value]) => ({
                key,
                value: String(value)
              }));
            } catch (e) {
              console.error('解析 extra_fields 失败:', e);
              return [];
            }
          })()}
        >
          {({ add, arrayFields }) => (
            <React.Fragment>
              <Button onClick={add} icon={<IconPlusCircle />} theme='light'>Add new line</Button>
              {
                arrayFields.map(({ field, key, remove }, i) => (
                  <div key={key} style={{ width: 1000, display: 'flex' }}>
                    <Input
                      field={`${field}.key`}
                      label={`${field}.key`}
                      style={{ width: 200, marginRight: 16 }}
                      rules={[
                        {
                          validator: (rule, value) => {
                            if (!value) return true;
                            const currentFields = (formApi.getValue('extra_fields_list') || []) as Array<{key: string; value: string}>;
                            const keyCount = currentFields.filter(
                              (item: any, index: number) =>
                                item.key === value &&
                                `extra_fields_list.${index}.key` !== field + '.key'
                            ).length;
                            if (keyCount > 1) {
                              throw new Error('键名不能重复');
                            }
                            return true;
                          }
                        }
                      ]}
                    />
                    <Input
                      field={`${field}.value`}
                      label={`${field}.value`}
                      style={{ width: 200, marginRight: 16 }}
                    />
                    <Button
                      type='danger'
                      theme='borderless'
                      icon={<IconMinusCircle />}
                      onClick={remove}
                      style={{ margin: 12 }}
                    />
                  </div>
                ))}
            </React.Fragment>
          )}
        </ArrayField> */}

        <div style={{ display: 'flex', alignItems: 'center', color: 'var(--semi-color-tertiary)' }}>
          <Switch
            field="isDtime"
            label={{ text: '定时发布' }}
            checkedText="｜"
            uncheckedText="〇"
          />
          <span style={{ paddingLeft: 12, fontSize: 12 }}>
            (当前+2小时 ≤ 可选时间 ≤
            当前+15天，转载稿件撞车判定以过审发布时间为准。上传速度不佳的机器请谨慎开启，或设置更大的延迟时间。)
          </span>
        </div>
        {values.isDtime ? (
          <ScrollList
            style={scrollStyle}
            header={'延迟时间'}
            footer={
              <Button
                size="small"
                type="primary"
                onClick={() => {
                  const delaySeconds = selectedHours * 3600 + selectedMinutes * 60
                  formApi.setValue('dtime', delaySeconds)
                  Notification.success({
                    title: '保存成功',
                    content: `延迟时间：${selectedHours}小时${selectedMinutes}分钟`,
                    duration: 3,
                    position: 'top',
                  })
                  console.log(delaySeconds)
                }}
              >
                确认
              </Button>
            }
          >
            <ScrollItem
              mode="wheel"
              cycled={true}
              list={allowedHoursList}
              type={1}
              selectedIndex={allowedHoursList.findIndex(item => item.value === selectedHours)}
              onSelect={item => setSelectedHours(item.value)}
            />
            <ScrollItem
              mode="wheel"
              cycled={true}
              list={allowedMinutesList}
              type={2}
              selectedIndex={allowedMinutesList.findIndex(item => item.value === selectedMinutes)}
              onSelect={item => setSelectedMinutes(item.value)}
            />
          </ScrollList>
        ) : null}
      </Section>

      <Section
        style={{ paddingBottom: 40 }}
        text={
          <div style={{ cursor: 'pointer' }} onClick={toggle}>
            更多设置{' '}
            {isOpen ? (
              <IconChevronUp style={{ marginLeft: 12 }} />
            ) : (
              <IconChevronDown style={{ marginLeft: 12 }} />
            )}
          </div>
        }
      >
        <Collapsible isOpen={isOpen} keepDOM>
          {collapsed}
        </Collapsible>
      </Section>
    </>
  )
}

export default TemplateFields
