import { app } from "../../../scripts/app.js";

const DEFAULT_ROW_COUNT = 3;
const MAX_ROW_COUNT = 20;

function clampRowCount(value) {
    const parsed = Number.parseInt(value, 10);
    if (Number.isNaN(parsed)) {
        return DEFAULT_ROW_COUNT;
    }
    return Math.max(1, Math.min(MAX_ROW_COUNT, parsed));
}

function defaultRows() {
    return Array.from({ length: MAX_ROW_COUNT }, () => ({ nodeId: "", fieldName: "" }));
}

function normalizeRows(raw) {
    const rows = defaultRows();
    let source = raw;

    if (raw && typeof raw === "object" && Array.isArray(raw.rows)) {
        source = raw.rows;
    }
    if (!Array.isArray(source)) {
        return rows;
    }

    for (let i = 0; i < Math.min(source.length, MAX_ROW_COUNT); i += 1) {
        const item = source[i];
        if (!item || typeof item !== "object") {
            continue;
        }
        rows[i].nodeId = item.nodeId == null ? "" : String(item.nodeId);
        rows[i].fieldName = item.fieldName == null ? "" : String(item.fieldName);
    }

    return rows;
}

function parseRowsFromWidget(value) {
    if (typeof value === "string") {
        const trimmed = value.trim();
        if (!trimmed) {
            return defaultRows();
        }
        try {
            return normalizeRows(JSON.parse(trimmed));
        } catch (error) {
            console.warn("RH_NodeInfoList10Node: invalid mappingTable JSON, resetting.", error);
            return defaultRows();
        }
    }

    return normalizeRows(value);
}

function toRowsJson(rows) {
    return JSON.stringify({
        rows: rows.map((row) => ({
            nodeId: row.nodeId == null ? "" : String(row.nodeId),
            fieldName: row.fieldName == null ? "" : String(row.fieldName),
        })),
    });
}

function prettifyNodeName(name) {
    if (name == null) {
        return "";
    }
    let text = String(name).trim();
    if (!text) {
        return "";
    }
    text = text.replace(/[_-]+/g, " ");
    text = text.replace(/([a-z0-9])([A-Z])/g, "$1 $2");
    text = text.replace(/\s+/g, " ").trim();
    return text;
}

function normalizeFieldsMap(rawMap) {
    const normalized = {};
    if (!rawMap || typeof rawMap !== "object" || Array.isArray(rawMap)) {
        return normalized;
    }
    Object.entries(rawMap).forEach(([nodeId, fields]) => {
        if (!Array.isArray(fields)) {
            return;
        }
        normalized[String(nodeId)] = dedupeStrings(fields);
    });
    return normalized;
}

function normalizeNodeLabels(rawLabels) {
    const normalized = {};
    if (!rawLabels || typeof rawLabels !== "object" || Array.isArray(rawLabels)) {
        return normalized;
    }
    Object.entries(rawLabels).forEach(([nodeId, label]) => {
        const id = String(nodeId);
        const text = label == null ? "" : String(label).trim();
        if (!id || !text) {
            return;
        }
        normalized[id] = text;
    });
    return normalized;
}

function buildSchemaBundle(fieldsByNodeId, nodeLabels = {}) {
    const safeFields = normalizeFieldsMap(fieldsByNodeId);
    const safeLabels = normalizeNodeLabels(nodeLabels);
    Object.keys(safeFields).forEach((nodeId) => {
        if (!safeLabels[nodeId]) {
            safeLabels[nodeId] = `${nodeId}. ${nodeId}`;
        }
    });
    return {
        fieldsByNodeId: safeFields,
        nodeLabels: safeLabels,
    };
}

function getFieldsByNodeId(schemaBundle) {
    return schemaBundle?.fieldsByNodeId || {};
}

function getNodeLabels(schemaBundle) {
    return schemaBundle?.nodeLabels || {};
}

