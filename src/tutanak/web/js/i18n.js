// Interface strings. Turkish is the default; English is switchable in settings.

const STRINGS = {
  tagline: ["Toplantı hafızan. Hepsi bu bilgisayarda.", "Your meeting memory. All on this computer."],
  local_badge: ["Yerel", "Local"],
  local_hint: [
    "Ses, slayt ve notlar bu bilgisayardan çıkmaz. Tutanak yalnızca yerel model sunucusuyla konuşur.",
    "Audio, slides and notes never leave this computer. Tutanak only talks to the local model server.",
  ],
  search_placeholder: ["Tüm toplantılarda ara: bütçe, karar, bir isim…", "Search every meeting: budget, a decision, a name…"],
  settings: ["Ayarlar", "Settings"],
  back: ["Toplantılar", "Meetings"],

  new_meeting: ["Yeni toplantı", "New meeting"],
  title_placeholder: ["Başlık (boş bırakırsan notlardan çıkarılır)", "Title (left empty, it is taken from the notes)"],
  src_mic: ["Mikrofon", "Microphone"],
  src_mic_hint: ["Senin söylediklerin", "What you say"],
  src_system: ["Sistem sesi", "System audio"],
  src_system_hint: ["Karşı tarafın söyledikleri", "What the others say"],
  src_screen: ["Ekran (slaytlar)", "Screen (slides)"],
  src_screen_hint: ["Yeni slaytları yakalar ve okur", "Captures and reads new slides"],
  start: ["Kaydı başlat", "Start recording"],
  starting: ["Başlatılıyor…", "Starting…"],
  import: ["Kayıt içe aktar", "Import a recording"],
  import_hint: ["Ses ya da video dosyası; buraya da sürükleyebilirsin", "Audio or video file; you can also drop it here"],
  uploading: ["Yükleniyor…", "Uploading…"],
  devices: ["Cihazlar", "Devices"],
  device_default: ["Varsayılan", "Default"],
  monitor: ["Ekran", "Monitor"],
  recording_now: ["Kayıt sürüyor", "Recording"],
  open_live: ["Canlı görünümü aç", "Open live view"],
  capture_missing: ["Ses yakalama bu makinede kullanılamıyor.", "Audio capture is not available on this machine."],
  asr_download: [
    "İlk kayıtta konuşma tanıma modeli ({model}, {size}) bir kez indirilir; ses o sırada da kaydedilir, yazıya dökme indirme bitince başlar.",
    "The first recording downloads the speech model ({model}, {size}) once; audio is recorded meanwhile and transcription starts when the download is done.",
  ],
  asr_missing: [
    "Konuşma tanıma kurulu değil: yalnızca ses kaydedilir. Kurmak için: pip install \"tutanak[asr]\"",
    "Speech recognition is not installed: only audio is recorded. Install it with: pip install \"tutanak[asr]\"",
  ],

  meetings: ["Toplantılar", "Meetings"],
  no_meetings: ["Henüz toplantı yok.", "No meetings yet."],
  no_meetings_hint: [
    "Bir kayıt başlat ya da mevcut bir ses/video dosyasını içe aktar.",
    "Start a recording or import an existing audio/video file.",
  ],
  today: ["Bugün", "Today"],
  yesterday: ["Dün", "Yesterday"],
  n_decisions: ["{n} karar", "{n} decisions"],
  n_actions: ["{n} yapılacak", "{n} action items"],
  n_slides: ["{n} slayt", "{n} slides"],
  status_recording: ["Kaydediliyor", "Recording"],
  status_processing: ["İşleniyor", "Processing"],
  status_failed: ["Başarısız", "Failed"],
  status_demo: ["Örnek", "Sample"],

  results_for: ["“{q}” için {n} sonuç", "{n} results for “{q}”"],
  no_results: ["Sonuç yok.", "No results."],
  ask_all: ["Toplantılarına sor", "Ask your meetings"],
  ask_placeholder: ["Bir soru sor: “Kart taşıma ne zaman yapılacak?”", "Ask a question: “When is the card migration?”"],
  ask_button: ["Sor", "Ask"],
  asking: ["Düşünüyor…", "Thinking…"],
  ask_no_model: [
    "Yerel model bağlı değil; cevap yazılamadı. En ilgili anlar aşağıda.",
    "No local model is connected, so no answer was written. The most relevant moments are below.",
  ],
  ask_nothing: ["Kayıtlarda bununla ilgili bir şey bulunamadı.", "Nothing about this was found in the recordings."],
  sources: ["Kaynaklar", "Sources"],
  kind_transcript: ["Konuşma", "Speech"],
  kind_slide: ["Slayt", "Slide"],
  kind_note: ["Not", "Note"],

  tab_summary: ["Özet", "Summary"],
  tab_notes: ["Notlar", "Notes"],
  tab_slides: ["Slaytlar", "Slides"],
  tab_ask: ["Sor", "Ask"],
  tldr: ["Kısaca", "In short"],
  key_points: ["Öne çıkanlar", "Key points"],
  decisions: ["Kararlar", "Decisions"],
  actions: ["Yapılacaklar", "Action items"],
  questions: ["Açık sorular", "Open questions"],
  bookmarks: ["İşaretler", "Bookmarks"],
  owner: ["Sorumlu", "Owner"],
  due: ["Tarih", "Due"],
  none_recorded: ["Yok", "None"],
  transcript: ["Transkript", "Transcript"],
  transcript_filter: ["Bu toplantıda ara", "Search this meeting"],
  transcript_empty: ["Henüz konuşma yok.", "No speech yet."],
  follow: ["Takip et", "Follow"],
  me: ["Ben", "Me"],
  others: ["Diğerleri", "Others"],
  on_screen: ["Ekrandaki slayt", "Slide on screen"],
  no_slides: ["Bu toplantıda slayt yakalanmadı.", "No slides were captured in this meeting."],
  said_here: ["Bu slayttayken söylenenler", "Said while this slide was up"],
  slide_text: ["Slayttaki metin", "Text on the slide"],
  no_notes: ["Bu toplantı için not yok.", "There are no notes for this meeting."],
  notes_pending: ["Notlar toplantı ilerledikçe burada birikir.", "Notes pile up here as the meeting goes on."],
  notes_failed: ["Notlar yazılamadı", "Notes were not written"],
  notes_failed_hint: [
    "Transkript ve slaytlar hazır. Yerel modeli çalıştırıp notları yeniden üretebilirsin.",
    "The transcript and slides are ready. Start the local model and regenerate the notes.",
  ],
  regenerate: ["Notları yeniden üret", "Regenerate notes"],
  retranscribe: ["Yeniden yazıya dök", "Transcribe again"],
  export_md: ["Markdown indir", "Download Markdown"],
  delete: ["Sil", "Delete"],
  delete_confirm: [
    "Bu toplantının sesi, slaytları ve notları kalıcı olarak silinecek. Emin misin?",
    "The audio, slides and notes of this meeting will be deleted permanently. Are you sure?",
  ],
  rename: ["Başlığı düzenle", "Edit title"],

  live: ["CANLI", "LIVE"],
  stop: ["Durdur", "Stop"],
  stopping: ["Durduruluyor…", "Stopping…"],
  mark: ["İşaretle", "Bookmark"],
  mark_hint: ["Bu anı sonra bakmak üzere işaretle", "Flag this moment to revisit later"],
  marked: ["{t} işaretlendi", "Marked {t}"],
  catchup: ["Ne kaçırdım?", "What did I miss?"],
  catchup_last: ["Son {n} dk", "Last {n} min"],
  catchup_title: ["Son {n} dakika", "The last {n} minutes"],
  catchup_before: ["Bu andan öncesini özetle", "Summarise what led up to here"],
  catchup_empty: ["Bu aralıkta konuşma yok.", "No speech in this window."],
  catchup_raw: [
    "Yerel model bağlı değil; bu aralığın transkripti:",
    "No local model is connected; here is the transcript of this window:",
  ],
  model_loading: [
    "Konuşma tanıma modeli yükleniyor (ilk kullanımda indirilir)…",
    "Loading the speech model (downloaded on first use)…",
  ],
  model_failed: ["Konuşma tanıma çalışmıyor; ses yine de kaydediliyor.", "Speech recognition is not working; audio is still being recorded."],
  behind: ["Yazıya dökme {n} sn geride", "Transcription is {n} s behind"],
  notes_off: ["Yerel model yok: canlı notlar kapalı", "No local model: live notes are off"],
  stage_queued: ["Sırada", "Queued"],
  stage_audio: ["Ses hazırlanıyor", "Preparing audio"],
  stage_transcribing: ["Yazıya dökülüyor", "Transcribing"],
  stage_slides: ["Slaytlar okunuyor", "Reading slides"],
  stage_notes: ["Notlar yazılıyor", "Writing notes"],
  stage_summary: ["Özet yazılıyor", "Writing summary"],
  stage_index: ["Arama dizini kuruluyor", "Building search index"],
  speed: ["Hız", "Speed"],
  no_audio: ["Bu toplantının ses kaydı yok; zaman çizelgesiyle gezebilirsin.", "This meeting has no audio; you can still browse the timeline."],

  s_general: ["Genel", "General"],
  s_ui_language: ["Arayüz dili", "Interface language"],
  s_speech: ["Konuşma tanıma", "Speech recognition"],
  s_language: ["Konuşulan dil", "Spoken language"],
  s_auto: ["Otomatik", "Automatic"],
  s_whisper_model: ["Whisper modeli", "Whisper model"],
  s_whisper_device: ["İşlemci", "Processor"],
  s_llm: ["Yerel dil modeli", "Local language model"],
  s_llm_url: ["Sunucu adresi (OpenAI uyumlu)", "Server URL (OpenAI-compatible)"],
  s_llm_model: ["Not modeli", "Notes model"],
  s_embed_model: ["Gömme modeli (anlamsal arama; boş = kapalı)", "Embedding model (semantic search; empty = off)"],
  s_allow_remote: ["Bu bilgisayarın dışındaki bir sunucuya izin ver", "Allow a server that is not on this computer"],
  s_allow_remote_hint: [
    "Açarsan transkript o sunucuya gönderilir. Varsayılan: kapalı.",
    "When enabled, transcripts are sent to that server. Off by default.",
  ],
  s_notes: ["Notlar", "Notes"],
  s_notes_language: ["Not dili", "Notes language"],
  s_same_as_speech: ["Konuşulan dille aynı", "Same as the spoken language"],
  s_note_interval: ["Not aralığı (saniye)", "Note interval (seconds)"],
  s_screen: ["Ekran ve slaytlar", "Screen and slides"],
  s_screen_interval: ["Ekran yakalama aralığı (saniye)", "Screen capture interval (seconds)"],
  s_ocr: ["Slayt okuma (OCR)", "Slide reading (OCR)"],
  s_off: ["Kapalı", "Off"],
  save: ["Kaydet", "Save"],
  saved: ["Kaydedildi", "Saved"],
  status: ["Durum", "Status"],
  st_audio: ["Ses yakalama", "Audio capture"],
  st_screen: ["Ekran yakalama", "Screen capture"],
  st_asr: ["Konuşma tanıma", "Speech recognition"],
  st_ocr: ["Slayt okuma", "Slide reading"],
  st_ffmpeg: ["ffmpeg (içe aktarma)", "ffmpeg (import)"],
  st_llm: ["Yerel model sunucusu", "Local model server"],
  st_llm_model: ["Not modeli", "Notes model"],
  st_embed: ["Gömme modeli", "Embedding model"],
  st_missing: ["yok", "missing"],
  st_unreachable: ["ulaşılamıyor", "not reachable"],
  st_not_installed: ["kurulu değil", "not installed"],
  st_loaded_on_demand: ["ilk kullanımda yüklenir", "loads on first use"],
  data_dir: ["Veri klasörü", "Data folder"],
  data_dir_hint: [
    "Her şey burada. Bu klasörü silmek tüm toplantıları siler.",
    "Everything is here. Deleting this folder deletes every meeting.",
  ],
};

