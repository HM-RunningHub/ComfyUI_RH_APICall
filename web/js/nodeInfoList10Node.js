import { app } from "../../../scripts/app.js";

const ROW_COUNT = 10;

function defaultRows() {
    return Array.from({ length: ROW_COUNT }, () => ({ nodeId: "", fieldName: "" }));
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

    for (let i = 0; i < Math.min(source.length, ROW_COUNT); i += 1) {
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
    return JSON.stringify(
        rows.map((row) => ({
            nodeId: row.nodeId == null ? "" : String(row.nodeId),
            fieldName: row.fieldName == null ? "" : String(row.fieldName),
        }))
    );
}

function hideWidget(widget) {
    widget.computeSize = () => [0, -4];
    widget.type = "hidden";
    if (widget.inputEl) {
        widget.inputEl.style.display = "none";
    }
}

app.registerExtension({
    name: "RunningHub.NodeInfoList10",

    nodeCreated(node) {
        if (node.comfyClass !== "RH_NodeInfoList10Node") {
            return;
        }

        const mappingWidget = node.widgets.find((w) => w.name === "mappingTable");
        if (!mappingWidget) {
            console.error("RH_NodeInfoList10Node: could not find mappingTable widget.");
            return;
        }

        hideWidget(mappingWidget);

        let rows = parseRowsFromWidget(mappingWidget.value);

        const container = document.createElement("div");
        container.style.width = "100%";
        container.style.margin = "6px 0";
        container.style.maxHeight = "280px";
        container.style.overflow = "auto";
        container.style.border = "1px solid #444";
        container.style.borderRadius = "4px";

        const table = document.createElement("table");
        table.style.width = "100%";
        table.style.borderCollapse = "collapse";
        table.style.fontSize = "12px";

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
        container.appendChild(table);

        const syncWidget = () => {
            mappingWidget.value = toRowsJson(rows);
        };

        const createInput = (value, onChange) => {
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
                syncWidget();
            });
            return input;
        };

        const renderRows = () => {
            tbody.innerHTML = "";
            rows.forEach((row, i) => {
                const tr = document.createElement("tr");

                const idxTd = document.createElement("td");
                idxTd.textContent = String(i + 1);
                idxTd.style.padding = "3px 6px";
                idxTd.style.borderBottom = "1px solid #333";
                tr.appendChild(idxTd);

                const nodeIdTd = document.createElement("td");
                nodeIdTd.style.padding = "3px 6px";
                nodeIdTd.style.borderBottom = "1px solid #333";
                nodeIdTd.appendChild(
                    createInput(row.nodeId, (v) => {
                        rows[i].nodeId = v;
                    })
                );
                tr.appendChild(nodeIdTd);

                const fieldNameTd = document.createElement("td");
                fieldNameTd.style.padding = "3px 6px";
                fieldNameTd.style.borderBottom = "1px solid #333";
                fieldNameTd.appendChild(
                    createInput(row.fieldName, (v) => {
                        rows[i].fieldName = v;
                    })
                );
                tr.appendChild(fieldNameTd);

                tbody.appendChild(tr);
            });
        };

        renderRows();
        syncWidget();

        node.addDOMWidget("mapping_table", "preview", container, {
            serialize: false,
            hideOnZoom: false,
        });

        const originalOnConfigure = node.onConfigure;
        node.onConfigure = function () {
            const result = originalOnConfigure?.apply(this, arguments);
            rows = parseRowsFromWidget(mappingWidget.value);
            renderRows();
            syncWidget();
            return result;
        };

        if (node.size[1] < 560) {
            node.setSize([Math.max(node.size[0], 640), 560]);
        }
    },
});
