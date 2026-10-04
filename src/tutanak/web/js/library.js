// Home: start a recording, import a file, browse and search every meeting.

import { api, onEvent } from "./api.js";
import { askBox, sourceRow } from "./ask.js";
import { fmtDate, fmtDuration, t } from "./i18n.js";
import { append, clear, fmtTs, h, highlight, icon, toast } from "./util.js";

export class LibraryView {
  constructor(app) {
    this.app = app;
    this.meetings = [];
    this.current = null;
    this.devices = null;
    this.searchToken = 0;
    this.form = { title: "", mic: true, system: true, screen: false, micId: "default", speakerId: "default", monitor: 1 };
  }

  async mount(el, route) {
    this.el = el;
    this.q = route.q;
    this.unsubscribe = onEvent((event) => this.onEvent(event));
    this.timer = setInterval(() => this.tick(), 1000);
    [this.meetings, this.current] = await Promise.all([api.get("/meetings"), api.get("/recordings/current")]);
    this.render();
    api.get("/devices").then((devices) => {
      this.devices = devices;
      if (!this.q) this.renderStart();
    });
  }

  update(route) {
    if (route.q === this.q) return;
    this.q = route.q;
    this.render();
  }

  unmount() {
    if (this.unsubscribe) this.unsubscribe();
    clearInterval(this.timer);
  }

  async onEvent(event) {
    if (["meeting", "recording"].includes(event.type)) {
      [this.meetings, this.current] = await Promise.all([api.get("/meetings"), api.get("/recordings/current")]);
      if (!this.q) this.render();
    } else if (event.type === "progress" && !this.q) {
      const meeting = this.meetings.find((m) => m.id === event.id);
      if (meeting) {
        meeting.progress = event.stage ? { stage: event.stage, fraction: event.fraction } : null;
        this.renderList();
      }
    }
  }

  tick() {
    if (this.current && this.liveClock) {
      this.current.elapsed += 1;
      this.liveClock.textContent = fmtTs(this.current.elapsed);
    }
  }

  render() {
    clear(this.el);
    if (this.q) {
      this.results = h("section", { class: "results" });
      this.el.append(this.results);
      this.search();
      return;
    }
    this.startPanel = h("section", { class: "panel start" });
    this.list = h("section", { class: "meeting-list" });
    this.el.append(this.startPanel, this.list);
    this.renderStart();
    this.renderList();
  }

  // ------------------------------------------------------------------ start panel

  renderStart() {
    if (!this.startPanel) return;
    clear(this.startPanel);
    this.liveClock = null;
    if (this.current) {
      this.liveClock = h("span", { class: "clock" }, fmtTs(this.current.elapsed));
      this.startPanel.append(
        h(
          "div",
          { class: "live-banner" },
          h("span", { class: "rec-dot" }),
          h("strong", null, t("recording_now")),
          this.liveClock,
          h("a", { class: "button primary", href: `#/m/${this.current.id}` }, t("open_live")),
        ),
      );
      return;
    }
    const form = this.form;
    const title = h("input", {
      type: "text",
      class: "title-input",
      placeholder: t("title_placeholder"),
      value: form.title,
      oninput: (event) => (form.title = event.target.value),
    });
    const source = (key, iconName, label, hint) =>
      h(
        "label",
        { class: "source" },
        h("input", { type: "checkbox", checked: form[key], onchange: (event) => (form[key] = event.target.checked) }),
        icon(iconName, 20),
        h("span", null, h("strong", null, label), h("small", null, hint)),
      );
    const unavailable = this.devices && !this.devices.available;
    this.startButton = h(
      "button",
      { class: "button record", disabled: unavailable, onclick: () => this.start() },
      h("span", { class: "rec-dot" }),
      t("start"),
    );
    const file = h("input", { type: "file", accept: "audio/*,video/*,.mkv,.m4a,.opus,.flac", hidden: true });
    file.addEventListener("change", () => file.files[0] && this.importFile(file.files[0]));
    this.importButton = h(
      "button",
      { class: "button", title: t("import_hint"), onclick: () => file.click() },
      icon("upload", 16),
      t("import"),
    );
    append(this.startPanel, [
      h("h2", null, t("new_meeting")),
      title,
      h(
        "div",
        { class: "sources" },
        source("mic", "mic", t("src_mic"), t("src_mic_hint")),
        source("system", "speaker", t("src_system"), t("src_system_hint")),
        source("screen", "screen", t("src_screen"), t("src_screen_hint")),
      ),
      this.deviceOptions(),
      unavailable ? h("p", { class: "notice error" }, t("capture_missing"), " ", this.devices.error || "") : null,
      this.modelHint(),
      h("div", { class: "start-actions" }, this.startButton, this.importButton, file),
    ]);
    this.startPanel.ondragover = (event) => {
      event.preventDefault();
      this.startPanel.classList.add("dragging");
    };
    this.startPanel.ondragleave = () => this.startPanel.classList.remove("dragging");
    this.startPanel.ondrop = (event) => {
      event.preventDefault();
      this.startPanel.classList.remove("dragging");
      const dropped = event.dataTransfer.files[0];
      if (dropped) this.importFile(dropped);
    };
  }

  // Recording works right away, but the first transcript waits for a one-time download.
  modelHint() {
    const asr = this.devices && this.devices.asr;
    if (!asr) return null;
    if (!asr.available) return h("p", { class: "hint" }, t("asr_missing"));
    if (asr.cached) return null;
    return h("p", { class: "hint" }, t("asr_download", { model: asr.model, size: asr.size_mb ? `≈ ${asr.size_mb} MB` : "" }));
  }

