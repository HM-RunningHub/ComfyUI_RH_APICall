import json

from .RH_Utils import anytype


class NodeInfoList10Node:
    ROW_COUNT = 10

    def __init__(self):
        self.node_info_list = []

    @classmethod
    def _default_mapping_rows(cls):
        return [{"nodeId": "", "fieldName": ""} for _ in range(cls.ROW_COUNT)]

    @classmethod
    def _normalize_rows(cls, data):
        rows = cls._default_mapping_rows()
        source_rows = data

        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            source_rows = data["rows"]

        if not isinstance(source_rows, list):
            return rows

        for i, item in enumerate(source_rows[: cls.ROW_COUNT]):
            if not isinstance(item, dict):
                continue
            node_id = item.get("nodeId", "")
            field_name = item.get("fieldName", "")
            rows[i]["nodeId"] = "" if node_id is None else str(node_id)
            rows[i]["fieldName"] = "" if field_name is None else str(field_name)

        return rows

    @classmethod
    def _parse_mapping_table(cls, mapping_table):
        if isinstance(mapping_table, str):
            mapping_table = mapping_table.strip()
            if mapping_table == "":
                return cls._default_mapping_rows()
            try:
                parsed = json.loads(mapping_table)
            except json.JSONDecodeError:
                return cls._default_mapping_rows()
            return cls._normalize_rows(parsed)

        return cls._normalize_rows(mapping_table)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mappingTable": (
                    "STRING",
                    {
                        "default": json.dumps(cls._default_mapping_rows(), ensure_ascii=False),
                        "multiline": False,
                    },
                ),
                "fieldValue1": (anytype, {"default": ""}),
                "fieldValue2": (anytype, {"default": ""}),
                "fieldValue3": (anytype, {"default": ""}),
                "fieldValue4": (anytype, {"default": ""}),
                "fieldValue5": (anytype, {"default": ""}),
                "fieldValue6": (anytype, {"default": ""}),
                "fieldValue7": (anytype, {"default": ""}),
                "fieldValue8": (anytype, {"default": ""}),
                "fieldValue9": (anytype, {"default": ""}),
                "fieldValue10": (anytype, {"default": ""}),
            },
            "optional": {
                "previousNodeInfoList": ("ARRAY", {"default": []}),
            },
        }

    RETURN_TYPES = ("ARRAY",)
    CATEGORY = "RunningHub"
    FUNCTION = "process"

    def process(
        self,
        mappingTable,
        fieldValue1,
        fieldValue2,
        fieldValue3,
        fieldValue4,
        fieldValue5,
        fieldValue6,
        fieldValue7,
        fieldValue8,
        fieldValue9,
        fieldValue10,
        previousNodeInfoList=None,
    ):
        rows = self._parse_mapping_table(mappingTable)
        field_values = [
            fieldValue1,
            fieldValue2,
            fieldValue3,
            fieldValue4,
            fieldValue5,
            fieldValue6,
            fieldValue7,
            fieldValue8,
            fieldValue9,
            fieldValue10,
        ]

        self.node_info_list = []

        if previousNodeInfoList:
            self.node_info_list.extend(previousNodeInfoList)

        for i in range(self.ROW_COUNT):
            row = rows[i] if i < len(rows) else {"nodeId": "", "fieldName": ""}
            node_id_raw = str(row.get("nodeId", "")).strip()
            field_name = str(row.get("fieldName", "")).strip()

            if node_id_raw == "" or field_name == "":
                continue

            try:
                node_id = int(node_id_raw)
            except (TypeError, ValueError):
                continue

            field_value = field_values[i]
            node_info = {
                "nodeId": node_id,
                "fieldName": field_name,
                "fieldValue": str(field_value),
            }
            self.node_info_list.append(node_info)

        return (self.node_info_list,)
