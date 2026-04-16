import json

from .RH_Utils import anytype


class NodeInfoList10Node:
    DEFAULT_ROW_COUNT = 3
    MAX_ROW_COUNT = 20

    def __init__(self):
        self.node_info_list = []

    @classmethod
    def _default_mapping_rows(cls):
        return [{"nodeId": "", "fieldName": ""} for _ in range(cls.MAX_ROW_COUNT)]

    @classmethod
    def _normalize_rows(cls, data):
        rows = cls._default_mapping_rows()
        source_rows = data

        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            source_rows = data["rows"]

        if not isinstance(source_rows, list):
            return rows

        for i, item in enumerate(source_rows[: cls.MAX_ROW_COUNT]):
            if not isinstance(item, dict):
                continue
            node_id = item.get("nodeId", "")
            field_name = item.get("fieldName", "")
            rows[i]["nodeId"] = "" if node_id is None else str(node_id)
            rows[i]["fieldName"] = "" if field_name is None else str(field_name)

        return rows

    @classmethod
    def _normalize_row_count(cls, row_count):
        try:
            value = int(row_count)
        except (TypeError, ValueError):
            value = cls.DEFAULT_ROW_COUNT
        return max(1, min(cls.MAX_ROW_COUNT, value))

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
        required = {
            "mappingTable": (
                "STRING",
                {
                    "default": json.dumps({"rows": cls._default_mapping_rows()}, ensure_ascii=False),
                    "multiline": False,
                },
            ),
            "rowCount": (
                "INT",
                {"default": cls.DEFAULT_ROW_COUNT, "min": 1, "max": cls.MAX_ROW_COUNT, "step": 1},
            ),
            "fieldValue_1": (anytype, {"default": "", "forceInput": True}),
        }
        optional = {
            "workflowSchema": ("STRING", {"default": "", "multiline": False}),
            "previousNodeInfoList": ("ARRAY", {"default": []}),
        }
        for i in range(2, cls.MAX_ROW_COUNT + 1):
            optional[f"fieldValue_{i}"] = (anytype, {"default": "", "forceInput": True})

        return {
            "required": required,
            "optional": optional,
        }

    RETURN_TYPES = ("ARRAY",)
    CATEGORY = "RunningHub"
    FUNCTION = "process"

    def process(
        self,
        mappingTable,
        rowCount,
        fieldValue_1,
        previousNodeInfoList=None,
        workflowSchema="",
        **kwargs,
    ):
        _ = workflowSchema
        rows = self._parse_mapping_table(mappingTable)
        row_count = self._normalize_row_count(rowCount)

        self.node_info_list = []

        if previousNodeInfoList:
            self.node_info_list.extend(previousNodeInfoList)

        for i in range(row_count):
            row = rows[i] if i < len(rows) else {"nodeId": "", "fieldName": ""}
            node_id_raw = str(row.get("nodeId", "")).strip()
            field_name = str(row.get("fieldName", "")).strip()

            if node_id_raw == "" or field_name == "":
                continue

            try:
                node_id = int(node_id_raw)
            except (TypeError, ValueError):
                continue

            if i == 0:
                field_value = fieldValue_1
            else:
                field_value = kwargs.get(f"fieldValue_{i + 1}", kwargs.get(f"fieldValue{i + 1}", ""))

            node_info = {
                "nodeId": node_id,
                "fieldName": field_name,
                "fieldValue": str(field_value),
            }
            self.node_info_list.append(node_info)

        return (self.node_info_list,)
