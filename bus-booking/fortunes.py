"""A stable, paired Russian and Turkish collection for passenger bookings."""
import secrets

# Keep these original eight entries and translations unchanged for old bookings and QR codes.
_LEGACY = [
 ('Сегодня удача едет с вами!', 'Bugün şans sizinle!'),
 ('Вас ждёт приятный день!', 'Sizi güzel bir gün bekliyor!'),
 ('Хорошие новости уже в пути!', 'İyi haberler yolda!'),
 ('Пусть дорога будет лёгкой!', 'Yolunuz açık olsun!'),
 ('Улыбка откроет новые возможности!', 'Bir gülümseme yeni fırsatlar açar!'),
 ('Счастливые события совсем рядом!', 'Güzel olaylar çok yakın!'),
 ('Сегодня отличный день для новых идей!', 'Bugün yeni fikirler için harika bir gün!'),
 ('Пусть всё сложится наилучшим образом!', 'Her şey gönlünüzce olsun!'),
]

# Two complete sentences make each pairing natural in both languages.
_OPENINGS = [
 ('Пусть сегодняшний день принесёт вам радость!', 'Bugün size neşe getirsin!'),
 ('Пусть дорога подарит вам хорошее настроение!', 'Yolculuk size güzel bir ruh hâli getirsin!'),
 ('Пусть сегодня всё получится!', 'Bugün her şey yolunda gitsin!'),
 ('Желаем вам прекрасного дня!', 'Size harika bir gün dileriz!'),
 ('Пусть удача будет рядом!', 'Şans hep yanınızda olsun!'),
 ('Пусть день начнётся с улыбки!', 'Gününüz bir gülümsemeyle başlasın!'),
 ('Пусть сегодня найдётся повод для радости!', 'Bugün sevinmek için bir nedeniniz olsun!'),
 ('Пусть путешествие будет спокойным!', 'Yolculuğunuz huzurlu geçsin!'),
 ('Пусть впереди будет много хорошего!', 'Önünüzde birçok güzel şey olsun!'),
 ('Пусть день приятно удивит вас!', 'Gününüz sizi güzelce şaşırtsın!'),
 ('Желаем вам лёгкой дороги!', 'Size rahat bir yolculuk dileriz!'),
 ('Пусть хорошее настроение сопровождает вас!', 'İyi ruh hâli size eşlik etsin!'),
 ('Пусть этот день будет добрым!', 'Bugününüz güzel geçsin!'),
 ('Пусть сегодня вам сопутствует удача!', 'Bugün şans size eşlik etsin!'),
 ('Пусть каждый час приносит что-то хорошее!', 'Her saat size güzel bir şey getirsin!'),
 ('Желаем вам спокойствия и сил!', 'Size huzur ve güç dileriz!'),
 ('Пусть день подарит приятные моменты!', 'Gününüz size güzel anlar getirsin!'),
 ('Пусть ваши планы легко осуществятся!', 'Planlarınız kolayca gerçekleşsin!'),
 ('Желаем вам больше поводов улыбаться!', 'Size gülümsemek için daha çok neden dileriz!'),
 ('Пусть сегодня всё складывается удачно!', 'Bugün işleriniz rast gitsin!'),
 ('Пусть дорога будет уютной и спокойной!', 'Yolculuğunuz rahat ve huzurlu olsun!'),
 ('Пусть этот день оставит хорошие впечатления!', 'Bugün sizde güzel izler bıraksın!'),
 ('Желаем вам вдохновения!', 'Size ilham dolu bir gün dileriz!'),
 ('Пусть рядом будут добрые люди!', 'Yanınızda iyi insanlar olsun!'),
 ('Пусть день принесёт новые возможности!', 'Gününüz yeni fırsatlar getirsin!'),
 ('Пусть дорога пройдёт приятно!', 'Yolculuğunuz keyifli geçsin!'),
 ('Желаем вам солнечного настроения!', 'Size güneşli bir ruh hâli dileriz!'),
 ('Пусть всё задуманное удаётся!', 'Dilediğiniz her şey gerçekleşsin!'),
 ('Пусть сегодня будет больше улыбок!', 'Bugün daha çok gülümseme olsun!'),
 ('Пусть день начнётся легко!', 'Gününüz güzel bir başlangıçla başlasın!'),
 ('Желаем вам удачи в каждом деле!', 'Her işinizde size başarı dileriz!'),
 ('Пусть сегодня случится что-то приятное!', 'Bugün güzel bir şey olsun!'),
]
_ENDINGS = [
 ('Впереди вас ждут приятные встречи.', 'Önünüzde keyifli buluşmalar var.'),
 ('Пусть рядом всегда будет поддержка.', 'Destek her zaman yanınızda olsun.'),
 ('Хорошие новости найдут к вам дорогу.', 'İyi haberler size ulaşsın.'),
 ('Пусть найдётся время для отдыха.', 'Dinlenmek için zamanınız olsun.'),
 ('Пусть хороших событий станет больше.', 'Güzel olaylar çoğalsın.'),
 ('Улыбка сделает этот день теплее.', 'Bir gülümseme bu günü ısıtsın.'),
 ('Пусть всё важное получается вовремя.', 'Önemli işleriniz zamanında tamamlansın.'),
 ('Пусть в пути вас ждёт комфорт.', 'Yolculuğunuz konforlu geçsin.'),
 ('Каждый день приносит новые возможности.', 'Her gün yeni fırsatlar getirir.'),
 ('Пусть близкие радуют вас вниманием.', 'Sevdikleriniz ilgileriyle sizi mutlu etsin.'),
 ('Пусть рядом будет хороший собеседник.', 'Yanınızda iyi bir sohbet arkadaşı olsun.'),
 ('Пусть маленькие радости встречаются чаще.', 'Küçük mutluluklar daha sık karşınıza çıksın.'),
 ('Пусть задуманное постепенно становится реальностью.', 'Hayalleriniz adım adım gerçekleşsin.'),
 ('Пусть день завершится приятным вечером.', 'Gününüz keyifli bir akşamla bitsin.'),
 ('Пусть работа приносит удовлетворение.', 'İşiniz size mutluluk versin.'),
 ('Пусть каждый новый шаг радует вас.', 'Her yeni adımınız sizi mutlu etsin.'),
 ('Пусть впереди будет время для любимых дел.', 'Sevdiğiniz şeylere zaman ayırabilin.'),
 ('Пусть приятные новости не заставят себя ждать.', 'Güzel haberler gecikmeden gelsin.'),
 ('Пусть этот день подарит вам энергию.', 'Bugün size enerji versin.'),
 ('Пусть дома вас ждут тепло и уют.', 'Evinizde sizi sıcaklık ve huzur karşılasın.'),
 ('Пусть все встречи пройдут хорошо.', 'Tüm görüşmeleriniz güzel geçsin.'),
 ('Пусть сегодня найдётся время для улыбки.', 'Bugün gülümsemek için zamanınız olsun.'),
 ('Пусть ваши усилия принесут хорошие результаты.', 'Emekleriniz güzel sonuçlar getirsin.'),
 ('Пусть рядом всегда будут надёжные друзья.', 'Güvenilir dostlarınız hep yanınızda olsun.'),
 ('Пусть этот день запомнится чем-то хорошим.', 'Bugün güzel bir şeyle aklınızda kalsın.'),
 ('Пусть вам сопутствуют спокойствие и уверенность.', 'Huzur ve güven size eşlik etsin.'),
 ('Пусть планы радуют, а дела спорятся.', 'Planlarınız sizi mutlu etsin, işleriniz kolaylaşsın.'),
 ('Пусть удачные идеи приходят легко.', 'Güzel fikirler kolayca aklınıza gelsin.'),
 ('Пусть на всё задуманное хватает сил.', 'Planladığınız her şeye gücünüz yetsin.'),
 ('Пусть в вашем дне будет больше света.', 'Gününüz daha aydınlık olsun.'),
 ('Пусть поводов для благодарности станет больше.', 'Şükredecek daha çok şeyiniz olsun.'),
]

_PAIRS = _LEGACY + [(a + ' ' + b, x + ' ' + y)
                    for a, x in _OPENINGS for b, y in _ENDINGS]
FORTUNES = tuple(ru for ru, _ in _PAIRS)
TRANSLATIONS = dict(_PAIRS)

def fortune_tr(value):
    return TRANSLATIONS.get(value, value)

def choose_fortune(already_in_use):
    available = [value for value in FORTUNES if value not in already_in_use]
    # Never prevent a booking if more than 1,000 confirmations are active at once.
    return secrets.choice(available or FORTUNES)
