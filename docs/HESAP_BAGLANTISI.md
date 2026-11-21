# Hesap bağlantısı — 23 Ekim 2026

The5ers hesabına 20:56 İstanbul saatinde bağlanıldı.

- Hesap geçmişi okunuyor. Komisyon, swap ve brokerın ayrı ücret alanları kaydediliyor.
- Hesap değiştirilmedi. Yeni emir gönderilmedi. Gerçek işlem kilidi kaldırılmadı.
- Arka plandaki kayıt her 60 saniyede yenileniyor.
- Megalord'un ortak gölge portföyü ayrı çalışıyor.

İlk sorguda hesap geçmişinde dört kayıt görüldü. Bunlar Megalord'un yeni ileri testine otomatik eklenmedi. Bir işlemin Megalord'a ait olduğunu söylemek için sinyal ile broker pozisyon numarası açıkça eşleşmeli. Yakın fiyat veya yakın saat yeterli sayılmıyor.

Bu hesap, ayrı ve ücretsiz bir demo hesap diye yeniden adlandırılmadı. Sunucu `FivePercentOnline-Real`; MT5 hesap türü alanı 0 döndü. Bu iki bilgi tek başına hesabın ekonomik koşullarını veya canlı emir yetkisini kanıtlamaz. Kullanıcının isteği bu hesaba okuma bağlantısı kurmak için uygulandı.

**Kâr henüz kanıtlanmadı.** Megalord gölge portföyünde şu an kapanmış işlem yok. Hesap geçmişinin okunması, gölge işlemlerin brokerda gerçekten dolduğu anlamına gelmez. Bilinmeyen giderler sıfır yazılmaz.

Kod tarafında eklenenler: `megalord/research/demo_journal.py` giriş ve çıkış komisyonlarını birlikte toplar, eksik veya kısmi kapanışı tamamlanmış işlem saymaz. `account_observer.py` kullanıcının seçtiği The5ers hesabını emir göndermeden gözler. Bağlı hesap değişirse kayıt durur.

Son odaklı testler: **11/11 geçti**. Daha önce muhasebe/pozisyon kontrolleriyle birlikte **77 test geçti**. Bunlar örtüşen kontrollerdir; ayrı piyasa örneklemi değildir.

Güncel bağlantı dosyası: `audit/account_observer/current.json`. İlk oturum: `audit/account_observer/20261023T175615_480675Z/`. `status.json` güncel sonucu; `raw/` değişmemiş hesap kayıtlarını içerir. Bu dosyalar kişisel hesap verisi içerir; bu çalışma onları dışarı yayımlamaz.

Durdurmak için etkin hesap kayıt klasörüne `STOP` adlı boş dosya koyulur. Hesap şifresi bu kodda veya örnek ayar dosyalarında saklanmaz.

Ayrı demo hesabı daha sonra kullanılacaksa hesap açma adımı [MetaTrader'ın resmi açıklamasında](https://www.metatrader5.com/en/terminal/help/startworking/acc_open) bulunur. Şu anki bağlantı için ayrı demo hesabı açılmış olduğu iddia edilmiyor.
