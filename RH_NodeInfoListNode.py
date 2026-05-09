class NodeInfoListNode:
    def __init__(self):
        # Initialize an empty node_info_list to store all nodeInfo
        self.node_info_list = []

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "nodeId": ("INT", {"default": 0, "min": 0, "max": 999999, "step": 1}),
                "fieldName": ("STRING", {"default": ""}),
                "fieldValue": ("STRING", {"default": ""}),
            },
            "optional": {
                "previousNodeInfoList": ("ARRAY", {"default": []}),  # Make it optional with default empty list
            }
        }

    RETURN_TYPES = ("ARRAY",)  # Change output type to ARRAY
    CATEGORY = "RunningHub"
    FUNCTION = "process"

    def process(self, nodeId, fieldName, fieldValue, previousNodeInfoList=None):
        """
        This node allows users to configure multiple nodeId, fieldName, and fieldValue parameters,
        and output multiple nodeInfoList as arrays. Supports chaining, multiple nodes will be merged into one array.
        """
        self.node_info_list = []
        # Output debug information to view previousNodeInfoList
        print(f"Processing nodeId: {nodeId}, fieldName: {fieldName}, fieldValue: {fieldValue}")
        print(f"previousNodeInfoList: {previousNodeInfoList}")
        # Current node_info
        node_info = {"nodeId": nodeId, "fieldName": fieldName, "fieldValue": fieldValue}

        # If the previous node has output (previousNodeInfoList), add it to the current node_info_list
        if previousNodeInfoList:
            self.node_info_list.extend(previousNodeInfoList)  # Merge the output from the previous node

        # Add the current node_info to node_info_list
        self.node_info_list.append(node_info)

        # Output debug information to view the current node_info_list
        print(f"Updated node_info_list: {self.node_info_list}")

        # Return the entire node_info_list array, containing current node and previous nodes' output
        return [self.node_info_list]
