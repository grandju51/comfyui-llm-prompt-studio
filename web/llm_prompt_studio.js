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
// grey = not asked yet. Asked only by the refresh / Check buttons; every run
// sets it too (onExecuted below).
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
    // Switched off: the LLM is not used, so its state is not shown either.
    if (typeof isEnabled === "function" && !isEnabled(node)) {
        if (node._llmOnline !== undefined) {
            node._llmOnline = undefined;
            app.graph?.setDirtyCanvas(true, false);
        }
        return;
    }
    const base = getWidget(node, "base_url")?.value || "http://localhost:1234/v1";
    const key = getWidget(node, "api_key")?.value || "";
    // "Checking" is shown only while a request is really in flight.
    node._llmChecking = true;
    syncBar(node);
    let online;
    try {
        online = await serverStatus(base, key, force);
    } finally {
        node._llmChecking = false;
    }
    node._llmOnline = online;
    syncBar(node);
    app.graph?.setDirtyCanvas(true, false);
}

// Nothing is requested on its own: no timer, nothing on load, on the LLM switch
// or on a URL edit. The server is asked only when a refresh / Check button is
// pressed; every run updates the dot too, from its own result (onExecuted).

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
        w.options.getMinHeight = () => (w._cocoHidden ? 0 : w._cocoBox || origMin?.());
        w.options.getMaxHeight = () => (w._cocoHidden ? 0 : w._cocoBox || origMax?.());
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
            // Hidden (display:none) reads as height 0: not a resize by the user.
            if (w._cocoHidden) return;
            if (w._cocoResync) {
                w._cocoResync = false;
                last = Math.round(el.offsetHeight) || last;
                return;
            }
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

// ====================================================================
// Compact layout: the node shows its text boxes, target_model and the story
// toggle; every other setting lives in a Settings dialog opened from the bar
// at the top of the node, next to the LLM on/off switch.
//
// NOTHING here removes or reorders a widget. widgets_values is positional, so
// the hidden widgets stay exactly where they were and keep their values: they
// are only given no height and no drawing (setHidden), and the dialog edits
// them through the same `.value` / `.callback` a click on the node would.
// ====================================================================
const BAR_H = 40;

// Everything the node keeps out of sight until the Settings dialog is opened.
// `enabled` is driven by the switch in the bar; control_after_generate is the
// combo the frontend attaches to `seed`.
const HIDDEN_NAMES = [
    "model", "api_key", "temperature", "top_p", "top_k", "min_p",
    "repeat_penalty", "max_tokens", "seed", "control_after_generate",
    "thinking", "strip_before_tag", "no_think_model", "keep_history",
    "max_history_turns", "reset_history", "timeout", "image_analysis_size",
    "audio_role", "video_stride", "video_max_frames", "video_frame_size",
    "video_fps", "video_role", "enable_min_p", "enable_repeat_penalty",
    "unload_after", "context_length", "context_slots", "on_llm_offline",
    "enabled",
];

const BOX_HINTS = {
    global_directives: "Global directives - your own rules, added for every target model",
    system_prompt: "System prompt - the LLM card (loaded from the target model)",
    user_prompt: "Your idea / message",
    story_sheet: "Story sheet - written by the node while story mode is on",
};
// Boxes the LLM never sees while the node is switched off.
const DIM_WHEN_OFF = ["global_directives", "system_prompt", "story_sheet"];

