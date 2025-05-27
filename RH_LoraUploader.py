import requests
import json
import os
import time
import hashlib # 用于计算 MD5

# 安全地尝试导入 folder_paths
try:
    import folder_paths
    comfyui_env_available = True
except ImportError:
    folder_paths = None # 如果不可用，则设置为 None
    comfyui_env_available = False
    print("RH_LoraUploader: 未找到 ComfyUI folder_paths。无法确定 LoRA 文件路径。")

def calculate_md5(file_path):
    """计算文件的 MD5 哈希值"""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

class RH_LoraUploader:
    @classmethod
    def INPUT_TYPES(cls):
        if not comfyui_env_available or not folder_paths:
            lora_files = ["手动输入 LoRA 文件名 (无 folder_paths)"]
        else:
            try:
                lora_files = folder_paths.get_filename_list("loras")
                if not lora_files:
                    lora_files = ["在 ComfyUI/models/loras 中未找到 LoRA"]
            except Exception as e:
                print(f"RH_LoraUploader: 获取 LoRA 列表时出错: {e}")
                lora_files = ["获取 LoRA 列表时出错"]

        return {
            "required": {
                "apiConfig": ("STRUCT",),
                "lora_name_select": (lora_files, ),
                "custom_lora_name_on_server": ("STRING", {"default": "", "multiline": False, "placeholder": "在服务器上的LoRA名称 (可选, 默认为文件名)"}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("uploaded_lora_filename_on_server",)
    FUNCTION = "upload_lora_and_get_filename"
    CATEGORY = "RunningHub"
    OUTPUT_NODE = False

    def upload_lora_and_get_filename(self, apiConfig, lora_name_select, custom_lora_name_on_server):
        if not comfyui_env_available or not folder_paths:
            raise ImportError("RH_LoraUploader 需要 folder_paths 模块来查找 LoRA 文件。")

        if not isinstance(apiConfig, dict) or not apiConfig.get("apiKey") or not apiConfig.get("base_url"):
            raise ValueError("提供给 RH_LoraUploader 的 apiConfig 结构无效或缺失。")

        if not lora_name_select or lora_name_select.strip() == "" or \
           lora_name_select.startswith("手动输入 LoRA 文件名") or \
           lora_name_select.startswith("在 ComfyUI/models/loras 中未找到") or \
           lora_name_select.startswith("获取 LoRA 列表时出错"):
            raise ValueError("未选择有效的 LoRA 文件或 LoRA 列表未正确加载。")

        apiKey = apiConfig["apiKey"]
        baseUrl = apiConfig["base_url"]

        try:
            lora_local_path = folder_paths.get_full_path("loras", lora_name_select)
            if not lora_local_path or not os.path.exists(lora_local_path):
                base_loras_path = os.path.join(folder_paths.get_folder_paths("loras")[0], lora_name_select)
                if os.path.exists(base_loras_path):
                    lora_local_path = base_loras_path
                else:
                    raise FileNotFoundError(f"未找到 LoRA 文件 '{lora_name_select}'。搜索路径: {lora_local_path} 及其他标准位置。")
            print(f"RH_LoraUploader: 找到本地 LoRA 文件于: {lora_local_path}")
            lora_md5_hex = calculate_md5(lora_local_path)
            print(f"RH_LoraUploader: 计算得到的 MD5: {lora_md5_hex}")
        except Exception as e:
            raise RuntimeError(f"查找或计算 LoRA 文件 '{lora_name_select}' MD5 时出错: {e}")

        server_lora_name_param = custom_lora_name_on_server.strip()
        if not server_lora_name_param:
            server_lora_name_param = os.path.splitext(os.path.basename(lora_name_select))[0]
        print(f"RH_LoraUploader: 用于 API 请求的 loraName 参数: {server_lora_name_param}")

        get_url_endpoint = f"{baseUrl}/api/openapi/getLoraUploadUrl"
        get_url_payload = {"apiKey": apiKey, "loraName": server_lora_name_param, "md5Hex": lora_md5_hex}
        headers_get_url = {'User-Agent': 'ComfyUI-RH_LoraUploader/1.1', 'Content-Type': 'application/json'}
        print(f"RH_LoraUploader: 步骤 1 - 请求上传 URL 从: {get_url_endpoint}，负载: {json.dumps(get_url_payload)}")
        
        response_get_url = None
        try:
            response_get_url = requests.post(get_url_endpoint, headers=headers_get_url, json=get_url_payload)
            response_get_url.raise_for_status()
            response_get_url_json = response_get_url.json()
            print(f"RH_LoraUploader: 步骤 1 - 获取 URL 响应 JSON: {json.dumps(response_get_url_json, ensure_ascii=False)}")
        except requests.exceptions.RequestException as e:
            error_message = f"步骤 1 - 请求上传 URL 失败: {e}"
            if hasattr(e, 'response') and e.response is not None:
                error_message += f"\n响应状态码: {e.response.status_code}\n响应内容: {e.response.text}"
            raise ConnectionError(error_message) from e
        except json.JSONDecodeError as e:
            raise ValueError(f"步骤 1 - 解析获取 URL 响应失败: {response_get_url.text if response_get_url else 'No response available'}") from e

        if response_get_url_json.get('code') != 0:
            raise ValueError(f"RunningHub API (获取 URL) 报告错误: {response_get_url_json.get('msg', '未知的 API 错误')}, 响应: {json.dumps(response_get_url_json, ensure_ascii=False)}")
        upload_data = response_get_url_json.get("data")
        if not upload_data or not isinstance(upload_data, dict):
            raise ValueError(f"从 API (获取 URL) 返回的 data 字段无效或缺失。响应: {json.dumps(response_get_url_json, ensure_ascii=False)}")
        presigned_upload_url = upload_data.get("url")
        server_filename_to_return = upload_data.get("fileName")
        if not presigned_upload_url or not server_filename_to_return:
            raise ValueError(f"从 API (获取 URL) 返回的数据中未找到 'url' 或 'fileName'。响应: {json.dumps(upload_data, ensure_ascii=False)}")
        print(f"RH_LoraUploader: 步骤 1 - 成功。预签名上传 URL: {presigned_upload_url}")
        print(f"RH_LoraUploader: 步骤 1 - 成功。服务器文件名: {server_filename_to_return}")

        # === 步骤 2: 上传文件到预签名 URL ===
        # ****** 关键尝试：使用 PUT 方法上传 ******
        headers_upload_file = {
            'User-Agent': 'ComfyUI-RH_LoraUploader/1.1', # 保持 User-Agent
            'Content-Type': 'application/octet-stream'  # 对于 PUT 通常也用这个
        }
        print(f"RH_LoraUploader: 步骤 2 - 准备使用 PUT 方法流式上传文件 {lora_local_path} 到 {presigned_upload_url}...")

        max_retries_upload = 3
        retry_delay_upload = 2
        last_exception_upload = None

        for attempt in range(max_retries_upload):
            try:
                with open(lora_local_path, 'rb') as f_lora:
                    # 使用 PUT 方法，并以流式方式发送文件数据
                    response_upload_file = requests.put( # <--- 改为 PUT
                        presigned_upload_url,
                        headers=headers_upload_file,
                        data=f_lora # requests 会流式处理文件对象
                    )
                
                print(f"RH_LoraUploader: 步骤 2 - 上传尝试 {attempt + 1}/{max_retries_upload} - 状态码: {response_upload_file.status_code}")
                
                if response_upload_file.status_code >= 400:
                     print(f"RH_LoraUploader: 步骤 2 - 上传错误响应内容 (状态码 {response_upload_file.status_code}): {response_upload_file.text}")
                
                response_upload_file.raise_for_status() 
                print(f"RH_LoraUploader: 步骤 2 - 文件成功上传到预签名 URL。")
                break 
            except requests.exceptions.RequestException as e:
                last_exception_upload = e
                print(f"RH_LoraUploader: 步骤 2 - 上传尝试 {attempt + 1} 失败: {e}")
                if hasattr(e, 'response') and e.response is not None:
                     print(f"RH_LoraUploader: 步骤 2 - 响应状态码: {e.response.status_code}")
                     print(f"RH_LoraUploader: 步骤 2 - 响应内容: {e.response.text}")
                
                if attempt < max_retries_upload - 1:
                    print(f"RH_LoraUploader: 步骤 2 - {retry_delay_upload} 秒后重试...")
                    time.sleep(retry_delay_upload)
                    retry_delay_upload *= 2 
                else:
                    print(f"RH_LoraUploader: 步骤 2 - 已达到最大重试次数 ({max_retries_upload})。")
                    error_detail = f"最后错误: {last_exception_upload}"
                    if hasattr(last_exception_upload, 'response') and last_exception_upload.response is not None:
                        error_detail += f"\n服务器响应: {last_exception_upload.response.status_code} - {last_exception_upload.response.text}"
                    raise ConnectionError(f"未能将 LoRA 文件上传到预签名 URL {presigned_upload_url}。{error_detail}") from last_exception_upload
        
        if last_exception_upload and attempt == max_retries_upload - 1:
            error_detail = f"最后错误: {last_exception_upload}"
            if hasattr(last_exception_upload, 'response') and last_exception_upload.response is not None:
                error_detail += f"\n服务器响应: {last_exception_upload.response.status_code} - {last_exception_upload.response.text}"
            raise ConnectionError(f"LoRA 文件上传在 {max_retries_upload} 次尝试后失败。{error_detail}")

        print(f"RH_LoraUploader: LoRA 上传流程完成。服务器文件名: {server_filename_to_return}")
        return (server_filename_to_return,)

NODE_CLASS_MAPPINGS = {
    "RH_LoraUploader": RH_LoraUploader
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "RH_LoraUploader": "RH LoRA Uploader"
}