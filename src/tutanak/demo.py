"""Offline demo: two sample meetings, no microphone, no model, no network.

The transcripts and slide texts below are fixed. The notes are *not* pasted in: a
scripted model replies the way a real one would, and the real pipeline validates the
reply, resolves each cited line to its timestamp, collects decisions and action
items, and builds the search index. So the demo exercises the same code as a real
meeting; only speech recognition and the language model are replaced.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel

from .llm import Messages, ScriptedLLM
from .notes import make_block, summarize
from .pipeline import Services
from .search import index_meeting

LANGUAGE = "Turkish"

# (start, end, speaker, text)
_ROADMAP_TRANSCRIPT = [
    (4, 16, "others", "Herkese merhaba, katıldığınız için teşekkürler. Bugün üçüncü çeyrek yol haritasını konuşacağız."),
    (17, 33, "others", "Gündemde dört başlık var: ikinci çeyrek sonuçları, ödeme altyapısı geçişi, mobil uygulama ve son olarak bütçe ile ekip planı."),
    (34, 49, "others", "Toplantının sonunda iki konuda karar almamız gerekiyor: geçiş takvimi ve mobil için dış ajansla çalışıp çalışmayacağımız."),
    (50, 62, "me", "Başlamadan sorayım, geçen hafta konuştuğumuz güvenlik denetimi bu gündeme dahil mi?"),
    (63, 80, "others", "Güzel soru. Denetim raporu henüz gelmedi, o yüzden bugün gündemde yok. Rapor gelince ayrı bir toplantı ayarlayacağım."),
    (82, 100, "others", "Kısaca hatırlatayım, ikinci çeyrekte ana hedefimiz ödeme başarı oranını yüzde doksan beşe çıkarmaktı."),
    (101, 120, "others", "Bu hedefi tutturamadık ama nedenini artık biliyoruz, bir sonraki slaytta ayrıntısına gireceğim."),
    (122, 146, "others", "Sorularınızı araya girerek sorabilirsiniz, sunum yaklaşık on dakika sürecek."),
    (152, 170, "others", "İkinci çeyrek sonuçlarına bakalım. Aktif kullanıcı sayımız kırk sekiz bin iki yüze çıktı, bu yüzde on sekizlik bir artış."),
    (171, 190, "others", "Ödeme başarı oranı yüzde doksan üç virgül dörtte kaldı. Hedef yüzde doksan beşti."),
    (191, 212, "others", "Başarısız ödemelerin büyük kısmı eski sağlayıcının üç boyutlu doğrulama ekranında zaman aşımına uğruyor."),
    (213, 228, "me", "Bu zaman aşımları belirli bir bankada mı yoğunlaşıyor, yoksa genel mi?"),
    (229, 250, "others", "İki bankada yoğunlaşıyor ama sorun sağlayıcı tarafında. Ayşe bunun dökümünü çıkardı, toplantıdan sonra paylaşacak."),
    (251, 270, "others", "Destek talepleri bin iki yüz kırka düştü, yüzde dokuz azalma var. Bunun ana sebebi yeni yardım merkezi."),
    (271, 292, "others", "Churn oranı yüzde üç virgül birde sabit kaldı. Burada bir iyileşme göremedik."),
    (293, 312, "me", "Churn tarafında ödeme hatası yaşayan kullanıcıların payını biliyor muyuz?"),
    (313, 328, "others", "Henüz bilmiyoruz. Bu analizi yapmamız lazım, şu an elimde net bir sayı yok."),
    (332, 352, "others", "Şimdi asıl konuya gelelim: ödeme altyapısı geçişi. Eski sağlayıcıyla sözleşmemiz otuz Kasım'da bitiyor."),
    (353, 374, "others", "Yeni sağlayıcı üç boyutlu güvenliğin iki nokta sıfır sürümünü ve taksit desteğini sunuyor."),
    (375, 396, "others", "Geçişi üç aşamada, toplam altı haftada yapmayı planlıyoruz. Önce yeni kullanıcılar, sonra yüzde yirmi trafik, sonra herkes."),
    (397, 416, "me", "Altı hafta bana sıkışık geliyor. Kayıtlı kartların taşınması tek başına iki hafta sürebilir."),
    (417, 440, "others", "Haklısın, en büyük risk kayıtlı kartların taşınması. Sağlayıcı bunun için bir aktarım aracı veriyor ama henüz test etmedik."),
    (441, 462, "others", "Mehmet, aktarım aracını test ortamında deneyip gelecek Cuma'ya kadar bir rapor çıkarabilir misin?"),
    (463, 474, "others", "Olur, Cuma'ya kadar hazırlarım."),
    (475, 496, "others", "O zaman şuna karar verelim: geçişe yirmi Ekim'de başlıyoruz ve ilk aşamada yalnızca yeni kullanıcıları alıyoruz."),
    (497, 516, "me", "Katılıyorum. Geri dönüş planını da yazılı hale getirelim, onu ben üstlenirim."),
    (522, 542, "others", "Son başlık bütçe ve ekip. Üçüncü çeyrek için bütçemiz iki virgül dört milyon lira."),
    (543, 562, "others", "Bu bütçeyle iki yeni backend geliştirici almayı planlıyoruz. İlanlar bu hafta açılacak."),
    (563, 586, "others", "Mobil uygulama için dış ajans teklifi üç yüz seksen bin lira. Alternatifi, işi kendi ekibimizle yapmak ama o zaman takvim iki ay uzuyor."),
    (587, 606, "me", "Ajansla çalışırsak kod kimde kalacak? Bakımını biz yapabilecek miyiz?"),
    (607, 628, "others", "Kod bizde kalıyor, sözleşmede var. Ama bakım için ekipten birinin sürece baştan dahil olması gerekiyor."),
    (629, 650, "others", "Ajans konusunda bugün karar vermeyelim. On beş Ekim'e kadar teklifleri karşılaştırıp karar vereceğiz."),
    (651, 668, "others", "Zeynep, iki ajanstan daha teklif alabilir misin? On beş Ekim'den önce elimizde olsun."),
    (669, 680, "others", "Tamam, başka soru yoksa burada bitirelim. Herkese teşekkürler."),
]  # fmt: skip

# (time shown, title, bullet lines)
_ROADMAP_SLIDES = [
    (5, "Q3 Yol Haritası: Gündem", ["1. Q2 sonuçları", "2. Ödeme altyapısı geçişi", "3. Mobil uygulama", "4. Bütçe ve ekip"]),
    (150, "Q2 Sonuçları", ["Aktif kullanıcı: 48.200 (+%18)", "Ödeme başarı oranı: %93,4 (hedef %95)", "Destek talebi: 1.240 (−%9)", "Churn: %3,1"]),
    (330, "Ödeme Altyapısı Geçişi", ["Eski sağlayıcı sözleşmesi 30 Kasım'da bitiyor", "Yeni sağlayıcı: 3D Secure 2.0, taksit desteği", "Geçiş: 3 aşama, 6 hafta", "Risk: kayıtlı kartların taşınması"]),
    (520, "Bütçe ve Ekip", ["Q3 bütçesi: 2,4 milyon TL", "2 yeni backend geliştirici", "Mobil için dış ajans teklifi: 380 bin TL", "Ajans kararı: 15 Ekim"]),
]  # fmt: skip

# One scripted model reply per block: (first segment, last segment, reply).
# "ref" is the line number inside that block's excerpt, as a real model would cite it.
_ROADMAP_BLOCKS = [
    (0, 7, {
        "topic": "Gündem ve Q2 hedefi",
        "points": [
            {"text": "Gündem: Q2 sonuçları, ödeme altyapısı geçişi, mobil uygulama, bütçe ve ekip.", "ref": 2},
            {"text": "Toplantı sonunda iki karar bekleniyor: geçiş takvimi ve mobil için dış ajans.", "ref": 3},
            {"text": "Q2'nin ana hedefi ödeme başarı oranını %95'e çıkarmaktı; hedef tutmadı.", "ref": 6},
        ],
        "decisions": [],
        "actions": [{"task": "Güvenlik denetimi raporu gelince ayrı bir toplantı ayarlamak", "owner": None, "due": None, "ref": 5}],
        "questions": [],
    }),
    (8, 16, {
        "topic": "Q2 sonuçları",
        "points": [
            {"text": "Aktif kullanıcı 48.200'e çıktı (+%18).", "ref": 1},
            {"text": "Ödeme başarı oranı %93,4'te kaldı; hedef %95'ti.", "ref": 2},
            {"text": "Başarısız ödemelerin çoğu eski sağlayıcının 3D doğrulama ekranındaki zaman aşımından kaynaklanıyor; iki bankada yoğunlaşıyor.", "ref": 3},
            {"text": "Destek talepleri 1.240'a düştü (−%9); ana sebep yeni yardım merkezi.", "ref": 6},
            {"text": "Churn %3,1'de sabit kaldı.", "ref": 7},
        ],
        "decisions": [],
        "actions": [{"task": "Banka bazlı zaman aşımı dökümünü paylaşmak", "owner": "Ayşe", "due": "toplantıdan sonra", "ref": 5}],
        "questions": [{"text": "Churn eden kullanıcıların ne kadarı ödeme hatası yaşadı? Analiz henüz yapılmadı.", "ref": 8}],
    }),
    (17, 25, {
        "topic": "Ödeme altyapısı geçişi",
        "points": [
            {"text": "Eski sağlayıcıyla sözleşme 30 Kasım'da bitiyor.", "ref": 1},
            {"text": "Yeni sağlayıcı 3D Secure 2.0 ve taksit desteği sunuyor.", "ref": 2},
            {"text": "Geçiş üç aşamada, toplam altı haftada planlanıyor: yeni kullanıcılar, %20 trafik, herkes.", "ref": 3},
            {"text": "En büyük risk kayıtlı kartların taşınması; sağlayıcının aktarım aracı henüz test edilmedi.", "ref": 5},
        ],
        "decisions": [{"text": "Geçişe 20 Ekim'de başlanacak; ilk aşamada yalnızca yeni kullanıcılar alınacak.", "ref": 8}],
        "actions": [
            {"task": "Aktarım aracını test ortamında deneyip rapor çıkarmak", "owner": "Mehmet", "due": "gelecek Cuma", "ref": 6},
            {"task": "Geri dönüş planını yazılı hale getirmek", "owner": "Ben", "due": None, "ref": 9},
        ],
        "questions": [],
    }),
    (26, 33, {
        "topic": "Bütçe, işe alım ve mobil ajans",
        "points": [
            {"text": "Q3 bütçesi 2,4 milyon TL.", "ref": 1},
            {"text": "İki yeni backend geliştirici alınacak; ilanlar bu hafta açılıyor.", "ref": 2},
            {"text": "Mobil için dış ajans teklifi 380 bin TL; işi içeride yapmak takvimi iki ay uzatıyor.", "ref": 3},
            {"text": "Ajansla çalışılırsa kod şirkette kalıyor; bakım için ekipten biri sürece baştan dahil olmalı.", "ref": 5},
        ],
        "decisions": [{"text": "Ajans kararı bugün verilmedi; teklifler karşılaştırılıp 15 Ekim'e kadar karar verilecek.", "ref": 6}],
        "actions": [{"task": "İki ajanstan daha teklif almak", "owner": "Zeynep", "due": "15 Ekim'den önce", "ref": 7}],
        "questions": [],
    }),
]  # fmt: skip

_ROADMAP_OVERVIEW = {
    "title": "Q3 Yol Haritası: Ödeme Geçişi ve Bütçe",
    "tldr": "Q2'de aktif kullanıcı %18 arttı ama ödeme başarı oranı %93,4'te kalarak %95 hedefinin altında kaldı; sorunun kaynağı eski ödeme sağlayıcısı. Yeni sağlayıcıya geçiş 20 Ekim'de, önce yalnızca yeni kullanıcılarla başlayacak. Mobil uygulama için dış ajans kararı 15 Ekim'e bırakıldı.",
    "key_points": [
        "Ödeme hatalarının çoğu eski sağlayıcının 3D doğrulama ekranındaki zaman aşımından geliyor.",
        "Eski sağlayıcı sözleşmesi 30 Kasım'da bitiyor; geçiş üç aşamada altı hafta sürecek.",
        "En büyük risk kayıtlı kartların taşınması; aktarım aracı test edilecek.",
        "Q3 bütçesi 2,4 milyon TL; iki backend geliştirici alınacak.",
        "Mobil ajans teklifi 380 bin TL; içeride yapmak takvimi iki ay uzatıyor.",
    ],
}  # fmt: skip

_SYNC_TRANSCRIPT = [
    (3, 18, "others", "Günaydın. Bugün kısa tutalım, üç konu var: aktarım aracı testi, işe alım ve dünkü kesinti."),
    (19, 42, "others", "Aktarım aracını test ortamında denedim. On bin kayıtlı kartın dokuz bin yedi yüz ellisi sorunsuz taşındı."),
    (43, 64, "others", "Kalan iki yüz elli kartta son kullanma tarihi geçmiş ya da banka kartı yenilemiş. Bunlar taşınamıyor, kullanıcıdan yeniden istememiz gerekecek."),
    (65, 80, "me", "Yani başarı oranı yüzde doksan yedi buçuk. Canlıda da benzer bir oran bekleyebilir miyiz?"),
    (81, 104, "others", "Büyük ihtimalle evet. Ama aktarım saatte en fazla elli bin kart işleyebiliyor, tüm kartlar için yaklaşık altı saatlik bir pencere lazım."),
    (105, 126, "others", "O zaman taşımayı gece yapalım. Yirmi yedi Ekim gecesi saat ikide başlatmayı öneriyorum."),
    (127, 140, "me", "Bana uyar. Geri dönüş planını dün wiki'ye yazdım, herkes bir göz atsın."),
    (141, 160, "others", "Anlaştık, kart taşıma yirmi yedi Ekim gecesi yapılacak. Mehmet operasyonu yönetecek."),
    (190, 210, "others", "İşe alıma geçelim. Backend ilanına kırk iki başvuru geldi, altı kişiyle ilk görüşmeyi yaptık."),
    (211, 232, "others", "İki aday teknik mülakata kaldı. Mülakatları bu hafta Perşembe ve Cuma günü yapacağız."),
    (233, 250, "me", "Teknik mülakata ben de girebilirim, Perşembe öğleden sonra uygunum."),
    (251, 276, "others", "Süper. Son konu dünkü kesinti. Saat on dört on ikide bildirim servisi yirmi üç dakika boyunca yanıt vermedi."),
    (277, 300, "others", "Sebep, kuyruktaki mesajların birikmesiyle belleğin dolması. Otomatik ölçekleme devreye girmedi çünkü eşik yanlış ayarlanmış."),
    (301, 322, "others", "Eşiği düzelttim ama kalıcı çözüm için kuyruğa bir üst sınır koymamız lazım. Bunu kim alabilir?"),
    (323, 338, "others", "Ben alırım, hafta sonuna kadar bitiririm."),
    (339, 356, "me", "Kesinti için müşterilere bir açıklama gönderdik mi?"),
    (357, 376, "others", "Henüz göndermedik. Destek ekibiyle konuşmam lazım, bunu bilmiyorum."),
    (377, 390, "others", "Tamam, bu kadar. Haftaya aynı saatte görüşürüz."),
]  # fmt: skip

_SYNC_BLOCKS = [
    (0, 7, {
        "topic": "Kayıtlı kart aktarım testi",
        "points": [
            {"text": "Test ortamında 10.000 kayıtlı kartın 9.750'si sorunsuz taşındı (%97,5).", "ref": 2},
            {"text": "Taşınamayan 250 kartın son kullanma tarihi geçmiş ya da kart yenilenmiş; kullanıcıdan yeniden istenecek.", "ref": 3},
            {"text": "Araç saatte en fazla 50.000 kart işliyor; tüm kartlar için yaklaşık altı saatlik pencere gerekiyor.", "ref": 5},
            {"text": "Geri dönüş planı wiki'ye yazıldı.", "ref": 7},
        ],
        "decisions": [{"text": "Kart taşıma 27 Ekim gecesi saat 02:00'de yapılacak; operasyonu Mehmet yönetecek.", "ref": 8}],
        "actions": [{"task": "Wiki'deki geri dönüş planını gözden geçirmek", "owner": "Herkes", "due": None, "ref": 7}],
        "questions": [],
    }),
    (8, 17, {
        "topic": "İşe alım ve bildirim servisi kesintisi",
        "points": [
            {"text": "Backend ilanına 42 başvuru geldi; altı ön görüşme yapıldı, iki aday teknik mülakata kaldı.", "ref": 1},
            {"text": "Bildirim servisi dün 14:12'de 23 dakika boyunca yanıt vermedi.", "ref": 4},
            {"text": "Sebep: kuyrukta biriken mesajların belleği doldurması; otomatik ölçekleme eşiği yanlış ayarlandığı için devreye girmedi.", "ref": 5},
            {"text": "Eşik düzeltildi; kalıcı çözüm olarak kuyruğa üst sınır konacak.", "ref": 6},
        ],
        "decisions": [],
        "actions": [
            {"task": "Teknik mülakatları yapmak", "owner": None, "due": "Perşembe ve Cuma", "ref": 2},
            {"task": "Bildirim kuyruğuna üst sınır koymak", "owner": None, "due": "hafta sonu", "ref": 7},
        ],
        "questions": [{"text": "Kesinti için müşterilere açıklama gönderildi mi? Destek ekibiyle konuşulacak.", "ref": 9}],
    }),
]  # fmt: skip

_SYNC_OVERVIEW = {
    "title": "Haftalık Mühendislik Senkronu",
    "tldr": "Kayıtlı kart aktarım aracı testte %97,5 başarıyla çalıştı; taşıma 27 Ekim gecesi yapılacak. Backend işe alımında iki aday teknik mülakata kaldı. Dünkü 23 dakikalık bildirim servisi kesintisinin sebebi yanlış ayarlanmış ölçekleme eşiğiydi; kuyruğa üst sınır konacak.",
    "key_points": [
        "10.000 kartın 9.750'si sorunsuz taşındı; kalan 250 kart kullanıcıdan yeniden istenecek.",
        "Aktarım saatte 50.000 kartla sınırlı; taşıma için altı saatlik gece penceresi seçildi.",
        "Backend ilanına 42 başvuru geldi, iki aday teknik mülakata kaldı.",
        "Bildirim servisi kesintisinin kök nedeni bellek dolması ve yanlış ölçekleme eşiği.",
    ],
}  # fmt: skip


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = ["arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else ["arial.ttf", "DejaVuSans.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def draw_slide(title: str, lines: list[str], number: int, total: int) -> Image.Image:
    """A plain presentation slide; also used to build test videos."""
    image = Image.new("RGB", (1600, 900), "#fbfaf7")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 1600, 190], fill="#1d3a4f")
    draw.rectangle([0, 190, 1600, 198], fill="#e0a43a")
    draw.text((90, 62), title, fill="#ffffff", font=_font(64, bold=True))
    y = 270
    for line in lines:
        numbered = line[:1].isdigit() and line[1:2] == "."
        if not numbered:
            draw.ellipse([96, y + 20, 112, y + 36], fill="#e0a43a")
        draw.text((96 if numbered else 140, y), line, fill="#22272b", font=_font(44))
        y += 96
    draw.text((1440, 830), f"{number} / {total}", fill="#8a8f94", font=_font(26))
    return image


def _scripted(replies: list[dict]) -> ScriptedLLM:
    queue = [json.dumps(reply, ensure_ascii=False) for reply in replies]

    def respond(messages: Messages, schema: type[BaseModel] | None) -> str:
        return queue.pop(0)

    return ScriptedLLM(respond, model="demo (scripted)")


def _seed(
    services: Services,
    *,
    started_at: datetime,
    transcript: list[tuple],
    slides: list[tuple],
    blocks: list[tuple],
    overview: dict,
    bookmarks: list[tuple[float, str]],
) -> str:
    store = services.store
    meeting_id = store.create_meeting(
        overview["title"], "demo", status="processing", started_at=started_at, language="tr"
    )
    store.update_meeting(meeting_id, duration=float(transcript[-1][1] + 4))
    segments = [store.add_segment(meeting_id, s, e, text, who) for s, e, who, text in transcript]

    slides_dir: Path = services.cfg.meeting_dir(meeting_id) / "slides"
    slides_dir.mkdir(exist_ok=True)
    for number, (t, title, lines) in enumerate(slides, 1):
        name = f"{number:04d}.jpg"
        draw_slide(title, lines, number, len(slides)).save(slides_dir / name, "JPEG", quality=85)
        store.add_slide(meeting_id, float(t), name, "\n".join([title, *lines]))

    llm = _scripted([reply for _, _, reply in blocks] + [overview])
    meeting = store.get_meeting(meeting_id)
    previous = None
    for first, last, _ in blocks:
        note = make_block(
            store,
            llm,
            meeting,
            segments[first : last + 1],
            language=LANGUAGE,
            previous_topic=previous,
        )
        previous = note["topic"] if note else previous
    summarize(store, llm, meeting_id, language=LANGUAGE)
    for t, label in bookmarks:
        store.add_bookmark(meeting_id, t, label)
    index_meeting(store, services.llm, meeting_id)
    store.update_meeting(meeting_id, status="ready")
    return meeting_id


def seed_demo(services: Services) -> list[str]:
    """Create the sample meetings unless they already exist. Returns their ids."""
    existing = [m["id"] for m in services.store.list_meetings() if m["source"] == "demo"]
    if existing:
        return existing
    today = datetime.now().replace(minute=0, second=0, microsecond=0)
    return [
        _seed(
            services,
            started_at=(today - timedelta(days=9)).replace(hour=10),
            transcript=_ROADMAP_TRANSCRIPT,
            slides=_ROADMAP_SLIDES,
            blocks=_ROADMAP_BLOCKS,
            overview=_ROADMAP_OVERVIEW,
            bookmarks=[(397.0, "Takvim itirazı")],
        ),
        _seed(
            services,
            started_at=(today - timedelta(days=2)).replace(hour=9, minute=30),
            transcript=_SYNC_TRANSCRIPT,
            slides=[],
            blocks=_SYNC_BLOCKS,
            overview=_SYNC_OVERVIEW,
            bookmarks=[],
        ),
    ]
