import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ComfyWidgets } from "../../scripts/widgets.js";

const NODE_NAME = "LLMPromptStudio";
// Both draw whatever the run sent back: the count alone for the simple one,
// the count plus the text it counted for the other.
const PREVIEW_NODES = new Set(["LLMTextTokenPreview", "LLMTokenCount"]);
let TEMPLATES = {};

async function loadTemplates() {
    if (Object.keys(TEMPLATES).length) return TEMPLATES;
    try {
        const r = await api.fetchApi("/llm_prompt_studio/templates");
        TEMPLATES = await r.json();
    } catch (e) {
        console.warn("[coco] could not load templates:", e);
    }
    return TEMPLATES;
}

function getWidget(node, name) {
    return node.widgets?.find((w) => w.name === name);
}

// Optional convenience: fill the (empty) model text field with the chat model
// detected at the address. Leaving the field empty also works: the node
// auto-detects at run time. Text-encoder / embedding models are skipped.
async function detectModel(node) {
    const base = getWidget(node, "base_url")?.value || "http://localhost:1234/v1";
    const key = getWidget(node, "api_key")?.value || "";
    const modelW = getWidget(node, "model");
    if (!modelW) return;
    try {
        const url =
            "/llm_prompt_studio/models?base_url=" +
            encodeURIComponent(base) +
            "&api_key=" +
            encodeURIComponent(key);
        const r = await api.fetchApi(url);
        const data = await r.json();
        const pick = data.suggested || (data.models && data.models[0]);
        if (pick) {
            modelW.value = pick;
            app.graph.setDirtyCanvas(true, true);
        } else {
            console.warn("[coco] no chat model found at", base, data.error || "");
        }
    } catch (e) {
        console.error("[coco] model detection failed:", e);
    }
}

// ------------------------------------------------------------- resizable boxes
// ComfyUI pins .comfy-multiline-input to `resize: none` AND recomputes the
// textarea height from the widget layout on every redraw, so showing the
// browser's handle is only half the job: without the second half the next
// redraw undoes the drag. options.getMinHeight/getMaxHeight are what a DOM
// widget's computeLayoutSize reads, so feeding the dragged height back through
// them is what makes the new size stick - and grows the node instead of
// overflowing it.
//
// The reserved height is NOT the height of the textarea: the frontend draws the
// widget's box at `computedHeight - 2 * margin` and stretches the element to
// fill it (h-full). Reporting the raw dragged height therefore leaves the
// element 2 * margin taller than the box it sits in, and that overflow is
// exactly what covered the widgets underneath.
const MIN_BOX_HEIGHT = 60;
const DEFAULT_WIDGET_MARGIN = 10;

// Heights live in node.properties, which litegraph serializes on its own.
// Never widgets_values: that array is positional, and one extra entry would
// shift every saved workflow by a slot.
function boxHeights(node) {
    if (!node.properties) node.properties = {};
    if (!node.properties.boxHeights) node.properties.boxHeights = {};
    return node.properties.boxHeights;
}

function makeResizable(node, w) {
    try {
        // .element is the textarea itself (no wrapper); .inputEl is its
        // deprecated alias, kept only for older frontends.
        const el = w?.element || w?.inputEl;
        if (!el || el.tagName !== "TEXTAREA" || el.dataset.cocoResize) return;
        el.dataset.cocoResize = "1";
        el.style.resize = "vertical";
        el.style.overflowY = "auto";
        el.style.minHeight = MIN_BOX_HEIGHT + "px";

        // A DOM widget is laid out through computeLayoutSize, which reads these
        // two options; a legacy canvas widget goes through computeSize instead.
        // Only ever answer once the user has actually dragged: claiming a height
        // before that takes the box out of the frontend's own distribution and
        // the node ends up shorter than its widgets - they then overlap whatever
        // sits below. Hence the `|| fall through` in every branch.
        w.options = w.options || {};
        const origMin = w.options.getMinHeight?.bind(w.options);
        const origMax = w.options.getMaxHeight?.bind(w.options);
        // _cocoBox = what the layout must reserve (margins included);
        // _cocoHeight = the textarea itself, for the legacy canvas path, which
        // has no margin to account for.
        w.options.getMinHeight = () => w._cocoBox || origMin?.();
        w.options.getMaxHeight = () => w._cocoBox || origMax?.();
        if (typeof w.computeSize === "function") {
            const origSize = w.computeSize.bind(w);
            w.computeSize = (width) =>
                w._cocoHeight ? [width, w._cocoHeight] : origSize(width);
        }

        // Grow the NODE by exactly what the box gained: asking for its computed
        // minimum instead would shrink a node the user had made taller. The
        // relayout resizes the element right back, firing the observer again -
        // comparing against the last height we caused stops the loop.
        const margin = typeof w.margin === "number" ? w.margin : DEFAULT_WIDGET_MARGIN;
        let last = Math.round(el.offsetHeight);
        new ResizeObserver(() => {
            const h = Math.max(MIN_BOX_HEIGHT, Math.round(el.offsetHeight));
            if (!h || Math.abs(h - last) < 2) return;
            const delta = h - last;
            last = h;
            // + the two margins the frontend subtracts again when it draws the
            // box, so the box ends up exactly as tall as the textarea.
            w._cocoHeight = h;
            w._cocoBox = h + 2 * margin;
            boxHeights(node)[w.name] = h;
            node.setSize([node.size[0], node.size[1] + delta]);
            app.graph.setDirtyCanvas(true, true);
        }).observe(el);

        // Restoring a saved height goes through the same path: setting the style
        // fires the observer, which applies the delta to the node.
        const saved = boxHeights(node)[w.name];
        if (saved && Math.abs(saved - last) >= 2) el.style.height = saved + "px";
    } catch (e) {
        console.warn("[coco] could not make", w?.name, "resizable:", e);
    }
}