const SETTINGS_LAYOUT = [
    { title: "Connection", icon: "🔌", rows: [
        { name: "base_url", label: "Server URL", kind: "text" },
        { name: "api_key", label: "API key", kind: "text", password: true },
        { name: "model", label: "Model", kind: "model" },
        { kind: "status", label: "Server" },
    ]},
    { title: "Sampling", icon: "🎛️", rows: [
        { name: "temperature", label: "Temperature", float: true, step: 0.01, slider: true },
        { name: "top_p", label: "Top P", float: true, step: 0.01, slider: true },
        { name: "top_k", label: "Top K" },
        { name: "min_p", label: "Min P", float: true, step: 0.01, slider: true, pair: "enable_min_p" },
        { name: "repeat_penalty", label: "Repeat penalty", float: true, step: 0.01, slider: true, pair: "enable_repeat_penalty" },
        { name: "max_tokens", label: "Max tokens" },
        { name: "seed", label: "Seed" },
        { name: "control_after_generate", label: "Seed after run" },
    ]},
    { title: "Thinking", icon: "🧠", rows: [
        { name: "thinking", label: "Thinking mode" },
        { name: "strip_before_tag", label: "Strip before tag", kind: "text" },
        { name: "no_think_model", label: "No-think model", kind: "text" },
    ]},
    { title: "Conversation memory", icon: "💬", rows: [
        { name: "keep_history", label: "Keep history" },
        { name: "max_history_turns", label: "History turns" },
        { name: "reset_history", label: "Reset history" },
    ]},
    { title: "Server behaviour", icon: "🖥️", rows: [
        { name: "timeout", label: "Timeout (s)" },
        { name: "context_length", label: "Context length" },
        { name: "context_slots", label: "Context slots" },
        { name: "unload_after", label: "Unload model after" },
        { name: "on_llm_offline", label: "If the LLM is offline" },
    ]},
    { title: "Pictures, video & audio", icon: "🖼️", rows: [
        { name: "image_analysis_size", label: "Image size" },
        { name: "video_role", label: "Video role" },
        { name: "video_frame_size", label: "Video frame size" },
        { name: "video_stride", label: "Video stride" },
        { name: "video_max_frames", label: "Video max frames" },
        { name: "video_fps", label: "Video FPS", float: true, step: 0.1 },
        { name: "audio_role", label: "Audio role" },
    ]},
    { title: "System prompt", icon: "📥", rows: [
        { kind: "preset", label: "Preset" },
    ]},
];

// ----------------------------------------------------------------- hiding
// Hidden = not drawn, no height. Reversible: the story buttons come and go
// with story mode. `hidden` is what the current frontend reads; the zero
// height keeps older layouts from reserving a row for it.
function setHidden(w, hide) {
    if (!w) return;
    if (hide) {
        if (w._cocoHidden) return;
        w._cocoHidden = true;
        w._cocoHadSize = Object.prototype.hasOwnProperty.call(w, "computeSize");
        w._cocoSize = w.computeSize;
        w._cocoHadOpt = !!w.options && "hidden" in w.options;
        w._cocoOptHidden = w.options?.hidden;
        w.computeSize = () => [0, -4];
        w.hidden = true;
        // A DOM widget (a text box) is a real element on the page: hide that
        // too. Only its visibility changes - the text inside is never touched.
        if (w.element?.style) {
            w._cocoDisplay = w.element.style.display;
            w.element.style.display = "none";
        }
        w.options = w.options || {};
        w.options.hidden = true;
    } else {
        if (!w._cocoHidden) return;
        w._cocoHidden = false;
        if (w._cocoHadSize) w.computeSize = w._cocoSize;
        else delete w.computeSize;
        w.hidden = false;
        if (w.element?.style) {
            w.element.style.display = w._cocoDisplay || "";
            // Coming back from height 0 is not the user resizing the box: the
            // next size change only re-learns the height (see makeResizable).
            w._cocoResync = true;
            setTimeout(() => { w._cocoResync = false; }, 500);
        }
        if (w._cocoHadOpt) w.options.hidden = w._cocoOptHidden;
        else if (w.options) delete w.options.hidden;
    }
}

function isEnabled(node) {
    const w = getWidget(node, "enabled");
    return w ? !!w.value : true;
}

function setW(node, w, v) {
    w.value = v;
    try {
        w.callback?.(v, app.canvas, node, null, null);
    } catch (e) {
        console.warn("[coco] callback of", w.name, "failed:", e);
    }
    node.graph?.setDirtyCanvas(true, true);
}

