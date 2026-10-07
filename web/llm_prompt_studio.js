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

// A widget the node draws but never sends. TWO flags are needed, because the
// frontend reads a different one on each path:
//   - the workflow serializer skips `widget.serialize === false` (widgets_values);
//   - graphToPrompt, which builds what the backend runs, skips only
//     `widget.options.serialize === false`.
// Setting just the first one leaves the widget in the prompt's `inputs`, and the
// backend hashes EVERY key of `inputs` into the execution cache signature
// (comfy_execution/caching.py). `generated_text` holds a different prompt after
// every run, so the node's signature changed on every queue and the LLM was
// called again - a fixed seed could not cache it. Set both, always.
function dontSerialize(w) {
    if (!w) return w;
    w.serialize = false;
    w.options = w.options || {};
    w.options.serialize = false;
    return w;
}

// -------------------------------------------------------------- model combo
// `model` is a STRING on the backend and stays one: a saved workflow may name a
// model the server is not serving right now, and an empty value is the "use
// whatever is loaded" mode. The dropdown is not a widget added next to it - a
// second widget cannot be put THERE anyway. widgets_values is saved indexed by
// widget position but reloaded by counting the serialized ones, so a
// non-serialized widget inserted among them writes a hole that shifts every
// saved workflow by a slot; and appending it at the bottom of the node is where
// nobody found it. So the text widget is swapped for a real combo IN ITS OWN
// SLOT: same name, same position, same serialization - nothing shifts, and a
// workflow saved before this change reloads with its typed name selected.
//
// Setting `type = "combo"` on the existing widget does NOT work: the frontend
// binds the renderer to the widget class at construction, so it keeps drawing a
// text field. Only a widget built by addWidget("combo", ...) is drawn as one.
const PICK_AUTO = "(auto) use the loaded model";
const PICK_TYPE = "✏️ type a name…";
// Both are sent as the model name when picked; the backend reads them as "auto"
// (_AUTO_MODEL in nodes.py). No value mapping to get wrong on the way out.

function modelWidget(node) {
    return getWidget(node, "model");
}

// Every entry the dropdown should offer, current value included so a forced
// name the server is not serving right now stays visible and selectable.
function modelValues(node, served) {
    const w = modelWidget(node);
    const cur = String(w?.value ?? "").trim();
    const out = [PICK_AUTO];
    for (const m of served || []) if (!out.includes(m)) out.push(m);
    if (cur && cur !== PICK_TYPE && !out.includes(cur)) out.push(cur);
    out.push(PICK_TYPE);
    return out;
}

// An empty value is what every workflow saved before the dropdown holds, and
// what the node def defaults to: show it as the auto entry rather than blank.
function normalizeModel(node) {
    const w = modelWidget(node);
    if (!w) return;
    const cur = String(w.value ?? "").trim();
    if (!cur || cur === PICK_TYPE) w.value = PICK_AUTO;
    w._last = w.value;
    w.options = w.options || {};
    w.options.values = modelValues(node, w.options.values?.filter(
        (v) => v !== PICK_AUTO && v !== PICK_TYPE) || []);
}

function swapModelToCombo(node) {
    const old = modelWidget(node);
    if (!old || old.type === "combo") return old;
    const i = node.widgets.indexOf(old);
    try {
        const combo = node.addWidget(
            "combo",
            "model",
            String(old.value ?? "").trim() || PICK_AUTO,
            (v) => {
                if (v !== PICK_TYPE) {
                    combo._last = v;
                    return;
                }
                // Typing a name by hand, for a model the address does not list
                // - or does not list yet. Picking the entry has already
                // overwritten the value, so what goes in the box is the name
                // remembered from the previous pick: it is edited, not retyped.
                const typed = window.prompt(
                    "Model name (leave empty to go back to auto):",
                    combo._last && combo._last !== PICK_AUTO ? combo._last : ""
                );
                if (typed === null) {
                    combo.value = combo._last || PICK_AUTO;  // cancelled
                } else {
                    const name = typed.trim();
                    combo.value = name || PICK_AUTO;
                    // Above the 'type a name' entry, so it stays the last one.
                    if (name && !combo.options.values.includes(name)) {
                        combo.options.values.splice(
                            combo.options.values.length - 1, 0, name);
                    }
                }
                combo._last = combo.value;
                app.graph.setDirtyCanvas(true, true);
            },
            { values: [PICK_AUTO, PICK_TYPE], tooltip: old.options?.tooltip }
        );
        node.widgets.pop();              // addWidget appends; we want the old slot
        node.widgets.splice(i, 1, combo);
        combo._last = combo.value;
        old.onRemove?.();
        return combo;
    } catch (e) {
        console.warn("[coco] could not turn 'model' into a dropdown:", e);
        return old;
    }
}