function parseSchemaFromWidget(value) {
    if (typeof value !== "string" || !value.trim()) {
        return buildSchemaBundle({});
    }

    try {
        const parsed = JSON.parse(value);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            return buildSchemaBundle({});
        }

        if (
            parsed.fieldsByNodeId &&
            typeof parsed.fieldsByNodeId === "object" &&
            !Array.isArray(parsed.fieldsByNodeId)
        ) {
            return buildSchemaBundle(parsed.fieldsByNodeId, parsed.nodeLabels || {});
        }

        return buildSchemaBundle(parsed);
    } catch (error) {
        console.warn("RH_NodeInfoList10Node: invalid workflowSchema JSON, resetting.", error);
        return buildSchemaBundle({});
    }
}

function toSchemaJson(schema) {
    return JSON.stringify(buildSchemaBundle(schema?.fieldsByNodeId || {}, schema?.nodeLabels || {}));
}

function hideWidget(widget) {
    if (!widget) {
        return;
    }
    widget.origType = widget.type;
    widget.origComputeSize = widget.computeSize;
    widget.computeSize = () => [0, 0];
    widget.type = "converted-widget";
    widget.hidden = true;
    widget.disabled = true;
    widget.onClick = () => {};
    widget.callback = null;
    widget.options = { ...(widget.options || {}), readonly: true };
    if (widget.inputEl) {
        widget.inputEl.style.display = "none";
        widget.inputEl.disabled = true;
        widget.inputEl.readOnly = true;
        widget.inputEl.style.pointerEvents = "none";
    }
}

function dedupeStrings(values) {
    const seen = new Set();
    const result = [];
    values.forEach((v) => {
        const str = v == null ? "" : String(v);
        if (!str || seen.has(str)) {
            return;
        }
        seen.add(str);
        result.push(str);
    });
    return result;
}

function parseWorkflowSchema(workflow) {
    const fieldsByNodeId = {};
    const nodeLabels = {};

    if (workflow && Array.isArray(workflow.nodes)) {
        const typeFields = {};

        // Pass 1: collect per-type field candidates from nodes that do expose input names.
        workflow.nodes.forEach((node) => {
            if (!node || typeof node !== "object") {
                return;
            }
            const nodeType = node.type == null ? "" : String(node.type);
            if (!nodeType) {
                return;
            }
            const fields = [];
            const inputs = Array.isArray(node.inputs) ? node.inputs : [];
            inputs.forEach((inp) => {
                if (!inp || typeof inp !== "object") {
                    return;
                }
                // Some workflows omit "name" but keep widget.name; use both.
                const candidate = inp.name ?? (inp.widget && inp.widget.name) ?? "";
                if (candidate != null && String(candidate).trim()) {
                    fields.push(String(candidate));
                }
            });
            const deduped = dedupeStrings(fields);
            if (deduped.length > 0) {
                typeFields[nodeType] = deduped;
            }
        });

        // Pass 2: build nodeId -> fields, with same-type fallback for nodes that have empty inputs.
        workflow.nodes.forEach((node) => {
            if (!node || typeof node !== "object") {
                return;
            }
            const nodeId = node.id == null ? "" : String(node.id);
            if (!nodeId) {
                return;
            }
            const nodeType = node.type == null ? "" : String(node.type);
            const fields = [];
            const inputs = Array.isArray(node.inputs) ? node.inputs : [];
            inputs.forEach((inp) => {
                if (!inp || typeof inp !== "object") {
                    return;
                }
                const candidate = inp.name ?? (inp.widget && inp.widget.name) ?? "";
                if (candidate != null && String(candidate).trim()) {
                    fields.push(String(candidate));
                }
            });
            let deduped = dedupeStrings(fields);
            if (deduped.length === 0 && nodeType && Array.isArray(typeFields[nodeType])) {
                deduped = [...typeFields[nodeType]];
            }
            fieldsByNodeId[nodeId] = deduped;

            const sAndRName = node?.properties?.["Node name for S&R"];
            const rawName = node.title || sAndRName || node.type || nodeId;
            const prettyName = prettifyNodeName(rawName) || nodeId;
            nodeLabels[nodeId] = `${nodeId}. ${prettyName}`;
        });
        return buildSchemaBundle(fieldsByNodeId, nodeLabels);
    }

    let prompt = workflow;
    if (workflow && typeof workflow === "object" && workflow.prompt && typeof workflow.prompt === "object") {
        prompt = workflow.prompt;
    }

    if (!prompt || typeof prompt !== "object" || Array.isArray(prompt)) {
        return buildSchemaBundle({});
    }

    Object.entries(prompt).forEach(([key, value]) => {
        if (!value || typeof value !== "object" || Array.isArray(value)) {
            return;
        }
        if (!value.inputs || typeof value.inputs !== "object" || Array.isArray(value.inputs)) {
            return;
        }
        const nodeId = String(key);
        fieldsByNodeId[nodeId] = dedupeStrings(Object.keys(value.inputs));
        nodeLabels[nodeId] = `${nodeId}. ${nodeId}`;
    });

    return buildSchemaBundle(fieldsByNodeId, nodeLabels);
}