function later(node, key, fn, ms = 350) {
    clearTimeout(node[key]);
    node[key] = setTimeout(fn, ms);
}

function h(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
}

// ------------------------------------------------------------------ styles
const STYLE_ID = "coco-styles";
function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const st = document.createElement("style");
    st.id = STYLE_ID;
    st.textContent = `
.coco-bar{width:100%;height:100%;min-width:0;overflow:hidden;container-type:inline-size;box-sizing:border-box;display:flex;align-items:center;gap:6px;padding:0 2px;pointer-events:auto;font:12px/1.2 system-ui,sans-serif;color:var(--fg-color,#e6e7ea)}
.coco-bar button{flex:none;white-space:nowrap;font:inherit;cursor:pointer;border-radius:999px;border:1px solid var(--border-color,#43464f);background:var(--comfy-input-bg,#2b2d33);color:inherit;padding:6px 10px;display:inline-flex;align-items:center;gap:6px;transition:background .15s,border-color .15s}
.coco-bar button:hover{border-color:#6ea8fe;background:rgba(110,168,254,.12)}
.coco-bar button:disabled{opacity:.45;cursor:default}
.coco-bar button:disabled:hover{border-color:var(--border-color,#43464f);background:var(--comfy-input-bg,#2b2d33)}
.coco-power .coco-led{width:9px;height:9px;border-radius:50%;background:#3ddc84;box-shadow:0 0 6px #3ddc84}
.coco-bar.off .coco-power{border-color:#f5a524;background:rgba(245,165,36,.12)}
.coco-bar.off .coco-power .coco-led{background:#f5a524;box-shadow:0 0 6px #f5a524}
.coco-stat{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;opacity:.75;display:flex;align-items:center;gap:6px}
.coco-stat i{width:7px;height:7px;border-radius:50%;background:#888;flex:none}
.coco-stat span{overflow:hidden;text-overflow:ellipsis}
@container (max-width:400px){.coco-check .tx{display:none}}
@container (max-width:330px){.coco-gear .tx,.coco-stat span{display:none}.coco-stat{flex:1 1 0}}
@container (max-width:230px){.coco-ptext{display:none}}
.coco-stat[data-state="true"] i{background:#3ddc84}
.coco-stat[data-state="false"] i{background:#ff4d4d}
.coco-stat[data-state="bypass"] i{background:#f5a524}
textarea.coco-box{box-sizing:border-box;background:var(--comfy-input-bg,#1d1e22)!important;border:1px solid var(--border-color,#3a3d45)!important;border-radius:8px!important;padding:8px 10px!important;font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace!important;transition:border-color .15s,opacity .15s,box-shadow .15s}
textarea.coco-box:focus{outline:none;border-color:#6ea8fe!important;box-shadow:0 0 0 2px rgba(110,168,254,.25)}
.coco-ovl{position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.55);backdrop-filter:blur(2px);display:flex;align-items:center;justify-content:center;font:13px/1.35 system-ui,sans-serif}
.coco-dlg{width:min(600px,94vw);max-height:86vh;display:flex;flex-direction:column;background:var(--comfy-menu-bg,#202226);color:var(--fg-color,#e6e7ea);border:1px solid var(--border-color,#3a3d45);border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,.6);overflow:hidden}
.coco-hd{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid var(--border-color,#3a3d45)}
.coco-hd h3{margin:0;font-size:15px;font-weight:600;flex:1}
.coco-x{cursor:pointer;border:0;background:transparent;color:inherit;font-size:18px;line-height:1;padding:4px 8px;border-radius:6px;opacity:.7}
.coco-x:hover{opacity:1;background:rgba(255,255,255,.08)}
.coco-body{overflow-y:auto;padding:12px 18px 6px}
.coco-card{margin:0 0 12px;border:1px solid var(--border-color,#3a3d45);border-radius:10px;background:rgba(255,255,255,.025)}
.coco-card>h4{margin:0;padding:9px 14px;font-size:11px;letter-spacing:.07em;text-transform:uppercase;opacity:.7;border-bottom:1px solid var(--border-color,#3a3d45)}
.coco-row{display:flex;align-items:center;gap:14px;padding:8px 14px;min-height:34px}
.coco-row+.coco-row{border-top:1px solid rgba(255,255,255,.05)}
.coco-lbl{flex:0 0 38%;min-width:0}
.coco-lbl small{display:block;opacity:.5;font-size:11px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.coco-ctl{flex:1;min-width:0;display:flex;align-items:center;gap:8px;justify-content:flex-end}
.coco-dlg input[type=text],.coco-dlg input[type=password],.coco-dlg input[type=number],.coco-dlg select{box-sizing:border-box;width:100%;background:var(--comfy-input-bg,#14151a);color:inherit;border:1px solid var(--border-color,#3a3d45);border-radius:7px;padding:6px 9px;font:inherit}
.coco-dlg input[type=number]{width:96px;flex:none}
.coco-dlg input:focus,.coco-dlg select:focus{outline:none;border-color:#6ea8fe}
.coco-dlg input[type=range]{flex:1;accent-color:#6ea8fe}
.coco-sw{position:relative;display:inline-block;width:38px;height:22px;flex:none}
.coco-sw input{opacity:0;width:0;height:0}
.coco-sw span{position:absolute;inset:0;border-radius:999px;background:#454851;cursor:pointer;transition:background .15s}
.coco-sw span:before{content:"";position:absolute;width:16px;height:16px;left:3px;top:3px;border-radius:50%;background:#fff;transition:transform .15s}
.coco-sw input:checked+span{background:#4c8dff}
.coco-sw input:checked+span:before{transform:translateX(16px)}
.coco-btn{cursor:pointer;border:1px solid var(--border-color,#43464f);background:var(--comfy-input-bg,#2b2d33);color:inherit;border-radius:7px;padding:6px 12px;font:inherit;white-space:nowrap}
.coco-btn:hover{border-color:#6ea8fe;background:rgba(110,168,254,.12)}
.coco-ft{padding:10px 18px;border-top:1px solid var(--border-color,#3a3d45);font-size:11px;opacity:.55}
.coco-pair{display:flex;align-items:center;gap:6px;font-size:11px;opacity:.8}
`;
    document.head.appendChild(st);
}