async function fetchModels(node) {
    const base = getWidget(node, "base_url")?.value || "http://localhost:1234/v1";
    const key = getWidget(node, "api_key")?.value || "";
    const url =
        "/llm_prompt_studio/models?base_url=" +
        encodeURIComponent(base) +
        "&api_key=" +
        encodeURIComponent(key);
    try {
        const r = await api.fetchApi(url);
        const data = await r.json();
        return { base, ...data };
    } catch (e) {
        console.error("[coco] model list failed:", e);
        return { base, models: [], suggested: null, error: String(e) };
    }
}

// Refresh the dropdown, and - only when asked - select the detected chat model.
// Text-encoder / embedding models are sorted to the bottom server-side.
async function detectModel(node, select = false) {
    const w = modelWidget(node);
    if (!w) return;
    const data = await fetchModels(node);
    const served = Array.isArray(data.models) ? data.models : [];
    if (!served.length) console.warn("[coco] no model at", data.base, data.error || "");
    w.options = w.options || {};
    w.options.values = modelValues(node, served);
    const pick = data.suggested || served[0];
    if (select && pick) w.value = pick;
    else if (!w.options.values.includes(w.value)) w.value = PICK_AUTO;
    w._last = w.value;
    app.graph.setDirtyCanvas(true, true);
}

// ------------------------------------------------------------- server status
// A dot on the title: green = the LLM answers at base_url, red = it does not,
// grey = not asked yet. Polled, so it turns red BEFORE a run, not only after
// one - and a run that had to fall back sets it too (onExecuted below).
const STATUS_EVERY_MS = 10000;
const DOT_COLORS = { true: "#3ddc84", false: "#ff4d4d", undefined: "#888888" };
// One request per address and per round, however many nodes point at it.
const statusCache = new Map();

function serverStatus(base, key, force = false) {
    const id = base + "\n" + key;
    const hit = statusCache.get(id);
    if (!force && hit && Date.now() - hit.at < STATUS_EVERY_MS - 500) return hit.promise;
    const url =
        "/llm_prompt_studio/status?base_url=" +
        encodeURIComponent(base) +
        "&api_key=" +
        encodeURIComponent(key);
    const promise = api
        .fetchApi(url)
        .then((r) => r.json())
        .then((d) => !!d.online)
        .catch(() => undefined); // ComfyUI itself unreachable: say nothing
    statusCache.set(id, { at: Date.now(), promise });
    return promise;
}

async function refreshStatus(node, force = false) {
    const base = getWidget(node, "base_url")?.value || "http://localhost:1234/v1";
    const key = getWidget(node, "api_key")?.value || "";
    const online = await serverStatus(base, key, force);
    if (online === node._llmOnline) return;
    node._llmOnline = online;
    app.graph?.setDirtyCanvas(true, false);
}

let statusTimer = null;
function startStatusPolling() {
    if (statusTimer) return;
    statusTimer = setInterval(() => {
        if (document.visibilityState !== "visible") return;
        for (const node of app.graph?._nodes || []) {
            if ((node.comfyClass || node.type) === NODE_NAME) refreshStatus(node);
        }
    }, STATUS_EVERY_MS);
}

