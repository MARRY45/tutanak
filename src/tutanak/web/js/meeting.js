// One meeting: live while it is being recorded, a rewindable record afterwards.

import { api, onEvent } from "./api.js";
import { askBox } from "./ask.js";
import { fmtDate, fmtDuration, t } from "./i18n.js";
import { append, clear, fmtTs, h, highlight, icon, lastAtOrBefore, queryTerms, toast } from "./util.js";

const SPEEDS = [1, 1.25, 1.5, 2];

export class MeetingView {
  constructor(app) {
    this.app = app;
    this.tab = "summary";
    this.cursor = 0;
    this.follow = true;
    this.filter = "";
    this.selectedSlide = null;
    this.catchup = null;
    this.audio = null;
  }

  async mount(el, route) {
    this.el = el;
    this.id = route.id;
    this.filter = route.q || "";
    this.unsubscribe = onEvent((event) => this.onEvent(event));
    await this.load();
    if (route.t !== null && !Number.isNaN(route.t)) this.seek(route.t);
    this.timer = setInterval(() => this.tick(), 1000);
  }

  update(route) {
    if (route.t !== null && !Number.isNaN(route.t)) this.seek(route.t);
  }

  unmount() {
    if (this.unsubscribe) this.unsubscribe();
    clearInterval(this.timer);
    if (this.audio) this.audio.pause();
  }

  async load() {
    this.data = await api.get(`/meetings/${this.id}`);
    this.live = this.data.live;
    if (this.live && this.tab === "summary") this.tab = "notes";
    this.build();
  }

  get meeting() {
    return this.data.meeting;
  }

  duration() {
    if (this.live) return this.live.elapsed;
    const segments = this.data.segments;
    return Math.max(this.meeting.duration || 0, segments.length ? segments[segments.length - 1].end : 0);
  }

  // ------------------------------------------------------------------ layout

  build() {
    const wasPlaying = this.audio && !this.audio.paused;
    if (this.audio) this.audio.pause();
    this.audio = null;
    this.header = h("div", { class: "meeting-head" });
    this.bar = h("div", { class: "bar-host" });
    this.tabs = h("div", { class: "tabs", role: "tablist" });
    this.content = h("div", { class: "tab-content" });
    this.slidePane = h("div", { class: "slide-pane" });
    this.transcript = h("div", { class: "transcript" });
    this.followButton = h(
      "button",
      { class: "button small ghost", hidden: true, onclick: () => this.setFollow(true) },
      t("follow"),
    );
    const filter = h("input", {
      type: "search",
      class: "filter-input",
      placeholder: t("transcript_filter"),
      value: this.filter,
      oninput: (event) => {
        this.filter = event.target.value;
        this.renderTranscript();
      },
    });
    this.transcript.addEventListener("wheel", () => this.setFollow(false), { passive: true });
    clear(this.el).append(
      this.header,
      this.bar,
      h(
        "div",
        { class: "meeting-grid" },
        h("section", { class: "panel main-col" }, this.tabs, this.content),
        h(
          "aside",
          { class: "side-col" },
          this.slidePane,
          h(
            "section",
            { class: "panel transcript-panel" },
            h("div", { class: "transcript-head" }, h("h3", null, t("transcript")), this.followButton, filter),
            this.transcript,
          ),
        ),
      ),
    );
    this.renderHeader();
    this.renderBar();
    this.renderTabs();
    this.renderTab();
    this.renderTranscript();
    this.updateCursor();
    if (wasPlaying && this.audio) this.audio.play().catch(() => {});
  }