// ----------------------------------------------------------------- the bar
// The bar REPLACES the base_url widget, in its own slot (the same trick as the
// model combo above): it is serialized under the name base_url and holds the
// URL as its value, so the position and the saved value of every widget stay
// as they were, and the top of the node is the one place left for it. The URL
// itself is edited in the Settings dialog.
function buildToolbar(node) {
    const old = getWidget(node, "base_url");
    if (!old || old._cocoBar || typeof node.addDOMWidget !== "function") return false;
    try {
        const bar = h("div", "coco-bar");
        const power = h("button", "coco-power");
        power.type = "button";
        power.append(h("span", "coco-led"), h("span", "coco-ptext", "LLM on"));
        power.title = "On: the prompt is written by the LLM. Off: user_prompt is " +
            "sent straight to the prompt output, nothing goes to the LLM.";
        const stat = h("span", "coco-stat");
        stat.append(h("i"), h("span", "coco-stxt", "checking…"));
        const check = h("button", "coco-check");
        check.type = "button";
        check.append(h("span", "ic", "↻"), h("span", "tx", "Check server"));
        const gear = h("button", "coco-gear");
        gear.type = "button";
        gear.title = "Settings";
        gear.append(h("span", "ic", "⚙"), h("span", "tx", "Settings"));
        bar.append(power, stat, check, gear);

        let url = String(old.value ?? "").trim() || "http://localhost:1234/v1";
        const i = node.widgets.indexOf(old);
        const w = node.addDOMWidget("base_url", "cocoBar", bar, {
            getValue: () => url,
            setValue: (v) => { url = String(v ?? ""); },
            getMinHeight: () => BAR_H + 8,
            getMaxHeight: () => BAR_H + 8,
            margin: 4,
        });
        if (node.widgets[node.widgets.length - 1] !== w) {
            // Not where addDOMWidget is expected to leave it: do not guess.
            const at = node.widgets.indexOf(w);
            if (at >= 0) node.widgets.splice(at, 1);
            return false;
        }
        node.widgets.pop();
        node.widgets.splice(i, 1, w);
        w._cocoBar = true;
        w.computeSize = (width) => [width, BAR_H + 8];
        old.onRemove?.();

        node._cocoBarEl = bar;
        power.addEventListener("click", () => {
            const ew = getWidget(node, "enabled");
            if (!ew) return;
            setW(node, ew, !ew.value);
            syncBar(node, true);
        });
        gear.addEventListener("click", () => openSettings(node));
        check.addEventListener("click", () => refreshStatus(node, true));
        if (!getWidget(node, "enabled")) power.style.display = "none";
        return true;
    } catch (e) {
        console.warn("[coco] could not build the toolbar:", e);
        return false;
    }
}

