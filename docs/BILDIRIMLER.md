# Megalord bildirimleri

Kullanıcının isteğiyle gölge test için Telegram bildirimleri açıldı. Mevcut Megalord botu ve hedef sohbeti kullanılır. Hesap şifreleri veya bot anahtarları mesajlara yazılmaz.

Mesaj türleri: yeni sinyal, girişin engellenmesi/beklemesi, gölge giriş, kısmi/nihai gölge çıkış, kaynak veya portföy takibinin durması ve yeniden çalışması.

Her mesaj “GÖLGE TEST” başlığını taşır. Fiyat takibindeki R sonucu bütün masraflar sonrası net kâr diye sunulmaz. Gerçek emir gönderimi kapalıdır.

Güncelleme: sinyal ve işlem mesajlarında o test kesitinin gerçekleşen fiyat PnL'si, açık fiyat PnL'si, kapanan işlem sayısı, TP1/TP2 vuruşları ve SL sayısı gösterilir. İki bacağın aynı stopla kapanması tek SL işlemidir. TP1 sonrası toplamı zararsız/kârlı bitiren koruma stopu SL zarar sayacına eklenmez. TP sayaçları hedef vuruşudur; TP1 ve TP2 aynı işlemde ayrı sayılabilir.

Komisyon/swap/broker ücreti sonrası nakit sonuç, yalnız ilgili Megalord işlemine eşleştirilmesi doğrulanmış broker kayıtları varsa gösterilir. Başka The5ers işlemleri toplama eklenmez. Veri yoksa “ölçülmedi” yazar; sıfır veya tahmini kâr yazılmaz. İşletme giderleri dahil tam net sonuç ayrı doğrulama gerektirir. Komisyon broker nakit sonucunda zaten bulunuyorsa tekrar düşülmez. Gölge giriş mesajında SL, TP1 ve TP2 fiyatları da yer alır.

Bildirimler ayrı `megalord.research.forward_notifications` sürecinde çalışır. Tarama ve portföy süreçleri yeniden başlatılmadı; dondurulmuş işlem kuralları değiştirilmedi. Donmuş araştırma protokolündeki bildirim kapalı alanı o süreçlerin kendi göndericileri için geçerlidir; yeni yan süreç kayıtları okuyup mesaj gönderir.

İlk açılıştan önceki eski kayıtlar topluca gönderilmez. Tekrarlanan bekleme/veto kararları aynı sinyal ve sebep için tekrar tekrar bildirilmez. Başarısız gönderimler kuyrukta saklanır ve tekrar denenir. Gönderimden hemen sonra, durum diske yazılmadan süreç kapanırsa aynı mesaj tekrar gelebilir; Telegram ile tam bir kez teslim garantisi yoktur.

Durum ve gönderim kuyruğu `audit/notifications/state.json`, çalışma kayıtları `stdout.log` ve `stderr.log` içindedir. Durdurmak için bu klasöre `STOP` adlı boş dosya koyulur. Bilgisayar kapanınca otomatik açılış kurulmuş değildir.

Yeni bildirim testleri 4/4 geçti. İlk bağlantı mesajının teslim sonucu ayrıca çalışma kaydından doğrulanmalıdır; yalnız testlerin geçmesi Telegram teslimi demek değildir.

PnL/sayaç güncellemesi sonrası bildirim testleri toplam 9/9 geçti. Testler çift SL sayımını, kısmi TP'yi, koruma stopunu, bilinmeyen komisyonu ve başka hesaptaki kârın Megalord'a karışmamasını kontrol eder. `state.json` içindeki `format-v2` gönderim kaydı yeni özet mesajının Telegram tarafından kabul edilip edilmediğini gösterir.
