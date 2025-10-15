# 更新说明 - [2025-10-15]

## 主要更新
*   **网络容错增强**: WebSocket 连接中断时自动切换到 HTTP 轮询，避免因网络波动导致任务失败
*   **多视频输出**: 新增 5 个独立视频输出端口 (video1-video5)，支持同时返回多个视频

# 更新说明 - [2025-07-18]
如果云端输出的是视频文件，节点能直接输出ComfyUI VIDEO,不需要再通过图片再合并成VIDEO
# 更新说明 - [2025-05-05]

本次重大升级，增强了功能并大幅提升了稳定性。

## 主要更新亮点
*   **支持调用AI WebAPP**:
*   **全面的输入支持**:
    *   **新增**: 支持 video、audio的文件上传。
*   **全面的输出支持**:
    *   **新增**: 支持 images、video frames、Latent、Text、Audio 文件输出。

*   **实时进度与监控**:
    *   增加了 ComfyUI **进度条** 显示，并提供任务状态反馈,更完善的控制台log输出
*   **增强的可靠性、并发控制**:
# ComfyUI 插件：调用和执行 RunningHub 工作流

该插件用于在本地 ComfyUI 环境中便捷地调用和执行 RunningHub 上的工作流。它是对 [RunningHub API 文档](https://gold-spleen-bf1.notion.site/RunningHub-API-1432ece0cf5f8026aaa8e4b9190f6f8e) 的一个 ComfyUI 实现。在使用本插件之前建议花2分钟阅读。如果你希望扩展该插件，或在使用过程中遇到问题，请参考上述文档。
### 可以通过本插件，将RunningHub方便的接入[Photoshop](https://github.com/NimaNzrii/comfyui-photoshop)，[变现宝](https://github.com/zhulu111/ComfyUI_Bxb) 等各种插件

## 使用步骤

### 1. 安装插件
在终端中运行以下命令以克隆插件到本地：
```bash
git clone https://github.com/HM-RunningHub/ComfyUI_RH_APICall
```
### 2. 注册并获取 API Key
访问 [RunningHub 官网](https://www.runninghub.cn) 注册账户并获取你的 API Key。

### 3. 在 ComfyUI 中调用 RunningHub 上的个人工作流
完成安装和配置后，你就可以在本地的 ComfyUI 环境中调用 RunningHub 上的个人工作流了。请把示例中的相关配置参数改成你自己的。

### 4. 本地工作流与RunningHub工作流节点对应关系与配置说明。
通过NodeInfolist节点，可以修改RH工作流每个节点的值。比如常见的提示词，种子，生成批次等
![image](https://github.com/user-attachments/assets/e6d76026-13bb-4ee7-8bcf-2cbc64a046ce)

### 5. 实例(工作流json在examples目录下)
#### ALL IN One
![image](https://github.com/user-attachments/assets/0dfe5998-206b-491f-a740-82938eb04e8d)
#### 文生图：
![image](https://github.com/user-attachments/assets/3b00beeb-1d0d-4fc2-b635-d31cfcf06887)
#### 图生图：
![image](https://github.com/user-attachments/assets/552bf53c-8913-474e-838a-c110e9dbc6d0)
#### 连接Photoshop
![image](https://github.com/user-attachments/assets/72c7ff4a-f6ef-43d5-a95c-242fbff5aafc)

