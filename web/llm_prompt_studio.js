import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ComfyWidgets } from "../../scripts/widgets.js";

const NODE_NAME = "LLMPromptStudio";
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
// redraw undoes the drag. computeSize is what the node reads when it lays its
// widgets out, so feeding the dragged height back into it is what makes the new
// size stick - and grow the node instead of overflowing it.
const MIN_BOX_HEIGHT = 60;

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
        const el = w?.inputEl || w?.element;
        if (!el || el.tagName !== "TEXTAREA" || el.dataset.cocoResize) return;
        el.dataset.cocoResize = "1";
        el.style.resize = "vertical";
        el.style.overflowY = "auto";
        el.style.minHeight = MIN_BOX_HEIGHT + "px";

        const saved = boxHeights(node)[w.name];
        if (saved) w._cocoHeight = saved;

        const orig = w.computeSize?.bind(w);
        w.computeSize = function (width) {
            if (!w._cocoHeight) return orig ? orig(width) : [width, MIN_BOX_HEIGHT];
            return [width, w._cocoHeight];
        };

        // The relayout we trigger resizes the element right back, which fires
        // the observer again: compare against the last height we caused, or the
        // two chase each other forever.
        let applied = w._cocoHeight || 0;
        new ResizeObserver(() => {
            const h = Math.max(MIN_BOX_HEIGHT, Math.round(el.offsetHeight));
            if (!h || Math.abs(h - applied) < 2) return;
            applied = h;
            w._cocoHeight = h;
            boxHeights(node)[w.name] = h;
            node.setSize([node.size[0], node.computeSize()[1]]);
            app.graph.setDirtyCanvas(true, true);
        }).observe(el);
    } catch (e) {
        console.warn("[coco] could not make", w?.name, "resizable:", e);
    }
}

// Every multiline box of the node, including ones added later (generated_text).
function makeAllResizable(node) {
    let restored = false;
    for (const w of node.widgets || []) {
        const had = w._cocoHeight;
        makeResizable(node, w);
        if (!had && w._cocoHeight) restored = true;
    }
    if (restored) {
        node.setSize([node.size[0], node.computeSize()[1]]);
        app.graph.setDirtyCanvas(true, true);
    }
}

app.registerExtension({
    name: "comfy.LLMPromptStudio",
    async setup() {
        await loadTemplates();
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
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