  deviceOptions() {
    const devices = this.devices;
    if (!devices || !devices.available) return null;
    const form = this.form;
    const select = (key, items, label) =>
      h(
        "label",
        { class: "field" },
        h("span", null, label),
        h(
          "select",
          { onchange: (event) => (form[key] = event.target.value) },
          h("option", { value: "default", selected: form[key] === "default" }, t("device_default")),
          items.map((d) => h("option", { value: d.id, selected: form[key] === d.id }, d.name)),
        ),
      );
    return h(
      "details",
      { class: "devices" },
      h("summary", null, t("devices")),
      h(
        "div",
        { class: "device-grid" },
        select("micId", devices.microphones, t("src_mic")),
        select("speakerId", devices.speakers, t("src_system")),
        devices.monitors.length > 1
          ? h(
              "label",
              { class: "field" },
              h("span", null, t("monitor")),
              h(
                "select",
                { onchange: (event) => (form.monitor = Number(event.target.value)) },
                devices.monitors.map((m) =>
                  h("option", { value: m.index, selected: form.monitor === m.index }, `${m.index} · ${m.width}×${m.height}`),
                ),
              ),
            )
          : null,
      ),
    );
  }

  async start() {
    const form = this.form;
    this.startButton.disabled = true;
    this.startButton.lastChild.textContent = t("starting");
    try {
      const state = await api.post("/recordings", {
        title: form.title || null,
        microphone: form.mic ? form.micId : null,
        system: form.system ? form.speakerId : null,
        screen: form.screen,
        monitor: form.monitor,
      });
      location.hash = `#/m/${state.id}`;
    } catch (error) {
      toast(error.message, "error");
      this.renderStart();
    }
  }

  async importFile(file) {
    const data = new FormData();
    data.append("file", file);
    data.append("last_modified", String(file.lastModified || ""));
    if (this.form.title) data.append("title", this.form.title);
    this.importButton.disabled = true;
    this.importButton.lastChild.textContent = t("uploading");
    try {
      const created = await api.upload("/import", data);
      location.hash = `#/m/${created.id}`;
    } catch (error) {
      toast(error.message, "error");
      this.renderStart();
    }
  }

  // ------------------------------------------------------------------ meeting list

  renderList() {
    if (!this.list) return;
    clear(this.list);
    this.list.append(h("h2", null, t("meetings")));
    if (!this.meetings.length) {
      this.list.append(h("div", { class: "empty" }, h("p", null, t("no_meetings")), h("p", { class: "muted" }, t("no_meetings_hint"))));
      return;
    }
    for (const meeting of this.meetings) this.list.append(this.card(meeting));
  }

  card(meeting) {
    const chips = [];
    if (meeting.status === "recording") chips.push(h("span", { class: "chip live" }, t("status_recording")));
    else if (meeting.status === "processing") {
      const stage = meeting.progress && meeting.progress.stage;
      chips.push(h("span", { class: "chip busy" }, stage ? t(`stage_${stage}`) : t("status_processing")));
    } else if (meeting.status === "failed") chips.push(h("span", { class: "chip error" }, t("status_failed")));
    if (meeting.source === "demo") chips.push(h("span", { class: "chip" }, t("status_demo")));
    if (meeting.decision_count) chips.push(h("span", { class: "chip" }, t("n_decisions", { n: meeting.decision_count })));
    if (meeting.action_count) chips.push(h("span", { class: "chip" }, t("n_actions", { n: meeting.action_count })));
    if (meeting.slide_count) chips.push(h("span", { class: "chip" }, t("n_slides", { n: meeting.slide_count })));
    return h(
      "a",
      { class: "card", href: `#/m/${meeting.id}` },
      h(
        "div",
        { class: "card-head" },
        h("h3", null, meeting.title),
        h("span", { class: "muted" }, `${fmtDate(meeting.started_at)} · ${fmtDuration(meeting.duration)}`),
      ),
      meeting.tldr ? h("p", { class: "card-tldr" }, meeting.tldr) : null,
      chips.length ? h("div", { class: "chips" }, chips) : null,
    );
  }

  // ------------------------------------------------------------------ search

  async search() {
    const query = this.q;
    const token = ++this.searchToken;
    let data;
    try {
      data = await api.get(`/search?q=${encodeURIComponent(query)}`);
    } catch (error) {
      toast(error.message, "error");
      return;
    }
    if (token !== this.searchToken || query !== this.q || !this.results) return;
    clear(this.results);
    const ask = askBox({ initial: query });
    this.results.append(
      h("div", { class: "panel ask-panel" }, h("h2", null, icon("spark", 18), t("ask_all")), ask.el),
      h("h2", { class: "results-title" }, t("results_for", { q: query, n: data.hits.length })),
    );
    if (!data.hits.length) {
      this.results.append(h("p", { class: "muted" }, t("no_results")));
      return;
    }
    const groups = new Map();
    for (const hit of data.hits) {
      if (!groups.has(hit.meeting_id)) groups.set(hit.meeting_id, []);
      groups.get(hit.meeting_id).push(hit);
    }
    for (const hits of groups.values()) {
      const first = hits[0];
      this.results.append(
        h(
          "div",
          { class: "result-group" },
          h(
            "a",
            { class: "result-meeting", href: `#/m/${first.meeting_id}?q=${encodeURIComponent(query)}` },
            h("strong", null, first.meeting_title),
            h("span", { class: "muted" }, fmtDate(first.meeting_started_at)),
          ),
          h(
            "div",
            { class: "source-list" },
            hits.map((hit) => {
              const row = sourceRow(hit, { showMeeting: false, snippet: highlight(hit.snippet, hit.terms) });
              row.href = `#/m/${hit.meeting_id}?t=${Math.floor(hit.start)}&q=${encodeURIComponent(query)}`;
              return row;
            }),
          ),
        ),
      );
    }
  }
}