function findInputIndex(node, name) {
    if (!node.inputs) {
        return -1;
    }
    return node.inputs.findIndex((input) => input && input.name === name);
}

function syncFieldValueSockets(node, rowCount) {
    for (let i = MAX_ROW_COUNT; i >= 2; i -= 1) {
        const name = `fieldValue_${i}`;
        const index = findInputIndex(node, name);
        if (i > rowCount && index >= 0) {
            node.removeInput(index);
        }
    }

    for (let i = 2; i <= rowCount; i += 1) {
        const name = `fieldValue_${i}`;
        if (findInputIndex(node, name) === -1) {
            node.addInput(name, "*", { shape: 7 });
        }
    }
}

function sanitizeLabelPart(value) {
    if (value == null) {
        return "";
    }
    let text = String(value).trim();
    if (!text) {
        return "";
    }
    text = text.replace(/^\d+\.\s*/, "");
    text = text.replace(/[^a-zA-Z0-9]+/g, "_");
    text = text.replace(/^_+|_+$/g, "");
    text = text.replace(/_+/g, "_");
    return text.toLowerCase();
}

function buildFieldValueDisplayName(index, row, nodeLabels) {
    const fallback = `fieldValue_${index}`;
    if (!row) {
        return fallback;
    }
    const nodeId = row.nodeId == null ? "" : String(row.nodeId).trim();
    const fieldName = row.fieldName == null ? "" : String(row.fieldName).trim();
    if (!nodeId || !fieldName) {
        return fallback;
    }
    const rawNodeLabel = nodeLabels[nodeId] || nodeId;
    const nodePart = sanitizeLabelPart(rawNodeLabel);
    const fieldPart = sanitizeLabelPart(fieldName);
    if (!nodePart || !fieldPart) {
        return fallback;
    }
    return `${nodePart}_${fieldPart}`;
}

function updateFieldValueInputLabels(node, rows, rowCount, schemaBundle) {
    const nodeLabels = getNodeLabels(schemaBundle);
    for (let i = 1; i <= rowCount; i += 1) {
        const input = node.inputs?.find((inp) => inp && inp.name === `fieldValue_${i}`);
        if (!input) {
            continue;
        }
        const displayName = buildFieldValueDisplayName(i, rows[i - 1], nodeLabels);
        input.label = displayName;
        input.localized_name = displayName;
    }
}

function sanitizeActiveRows(rows, schema, rowCount) {
    const nodeIds = new Set(Object.keys(schema));
    for (let i = 0; i < rowCount; i += 1) {
        const row = rows[i];
        if (!row) {
            continue;
        }
        if (!nodeIds.has(row.nodeId)) {
            row.nodeId = "";
            row.fieldName = "";
            continue;
        }
        const fields = schema[row.nodeId] || [];
        if (!fields.includes(row.fieldName)) {
            row.fieldName = "";
        }
    }
}