function toast(severity, summary, detail) {
    try {
        app.extensionManager.toast.add({ severity, summary, detail, life: 8000 });
    } catch (e) {
        console.warn("[coco]", summary, detail);
    }
}

// ------------------------------------------------------------------ story sheet
// story_sheet is a real, serialized widget: the backend reads it as the story so
// far and each run sends back the updated one, written here into the same box.
// That is the whole persistence - the story is saved with the workflow, and the
// user can read and correct it. One step of undo is kept, for the run that
// changed what it should not have (and for a 'new story' clicked by mistake).
function setStorySheet(node, text) {
    const w = getWidget(node, "story_sheet");
    if (!w) return;
    const next = String(text ?? "");
    if (next === String(w.value ?? "")) return;
    node._prevSheet = String(w.value ?? "");
    w.value = next;
    app.graph?.setDirtyCanvas(true, true);
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

// A display-only text box added at run time. Never serialized: widgets_values is
// positional, so one extra entry there would shift every saved workflow by a
// slot - and the prompt's `inputs` feed the execution cache, so a box holding
// the last run's text would invalidate the node on every queue.
function readonlyBox(node, name) {
    let w = node.widgets?.find((x) => x.name === name);
    if (!w) {
        w = ComfyWidgets["STRING"](
            node,
            name,
            ["STRING", { multiline: true }],
            app
        ).widget;
        dontSerialize(w);
        const el = w.element || w.inputEl;
        if (el) {
            el.readOnly = true;
            el.style.opacity = "0.85";
        }
    }
    return w;
}

// ------------------------------------------------------- growing image slots
// The node declares eight picture sockets (nodes.py, MAX_PICTURES) so it still
// works without this file, but eight is a pool, not a ceiling: here the node
// shows exactly ONE empty socket after the last filled one, and grows another
// the moment that one is taken. They are the last sockets of the node, so a
// socket added at the end lands right under the others. The backend resolves any image_N it is sent
// (_PictureSlots in nodes.py), so the numbering may run as high as you connect.
//
// Socket 1 is named "image", not "image_1": that is the name the node shipped
// with, and a workflow saved before this change still carries it.
const PICTURE_RE = /^image(?:_(\d+))?$/;

function pictureIndex(name) {
    const m = PICTURE_RE.exec(name || "");
    if (!m) return 0;
    return m[1] ? parseInt(m[1], 10) : 1;
}

function pictureTooltip(n) {
    return (
        "Extra reference image. Connected images are numbered in socket order, " +
        "so this one reaches the LLM as <Picture " + n + "> when every socket " +
        "above it is used too. Filling it grows another socket."
    );
}

// A wire belongs to a socket POSITION, not to the socket: the frontend files
// every link under (node, input index), and input.link is read back from that
// index. So input objects must never be moved around by hand - this file used
// to lift image_9 back up under image_8 by splicing node.inputs, which left
// every wire below it on the socket one step further down: seed landed on
// max_tokens, user_prompt on system_prompt. Sockets are only ever added with
// addInput() (at the very end, where a reload puts them too) and removed with
// removeInput(), the two calls that move the wires along with them.
function inputConnected(node, i) {
    if (typeof node.isInputConnected === "function") return node.isInputConnected(i);
    return node.inputs[i]?.link != null;
}

function syncPictureSlots(node) {
    try {
        if (!node.inputs) return;
        let filled = 0;
        node.inputs.forEach((inp, i) => {
            const n = pictureIndex(inp.name);
            if (n && inputConnected(node, i)) filled = Math.max(filled, n);
        });
        // One free socket after the last filled one, and not one more.
        const want = filled + 1;

        // Trailing empties go, from the bottom up so the indices stay valid.
        // A hole in the middle stays: the node numbers the pictures by the
        // sockets that carry one, so image + image_3 is still <Picture 1> and
        // <Picture 2>, and removing the gap would move a wire the user made.
        for (let i = node.inputs.length - 1; i >= 0; i--) {
            const n = pictureIndex(node.inputs[i].name);
            if (n > 1 && n > want && !inputConnected(node, i)) node.removeInput(i);
        }

        let highest = 0;
        for (const inp of node.inputs) highest = Math.max(highest, pictureIndex(inp.name));
        for (let n = highest + 1; n <= want; n++) {
            node.addInput("image_" + n, "IMAGE", { tooltip: pictureTooltip(n) });
        }
        app.graph?.setDirtyCanvas(true, true);
    } catch (e) {
        console.warn("[coco] could not grow the image slots:", e);
    }
}

// ------------------------------------------------------ reopening a workflow
// A saved node lists its inputs in the order they had on screen, and each link
// says which POSITION it lands on. On load, the frontend rebuilds the inputs in
// the node definition's order instead - picture sockets that had been trimmed
// come back in the middle, sockets added at run time go to the end - but it
// keeps the saved positions on the links. Any difference between the two
// orders moved wires onto the wrong sockets, and every save made it worse.
//
// So before the graph is built (beforeConfigureGraph), each saved node is
// rewritten in the very order the frontend is about to give it, and its links
// are pointed at the new positions. Inputs are matched by NAME, which is what
// the frontend itself goes by. The order is read off a throwaway node, so it
// is the real one rather than a guess at it.
let probing = false;
let definedOrder = null;

function definedInputs() {
    if (definedOrder) return definedOrder;
    try {
        probing = true;
        const probe = globalThis.LiteGraph?.createNode(NODE_NAME);
        definedOrder = probe?.inputs?.map((i) => ({
            name: i.name,
            type: i.type,
            widget: i.widget ? { name: i.widget.name } : undefined,
        })) || null;
    } catch (e) {
        console.warn("[coco] could not read the input order:", e);
    } finally {
        probing = false;
    }
    return definedOrder;
}

function linkTarget(link) {
    return Array.isArray(link) ? [link[0], link[3]] : [link?.id, link?.target_id];
}

function setLinkSlot(link, slot) {
    if (Array.isArray(link)) link[4] = slot;
    else link.target_slot = slot;
}

function realignGraph(graph, defined) {
    const byNode = new Map();
    for (const node of graph?.nodes || []) {
        if (node?.type !== NODE_NAME || !Array.isArray(node.inputs)) continue;
        const saved = new Map(node.inputs.map((inp) => [inp.name, inp]));
        const definedNames = new Set(defined.map((d) => d.name));
        const ordered = defined
            .map((d) => saved.get(d.name) ?? { ...d, link: null })
            .concat(node.inputs.filter((inp) => !definedNames.has(inp.name)));
        if (ordered.every((inp, i) => inp.name === node.inputs[i]?.name)) continue;
        const slots = new Map();
        ordered.forEach((inp, i) => {
            if (inp.link != null) slots.set(String(inp.link), i);
        });
        node.inputs = ordered;
        byNode.set(String(node.id), slots);
    }
    if (!byNode.size) return 0;
    let moved = 0;
    for (const link of graph.links || []) {
        const [id, target] = linkTarget(link);
        const slot = byNode.get(String(target))?.get(String(id));
        if (slot === undefined) continue;
        setLinkSlot(link, slot);
        moved++;
    }
    return moved;
}

function realignWorkflow(graphData) {
    try {
        const defined = definedInputs();
        if (!defined || !graphData) return;
        let moved = realignGraph(graphData, defined);
        for (const sub of graphData.definitions?.subgraphs || []) {
            moved += realignGraph(sub, defined);
        }
        if (moved) console.info("[coco] realigned %d wire(s) on reopening", moved);
    } catch (e) {
        console.warn("[coco] could not realign the saved wires:", e);
    }
}

app.registerExtension({
    name: "comfy.LLMPromptStudio",
    async setup() {
        startStatusPolling();
        await loadTemplates();
    },
    async beforeConfigureGraph(graphData) {
        realignWorkflow(graphData);
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
            // The node built only to read the input order: never shown.
            if (probing) return ret;

            // Turn `model` into a dropdown, in its own slot. Done before
            // configure() runs, so a saved value lands straight in the combo.
            swapModelToCombo(node);

            const detectBtn = node.addWidget(
                "button",
                "🔄 Refresh the model list",
                null,
                () => detectModel(node)
            );
            dontSerialize(detectBtn);

            // Pointing at another server must not keep offering the old list.
            for (const name of ["base_url", "api_key"]) {
                const w = getWidget(node, name);
                if (!w) continue;
                const orig = w.callback;
                w.callback = function () {
                    const r = orig?.apply(this, arguments);
                    detectModel(node);
                    refreshStatus(node, true);
                    return r;
                };
            }

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
            dontSerialize(presetBtn);

            dontSerialize(node.addWidget("button", "🆕 New story (empty the sheet)",
                null, () => setStorySheet(node, "")));
            dontSerialize(node.addWidget("button", "↩ Undo the last sheet change",
                null, () => {
                    if (node._prevSheet === undefined) {
                        toast("info", "Story sheet", "Nothing to undo yet.");
                        return;
                    }
                    setStorySheet(node, node._prevSheet);
                }));

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
                // A saved name is kept as the selected entry; the list around
                // it is refreshed either way. Auto stays the default pick.
                normalizeModel(node);
                detectModel(node);
                // After configure(), so a saved workflow's box heights are back.
                makeAllResizable(node);
                // Same reason: a workflow using image_6 must have its wires
                // restored before the empty sockets around them are trimmed.
                syncPictureSlots(node);
                refreshStatus(node);
            }, 250);

            return ret;
        };

        // Connecting the last empty picture socket grows the next one;
        // unwiring the bottom ones takes the spares away again.
        const onConnectionsChange = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function (type, index, connected, link_info) {
            const r = onConnectionsChange?.apply(this, arguments);
            // LiteGraph.INPUT === 1. While the graph is still being rebuilt the
            // links are half restored, so counting sockets then would trim the
            // ones a wire is about to land on.
            if (type === 1 && !app.configuringGraph) {
                const node = this;
                setTimeout(() => syncPictureSlots(node), 0);
            }
            return r;
        };

        // A workflow loaded from disk goes through configure(), not through the
        // creation path above when the node already exists in the graph data.
        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const r = onConfigure?.apply(this, arguments);
            const node = this;
            setTimeout(() => syncPictureSlots(node), 300);
            return r;
        };

        // The status dot, right end of the title bar.
        const onDrawForeground = nodeType.prototype.onDrawForeground;
        nodeType.prototype.onDrawForeground = function (ctx) {
            const r = onDrawForeground?.apply(this, arguments);
            if (this.flags?.collapsed) return r;
            const h = globalThis.LiteGraph?.NODE_TITLE_HEIGHT || 30;
            ctx.save();
            ctx.beginPath();
            ctx.arc(this.size[0] - h / 2, -h / 2, 5, 0, Math.PI * 2);
            ctx.fillStyle = DOT_COLORS[this._llmOnline];
            ctx.fill();
            ctx.lineWidth = 1;
            ctx.strokeStyle = "rgba(0, 0, 0, 0.5)";
            ctx.stroke();
            ctx.restore();
            return r;
        };

        // Show the generated prompt (cleaned, no thinking) on the node itself.
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            const node = this;
            // A stand-in went down the graph instead of a fresh prompt: say so
            // where it cannot be missed, the preview alone is easy to overlook.
            const offline = message?.llm_offline;
            if (offline) {
                node._llmOnline = false;
                toast("warn", "LLM offline",
                      Array.isArray(offline) ? offline.join("") : String(offline));
            } else if (message?.text !== undefined) {
                node._llmOnline = true;
            }
            const join = (v) => (Array.isArray(v) ? v.join("") : String(v));
            if (message?.story_sheet !== undefined) {
                setStorySheet(node, join(message.story_sheet));
            }
            if (message?.story_note) toast("warn", "Story mode", join(message.story_note));
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
                dontSerialize(w); // preview only: saved nowhere, sent nowhere
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