let lang = "tr";

export function setLanguage(next) {
  lang = next === "en" ? "en" : "tr";
  document.documentElement.lang = lang;
}

export function language() {
  return lang;
}

export function t(key, vars) {
  const entry = STRINGS[key];
  let text = entry ? entry[lang === "en" ? 1 : 0] : key;
  for (const [name, value] of Object.entries(vars || {})) text = text.replaceAll(`{${name}}`, value);
  return text;
}

export function fmtDate(iso) {
  const date = new Date(iso);
  const locale = lang === "en" ? "en-GB" : "tr-TR";
  const day = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = Math.round((day(new Date()) - day(date)) / 86400000);
  const time = date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  if (diff === 0) return `${t("today")} ${time}`;
  if (diff === 1) return `${t("yesterday")} ${time}`;
  const sameYear = date.getFullYear() === new Date().getFullYear();
  const options = { day: "numeric", month: "long", ...(sameYear ? {} : { year: "numeric" }) };
  return `${date.toLocaleDateString(locale, options)} ${time}`;
}

export function fmtDuration(seconds) {
  const minutes = Math.round((seconds || 0) / 60);
  if (minutes < 1) return lang === "en" ? "<1 min" : "<1 dk";
  if (minutes < 60) return lang === "en" ? `${minutes} min` : `${minutes} dk`;
  const hrs = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return lang === "en" ? `${hrs} h ${rest} min` : `${hrs} sa ${rest} dk`;
}