function statusText(node, on) {
    if (!on) return ["bypass", "Prompt passes straight through"];
    if (node._llmChecking) return ["undefined", "Checking…"];
    if (node._llmOnline === true) return ["true", "Online"];
    if (node._llmOnline === false) return ["false", "Offline"];
    return ["undefined", "Not checked"];
}

function syncBar(node, force = false) {
    const bar = node._cocoBarEl;
    if (!bar) return;
    const on = isEnabled(node);
    const key = on + "|" + node._llmOnline + "|" + !!node._llmChecking;
    if (!force && node._cocoBarKey === key) return;
    node._cocoBarKey = key;
    bar.classList.toggle("off", !on);
    bar.querySelector(".coco-ptext").textContent = on ? "LLM on" : "LLM off";
    const cb = bar.querySelector(".coco-check");
    cb.disabled = !on || !!node._llmChecking;
    cb.title = on ? "Ask the LLM server whether it is up (nothing else is checked automatically)"
                  : "Switch the LLM on to check the server";
    const [state, text] = statusText(node, on);
    const stat = bar.querySelector(".coco-stat");
    stat.dataset.state = state;
    bar.querySelector(".coco-stxt").textContent = text;
    stat.title = on ? "LLM server: " + text.toLowerCase() : text;
    for (const name of DIM_WHEN_OFF) {
        const el = getWidget(node, name)?.element;
        if (el) el.style.opacity = on ? "" : "0.4";
    }
    app.graph?.setDirtyCanvas(true, false);
}

// Story buttons only matter while story mode is on.
function syncStoryButtons(node) {
    const on = !!getWidget(node, "story_mode")?.value;
    if (node._cocoStoryOn === on) return;
    node._cocoStoryOn = on;
    for (const b of node._cocoStoryBtns || []) setHidden(b, !on);
    // The sheet box shows only in story mode. Its text stays in the widget (and
    // in the saved workflow) either way; a run in story mode still writes to it.
    setHidden(getWidget(node, "story_sheet"), !on);
    if (on && node.computeSize) {
        const need = node.computeSize()[1];
        if (need > node.size[1]) node.setSize([node.size[0], need]);
    }
    app.graph?.setDirtyCanvas(true, true);
}

// Called every redraw: cheap comparisons only, the work is deferred.
function syncUI(node) {
    const key = isEnabled(node) + "|" + node._llmOnline + "|" + !!node._llmChecking + "|" +
        !!getWidget(node, "story_mode")?.value;
    if (node._cocoUIKey === key) return;
    node._cocoUIKey = key;
    setTimeout(() => {
        syncBar(node);
        syncStoryButtons(node);
    }, 0);
}

