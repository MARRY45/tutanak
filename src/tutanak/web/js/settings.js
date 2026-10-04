// Settings and a status check of every local component.

import { api } from "./api.js";
import { t } from "./i18n.js";
import { append, clear, h, icon, toast } from "./util.js";

const LANGUAGES = ["tr", "en", "de", "fr", "es", "it", "pt", "nl", "ru", "ar", "az", "ja", "ko", "zh"];
const WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"];

export class SettingsView {
  constructor(app) {
    this.app = app;
  }

  async mount(el) {
    this.el = el;
    this.values = { ...this.app.settings };
    this.status = h("section", { class: "panel status-panel" }, h("h2", null, t("status")), h("p", { class: "muted pulse" }, "…"));
    clear(el).append(h("a", { class: "back", href: "#/" }, icon("back", 16), t("back")), h("div", { class: "settings-grid" }, this.form(), this.status));
    this.loadStatus();
  }

  field(label, control, hint) {
    return h("label", { class: "field" }, h("span", null, label), control, hint ? h("small", { class: "muted" }, hint) : null);
  }

  select(key, options) {
    return h(
      "select",
      { onchange: (event) => (this.values[key] = event.target.value) },
      options.map(([value, label]) => h("option", { value, selected: String(this.values[key]) === String(value) }, label)),
    );
  }

  text(key, attrs = {}) {
    return h("input", {
      type: "text",
      value: this.values[key],
      spellcheck: false,
      ...attrs,
      oninput: (event) => (this.values[key] = attrs.type === "number" ? Number(event.target.value) : event.target.value),
    });
  }

  form() {
    const auto = ["auto", t("s_auto")];
    const languages = LANGUAGES.map((code) => [code, code.toUpperCase()]);
    const remote = h("input", {
      type: "checkbox",
      checked: this.values.allow_remote_llm,
      onchange: (event) => (this.values.allow_remote_llm = event.target.checked),
    });
    this.modelList = h("datalist", { id: "llm-models" });
    return h(
      "form",
      {
        class: "panel settings-form",
        onsubmit: (event) => {
          event.preventDefault();
          this.save();
        },
      },
      h("h2", null, t("s_general")),
      this.field(t("s_ui_language"), this.select("ui_language", [["tr", "Türkçe"], ["en", "English"]])),

      h("h2", null, t("s_speech")),
      this.field(t("s_language"), this.select("language", [auto, ...languages])),
      this.field(t("s_whisper_model"), this.select("whisper_model", [auto, ...WHISPER_MODELS.map((m) => [m, m])])),
      this.field(t("s_whisper_device"), this.select("whisper_device", [auto, ["cuda", "GPU (CUDA)"], ["cpu", "CPU"]])),

      h("h2", null, t("s_llm")),
      this.field(t("s_llm_url"), this.text("llm_base_url")),
      this.field(t("s_llm_model"), this.text("llm_model", { list: "llm-models" })),
      this.field(t("s_embed_model"), this.text("embed_model", { list: "llm-models" })),
      this.modelList,
      h("label", { class: "check" }, remote, h("span", null, t("s_allow_remote"), h("small", { class: "muted" }, t("s_allow_remote_hint")))),

      h("h2", null, t("s_notes")),
      this.field(t("s_notes_language"), this.select("notes_language", [["auto", t("s_same_as_speech")], ...languages])),
      this.field(t("s_note_interval"), this.text("note_interval_s", { type: "number", min: 30, max: 900, step: 30 })),

      h("h2", null, t("s_screen")),
      this.field(t("s_screen_interval"), this.text("screen_interval_s", { type: "number", min: 0.5, max: 30, step: 0.5 })),
      this.field(t("s_ocr"), this.select("ocr_engine", [auto, ["windows", "Windows OCR"], ["rapidocr", "RapidOCR"], ["off", t("s_off")]])),

      h("div", { class: "form-actions" }, h("button", { class: "button primary", type: "submit" }, t("save"))),
    );
  }

  async save() {
    try {
      const languageChanged = this.values.ui_language !== this.app.settings.ui_language;
      this.app.settings = await api.put("/settings", this.values);
      toast(t("saved"));
      if (languageChanged) this.app.rebuild();
      else this.loadStatus();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async loadStatus() {
    let health;
    try {
      health = await api.get("/health");
    } catch (error) {
      clear(this.status).append(h("h2", null, t("status")), h("p", { class: "notice error" }, error.message));
      return;
    }
    const row = (label, ok, detail) =>
      h("li", { class: ok ? "ok" : "off" }, h("span", { class: "dot" }), h("span", { class: "status-name" }, label), h("span", { class: "muted" }, detail));
    const asr = health.asr;
    const llm = health.llm;
    const rows = [
      row(t("st_audio"), health.capture.audio, health.capture.audio ? "" : t("st_not_installed")),
      row(t("st_screen"), health.capture.screen, health.capture.screen ? "" : t("st_not_installed")),
      row(
        t("st_asr"),
        asr.available,
        asr.available ? `${asr.model} · ${asr.device.toUpperCase()}${asr.loaded ? "" : ` · ${t("st_loaded_on_demand")}`}` : t("st_not_installed"),
      ),
      row(t("st_ocr"), Boolean(health.ocr.engine), health.ocr.engine || t("st_not_installed")),
      row(t("st_ffmpeg"), health.ffmpeg, health.ffmpeg ? "" : t("st_missing")),
      row(t("st_llm"), llm.reachable, llm.error || (llm.reachable ? llm.base_url : `${llm.base_url} · ${t("st_unreachable")}`)),
    ];
    if (llm.reachable) {
      rows.push(row(t("st_llm_model"), llm.model_available, llm.model_available ? llm.model : `${llm.model} · ${t("st_missing")}`));
      if (llm.embed_model) {
        rows.push(row(t("st_embed"), llm.embed_available, llm.embed_available ? llm.embed_model : `${llm.embed_model} · ${t("st_missing")}`));
      }
    }
    if (this.modelList) append(clear(this.modelList), llm.models.map((name) => h("option", { value: name })));
    clear(this.status).append(
      h("h2", null, t("status")),
      h("ul", { class: "status-list" }, rows),
      h("h3", { class: "label" }, t("data_dir")),
      h("code", { class: "path" }, health.data_dir),
      h("p", { class: "muted" }, t("data_dir_hint")),
      h("p", { class: "muted version" }, `Tutanak ${health.version}`),
    );
  }
}