  renderHeader() {
    const meeting = this.meeting;
    const title = h("h1", { class: "meeting-title", title: t("rename"), contentEditable: "plaintext-only", spellcheck: false }, meeting.title);
    title.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        title.blur();
      }
    });
    title.addEventListener("blur", async () => {
      const next = title.textContent.trim();
      if (!next || next === meeting.title) {
        title.textContent = meeting.title;
        return;
      }
      await api.patch(`/meetings/${this.id}`, { title: next });
      meeting.title = next;
    });
    const busy = this.live || meeting.status === "processing";
    clear(this.header).append(
      h("a", { class: "back", href: "#/" }, icon("back", 16), t("back")),
      h(
        "div",
        { class: "meeting-title-row" },
        title,
        h(
          "div",
          { class: "meeting-actions" },
          h("a", { class: "button small", href: `/api/meetings/${this.id}/export.md`, download: "" }, icon("download", 15), t("export_md")),
          busy
            ? null
            : h("button", { class: "button small", onclick: () => this.regenerate(false) }, icon("refresh", 15), t("regenerate")),
          busy || !meeting.audio_file
            ? null
            : h("button", { class: "button small", onclick: () => this.regenerate(true) }, icon("mic", 15), t("retranscribe")),
          busy ? null : h("button", { class: "button small danger", onclick: () => this.remove() }, icon("trash", 15), t("delete")),
        ),
      ),
      h(
        "p",
        { class: "muted meeting-meta" },
        [fmtDate(meeting.started_at), fmtDuration(this.duration()), (meeting.language || "").toUpperCase(), meeting.source === "demo" ? t("status_demo") : null]
          .filter(Boolean)
          .join(" · "),
      ),
    );
  }

  // ------------------------------------------------------------------ bar (live / player)

  renderBar() {
    clear(this.bar);
    const meeting = this.meeting;
    if (this.live) this.bar.append(this.liveBar());
    else {
      if (meeting.status === "processing") this.bar.append(this.progressBanner());
      if (meeting.status === "failed") this.bar.append(h("div", { class: "notice error" }, t("status_failed"), ": ", meeting.error || ""));
      this.bar.append(this.player());
    }
    if (this.catchup) this.bar.append(this.catchupPanel());
  }

  progressBanner() {
    const progress = this.data.progress || {};
    this.progressLabel = h("span", null, progress.stage ? t(`stage_${progress.stage}`) : t("status_processing"));
    this.progressFill = h("span", { class: "progress-fill" });
    const banner = h("div", { class: "notice busy" }, h("span", { class: "spinner" }), this.progressLabel, h("span", { class: "progress" }, this.progressFill));
    this.setProgress(progress);
    return banner;
  }

  setProgress(progress) {
    if (!this.progressLabel) return;
    this.progressLabel.textContent = progress.stage ? t(`stage_${progress.stage}`) : t("status_processing");
    const known = typeof progress.fraction === "number";
    this.progressFill.parentElement.hidden = !known;
    if (known) this.progressFill.style.width = `${Math.round(progress.fraction * 100)}%`;
  }

  catchupButtons(label) {
    return h(
      "div",
      { class: "catchup-buttons" },
      h("span", { class: "catchup-label" }, icon("rewind", 15), label),
      [2, 5, 10].map((n) => h("button", { class: "button small", onclick: () => this.runCatchup(n) }, t("catchup_last", { n }))),
    );
  }

  liveBar() {
    this.clock = h("span", { class: "clock" }, fmtTs(this.live.elapsed));
    this.meterMic = h("span", { class: "meter-fill" });
    this.meterSystem = h("span", { class: "meter-fill" });
    this.liveStatus = h("span", { class: "muted live-status" });
    this.stopButton = h("button", { class: "button danger", onclick: () => this.stop() }, icon("stop", 15), t("stop"));
    this.setLiveStatus();
    return h(
      "div",
      { class: "live-bar" },
      h("div", { class: "live-main" }, h("span", { class: "rec-dot" }), h("strong", { class: "live-tag" }, t("live")), this.clock),
      h(
        "div",
        { class: "meters", title: `${t("src_mic")} / ${t("src_system")}` },
        h("span", { class: "meter" }, icon("mic", 14), h("span", { class: "meter-track" }, this.meterMic)),
        h("span", { class: "meter" }, icon("speaker", 14), h("span", { class: "meter-track" }, this.meterSystem)),
      ),
      this.liveStatus,
      h("span", { class: "spacer" }),
      this.catchupButtons(t("catchup")),
      h("button", { class: "button", title: t("mark_hint"), onclick: () => this.bookmark() }, icon("bookmark", 15), t("mark")),
      this.stopButton,
    );
  }

  setLiveStatus() {
    if (!this.liveStatus || !this.live) return;
    const live = this.live;
    const parts = [];
    if (live.model_state === "loading" || live.model_state === "idle") parts.push(t("model_loading"));
    if (live.model_state === "failed") parts.push(t("model_failed"));
    if (live.backlog_s > 45) parts.push(t("behind", { n: Math.round(live.backlog_s) }));
    if (live.notes_available === false) parts.push(t("notes_off"));
    for (const warning of live.warnings || []) parts.push(warning);
    this.liveStatus.textContent = parts.join(" · ");
  }

  player() {
    const meeting = this.meeting;
    const duration = this.duration();
    this.playButton = h("button", { class: "button round", "aria-label": "play", onclick: () => this.togglePlay() }, icon("play", 18));
    this.timeLabel = h("span", { class: "clock" });
    this.fill = h("span", { class: "timeline-fill" });
    this.thumb = h("span", { class: "timeline-thumb" });
    const marks = [];
    const at = (seconds) => `${Math.min(100, (seconds / Math.max(duration, 1)) * 100)}%`;
    this.data.notes.forEach((note, index) => {
      const band = h("span", { class: `band ${index % 2 ? "odd" : ""}`, title: `${fmtTs(note.start)} · ${note.topic}` });
      band.style.left = at(note.start);
      band.style.width = `${Math.max(0.3, ((note.end - note.start) / Math.max(duration, 1)) * 100)}%`;
      marks.push(band);
    });
    for (const slide of this.data.slides) {
      const tick = h("span", { class: "tick slide-tick", title: `${t("kind_slide")} · ${fmtTs(slide.t)}` });
      tick.style.left = at(slide.t);
      marks.push(tick);
    }
    for (const mark of this.data.bookmarks) {
      const pin = h("span", { class: "tick bookmark-tick", title: `${fmtTs(mark.t)} ${mark.label}` });
      pin.style.left = at(mark.t);
      marks.push(pin);
    }
    const timeline = h("div", { class: "timeline", role: "slider", tabIndex: 0, "aria-label": "timeline" }, marks, this.fill, this.thumb);
    const seekFromPointer = (event) => {
      const box = timeline.getBoundingClientRect();
      const fraction = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
      this.seek(fraction * this.duration());
    };
    timeline.addEventListener("pointerdown", (event) => {
      timeline.setPointerCapture(event.pointerId);
      seekFromPointer(event);
      timeline.onpointermove = seekFromPointer;
    });
    timeline.addEventListener("pointerup", () => (timeline.onpointermove = null));
    timeline.addEventListener("keydown", (event) => {
      if (event.key === "ArrowRight") this.seek(this.cursor + 5);
      if (event.key === "ArrowLeft") this.seek(this.cursor - 5);
      if (event.key === " ") {
        event.preventDefault();
        this.togglePlay();
      }
    });

    const controls = [this.playButton, this.timeLabel, timeline];
    if (meeting.audio_file) {
      this.audio = new Audio(`/api/meetings/${this.id}/audio`);
      this.audio.preload = "metadata";
      this.audio.currentTime = this.cursor;
      this.audio.addEventListener("timeupdate", () => {
        this.cursor = this.audio.currentTime;
        this.updateCursor(true);
      });
      const setIcon = () => this.playButton.replaceChildren(icon(this.audio.paused ? "play" : "pause", 18));
      this.audio.addEventListener("play", setIcon);
      this.audio.addEventListener("pause", setIcon);
      const speed = h(
        "select",
        { class: "speed", title: t("speed"), onchange: (event) => (this.audio.playbackRate = Number(event.target.value)) },
        SPEEDS.map((s) => h("option", { value: s }, `${s}×`)),
      );
      controls.push(speed);
    } else {
      this.playButton.disabled = true;
      this.playButton.title = t("no_audio");
    }
    controls.push(
      h("button", { class: "button small", title: t("mark_hint"), onclick: () => this.bookmark() }, icon("bookmark", 14), t("mark")),
    );
    return h(
      "div",
      { class: "player" },
      h("div", { class: "player-row" }, controls),
      this.data.segments.length ? this.catchupButtons(t("catchup_before")) : null,
    );
  }

  togglePlay() {
    if (!this.audio) return;
    if (this.audio.paused) this.audio.play().catch((error) => toast(error.message, "error"));
    else this.audio.pause();
  }

  seek(seconds, { play = false } = {}) {
    this.cursor = Math.min(Math.max(0, seconds), Math.max(this.duration(), seconds));
    if (this.audio) {
      this.audio.currentTime = this.cursor;
      if (play) this.audio.play().catch(() => {});
    }
    this.follow = true;
    if (this.followButton) this.followButton.hidden = true;
    this.updateCursor(false);
    this.scrollToActive("instant"); // a jump, not playback: do not animate across the meeting
  }

  setFollow(on) {
    this.follow = on;
    if (this.followButton) this.followButton.hidden = on;
    if (on) this.scrollToActive("instant");
  }

  // Everything that depends on "where we are" in the meeting.
  updateCursor(fromPlayback = false) {
    const duration = this.duration();
    if (this.fill) {
      const percent = `${Math.min(100, (this.cursor / Math.max(duration, 1)) * 100)}%`;
      this.fill.style.width = percent;
      this.thumb.style.left = percent;
      this.timeLabel.textContent = `${fmtTs(this.cursor)} / ${fmtTs(duration)}`;
    }
    if (!this.live) {
      const index = this.activeIndex();
      if (index !== this.activeSegment) {
        this.activeSegment = index;
        for (const row of this.transcript.querySelectorAll(".seg.active")) row.classList.remove("active");
        const row = index >= 0 ? this.transcript.querySelector(`[data-index="${index}"]`) : null;
        if (row) {
          row.classList.add("active");
          if (this.follow && !this.filter) this.scrollToActive();
        }
      }
    }
    this.renderSlidePane(fromPlayback);
  }

  activeIndex() {
    return lastAtOrBefore(this.data.segments, this.cursor + 0.05, "start");
  }

  scrollToActive(behavior = "smooth") {
    const row = this.transcript && this.transcript.querySelector(".seg.active");
    if (!row) return;
    const box = this.transcript;
    // .transcript is the offset parent (position: relative), so offsetTop is already
    // measured from its top edge.
    box.scrollTo({ top: row.offsetTop - box.clientHeight / 3, behavior });
  }

  // ------------------------------------------------------------------ transcript

  timeChip(seconds) {
    return h(
      "button",
      { class: "ts", onclick: (event) => { event.stopPropagation(); this.seek(seconds, { play: true }); } },
      fmtTs(seconds),
    );
  }

  segmentRow(segment, index, terms) {
    const speaker = segment.speaker ? h("span", { class: `who ${segment.speaker}` }, t(segment.speaker)) : null;
    return h(
      "div",
      { class: "seg", dataset: { index }, onclick: () => this.seek(segment.start, { play: true }) },
      h("span", { class: "seg-time" }, fmtTs(segment.start)),
      h("p", null, speaker, speaker ? " " : null, highlight(segment.text, terms)),
    );
  }

  renderTranscript() {
    const terms = queryTerms(this.filter);
    clear(this.transcript);
    this.activeSegment = undefined;
    const segments = this.data.segments;
    let shown = 0;
    segments.forEach((segment, index) => {
      if (terms.length) {
        const folded = queryTerms(segment.text);
        if (!terms.every((term) => folded.some((word) => word.startsWith(term)))) return;
      }
      this.transcript.append(this.segmentRow(segment, index, terms));
      shown += 1;
    });
    if (!shown) this.transcript.append(h("p", { class: "muted pad" }, segments.length ? t("no_results") : t("transcript_empty")));
    this.updateCursor();
    if (this.live && this.follow) this.transcript.scrollTop = this.transcript.scrollHeight;
  }

  appendSegment(segment) {
    const segments = this.data.segments;
    segments.push(segment);
    if (segments.length === 1) clear(this.transcript);
    const terms = queryTerms(this.filter);
    if (terms.length) {
      const folded = queryTerms(segment.text);
      if (!terms.every((term) => folded.some((word) => word.startsWith(term)))) return;
    }
    const row = this.segmentRow(segment, segments.length - 1, terms);
    row.classList.add("fresh");
    this.transcript.append(row);
    if (this.follow) this.transcript.scrollTo({ top: this.transcript.scrollHeight, behavior: "smooth" });
  }

  // ------------------------------------------------------------------ slides (side pane)

  slideUrl(slide) {
    return `/api/meetings/${this.id}/slides/${slide.image}`;
  }

  renderSlidePane() {
    const slides = this.data.slides;
    const at = this.live ? Infinity : this.cursor + 0.05;
    const index = slides.length ? Math.max(0, lastAtOrBefore(slides, at, "t")) : -1;
    if (index === this.shownSlide && this.slidePane.childElementCount) return;
    this.shownSlide = index;
    clear(this.slidePane);
    if (index < 0) return;
    const slide = slides[index];
    this.slidePane.append(
      h(
        "section",
        { class: "panel slide-now" },
        h("div", { class: "slide-now-head" }, h("h3", null, t("on_screen")), h("span", { class: "muted" }, `${index + 1}/${slides.length} · ${fmtTs(slide.t)}`)),
        h(
          "button",
          {
            class: "slide-button",
            onclick: () => {
              this.selectedSlide = index;
              this.setTab("slides");
            },
          },
          h("img", { src: this.slideUrl(slide), alt: (slide.text || "").slice(0, 200) }),
        ),
      ),
    );
  }

  // ------------------------------------------------------------------ tabs

  renderTabs() {
    const counts = { notes: this.data.notes.length, slides: this.data.slides.length };
    append(
      clear(this.tabs),
      ["summary", "notes", "slides", "ask"].map((name) =>
        h(
          "button",
          { class: `tab ${this.tab === name ? "active" : ""}`, role: "tab", onclick: () => this.setTab(name) },
          t(`tab_${name}`),
          counts[name] ? h("span", { class: "count" }, counts[name]) : null,
        ),
      ),
    );
  }

  setTab(name) {
    this.tab = name;
    this.renderTabs();
    this.renderTab();
  }

  renderTab() {
    clear(this.content);
    const render = { summary: this.summaryTab, notes: this.notesTab, slides: this.slidesTab, ask: this.askTab }[this.tab];
    render.call(this);
  }

  itemList(items, textKey = "text") {
    return h(
      "ul",
      { class: "items" },
      items.map((item) => {
        const meta = [item.owner ? `${t("owner")}: ${item.owner}` : null, item.due ? `${t("due")}: ${item.due}` : null].filter(Boolean);
        return h(
          "li",
          null,
          item.t != null ? this.timeChip(item.t) : null,
          h("span", null, item[textKey], meta.length ? h("small", { class: "muted item-meta" }, meta.join(" · ")) : null),
        );
      }),
    );
  }

  section(title, iconName, body) {
    return h("div", { class: "section" }, h("h3", { class: "label" }, iconName ? icon(iconName, 15) : null, title), body);
  }

  notesFailed() {
    return h(
      "div",
      { class: "notice" },
      h("strong", null, t("notes_failed")),
      this.meeting.error ? `: ${this.meeting.error}` : "",
      h("p", null, t("notes_failed_hint")),
      h("button", { class: "button small primary", onclick: () => this.regenerate(false) }, icon("refresh", 15), t("regenerate")),
    );
  }

  summaryTab() {
    const meeting = this.meeting;
    const summary = meeting.summary;
    const bookmarks = this.data.bookmarks;
    if (!summary) {
      if (meeting.status === "ready" && this.data.segments.length) this.content.append(this.notesFailed());
      else this.content.append(h("p", { class: "muted" }, this.live || meeting.status === "processing" ? t("notes_pending") : t("no_notes")));
    } else {
      this.content.append(this.section(t("tldr"), null, h("p", { class: "tldr" }, summary.tldr)));
      if (summary.key_points.length) {
        this.content.append(this.section(t("key_points"), null, h("ul", { class: "points" }, summary.key_points.map((p) => h("li", null, p)))));
      }
      if (summary.decisions.length) this.content.append(this.section(t("decisions"), "check", this.itemList(summary.decisions)));
      if (summary.actions.length) this.content.append(this.section(t("actions"), "clock", this.itemList(summary.actions, "task")));
      if (summary.questions.length) this.content.append(this.section(t("questions"), null, this.itemList(summary.questions)));
    }
    if (bookmarks.length) {
      this.content.append(
        this.section(
          t("bookmarks"),
          "bookmark",
          h(
            "ul",
            { class: "items" },
            bookmarks.map((mark) => {
              const near = this.data.segments[lastAtOrBefore(this.data.segments, mark.t + 0.5, "start")];
              return h(
                "li",
                null,
                this.timeChip(mark.t),
                h("span", null, mark.label ? h("strong", null, mark.label, near ? " · " : "") : null, near ? h("span", { class: "muted" }, near.text) : null),
                h(
                  "button",
                  { class: "icon-button small", "aria-label": t("delete"), onclick: () => this.removeBookmark(mark) },
                  icon("close", 14),
                ),
              );
            }),
          ),
        ),
      );
    }
  }

  notesTab() {
    const notes = this.data.notes;
    if (!notes.length) {
      if (!this.live && this.meeting.status === "ready" && this.data.segments.length) this.content.append(this.notesFailed());
      else this.content.append(h("p", { class: "muted" }, this.live || this.meeting.status === "processing" ? t("notes_pending") : t("no_notes")));
      return;
    }
    for (const note of notes) {
      const tagged = (items, label, textKey = "text") =>
        items.map((item) =>
          h(
            "li",
            { class: "tagged" },
            item.t != null ? this.timeChip(item.t) : null,
            h("span", null, h("span", { class: "tag" }, label), " ", item[textKey], item.owner ? h("small", { class: "muted item-meta" }, `${t("owner")}: ${item.owner}${item.due ? ` · ${t("due")}: ${item.due}` : ""}`) : null),
          ),
        );
      this.content.append(
        h(
          "article",
          { class: "note-block" },
          h("header", null, this.timeChip(note.start), h("h3", null, note.topic), h("span", { class: "muted" }, `${fmtTs(note.start)}–${fmtTs(note.end)}`)),
          h(
            "ul",
            { class: "items" },
            note.points.map((point) => h("li", null, point.t != null ? this.timeChip(point.t) : null, h("span", null, point.text))),
            tagged(note.decisions, t("decisions")),
            tagged(note.actions, t("actions"), "task"),
            tagged(note.questions, t("questions")),
          ),
        ),
      );
    }
  }

  slidesTab() {
    const slides = this.data.slides;
    if (!slides.length) {
      this.content.append(h("p", { class: "muted" }, t("no_slides")));
      return;
    }
    if (this.selectedSlide !== null && slides[this.selectedSlide]) {
      const index = this.selectedSlide;
      const slide = slides[index];
      const until = index + 1 < slides.length ? slides[index + 1].t : Infinity;
      const said = this.data.segments.filter((s) => s.start >= slide.t - 0.5 && s.start < until);
      this.content.append(
        h(
          "div",
          { class: "slide-detail" },
          h(
            "div",
            { class: "slide-detail-head" },
            this.timeChip(slide.t),
            h("strong", null, `${t("kind_slide")} ${index + 1}/${slides.length}`),
            h("span", { class: "spacer" }),
            h("button", { class: "icon-button small", "aria-label": "close", onclick: () => { this.selectedSlide = null; this.renderTab(); } }, icon("close", 16)),
          ),
          h("img", { src: this.slideUrl(slide), alt: "" }),
          slide.text ? this.section(t("slide_text"), null, h("pre", { class: "ocr" }, slide.text)) : null,
          said.length
            ? this.section(
                t("said_here"),
                null,
                h("div", { class: "said" }, said.map((s) => h("p", null, this.timeChip(s.start), " ", s.text))),
              )
            : null,
        ),
      );
    }
    this.content.append(
      h(
        "div",
        { class: "slide-grid" },
        slides.map((slide, index) =>
          h(
            "button",
            {
              class: `slide-card ${this.selectedSlide === index ? "active" : ""}`,
              onclick: () => {
                this.selectedSlide = index;
                this.seek(slide.t);
                this.renderTab();
                this.content.scrollIntoView({ block: "nearest" });
              },
            },
            h("img", { src: this.slideUrl(slide), alt: "", loading: "lazy" }),
            h("span", { class: "slide-caption" }, h("span", { class: "ts-static" }, fmtTs(slide.t)), (slide.text || "").split("\n")[0]),
          ),
        ),
      ),
    );
  }

  askTab() {
    if (!this.askWidget) {
      this.askWidget = askBox({
        meetingId: this.id,
        showMeeting: false,
        onOpen: (passage) => this.seek(passage.start, { play: true }),
      });
    }
    this.content.append(this.askWidget.el);
    this.askWidget.input.focus();
  }

  // ------------------------------------------------------------------ actions

  async runCatchup(minutes) {
    this.catchup = { minutes, loading: true };
    this.renderBar();
    try {
      const body = { minutes };
      if (!this.live) body.until = Math.max(this.cursor, 1);
      this.catchup = { minutes, result: await api.post(`/meetings/${this.id}/catchup`, body) };
    } catch (error) {
      this.catchup = null;
      toast(error.message, "error");
    }
    this.renderBar();
    this.updateCursor();
  }

  catchupPanel() {
    const { minutes, loading, result } = this.catchup;
    const close = h("button", { class: "icon-button small", "aria-label": "close", onclick: () => { this.catchup = null; this.renderBar(); this.updateCursor(); } }, icon("close", 16));
    const panel = h("div", { class: "panel catchup" }, h("div", { class: "catchup-head" }, h("h3", null, icon("rewind", 16), t("catchup_title", { n: minutes })), close));
    if (loading) {
      panel.append(h("p", { class: "muted pulse" }, t("asking")));
      return panel;
    }
    panel.firstChild.firstChild.append(h("span", { class: "muted range" }, `${fmtTs(result.start)}–${fmtTs(result.end)}`));
    if (!result.segments.length) panel.append(h("p", { class: "muted" }, t("catchup_empty")));
    else if (result.headline) {
      panel.append(h("p", { class: "tldr" }, result.headline), this.itemList(result.points));
    } else {
      panel.append(
        h("p", { class: "notice" }, t("catchup_raw")),
        h("div", { class: "said" }, result.segments.map((s) => h("p", null, this.timeChip(s.start), " ", s.text))),
      );
    }
    return panel;
  }

  async bookmark() {
    const body = this.live ? {} : { t: Math.round(this.cursor * 10) / 10 };
    const mark = await api.post(`/meetings/${this.id}/bookmarks`, body);
    if (!this.data.bookmarks.some((b) => b.id === mark.id)) this.data.bookmarks.push(mark);
    this.data.bookmarks.sort((a, b) => a.t - b.t);
    toast(t("marked", { t: fmtTs(mark.t) }));
    if (!this.live) this.renderBar();
    if (this.tab === "summary") this.renderTab();
    this.updateCursor();
  }

  async removeBookmark(mark) {
    await api.del(`/meetings/${this.id}/bookmarks/${mark.id}`);
    this.data.bookmarks = this.data.bookmarks.filter((b) => b.id !== mark.id);
    this.renderBar();
    this.renderTab();
    this.updateCursor();
  }

  async stop() {
    this.stopButton.disabled = true;
    this.stopButton.lastChild.textContent = t("stopping");
    try {
      await api.post("/recordings/stop");
    } catch (error) {
      toast(error.message, "error");
    }
    await this.load();
  }

  async regenerate(transcript) {
    await api.post(`/meetings/${this.id}/regenerate`, { transcript });
    await this.load();
  }

  async remove() {
    if (!window.confirm(t("delete_confirm"))) return;
    await api.del(`/meetings/${this.id}`);
    location.hash = "#/";
  }

  // ------------------------------------------------------------------ live updates

  tick() {
    if (!this.live) return;
    this.live.elapsed += 1;
    if (this.clock) this.clock.textContent = fmtTs(this.live.elapsed);
  }

  onEvent(event) {
    if (event.id !== this.id || !this.data) return;
    const data = this.data;
    switch (event.type) {
      case "segment":
        if (!data.segments.some((s) => s.id === event.segment.id)) this.appendSegment(event.segment);
        break;
      case "note":
        if (!data.notes.some((n) => n.id === event.note.id)) {
          data.notes.push(event.note);
          this.renderTabs();
          if (this.tab === "notes") this.renderTab();
        }
        break;
      case "slide":
        if (!data.slides.some((s) => s.id === event.slide.id)) {
          data.slides.push(event.slide);
          this.renderTabs();
          if (this.tab === "slides") this.renderTab();
          this.renderSlidePane();
        }
        break;
      case "slide_text": {
        const slide = data.slides.find((s) => s.id === event.slide_id);
        if (slide) slide.text = event.text;
        break;
      }
      case "bookmark":
        if (!data.bookmarks.some((b) => b.id === event.bookmark.id)) data.bookmarks.push(event.bookmark);
        break;
      case "level":
        if (this.live && this.meterMic) {
          this.live.elapsed = event.elapsed;
          this.live.backlog_s = event.backlog_s;
          const width = (level) => `${Math.min(100, Math.round(Math.sqrt(level) * 100))}%`;
          this.meterMic.style.width = width(event.mic);
          this.meterSystem.style.width = width(event.system);
          this.setLiveStatus();
        }
        break;
      case "model":
        if (this.live) {
          this.live.model_state = event.state;
          this.setLiveStatus();
        }
        break;
      case "warning":
        if (this.live) {
          this.live.warnings = [...(this.live.warnings || []), event.message];
          this.setLiveStatus();
        }
        break;
      case "progress":
        data.progress = event.stage ? { stage: event.stage, fraction: event.fraction } : null;
        this.setProgress(data.progress || {});
        break;
      case "recording":
      case "meeting":
        this.load();
        break;
    }
  }
}