// ------------------------------------------------------------ applying it
function applyCompactUI(node, barOK) {
    // Without the bar there is nowhere to put the switch: leave the server URL
    // and the switch where the node always had them, and add a button.
    const names = HIDDEN_NAMES.filter((n) => barOK || n !== "enabled");
    for (const n of names) setHidden(getWidget(node, n), true);
    const btns = node._cocoBtns || {};
    setHidden(btns.refresh, true);
    setHidden(btns.preset, true);
    node._cocoStoryBtns = [btns.newStory, btns.undo].filter(Boolean);
    for (const b of node._cocoStoryBtns) setHidden(b, true);
    node._cocoStoryOn = false;
    setHidden(getWidget(node, "story_sheet"), true);
    if (!barOK) {
        dontSerialize(node.addWidget("button", "⚙ Settings", null, () => openSettings(node)));
    }
}

// After configure(): the saved values and heights are back.
function finishCompactUI(node) {
    for (const [name, text] of Object.entries(BOX_HINTS)) {
        const el = getWidget(node, name)?.element;
        if (!el || el.tagName !== "TEXTAREA") continue;
        el.classList.add("coco-box");
        el.placeholder = text;
        el.title = text;
    }
    node._cocoBarKey = undefined;
    node._cocoUIKey = null;
    syncBar(node, true);
    syncStoryButtons(node);
    // First time this layout meets a node (new, or saved by an older version):
    // shrink it to what is left. Never again, so a size the user sets stays.
    if (!node.properties) node.properties = {};
    if (node.properties.cocoLayout !== 2) {
        node.properties.cocoLayout = 2;
        setTimeout(() => {
            try {
                const need = node.computeSize()[1] + 10;
                node.setSize([Math.max(node.size[0], 420), need]);
                app.graph?.setDirtyCanvas(true, true);
            } catch (e) {
                console.warn("[coco] could not fit the node:", e);
            }
        }, 350);
    }
}

// --------------------------------------------------------- settings dialog
let openDialog = null;

function closeSettings() {
    if (!openDialog) return;
    document.removeEventListener("keydown", openDialog.onKey, true);
    openDialog.overlay.remove();
    openDialog = null;
}

function hintOf(w) {
    const t = String(w?.tooltip || w?.options?.tooltip || "").replace(/\s+/g, " ").trim();
    if (!t) return "";
    const first = t.split(/(?<=[.!?])\s/)[0];
    return first.length > 120 ? first.slice(0, 117) + "…" : first;
}

function numberControl(node, w, row, sync) {
    const o = w.options || {};
    const wrap = h("div", "coco-ctl");
    const num = h("input");
    num.type = "number";
    if (o.min != null) num.min = o.min;
    if (o.max != null) num.max = o.max;
    num.step = row.step ?? (row.float ? 0.01 : 1);
    let rng = null;
    if (row.slider && o.min != null && o.max != null) {
        rng = h("input");
        rng.type = "range";
        rng.min = o.min;
        rng.max = o.max;
        rng.step = num.step;
        wrap.append(rng);
    }
    wrap.append(num);
    const commit = (raw) => {
        let v = parseFloat(raw);
        if (Number.isFinite(v)) {
            if (o.min != null) v = Math.max(o.min, v);
            if (o.max != null) v = Math.min(o.max, v);
            v = row.float ? Math.round(v * 1000) / 1000 : Math.round(v);
            setW(node, w, v);
        }
        sync();
    };
    num.addEventListener("change", () => commit(num.value));
    if (rng) {
        rng.addEventListener("input", () => { num.value = rng.value; });
        rng.addEventListener("change", () => commit(rng.value));
    }
    return { el: wrap, sync: () => {
        num.value = w.value;
        if (rng) rng.value = w.value;
    }};
}