function protectDomInteraction(element) {
    if (!element) {
        return;
    }
    const stop = (event) => {
        event.stopPropagation();
    };
    ["pointerdown", "mousedown", "mouseup", "click", "dblclick", "wheel", "touchstart"].forEach((evt) => {
        element.addEventListener(evt, stop);
    });
}

app.registerExtension({
    name: "RunningHub.NodeInfoList10",

    nodeCreated(node) {
        if (node.comfyClass !== "RH_NodeInfoList10Node") {
            return;
        }

        const mappingWidget = node.widgets.find((w) => w.name === "mappingTable");
        const rowCountWidget = node.widgets.find((w) => w.name === "rowCount");
        const schemaWidget = node.widgets.find((w) => w.name === "workflowSchema");

        if (!mappingWidget || !rowCountWidget || !schemaWidget) {
            console.error("RH_NodeInfoList10Node: required hidden widgets are missing.");
            return;
        }

        hideWidget(mappingWidget);
        hideWidget(rowCountWidget);
        hideWidget(schemaWidget);

        let rows = parseRowsFromWidget(mappingWidget.value);
        let rowCount = clampRowCount(rowCountWidget.value);
        let workflowSchema = parseSchemaFromWidget(schemaWidget.value);

        if (Object.keys(getFieldsByNodeId(workflowSchema)).length > 0) {
            sanitizeActiveRows(rows, getFieldsByNodeId(workflowSchema), rowCount);
        }

        const container = document.createElement("div");
        container.style.width = "100%";
        container.style.margin = "6px 0";
        container.style.border = "1px solid #444";
        container.style.borderRadius = "4px";
        container.style.overflow = "hidden";

        const rowsInfo = document.createElement("div");
        rowsInfo.style.display = "flex";
        rowsInfo.style.alignItems = "center";
        rowsInfo.style.gap = "6px";
        rowsInfo.style.padding = "6px";
        rowsInfo.style.borderBottom = "1px solid #444";
        rowsInfo.style.background = "#161616";
        rowsInfo.style.fontSize = "12px";
        rowsInfo.style.color = "#ddd";

        const countLabel = document.createElement("span");
        countLabel.style.whiteSpace = "nowrap";
        rowsInfo.appendChild(countLabel);

        const controlsRow = document.createElement("div");
        controlsRow.style.display = "flex";
        controlsRow.style.alignItems = "center";
        controlsRow.style.gap = "6px";
        controlsRow.style.marginLeft = "auto";

        const minusButton = document.createElement("button");
        minusButton.textContent = "- Row";

        const plusButton = document.createElement("button");
        plusButton.textContent = "+ Row";

        const loadButton = document.createElement("button");
        loadButton.textContent = "Load Workflow JSON";

        controlsRow.appendChild(minusButton);
        controlsRow.appendChild(plusButton);
        controlsRow.appendChild(loadButton);
        rowsInfo.appendChild(controlsRow);

        protectDomInteraction(rowsInfo);
        protectDomInteraction(controlsRow);
        protectDomInteraction(minusButton);
        protectDomInteraction(plusButton);
        protectDomInteraction(loadButton);

        container.appendChild(rowsInfo);

        const tableWrap = document.createElement("div");
        tableWrap.style.maxHeight = "320px";
        tableWrap.style.overflow = "auto";
        container.appendChild(tableWrap);

        const table = document.createElement("table");
        table.style.width = "100%";
        table.style.borderCollapse = "collapse";
        table.style.fontSize = "12px";
        tableWrap.appendChild(table);

        const thead = document.createElement("thead");
        const headerRow = document.createElement("tr");
        ["index", "nodeId", "fieldName"].forEach((title) => {
            const th = document.createElement("th");
            th.textContent = title;
            th.style.textAlign = "left";
            th.style.padding = "4px 6px";
            th.style.borderBottom = "1px solid #444";
            th.style.background = "#222";
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);

        const tbody = document.createElement("tbody");
        table.appendChild(tbody);

        const syncHiddenWidgets = () => {
            mappingWidget.value = toRowsJson(rows);
            rowCountWidget.value = rowCount;
            schemaWidget.value = toSchemaJson(workflowSchema);
            countLabel.textContent = `Rows: ${rowCount}`;
            updateFieldValueInputLabels(node, rows, rowCount, workflowSchema);
        };

        const refreshNodeSize = () => {
            const height = Math.max(260, 180 + rowCount * 26);
            const width = Math.max(node.size[0], 680);
            node.setSize([width, height]);
        };

        const createTextInput = (value, onChange) => {
            const input = document.createElement("input");
            input.type = "text";
            input.value = value;
            input.style.width = "100%";
            input.style.boxSizing = "border-box";
            input.style.padding = "2px 4px";
            input.style.border = "1px solid #555";
            input.style.borderRadius = "3px";
            input.style.background = "#111";
            input.style.color = "#ddd";
            input.addEventListener("input", (event) => {
                onChange(event.target.value);
                syncHiddenWidgets();
            });
            protectDomInteraction(input);
            return input;
        };

        const createSelect = (options, selectedValue, onChange) => {
            const select = document.createElement("select");
            select.style.width = "100%";
            select.style.boxSizing = "border-box";
            select.style.padding = "2px 4px";
            select.style.border = "1px solid #555";
            select.style.borderRadius = "3px";
            select.style.background = "#111";
            select.style.color = "#ddd";

            const empty = document.createElement("option");
            empty.value = "";
            empty.textContent = "";
            select.appendChild(empty);

            options.forEach((opt) => {
                const option = document.createElement("option");
                option.value = opt.value;
                option.textContent = opt.label;
                select.appendChild(option);
            });

            select.value = options.some((opt) => opt.value === selectedValue) ? selectedValue : "";
            select.addEventListener("change", (event) => {
                onChange(event.target.value);
                syncHiddenWidgets();
                renderRows();
            });
            protectDomInteraction(select);
            return select;
        };

        const renderRows = () => {
            tbody.innerHTML = "";
            const fieldsByNodeId = getFieldsByNodeId(workflowSchema);
            const nodeLabels = getNodeLabels(workflowSchema);
            const schemaNodeIds = Object.keys(fieldsByNodeId);
            const hasSchema = schemaNodeIds.length > 0;

            for (let i = 0; i < rowCount; i += 1) {
                const row = rows[i];
                const tr = document.createElement("tr");

                const idxTd = document.createElement("td");
                idxTd.textContent = String(i + 1);
                idxTd.style.padding = "3px 6px";
                idxTd.style.borderBottom = "1px solid #333";
                tr.appendChild(idxTd);

                const nodeIdTd = document.createElement("td");
                nodeIdTd.style.padding = "3px 6px";
                nodeIdTd.style.borderBottom = "1px solid #333";

                if (hasSchema) {
                    const nodeOptions = schemaNodeIds.map((nodeId) => ({
                        value: nodeId,
                        label: nodeLabels[nodeId] || `${nodeId}. ${nodeId}`,
                    }));
                    nodeIdTd.appendChild(
                        createSelect(nodeOptions, row.nodeId, (value) => {
                            row.nodeId = value;
                            const fields = fieldsByNodeId[row.nodeId] || [];
                            if (!fields.includes(row.fieldName)) {
                                row.fieldName = "";
                            }
                        })
                    );
                } else {
                    nodeIdTd.appendChild(
                        createTextInput(row.nodeId, (value) => {
                            row.nodeId = value;
                        })
                    );
                }
                tr.appendChild(nodeIdTd);

                const fieldNameTd = document.createElement("td");
                fieldNameTd.style.padding = "3px 6px";
                fieldNameTd.style.borderBottom = "1px solid #333";

                if (hasSchema) {
                    const fields = fieldsByNodeId[row.nodeId] || [];
                    const fieldOptions = fields.map((field) => ({ value: field, label: field }));
                    fieldNameTd.appendChild(
                        createSelect(fieldOptions, row.fieldName, (value) => {
                            row.fieldName = value;
                        })
                    );
                } else {
                    fieldNameTd.appendChild(
                        createTextInput(row.fieldName, (value) => {
                            row.fieldName = value;
                        })
                    );
                }
                tr.appendChild(fieldNameTd);
                tbody.appendChild(tr);
            }

            refreshNodeSize();
        };

        const applyRowCount = (nextCount) => {
            rowCount = clampRowCount(nextCount);
            syncFieldValueSockets(node, rowCount);
            syncHiddenWidgets();
            renderRows();
            node.graph?.setDirtyCanvas(true, true);
        };
        minusButton.addEventListener("click", () => {
            applyRowCount(rowCount - 1);
        });
        plusButton.addEventListener("click", () => {
            applyRowCount(rowCount + 1);
        });

        const workflowFileInput = document.createElement("input");
        workflowFileInput.type = "file";
        workflowFileInput.accept = ".json,application/json";
        workflowFileInput.style.display = "none";
        document.body.appendChild(workflowFileInput);

        workflowFileInput.addEventListener("change", async () => {
            try {
                if (!workflowFileInput.files || workflowFileInput.files.length === 0) {
                    return;
                }
                const file = workflowFileInput.files[0];
                const text = await file.text();
                const parsed = JSON.parse(text);
                const parsedSchema = parseWorkflowSchema(parsed);
                if (Object.keys(getFieldsByNodeId(parsedSchema)).length === 0) {
                    alert("No valid workflow node schema found in this JSON.");
                    return;
                }
                workflowSchema = parsedSchema;
                sanitizeActiveRows(rows, getFieldsByNodeId(workflowSchema), rowCount);
                syncHiddenWidgets();
                renderRows();
                node.graph?.setDirtyCanvas(true, true);
            } catch (error) {
                console.error("RH_NodeInfoList10Node: failed to load workflow JSON.", error);
                alert(`Failed to load workflow JSON: ${error.message}`);
            }
        });

        const openWorkflowPicker = () => {
            workflowFileInput.value = "";
            if (typeof workflowFileInput.showPicker === "function") {
                try {
                    const pickerResult = workflowFileInput.showPicker();
                    if (pickerResult && typeof pickerResult.catch === "function") {
                        pickerResult.catch(() => {
                            workflowFileInput.click();
                        });
                    }
                } catch (error) {
                    workflowFileInput.click();
                }
            } else {
                workflowFileInput.click();
            }
        };
        loadButton.addEventListener("click", () => {
            openWorkflowPicker();
        });

        node.addDOMWidget("mapping_table_dynamic", "preview", container, {
            serialize: false,
            hideOnZoom: false,
        });

        const originalOnRemoved = node.onRemoved;
        node.onRemoved = function () {
            if (workflowFileInput && workflowFileInput.parentNode) {
                workflowFileInput.parentNode.removeChild(workflowFileInput);
            }
            return originalOnRemoved?.apply(this, arguments);
        };

        const originalOnConfigure = node.onConfigure;
        node.onConfigure = function () {
            const result = originalOnConfigure?.apply(this, arguments);
            rows = parseRowsFromWidget(mappingWidget.value);
            rowCount = clampRowCount(rowCountWidget.value);
            workflowSchema = parseSchemaFromWidget(schemaWidget.value);
            if (Object.keys(getFieldsByNodeId(workflowSchema)).length > 0) {
                sanitizeActiveRows(rows, getFieldsByNodeId(workflowSchema), rowCount);
            }
            syncFieldValueSockets(node, rowCount);
            syncHiddenWidgets();
            renderRows();
            node.graph?.setDirtyCanvas(true, true);
            return result;
        };

        syncFieldValueSockets(node, rowCount);
        syncHiddenWidgets();
        renderRows();
        node.graph?.setDirtyCanvas(true, true);
    },
});
