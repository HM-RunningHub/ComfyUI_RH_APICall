from .RH_SettingsNode import SettingsNode
from .RH_NodeInfoListNode import NodeInfoListNode
from .RH_NodeInfoList10Node import NodeInfoList10Node
from .RH_ExecuteNode import ExecuteNode
from .RH_ImageUploaderNode import ImageUploaderNode
from .RH_VideoUploader import RH_VideoUploader
from .RH_AudioUploader import RH_AudioUploader
from .RH_VideoUploaderPath import RH_VideoUploaderPath

from .RH_Utils import *

NODE_CLASS_MAPPINGS = {
    "RH_SettingsNode": SettingsNode,
    "RH_NodeInfoListNode": NodeInfoListNode,
    "RH_NodeInfoList10Node": NodeInfoList10Node,
    "RH_ExecuteNode": ExecuteNode,
    "RH_ImageUploaderNode": ImageUploaderNode,
    "RH_Utils": AnyToStringNode,
    "RH_ExtractImage": RH_Extract_Image_From_List,
    "RH_BatchImages": RH_Batch_Images_From_List,
    "RH_VideoUploader": RH_VideoUploader,
    "RH_AudioUploader": RH_AudioUploader,
    "RH_VideoUploaderPath": RH_VideoUploaderPath,

}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RH_SettingsNode": "RH Settings",
    "RH_NodeInfoListNode": "RH Node Info List",
    "RH_NodeInfoList10Node": "RH Node Info List (Dynamic)",
    "RH_ExecuteNode": "RH Execute",
    "RH_ImageUploaderNode": "RH Image Uploader",
    "RH_Utils": "RH Anything to String",
    "RH_ExtractImage": "RH Extract Image From ImageList",
    "RH_BatchImages": "RH Batch Images From ImageList",
    "RH_VideoUploader": "RH Video Uploader",
    "RH_AudioUploader": "RH Audio Uploader",
    "RH_VideoUploaderPath": "RH Video Uploader Path",

}

# Web Directory Setup
# Tells ComfyUI where to find the JavaScript files associated with nodes in this package
WEB_DIRECTORY = "./web/js"


__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