function toggleControl(node, w) {
    const lab = h("label", "coco-sw");
    const cb = h("input");
    cb.type = "checkbox";
    lab.append(cb, h("span"));
    cb.addEventListener("change", () => setW(node, w, cb.checked));
    return { el: lab, sync: () => { cb.checked = !!w.value; } };
}

function comboControl(node, w, sync) {
    const sel = h("select");
    const valuesOf = () => {
        const v = w.options?.values;
        const list = typeof v === "function" ? v(w, node) : v;
        return Array.isArray(list) ? list.map(String) : [];
    };
    sel.addEventListener("change", () => {
        setW(node, w, sel.value);
        sync();
    });
    return { el: sel, sync: () => {
        const list = valuesOf();
        const cur = String(w.value ?? "");
        if (cur && !list.includes(cur)) list.push(cur);
        const sig = list.join("\n");
        if (sel._sig !== sig) {
            sel.replaceChildren(...list.map((v) => {
                const o = h("option", "", v);
                o.value = v;
                return o;
            }));
            sel._sig = sig;
        }
        sel.value = cur;
    }};
}

function textControl(node, w, row) {
    const inp = h("input");
    inp.type = row.password ? "password" : "text";
    inp.spellcheck = false;
    inp.addEventListener("change", () => setW(node, w, inp.value.trim()));
    return { el: inp, sync: () => { inp.value = String(w.value ?? ""); } };
}

function openSettings(node) {
    closeSettings();
    injectStyles();
    const overlay = h("div", "coco-ovl");
    const dlg = h("div", "coco-dlg");
    overlay.append(dlg);

    const hd = h("div", "coco-hd");
    hd.append(h("h3", "", "⚙ coco settings"));
    const x = h("button", "coco-x", "✕");
    x.type = "button";
    x.title = "Close (Esc)";
    hd.append(x);
    const body = h("div", "coco-body");
    const ft = h("div", "coco-ft",
        "Changes apply immediately and are saved with the workflow. " +
        "The text boxes and the story toggle stay on the node.");
    dlg.append(hd, body, ft);

    const syncers = [];
    const syncAll = () => syncers.forEach((f) => f());

    for (const sec of SETTINGS_LAYOUT) {
        const card = h("div", "coco-card");
        card.append(h("h4", "", sec.icon + "  " + sec.title));
        let count = 0;
        for (const row of sec.rows) {
            const w = row.name ? getWidget(node, row.name) : null;
            if (row.name && !w) continue;   // an older backend without it
            const line = h("div", "coco-row");
            const lbl = h("div", "coco-lbl");
            lbl.append(h("div", "", row.label));
            const hint = hintOf(w);
            if (hint) {
                const s = h("small", "", hint);
                s.title = String(w.tooltip || w.options?.tooltip || "");
                lbl.append(s);
            }
            const ctl = h("div", "coco-ctl");
            line.append(lbl, ctl);

            const add = (c) => { ctl.append(c.el); syncers.push(c.sync); c.sync(); };
            if (row.kind === "status") {
                const st = h("span", "coco-stat");
                st.append(h("i"), h("span", "", ""));
                const btn = h("button", "coco-btn", "Check");
                btn.type = "button";
                btn.addEventListener("click", () => refreshStatus(node, true).then(syncAll));
                ctl.append(st, btn);
                syncers.push(() => {
                    const [state, text] = statusText(node, isEnabled(node));
                    st.dataset.state = state;
                    st.lastChild.textContent = text;
                });
            } else if (row.kind === "preset") {
                const btn = h("button", "coco-btn", "📥 Load preset for the target model");
                btn.type = "button";
                btn.title = "Replaces the system prompt box with the preset of the selected target model.";
                btn.addEventListener("click", () => node._cocoApplyTemplate?.());
                ctl.append(btn);
            } else if (row.kind === "model") {
                add(comboControl(node, w, syncAll));
                const btn = h("button", "coco-btn", "🔄");
                btn.type = "button";
                btn.title = "Re-read the model list from the server";
                btn.addEventListener("click", async () => {
                    await Promise.all([detectModel(node), refreshStatus(node, true)]);
                    syncAll();
                });
                ctl.append(btn);
            } else if (row.kind === "text") {
                add(textControl(node, w, row));
            } else if (w.type === "combo") {
                add(comboControl(node, w, syncAll));
            } else if (typeof w.value === "boolean") {
                add(toggleControl(node, w));
            } else if (w.type === "number" || typeof w.value === "number") {
                add(numberControl(node, w, row, syncAll));
            } else {
                add(textControl(node, w, row));
            }

            const pw = row.pair ? getWidget(node, row.pair) : null;
            if (pw) {
                const pair = h("div", "coco-pair");
                const t = toggleControl(node, pw);
                pair.append(t.el);
                pair.title = hintOf(pw) || "Send this value to the server";
                ctl.append(pair);
                syncers.push(t.sync);
                t.sync();
            }
            card.append(line);
            count++;
        }
        if (count) body.append(card);
    }

    const onKey = (e) => {
        if (e.key === "Escape") {
            e.stopPropagation();
            closeSettings();
        }
    };
    document.addEventListener("keydown", onKey, true);
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) closeSettings(); });
    x.addEventListener("click", closeSettings);
    document.body.appendChild(overlay);
    openDialog = { overlay, onKey };
}