// Every multiline box of the node, including ones added later (generated_text).
function makeAllResizable(node) {
    for (const w of node.widgets || []) makeResizable(node, w);
}

// A display-only text box added at run time. serialize:false so it never lands
// in widgets_values, which is positional: one extra entry there would shift
// every saved workflow by a slot.
function readonlyBox(node, name) {
    let w = node.widgets?.find((x) => x.name === name);
    if (!w) {
        w = ComfyWidgets["STRING"](
            node,
            name,
            ["STRING", { multiline: true }],
            app
        ).widget;
        w.serialize = false;
        const el = w.element || w.inputEl;
        if (el) {
            el.readOnly = true;
            el.style.opacity = "0.85";
        }
    }
    return w;
}

app.registerExtension({
    name: "comfy.LLMPromptStudio",
    async setup() {
        await loadTemplates();
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (PREVIEW_NODES.has(nodeData.name)) {
            // Token count first, then the text it counted. Both boxes are
            // rebuilt from the run's result, never from widgets_values.
            const onExec = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (message) {
                onExec?.apply(this, arguments);
                const node = this;
                const join = (v) =>
                    v === undefined || v === null
                        ? null
                        : Array.isArray(v)
                        ? v.join("")
                        : String(v);

                const info = join(message?.info);
                if (info !== null) {
                    const w = readonlyBox(node, "token_count");
                    w.value = info;
                    const el = w.element || w.inputEl;
                    // Over budget is the one thing worth spotting without reading.
                    if (el) el.style.color = info.includes("OVER") ? "#ff6b6b" : "";
                    makeResizable(node, w);
                }

                const text = join(message?.text);
                if (text !== null) {
                    const w = readonlyBox(node, "preview_text");
                    w.value = text;
                    makeResizable(node, w);
                }
                app.graph.setDirtyCanvas(true, true);
            };

            // A saved workflow reopens with the boxes gone (serialize:false), so
            // the node must not keep the height they had reserved.
            const onConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function () {
                const r = onConfigure?.apply(this, arguments);
                setTimeout(() => makeAllResizable(this), 250);
                return r;
            };
            return;
        }

        if (nodeData.name !== NODE_NAME) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = onNodeCreated?.apply(this, arguments);
            const node = this;

            // Optional: detect & show which model will be used (field can stay empty).
            // serialize:false so these buttons never end up in widgets_values
            // (which would shift the positional mapping of the real inputs).
            const detectBtn = node.addWidget(
                "button",
                "🔄 Detect model (optional)",
                null,
                () => detectModel(node)
            );
            detectBtn.serialize = false;

            const applyTemplate = async () => {
                await loadTemplates();
                const tw = getWidget(node, "target_model");
                const sw = getWidget(node, "system_prompt");
                if (!tw || !sw) return;
                const t = TEMPLATES[tw.value];
                if (t != null) {
                    sw.value = t;
                    app.graph.setDirtyCanvas(true, true);
                }
            };

            const presetBtn = node.addWidget(
                "button",
                "📥 Load preset prompt",
                null,
                applyTemplate
            );
            presetBtn.serialize = false;

            // Auto-load the matching preset into the system prompt box on change.
            const tw = getWidget(node, "target_model");
            if (tw) {
                const origCb = tw.callback;
                tw.callback = function () {
                    const r = origCb?.apply(this, arguments);
                    applyTemplate();
                    return r;
                };
            }

            // On a brand-new node: pre-fill the preset and show the detected model.
            // Never clobber a saved/edited value, and never write error text into
            // the model field (it stays empty -> auto-detected at run time).
            setTimeout(() => {
                const sw = getWidget(node, "system_prompt");
                if (sw && (!sw.value || !sw.value.trim())) applyTemplate();
                const mw = getWidget(node, "model");
                if (mw && (!mw.value || !mw.value.trim())) detectModel(node);
                // After configure(), so a saved workflow's box heights are back.
                makeAllResizable(node);
            }, 250);

            return ret;
        };

        // Show the generated prompt (cleaned, no thinking) on the node itself.
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            const node = this;
            const text = message?.text;
            if (text === undefined || text === null) return;
            const value = Array.isArray(text) ? text.join("") : String(text);

            let w = node.widgets?.find((x) => x.name === "generated_text");
            if (!w) {
                w = ComfyWidgets["STRING"](
                    node,
                    "generated_text",
                    ["STRING", { multiline: true }],
                    app
                ).widget;
                w.serialize = false; // preview only; never saved into widgets_values
                if (w.inputEl) {
                    w.inputEl.readOnly = true;
                    w.inputEl.style.opacity = "0.85";
                }
            }
            w.value = value;
            makeResizable(node, w);
            app.graph.setDirtyCanvas(true, true);
        };
    },
});