app.registerExtension({
    name: "comfy.LLMPromptStudio",
    async setup() {
        injectStyles();
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
            // The bar takes the slot of base_url (top of the node).
            const barOK = buildToolbar(node);

            const detectBtn = node.addWidget(
                "button",
                "🔄 Refresh the model list",
                null,
                () => { detectModel(node); refreshStatus(node, true); }
            );
            dontSerialize(detectBtn);

            // Pointing at another server must not keep offering the old list.
            for (const name of ["base_url", "api_key"]) {
                const w = getWidget(node, name);
                if (!w) continue;
                const orig = w.callback;
                w.callback = function () {
                    const r = orig?.apply(this, arguments);
                    // Another server: what was known about the old one no
                    // longer holds. Nothing is requested - the dot goes grey
                    // until a refresh button or a run says otherwise.
                    node._llmOnline = undefined;
                    app.graph?.setDirtyCanvas(true, false);
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
            node._cocoApplyTemplate = applyTemplate;

            const newBtn = dontSerialize(node.addWidget("button", "🆕 New story (empty the sheet)",
                null, () => setStorySheet(node, "")));
            const undoBtn = dontSerialize(node.addWidget("button", "↩ Undo the last sheet change",
                null, () => {
                    if (node._prevSheet === undefined) {
                        toast("info", "Story sheet", "Nothing to undo yet.");
                        return;
                    }
                    setStorySheet(node, node._prevSheet);
                }));

            // Everything but the text boxes, target_model and the story toggle
            // goes behind the Settings dialog.
            node._cocoBtns = { refresh: detectBtn, preset: presetBtn,
                               newStory: newBtn, undo: undoBtn };
            applyCompactUI(node, barOK);

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
                // After configure(), so a saved workflow's box heights are back.
                makeAllResizable(node);
                finishCompactUI(node);
                // Same reason: a workflow using image_6 must have its wires
                // restored before the empty sockets around them are trimmed.
                syncPictureSlots(node);
                // The one automatic check: once, as the node appears (the
                // model list is NOT fetched; the 🔄 button does that).
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
            ctx.fillStyle = isEnabled(this) ? DOT_COLORS[this._llmOnline] : "#f5a524";
            syncUI(this);
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
                const gel = w.element || w.inputEl;
                if (gel) {
                    gel.readOnly = true;
                    gel.style.opacity = "0.85";
                    gel.classList.add("coco-box");
                }
            }
            w.value = value;
            makeResizable(node, w);
            app.graph.setDirtyCanvas(true, true);
        };
    },
});
